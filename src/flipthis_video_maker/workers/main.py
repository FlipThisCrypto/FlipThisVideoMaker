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

from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.config.workers import load_worker_configuration
from flipthis_video_maker.database.session import SessionLocal
from flipthis_video_maker.domain.enums import JobState, WorkerState
from flipthis_video_maker.domain.models import Job, Project
from flipthis_video_maker.media.ffmpeg import MediaCancelled
from flipthis_video_maker.pipeline.mock_pipeline import MockPipeline, PipelineCancelled
from flipthis_video_maker.pipeline.shot_regeneration import MockShotRegenerator
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
) -> None:
    log_path: Path | None = None
    try:
        project = db.get(Project, job.project_id)
        if project is None:
            raise RuntimeError(f"Job project is missing: {job.project_id}")
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

        if job.job_type == "mock_project_render":
            render = await MockPipeline(db, cancellation_requested, report_progress).run(
                job.project_id
            )
            output_asset_id = render.creation_metadata.get("output_asset_id")
            if not isinstance(output_asset_id, str):
                raise RuntimeError("Render completed without a final output asset")
        elif job.job_type == "mock_shot_regeneration":
            if job.shot_id is None:
                raise RuntimeError("Shot regeneration job has no shot ID")
            prompt = job.payload.get("prompt")
            negative_prompt = job.payload.get("negative_prompt")
            settings = job.payload.get("generation_settings")
            candidate = await MockShotRegenerator(db, cancellation_requested, report_progress).run(
                job.shot_id,
                same_seed=bool(job.payload.get("same_seed", True)),
                prompt=prompt if isinstance(prompt, str) else None,
                negative_prompt=negative_prompt if isinstance(negative_prompt, str) else None,
                generation_settings=settings if isinstance(settings, dict) else None,
            )
            if candidate.output_asset_id is None:
                raise RuntimeError("Shot regeneration completed without a candidate asset")
            output_asset_id = candidate.output_asset_id
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


async def loop(device: str, poll_seconds: float = 1, *, once: bool = False) -> None:
    stopped = False

    def stop(*_args: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    settings = get_settings()
    configuration = load_worker_configuration(settings.worker_config).require(device)
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
