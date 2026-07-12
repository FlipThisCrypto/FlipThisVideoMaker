from datetime import UTC, datetime

from sqlalchemy import case, exists, select, update
from sqlalchemy.orm import Session

from flipthis_video_maker.domain.enums import JobState
from flipthis_video_maker.domain.models import Job, Worker


def has_queued(db: Session, assignment: str) -> bool:
    return (
        db.scalar(
            select(Job.id)
            .where(
                Job.state == JobState.QUEUED.value,
                Job.gpu_assignment == assignment,
            )
            .limit(1)
        )
        is not None
    )


def claim_next(
    db: Session,
    assignment: str,
    *,
    worker_id: str | None = None,
    worker_instance_id: str | None = None,
) -> Job | None:
    """Atomically claim the oldest highest-priority job for one exact worker queue."""
    if (worker_id is None) is not (worker_instance_id is None):
        raise ValueError("worker_id and worker_instance_id must be provided together")
    started_at = datetime.now(UTC)
    candidate_id = (
        select(Job.id)
        .where(
            Job.state == JobState.QUEUED.value,
            Job.gpu_assignment == assignment,
        )
        .order_by(Job.priority, Job.created_at)
        .limit(1)
        .scalar_subquery()
    )
    claim = update(Job).where(Job.id == candidate_id, Job.state == JobState.QUEUED.value)
    if worker_id is not None and worker_instance_id is not None:
        claim = claim.where(
            exists(
                select(Worker.id).where(
                    Worker.id == worker_id,
                    Worker.instance_id == worker_instance_id,
                )
            )
        )
    result = db.execute(
        claim.values(
            state=JobState.RUNNING.value,
            attempt_number=Job.attempt_number + 1,
            started_at=started_at,
            completed_at=None,
        ).returning(Job.id)
    )
    job_id = result.scalar_one_or_none()
    db.commit()
    return db.get(Job, job_id) if job_id else None


def request_cancellation(db: Session, job: Job) -> JobState:
    """Atomically request cancellation without trusting a possibly stale ORM instance."""
    timestamp = datetime.now(UTC)
    state = db.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.state.in_([JobState.QUEUED.value, JobState.RUNNING.value]),
        )
        .values(
            state=case(
                (Job.state == JobState.QUEUED.value, JobState.CANCELLED.value),
                else_=JobState.CANCEL_REQUESTED.value,
            ),
            completed_at=case(
                (Job.state == JobState.QUEUED.value, timestamp),
                else_=Job.completed_at,
            ),
        )
        .returning(Job.state)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if state is None:
        state = db.scalar(select(Job.state).where(Job.id == job.id))
    db.commit()
    if state is None:
        raise ValueError(f"Unknown job {job.id}")
    db.refresh(job)
    if state == JobState.CANCEL_REQUESTED.value:
        return JobState.CANCEL_REQUESTED
    if state == JobState.CANCELLED.value:
        return JobState.CANCELLED
    raise ValueError(f"Cannot cancel job in state {state}")


def mark_succeeded(db: Session, job: Job, output_asset_ids: list[str]) -> JobState:
    """Complete a running job only if a concurrent cancellation has not already won."""
    completed_at = datetime.now(UTC)
    updated_id = db.execute(
        update(Job)
        .where(Job.id == job.id, Job.state == JobState.RUNNING.value)
        .values(
            output_asset_ids=output_asset_ids,
            state=JobState.SUCCEEDED.value,
            progress=1,
            current_stage="complete",
            completed_at=completed_at,
        )
        .returning(Job.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    state: str | None
    if updated_id is not None:
        state = JobState.SUCCEEDED.value
    else:
        state = db.scalar(select(Job.state).where(Job.id == job.id))
    db.commit()
    if state is None:
        raise RuntimeError(f"Job disappeared while completing: {job.id}")
    db.refresh(job)
    return JobState(state)


def mark_cancelled(db: Session, job: Job) -> JobState:
    """Finalize cancellation without overwriting a job that already completed."""
    completed_at = datetime.now(UTC)
    updated_id = db.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.state.in_([JobState.RUNNING.value, JobState.CANCEL_REQUESTED.value]),
        )
        .values(
            state=JobState.CANCELLED.value,
            current_stage="cancelled",
            completed_at=completed_at,
        )
        .returning(Job.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    state: str | None
    if updated_id is not None:
        state = JobState.CANCELLED.value
    else:
        state = db.scalar(select(Job.state).where(Job.id == job.id))
    db.commit()
    if state is None:
        raise RuntimeError(f"Job disappeared while cancelling: {job.id}")
    db.refresh(job)
    return JobState(state)


def mark_failed(db: Session, job: Job, error_info: dict[str, object]) -> JobState:
    """Fail only a still-running job; a concurrent cancellation or success remains authoritative."""
    completed_at = datetime.now(UTC)
    updated_id = db.execute(
        update(Job)
        .where(Job.id == job.id, Job.state == JobState.RUNNING.value)
        .values(
            state=JobState.FAILED.value,
            current_stage="failed",
            error_info=error_info,
            completed_at=completed_at,
        )
        .returning(Job.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    state: str | None
    if updated_id is not None:
        state = JobState.FAILED.value
    else:
        state = db.scalar(select(Job.state).where(Job.id == job.id))
    db.commit()
    if state is None:
        raise RuntimeError(f"Job disappeared while failing: {job.id}")
    db.refresh(job)
    return JobState(state)


def retry(db: Session, job: Job, max_retries: int) -> None:
    if job.state not in {JobState.FAILED.value, JobState.CANCELLED.value}:
        raise ValueError(f"Cannot retry job in state {job.state}")
    if job.attempt_number > max_retries:
        raise ValueError("Maximum retry count reached")
    job.state = JobState.QUEUED.value
    job.error_info = {}
    job.progress = 0
    job.current_stage = "queued for retry"
    job.started_at = None
    job.completed_at = None
    db.commit()
