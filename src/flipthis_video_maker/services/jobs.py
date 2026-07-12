from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from flipthis_video_maker.domain.enums import JobState
from flipthis_video_maker.domain.models import Job


def claim_next(db: Session, assignment: str) -> Job | None:
    """Atomically claim the oldest highest-priority job for one exact worker queue."""
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
    result = db.execute(
        update(Job)
        .where(Job.id == candidate_id, Job.state == JobState.QUEUED.value)
        .values(
            state=JobState.RUNNING.value,
            attempt_number=Job.attempt_number + 1,
            started_at=started_at,
            completed_at=None,
        )
        .returning(Job.id)
    )
    job_id = result.scalar_one_or_none()
    db.commit()
    return db.get(Job, job_id) if job_id else None


def request_cancellation(db: Session, job: Job) -> JobState:
    if job.state == JobState.QUEUED.value:
        job.state = JobState.CANCELLED.value
        job.completed_at = datetime.now(UTC)
    elif job.state == JobState.RUNNING.value:
        job.state = JobState.CANCEL_REQUESTED.value
    elif job.state == JobState.CANCEL_REQUESTED.value:
        return JobState.CANCEL_REQUESTED
    else:
        raise ValueError(f"Cannot cancel job in state {job.state}")
    db.commit()
    return JobState(job.state)


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
