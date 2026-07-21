import argparse
import asyncio
import os
import signal
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from flipthis_video_maker.config.render_finalization import (
    RENDER_FINALIZATION_EXECUTION_KEY,
    RenderFinalizationExecution,
    render_finalization_execution_from_payload,
)
from flipthis_video_maker.config.render_profiles import (
    RENDER_PROFILE_EXECUTION_KEY,
    RenderProfileConfigurationFile,
    RenderProfileExecution,
    load_render_profile_configuration,
    render_profile_execution_from_payload,
)
from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.config.workers import load_worker_configuration
from flipthis_video_maker.contracts.video_generation import FirstLastFrameGenerationRequest
from flipthis_video_maker.database.session import SessionLocal
from flipthis_video_maker.domain.enums import JobState, WorkerState
from flipthis_video_maker.domain.models import Asset, Job, Project, VideoChain, VideoChainClip
from flipthis_video_maker.media.ffmpeg import MediaCancelled
from flipthis_video_maker.pipeline.mock_pipeline import MockPipeline, PipelineCancelled
from flipthis_video_maker.pipeline.shot_regeneration import MockShotRegenerator
from flipthis_video_maker.pipeline.video_chain import VideoChainPipeline
from flipthis_video_maker.providers.base.errors import (
    ProviderCleanupResult,
    ProviderExecutionError,
    ProviderFailureKind,
    ProviderOutOfMemoryError,
)
from flipthis_video_maker.providers.base.models import Capability, ImageRequest
from flipthis_video_maker.providers.base.protocols import OOMRecoverableProvider
from flipthis_video_maker.providers.registry import (
    configured_first_last_frame_provider,
    configured_image_provider,
    configured_perceptual_metric_provider,
)
from flipthis_video_maker.scheduler.gpu import (
    GPULock,
    GPUTelemetryRecorder,
    evaluate_vram_admission,
    probe_gpus,
)
from flipthis_video_maker.services.asset_inputs import validated_asset_input
from flipthis_video_maker.services.chain_automation import (
    auto_accept_publish_and_replenish,
    continue_after_generated_target,
)
from flipthis_video_maker.services.job_logs import append_job_log
from flipthis_video_maker.services.jobs import (
    claim_next,
    has_queued,
    mark_cancelled,
    mark_failed,
    mark_succeeded,
    reconcile_expired_job_leases,
)
from flipthis_video_maker.services.render_finalization import revalidate_render_finalization
from flipthis_video_maker.services.target_frames import (
    TARGET_FRAME_ASSET_ID_KEY,
    target_request_from_job,
)
from flipthis_video_maker.services.video_chains import (
    CHAIN_CLIP_ID_KEY,
    FLF_REQUEST_KEY,
    SUPPORTED_KEYFRAME_MIME_TYPES,
    request_from_clip,
    request_input_asset_ids,
)
from flipthis_video_maker.services.workers import WorkerHeartbeatReporter
from flipthis_video_maker.storage.assets import register_asset

logger = structlog.get_logger(__name__)
WorkerStatusCallback = Callable[[WorkerState, str | None], None]


class JobLeaseLost(RuntimeError):
    pass


def _update_owned_running_job(
    db: Session,
    job: Job,
    values: dict[str, object],
    *,
    worker_id: str | None,
    worker_instance_id: str | None,
) -> None:
    """Persist execution state only while this worker generation owns a live lease."""
    timestamp = datetime.now(UTC)
    statement = update(Job).where(
        Job.id == job.id,
        Job.state == JobState.RUNNING.value,
    )
    if worker_id is not None and worker_instance_id is not None:
        statement = statement.where(
            Job.claimed_by_worker_id == worker_id,
            Job.claimed_by_instance_id == worker_instance_id,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > timestamp,
        )
    updated_id = db.execute(
        statement.values(**values).returning(Job.id).execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if updated_id is None:
        db.rollback()
        raise JobLeaseLost("Job lease was lost while recording execution state")
    db.commit()
    db.refresh(job)


async def process_next(
    db: Session,
    assignment: str,
    *,
    physical_gpu: int | None = None,
    minimum_free_vram_mb: int = 0,
    worker_status: WorkerStatusCallback | None = None,
    worker_id: str | None = None,
    worker_instance_id: str | None = None,
    shutdown_requested: Callable[[], bool] | None = None,
    render_profiles: RenderProfileConfigurationFile | None = None,
    lease_seconds: float = 30,
) -> bool:
    """Admit, atomically claim, and process at most one exact-queue job."""
    recoveries = reconcile_expired_job_leases(db)
    for recovery in recoveries:
        logger.error(
            "expired_job_lease_reconciled",
            job_id=recovery.job_id,
            previous_state=recovery.previous_state.value,
            recovered_state=recovery.recovered_state.value,
            worker_id=recovery.worker_id,
            worker_instance_id=recovery.worker_instance_id,
            expired_at=recovery.expired_at.isoformat(),
            retry_safe=False,
        )
    lock = GPULock(str(physical_gpu)) if physical_gpu is not None else nullcontext()
    with lock:
        assigned_gpu_metrics: dict[str, object] | None = None
        if physical_gpu is not None:
            if not has_queued(db, assignment):
                return False
            probe = probe_gpus()
            decision = evaluate_vram_admission(
                probe,
                physical_gpu=physical_gpu,
                minimum_free_vram_mb=minimum_free_vram_mb,
            )
            logger.info(
                "gpu_vram_admission",
                assignment=assignment,
                **decision.model_dump(),
            )
            if not decision.admitted:
                return False
            assigned_gpu_metrics = next(
                metric.model_dump() for metric in probe.metrics if metric.index == physical_gpu
            )

        job = claim_next(
            db,
            assignment,
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
            lease_seconds=lease_seconds,
        )
        if job is None:
            return False
        if worker_status is not None:
            worker_status(WorkerState.BUSY, job.id)
        try:
            await _process_claimed_job(
                db,
                job,
                assignment,
                assigned_gpu_metrics,
                physical_gpu,
                shutdown_requested,
                render_profiles,
                worker_id,
                worker_instance_id,
            )
        finally:
            if worker_status is not None:
                worker_status(WorkerState.IDLE, None)
    return True


async def _process_claimed_job(
    db: Session,
    job: Job,
    assignment: str,
    assigned_gpu_metrics: dict[str, object] | None,
    physical_gpu: int | None,
    shutdown_requested: Callable[[], bool] | None,
    render_profiles: RenderProfileConfigurationFile | None,
    worker_id: str | None,
    worker_instance_id: str | None,
) -> None:
    log_path: Path | None = None
    post_success: Callable[[], object] | None = None
    post_success_chain: VideoChain | None = None
    try:
        project = db.get(Project, job.project_id)
        if project is None:
            raise RuntimeError(f"Job project is missing: {job.project_id}")
        profile_execution = _resolve_render_profile_execution(
            db,
            job,
            project,
            render_profiles,
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
        )
        finalization_execution: RenderFinalizationExecution | None = None
        music_asset: Asset | None = None
        if job.job_type == "mock_project_render":
            finalization_execution = _resolve_render_finalization_execution(
                db,
                job,
                worker_id=worker_id,
                worker_instance_id=worker_instance_id,
            )
            music_asset = revalidate_render_finalization(
                db,
                project,
                finalization_execution,
            )
            expected_input_asset_ids = [music_asset.id] if music_asset is not None else []
            if job.input_asset_ids != expected_input_asset_ids:
                raise RuntimeError("Render Job input Assets do not match its finalization snapshot")
        log_path = (
            Path(project.root_asset_directory)
            / "logs"
            / f"job-{job.id}-attempt-{job.attempt_number}.jsonl"
        )
        _update_owned_running_job(
            db,
            job,
            {"log_path": str(log_path), "current_stage": "rendering"},
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
        )
        append_job_log(
            log_path,
            "job_claimed",
            job_id=job.id,
            job_type=job.job_type,
            device=assignment,
            attempt=job.attempt_number,
            gpu_metrics=assigned_gpu_metrics,
            requested_render_profile=profile_execution.requested_profile,
            effective_render_profile=profile_execution.effective_profile,
            subtitle_mode=(
                finalization_execution.subtitle.mode if finalization_execution else None
            ),
            normalize_audio=(
                finalization_execution.audio.normalize if finalization_execution else None
            ),
            music_asset_id=music_asset.id if music_asset is not None else None,
        )
        bind = db.get_bind()
        job_id = job.id

        def cancellation_requested() -> bool:
            with Session(bind) as status_db:
                row = status_db.execute(
                    select(
                        Job.state,
                        Job.error_info,
                        Job.claimed_by_worker_id,
                        Job.claimed_by_instance_id,
                        Job.lease_expires_at,
                    ).where(Job.id == job_id)
                ).one_or_none()
            if row is None:
                raise RuntimeError(f"Job disappeared while running: {job_id}")
            state, persisted_error, claimed_worker, claimed_instance, lease_expires_at = row
            if state in {JobState.CANCEL_REQUESTED.value, JobState.CANCELLED.value}:
                return True
            if state != JobState.RUNNING.value:
                failure_kind = (
                    persisted_error.get("failure_kind")
                    if isinstance(persisted_error, dict)
                    else None
                )
                raise JobLeaseLost(
                    f"Job ownership was lost in state {state}; failure_kind={failure_kind}"
                )
            if worker_id is not None and worker_instance_id is not None:
                if claimed_worker != worker_id or claimed_instance != worker_instance_id:
                    raise JobLeaseLost("Job boot-generation ownership changed while running")
                if lease_expires_at is None or _as_utc(lease_expires_at) <= datetime.now(UTC):
                    raise JobLeaseLost("Job lease expired while work was running")
            if shutdown_requested is not None and shutdown_requested():
                return True
            return False

        def report_progress(value: float, stage: str) -> None:
            _update_owned_running_job(
                db,
                job,
                {"progress": value, "current_stage": stage},
                worker_id=worker_id,
                worker_instance_id=worker_instance_id,
            )
            append_job_log(log_path, "progress", progress=value, stage=stage)

        def persist_profile_fallback(
            advanced: RenderProfileExecution,
            _error: ProviderOutOfMemoryError,
            _cleanup: ProviderCleanupResult,
        ) -> None:
            payload = {
                **(job.payload or {}),
                RENDER_PROFILE_EXECUTION_KEY: advanced.model_dump(mode="json"),
            }
            _update_owned_running_job(
                db,
                job,
                {"payload": payload},
                worker_id=worker_id,
                worker_instance_id=worker_instance_id,
            )
            record = advanced.fallback_history[-1]
            _append_job_log_safely(
                log_path,
                "render_profile_fallback",
                **record.model_dump(mode="json"),
            )

        def record_provider_cleanup(
            error: ProviderOutOfMemoryError,
            cleanup: ProviderCleanupResult,
        ) -> None:
            _append_job_log_safely(log_path, "provider_oom", **error.to_safe_dict())
            _append_job_log_safely(
                log_path,
                "provider_cleanup",
                provider_id=cleanup.provider_id,
                completed=cleanup.completed,
                retry_safe=cleanup.retry_safe,
                action_code=cleanup.action_code,
            )

        if job.job_type == "mock_project_render":
            render = await MockPipeline(
                db,
                cancellation_requested,
                report_progress,
                render_profile_execution=profile_execution,
                render_finalization_execution=finalization_execution,
                music_asset=music_asset,
                job_attempt=job.attempt_number,
                gpu_assignment=assignment,
                profile_fallback=persist_profile_fallback,
                provider_cleanup=record_provider_cleanup,
            ).run(job.project_id)
            output_asset_id = render.creation_metadata.get("output_asset_id")
            if not isinstance(output_asset_id, str):
                raise RuntimeError("Render completed without a final output asset")
        elif job.job_type == "mock_shot_regeneration":
            if job.shot_id is None:
                raise RuntimeError("Shot regeneration job has no shot ID")
            prompt = job.payload.get("prompt")
            negative_prompt = job.payload.get("negative_prompt")
            settings = job.payload.get("generation_settings")
            candidate = await MockShotRegenerator(
                db,
                cancellation_requested,
                report_progress,
                render_profile_execution=profile_execution,
                job_attempt=job.attempt_number,
                gpu_assignment=assignment,
                profile_fallback=persist_profile_fallback,
                provider_cleanup=record_provider_cleanup,
            ).run(
                job.shot_id,
                same_seed=bool(job.payload.get("same_seed", True)),
                prompt=prompt if isinstance(prompt, str) else None,
                negative_prompt=negative_prompt if isinstance(negative_prompt, str) else None,
                generation_settings=settings if isinstance(settings, dict) else None,
            )
            if candidate.output_asset_id is None:
                raise RuntimeError("Shot regeneration completed without a candidate asset")
            output_asset_id = candidate.output_asset_id
        elif job.job_type == "video_chain_target_generation":
            target_request = target_request_from_job(job)
            chain = db.get(VideoChain, target_request.chain_id)
            if chain is None or chain.project_id != project.id:
                raise RuntimeError("Target-frame Job chain does not belong to its project")
            if target_request.predecessor_clip_id is not None:
                predecessor = db.get(VideoChainClip, target_request.predecessor_clip_id)
                if (
                    predecessor is None
                    or predecessor.chain_id != chain.id
                    or predecessor.actual_last_frame_asset_id
                    != target_request.continuity_source_asset_id
                ):
                    raise RuntimeError("Target-frame Job continuity lineage is stale")
            source, source_path = validated_asset_input(
                db,
                project,
                target_request.continuity_source_asset_id,
                allowed_mime_types=SUPPORTED_KEYFRAME_MIME_TYPES,
            )
            if job.input_asset_ids != [source.id]:
                raise RuntimeError("Target-frame Job inputs do not match its request snapshot")
            staged_asset_id = (job.payload or {}).get(TARGET_FRAME_ASSET_ID_KEY)
            if isinstance(staged_asset_id, str):
                target_asset, _target_path = validated_asset_input(
                    db,
                    project,
                    staged_asset_id,
                    allowed_mime_types=SUPPORTED_KEYFRAME_MIME_TYPES,
                )
            else:
                image_provider = configured_image_provider(
                    get_settings().provider_config,
                    target_request.provider_id,
                    cancel_requested=cancellation_requested,
                )
                info = image_provider.info()
                if (
                    not info.available
                    or Capability.IMAGE_GENERATION not in info.capabilities
                    or info.model_identity != target_request.provider_model
                ):
                    raise RuntimeError(
                        "Target-frame provider capability snapshot no longer matches"
                    )
                health = await image_provider.health()
                if health.get("ok") is not True:
                    raise RuntimeError(
                        "Target-frame provider failed its execution-time health check"
                    )
                report_progress(0.1, "generating target frame")
                target_path = (
                    Path(project.root_asset_directory)
                    / "chains"
                    / chain.id
                    / "targets"
                    / f"target-{uuid.uuid4().hex}.png"
                )
                try:
                    await image_provider.generate(
                        ImageRequest(
                            prompt=target_request.prompt,
                            negative_prompt=target_request.negative_prompt,
                            output_path=target_path,
                            reference_image=source_path,
                            width=target_request.width,
                            height=target_request.height,
                            seed=target_request.seed,
                            label="automatic chain target",
                        )
                    )
                except ProviderOutOfMemoryError as error:
                    if not isinstance(image_provider, OOMRecoverableProvider):
                        raise
                    cleanup = await image_provider.cleanup_after_oom(error)
                    record_provider_cleanup(error, cleanup)
                    if cleanup.provider_id != error.provider_id:
                        raise RuntimeError(
                            "Target provider cleanup result does not match its OOM"
                        ) from error
                    raise
                if cancellation_requested():
                    raise PipelineCancelled("Target generation cancelled before publication")
                target_asset = register_asset(
                    db,
                    project_id=project.id,
                    shot_id=None,
                    kind="generated_chain_target_frame",
                    path=target_path,
                    provider=target_request.provider_id,
                    model=target_request.provider_model,
                    prompt=target_request.prompt,
                    seed=target_request.seed,
                    parents=[source.id],
                    generation_parameters={
                        "request_digest": target_request.digest(),
                        "chain_id": chain.id,
                        "predecessor_clip_id": target_request.predecessor_clip_id,
                        "negative_prompt": target_request.negative_prompt,
                        "continuity_conditioned": Capability.IMAGE_EDITING in info.capabilities,
                    },
                )
                _update_owned_running_job(
                    db,
                    job,
                    {
                        "payload": {
                            **(job.payload or {}),
                            TARGET_FRAME_ASSET_ID_KEY: target_asset.id,
                        }
                    },
                    worker_id=worker_id,
                    worker_instance_id=worker_instance_id,
                )
            report_progress(0.95, "target frame validated")
            output_asset_id = target_asset.id
            target_chain = chain

            def continue_target() -> object:
                return continue_after_generated_target(db, project, target_chain, job, target_asset)

            post_success = continue_target
            post_success_chain = target_chain
        elif job.job_type == "video_chain_clip_generation":
            clip_id = job.payload.get(CHAIN_CLIP_ID_KEY)
            if not isinstance(clip_id, str):
                raise RuntimeError("Video chain Job has no clip ID")
            clip = db.get(VideoChainClip, clip_id)
            if clip is None:
                raise RuntimeError("Video chain clip is missing")
            chain = db.get(VideoChain, clip.chain_id)
            if chain is None or chain.project_id != project.id:
                raise RuntimeError("Video chain clip does not belong to the Job project")
            request = request_from_clip(clip)
            job_request = FirstLastFrameGenerationRequest.model_validate(
                job.payload.get(FLF_REQUEST_KEY)
            )
            if job_request.digest() != clip.request_digest or job_request != request:
                raise RuntimeError("Video chain Job request does not match its clip snapshot")
            if job.input_asset_ids != request_input_asset_ids(request):
                raise RuntimeError("Video chain Job inputs do not match its request snapshot")
            provider = configured_first_last_frame_provider(
                get_settings().provider_config,
                request.provider_id,
            )
            expected_gpu_assignment = getattr(provider, "gpu_assignment", None)
            if (
                expected_gpu_assignment is not None
                and job.gpu_assignment != expected_gpu_assignment
            ):
                raise RuntimeError("Local generation provider queue does not match the claimed Job")
            metric_provider_id = get_settings().perceptual_metric_provider_id
            metric_provider = (
                configured_perceptual_metric_provider(
                    get_settings().provider_config,
                    metric_provider_id,
                    cancel_requested=cancellation_requested,
                )
                if metric_provider_id
                else None
            )
            telemetry = (
                GPUTelemetryRecorder(physical_gpu).start() if physical_gpu is not None else None
            )
            try:
                output = await VideoChainPipeline(
                    db,
                    provider=provider,
                    perceptual_metric_provider=metric_provider,
                    gpu_telemetry=telemetry,
                    cancel_requested=cancellation_requested,
                    progress=report_progress,
                ).run(project, clip)
            finally:
                if telemetry is not None:
                    telemetry_result = telemetry.stop()
                    _append_job_log_safely(
                        log_path,
                        "gpu_telemetry_complete",
                        **telemetry_result.model_dump(mode="json"),
                    )
            output_asset_id = output.id

            def finalize_automated_clip() -> object:
                return auto_accept_publish_and_replenish(db, project, chain, clip)

            post_success = finalize_automated_clip
            post_success_chain = chain
        else:
            raise RuntimeError(f"Unsupported job type: {job.job_type}")
        completion = mark_succeeded(
            db,
            job,
            [output_asset_id],
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
        )
        if completion is JobState.CANCEL_REQUESTED:
            raise PipelineCancelled("Cancellation requested before job completion")
        if completion is not JobState.SUCCEEDED:
            raise RuntimeError(f"Cannot complete job in state {completion.value}")
        _append_job_log_safely(
            log_path,
            "job_succeeded",
            job_id=job.id,
            output_asset_ids=job.output_asset_ids,
        )
        if post_success is not None:
            try:
                post_success()
            except Exception as error:
                db.rollback()
                if post_success_chain is not None:
                    post_success_chain.stream_state = {
                        **(post_success_chain.stream_state or {}),
                        "automation_status": "post_success_failed",
                        "last_automation_error": {
                            "type": type(error).__name__,
                            "message": "Automation post-success action failed; inspect local logs",
                        },
                    }
                    db.commit()
                _append_job_log_safely(
                    log_path,
                    "post_success_automation_failed",
                    error_type=type(error).__name__,
                )
    except (PipelineCancelled, MediaCancelled):
        db.rollback()
        _mark_chain_clip_terminal(
            db,
            job,
            cancelled=True,
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
        )
        state = mark_cancelled(
            db,
            job,
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
        )
        if state is JobState.CANCELLED:
            _append_job_log_safely(log_path, "job_cancelled", job_id=job.id)
        else:
            logger.warning(
                "job_cancellation_lost_terminal_race",
                job_id=job.id,
                state=state.value,
            )
    except Exception as error:
        db.rollback()
        error_info: dict[str, object] = {
            "type": type(error).__name__,
            "message": str(error),
            "gpu_metrics": [assigned_gpu_metrics] if assigned_gpu_metrics is not None else [],
        }
        if isinstance(error, ProviderExecutionError):
            error_info["provider_error"] = error.to_safe_dict()
        provider_cancelled = (
            isinstance(error, ProviderExecutionError)
            and error.failure_kind is ProviderFailureKind.CANCELLED
        )
        _mark_chain_clip_terminal(
            db,
            job,
            cancelled=provider_cancelled,
            error_info=error_info,
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
        )
        if provider_cancelled:
            state = mark_cancelled(
                db,
                job,
                worker_id=worker_id,
                worker_instance_id=worker_instance_id,
            )
        else:
            state = mark_failed(
                db,
                job,
                error_info,
                worker_id=worker_id,
                worker_instance_id=worker_instance_id,
            )
            if state is JobState.CANCEL_REQUESTED:
                state = mark_cancelled(
                    db,
                    job,
                    worker_id=worker_id,
                    worker_instance_id=worker_instance_id,
                )
        if state is JobState.CANCELLED:
            _append_job_log_safely(
                log_path,
                "job_cancelled",
                job_id=job.id,
                reason="error_after_cancellation",
            )
        elif state is JobState.FAILED:
            _append_job_log_safely(
                log_path,
                "job_failed",
                job_id=job.id,
                error_type=type(error).__name__,
                message=str(error),
            )
        else:
            logger.exception(
                "job_error_after_terminal_state",
                job_id=job.id,
                state=state.value,
                error_type=type(error).__name__,
            )


def _append_job_log_safely(log_path: Path | None, event: str, **data: Any) -> None:
    if log_path is None:
        logger.error("job_log_path_unavailable", event_name=event, **data)
        return
    try:
        append_job_log(log_path, event, **data)
    except Exception as error:
        logger.exception(
            "job_log_write_failed",
            event_name=event,
            path=str(log_path),
            error_type=type(error).__name__,
        )


def _mark_chain_clip_terminal(
    db: Session,
    job: Job,
    *,
    cancelled: bool,
    error_info: dict[str, object] | None = None,
    worker_id: str | None = None,
    worker_instance_id: str | None = None,
) -> None:
    if job.job_type != "video_chain_clip_generation":
        return
    clip_id = (job.payload or {}).get(CHAIN_CLIP_ID_KEY)
    if not isinstance(clip_id, str):
        return
    from flipthis_video_maker.contracts.video_generation import ChainClipState

    clip = db.get(VideoChainClip, clip_id)
    if clip is None:
        return
    current_job = db.get(Job, job.id)
    if current_job is None:
        return
    if (
        worker_id is not None
        and worker_instance_id is not None
        and (
            current_job.claimed_by_worker_id != worker_id
            or current_job.claimed_by_instance_id != worker_instance_id
        )
    ):
        return
    if current_job.state not in {
        JobState.RUNNING.value,
        JobState.CANCEL_REQUESTED.value,
    }:
        return
    if worker_id is not None and (
        current_job.lease_expires_at is None
        or _as_utc(current_job.lease_expires_at) <= datetime.now(UTC)
    ):
        return
    clip.state = ChainClipState.CANCELLED.value if cancelled else ChainClipState.FAILED.value
    clip.failure_info = error_info or {}
    provider_error = (error_info or {}).get("provider_error")
    if isinstance(provider_error, dict):
        provider_job_id = provider_error.get("provider_job_id")
        if isinstance(provider_job_id, str):
            clip.provider_job_id = provider_job_id
    db.commit()


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _resolve_render_profile_execution(
    db: Session,
    job: Job,
    project: Project,
    render_profiles: RenderProfileConfigurationFile | None,
    *,
    worker_id: str | None,
    worker_instance_id: str | None,
) -> RenderProfileExecution:
    payload = job.payload or {}
    if RENDER_PROFILE_EXECUTION_KEY in payload:
        return render_profile_execution_from_payload(payload)

    profiles = render_profiles or load_render_profile_configuration(
        get_settings().render_profile_config
    )
    execution = RenderProfileExecution.resolve(profiles, project.resolution_profile)
    updated_payload = {
        **payload,
        RENDER_PROFILE_EXECUTION_KEY: execution.model_dump(mode="json"),
    }
    _update_owned_running_job(
        db,
        job,
        {"payload": updated_payload},
        worker_id=worker_id,
        worker_instance_id=worker_instance_id,
    )
    return execution


def _resolve_render_finalization_execution(
    db: Session,
    job: Job,
    *,
    worker_id: str | None,
    worker_instance_id: str | None,
) -> RenderFinalizationExecution:
    payload = job.payload or {}
    if RENDER_FINALIZATION_EXECUTION_KEY in payload:
        return render_finalization_execution_from_payload(payload)

    execution = RenderFinalizationExecution.compatibility_default()
    updated_payload = {
        **payload,
        RENDER_FINALIZATION_EXECUTION_KEY: execution.model_dump(mode="json"),
    }
    _update_owned_running_job(
        db,
        job,
        {"payload": updated_payload},
        worker_id=worker_id,
        worker_instance_id=worker_instance_id,
    )
    return execution


async def loop(device: str, poll_seconds: float = 1, *, once: bool = False) -> None:
    stopped = False

    def stop(*_args: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    settings = get_settings()
    configuration = load_worker_configuration(settings.worker_config).require(device)
    render_profiles = load_render_profile_configuration(settings.render_profile_config)
    if configuration.physical_gpu is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(configuration.physical_gpu)
    reporter = WorkerHeartbeatReporter(
        SessionLocal,
        configuration.id,
        configuration.assignment,
        heartbeat_seconds=settings.worker_heartbeat_seconds,
        lease_seconds=settings.job_lease_seconds,
    )
    with reporter:
        while not stopped:
            if reporter.generation_lost:
                raise RuntimeError(f"Worker generation was superseded: {configuration.id}")
            with SessionLocal() as db:
                processed = await process_next(
                    db,
                    configuration.assignment,
                    physical_gpu=configuration.physical_gpu,
                    minimum_free_vram_mb=settings.min_free_vram_mb,
                    worker_status=reporter.set_state,
                    worker_id=reporter.worker_id,
                    worker_instance_id=reporter.instance_id,
                    shutdown_requested=lambda: stopped,
                    render_profiles=render_profiles,
                    lease_seconds=settings.job_lease_seconds,
                )
            if once:
                return
            if not processed:
                await asyncio.sleep(poll_seconds)


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cpu", help="Configured logical worker ID")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    asyncio.run(loop(args.device, once=args.once))


if __name__ == "__main__":
    run()
