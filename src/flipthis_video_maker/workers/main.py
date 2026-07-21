import argparse
import asyncio
import os
import signal
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
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
from flipthis_video_maker.domain.models import Asset, Job, Project
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
from flipthis_video_maker.providers.registry import configured_first_last_frame_provider
from flipthis_video_maker.scheduler.gpu import (
    GPULock,
    evaluate_vram_admission,
    probe_gpus,
)
from flipthis_video_maker.services.job_logs import append_job_log
from flipthis_video_maker.services.jobs import (
    claim_next,
    has_queued,
    mark_cancelled,
    mark_failed,
    mark_succeeded,
)
from flipthis_video_maker.services.render_finalization import revalidate_render_finalization
from flipthis_video_maker.services.video_chains import (
    CHAIN_CLIP_ID_KEY,
    FLF_REQUEST_KEY,
    request_from_clip,
    request_input_asset_ids,
)
from flipthis_video_maker.services.workers import WorkerHeartbeatReporter

logger = structlog.get_logger(__name__)
WorkerStatusCallback = Callable[[WorkerState, str | None], None]


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
) -> bool:
    """Admit, atomically claim, and process at most one exact-queue job."""
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
                shutdown_requested,
                render_profiles,
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
    shutdown_requested: Callable[[], bool] | None,
    render_profiles: RenderProfileConfigurationFile | None,
) -> None:
    log_path: Path | None = None
    try:
        project = db.get(Project, job.project_id)
        if project is None:
            raise RuntimeError(f"Job project is missing: {job.project_id}")
        profile_execution = _resolve_render_profile_execution(
            db,
            job,
            project,
            render_profiles,
        )
        finalization_execution: RenderFinalizationExecution | None = None
        music_asset: Asset | None = None
        if job.job_type == "mock_project_render":
            finalization_execution = _resolve_render_finalization_execution(db, job)
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
        job.log_path = str(log_path)
        db.commit()
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
        job.current_stage = "rendering"
        db.commit()
        bind = db.get_bind()
        job_id = job.id

        def cancellation_requested() -> bool:
            if shutdown_requested is not None and shutdown_requested():
                return True
            with Session(bind) as status_db:
                state = status_db.scalar(select(Job.state).where(Job.id == job_id))
            if state is None:
                raise RuntimeError(f"Job disappeared while running: {job_id}")
            return state == JobState.CANCEL_REQUESTED.value

        def report_progress(value: float, stage: str) -> None:
            job.progress = value
            job.current_stage = stage
            db.commit()
            append_job_log(log_path, "progress", progress=value, stage=stage)

        def persist_profile_fallback(
            advanced: RenderProfileExecution,
            _error: ProviderOutOfMemoryError,
            _cleanup: ProviderCleanupResult,
        ) -> None:
            job.payload = {
                **(job.payload or {}),
                RENDER_PROFILE_EXECUTION_KEY: advanced.model_dump(mode="json"),
            }
            db.commit()
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
        elif job.job_type == "video_chain_clip_generation":
            clip_id = job.payload.get(CHAIN_CLIP_ID_KEY)
            if not isinstance(clip_id, str):
                raise RuntimeError("Video chain Job has no clip ID")
            from flipthis_video_maker.domain.models import VideoChain, VideoChainClip

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
            output = await VideoChainPipeline(
                db,
                provider=provider,
                cancel_requested=cancellation_requested,
                progress=report_progress,
            ).run(project, clip)
            output_asset_id = output.id
        else:
            raise RuntimeError(f"Unsupported job type: {job.job_type}")
        completion = mark_succeeded(db, job, [output_asset_id])
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
    except (PipelineCancelled, MediaCancelled):
        db.rollback()
        _mark_chain_clip_terminal(db, job, cancelled=True)
        state = mark_cancelled(db, job)
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
        )
        if provider_cancelled:
            state = mark_cancelled(db, job)
        else:
            state = mark_failed(db, job, error_info)
            if state is JobState.CANCEL_REQUESTED:
                state = mark_cancelled(db, job)
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
) -> None:
    if job.job_type != "video_chain_clip_generation":
        return
    clip_id = (job.payload or {}).get(CHAIN_CLIP_ID_KEY)
    if not isinstance(clip_id, str):
        return
    from flipthis_video_maker.contracts.video_generation import ChainClipState
    from flipthis_video_maker.domain.models import VideoChainClip

    clip = db.get(VideoChainClip, clip_id)
    if clip is None:
        return
    clip.state = ChainClipState.CANCELLED.value if cancelled else ChainClipState.FAILED.value
    clip.failure_info = error_info or {}
    provider_error = (error_info or {}).get("provider_error")
    if isinstance(provider_error, dict):
        provider_job_id = provider_error.get("provider_job_id")
        if isinstance(provider_job_id, str):
            clip.provider_job_id = provider_job_id
    db.commit()


def _resolve_render_profile_execution(
    db: Session,
    job: Job,
    project: Project,
    render_profiles: RenderProfileConfigurationFile | None,
) -> RenderProfileExecution:
    payload = job.payload or {}
    if RENDER_PROFILE_EXECUTION_KEY in payload:
        return render_profile_execution_from_payload(payload)

    profiles = render_profiles or load_render_profile_configuration(
        get_settings().render_profile_config
    )
    execution = RenderProfileExecution.resolve(profiles, project.resolution_profile)
    job.payload = {
        **payload,
        RENDER_PROFILE_EXECUTION_KEY: execution.model_dump(mode="json"),
    }
    db.commit()
    return execution


def _resolve_render_finalization_execution(
    db: Session,
    job: Job,
) -> RenderFinalizationExecution:
    payload = job.payload or {}
    if RENDER_FINALIZATION_EXECUTION_KEY in payload:
        return render_finalization_execution_from_payload(payload)

    execution = RenderFinalizationExecution.compatibility_default()
    job.payload = {
        **payload,
        RENDER_FINALIZATION_EXECUTION_KEY: execution.model_dump(mode="json"),
    }
    db.commit()
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
