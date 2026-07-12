import argparse
import asyncio
import os
import signal
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from flipthis_video_maker.database.session import SessionLocal
from flipthis_video_maker.domain.enums import JobState
from flipthis_video_maker.domain.models import Project
from flipthis_video_maker.pipeline.mock_pipeline import MockPipeline, PipelineCancelled
from flipthis_video_maker.pipeline.shot_regeneration import MockShotRegenerator
from flipthis_video_maker.scheduler.gpu import GPULock, discover_gpus
from flipthis_video_maker.services.job_logs import append_job_log
from flipthis_video_maker.services.jobs import claim_next


async def process_next(db: Session, device: str) -> bool:
    job = claim_next(db, device)
    if job is None:
        return False

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
        device=device,
        attempt=job.attempt_number,
    )

    lock = GPULock(device.removeprefix("gpu")) if device.startswith("gpu") else nullcontext()
    try:
        with lock:
            job.current_stage = "rendering"
            db.commit()

            def cancellation_requested() -> bool:
                db.refresh(job, attribute_names=["state"])
                return job.state == JobState.CANCEL_REQUESTED.value

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
                candidate = await MockShotRegenerator(
                    db, cancellation_requested, report_progress
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
            else:
                raise RuntimeError(f"Unsupported job type: {job.job_type}")
            job.output_asset_ids = [output_asset_id]
            job.state = JobState.SUCCEEDED.value
            job.progress = 1
            job.current_stage = "complete"
            job.completed_at = datetime.now(UTC)
            db.commit()
            append_job_log(log_path, "job_succeeded", output_asset_ids=job.output_asset_ids)
    except PipelineCancelled:
        db.rollback()
        job.state = JobState.CANCELLED.value
        job.current_stage = "cancelled"
        job.completed_at = datetime.now(UTC)
        db.commit()
        append_job_log(log_path, "job_cancelled")
    except Exception as error:
        db.rollback()
        job.state = JobState.FAILED.value
        job.error_info = {
            "type": type(error).__name__,
            "message": str(error),
            "gpu_metrics": discover_gpus(),
        }
        job.completed_at = datetime.now(UTC)
        db.commit()
        append_job_log(
            log_path,
            "job_failed",
            error_type=type(error).__name__,
            message=str(error),
        )
    return True


async def loop(device: str, poll_seconds: float = 1, *, once: bool = False) -> None:
    stopped = False

    def stop(*_args: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if device.startswith("gpu"):
        os.environ["CUDA_VISIBLE_DEVICES"] = device.removeprefix("gpu")
    while not stopped:
        with SessionLocal() as db:
            processed = await process_next(db, device)
        if once:
            return
        if not processed:
            await asyncio.sleep(poll_seconds)


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cpu", choices=["cpu", "gpu0", "gpu1"])
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    asyncio.run(loop(args.device, once=args.once))


if __name__ == "__main__":
    run()
