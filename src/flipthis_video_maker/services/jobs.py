from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, exists, select, update
from sqlalchemy.orm import Session

from flipthis_video_maker.domain.enums import JobState
from flipthis_video_maker.domain.models import Job, VideoChainClip, Worker


@dataclass(frozen=True)
class JobLeaseRecovery:
    job_id: str
    previous_state: JobState
    recovered_state: JobState
    worker_id: str
    worker_instance_id: str
    expired_at: datetime


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
    lease_seconds: float = 30,
    claimed_at: datetime | None = None,
) -> Job | None:
    """Atomically claim the oldest highest-priority job for one exact worker queue."""
    if (worker_id is None) is not (worker_instance_id is None):
        raise ValueError("worker_id and worker_instance_id must be provided together")
    if lease_seconds <= 0:
        raise ValueError("Job lease duration must be positive")
    started_at = claimed_at or datetime.now(UTC)
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
                    Worker.assignment == assignment,
                )
            )
        )
    ownership: dict[str, object] = {
        "claimed_by_worker_id": worker_id,
        "claimed_by_instance_id": worker_instance_id,
        "lease_heartbeat_at": started_at if worker_id is not None else None,
        "lease_expires_at": (
            started_at + timedelta(seconds=lease_seconds) if worker_id is not None else None
        ),
    }
    result = db.execute(
        claim.values(
            state=JobState.RUNNING.value,
            attempt_number=Job.attempt_number + 1,
            started_at=started_at,
            completed_at=None,
            **ownership,
        ).returning(Job.id)
    )
    job_id = result.scalar_one_or_none()
    db.commit()
    db.expire_all()
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


def mark_succeeded(
    db: Session,
    job: Job,
    output_asset_ids: list[str],
    *,
    worker_id: str | None = None,
    worker_instance_id: str | None = None,
) -> JobState:
    """Complete a running job only if a concurrent cancellation has not already won."""
    completed_at = datetime.now(UTC)
    _validate_owner_pair(worker_id, worker_instance_id)
    statement = update(Job).where(Job.id == job.id, Job.state == JobState.RUNNING.value)
    if worker_id is not None and worker_instance_id is not None:
        statement = statement.where(
            Job.claimed_by_worker_id == worker_id,
            Job.claimed_by_instance_id == worker_instance_id,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > completed_at,
        )
    updated_id = db.execute(
        statement.values(
            output_asset_ids=output_asset_ids,
            state=JobState.SUCCEEDED.value,
            progress=1,
            current_stage="complete",
            completed_at=completed_at,
            lease_expires_at=None,
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


def mark_cancelled(
    db: Session,
    job: Job,
    *,
    worker_id: str | None = None,
    worker_instance_id: str | None = None,
) -> JobState:
    """Finalize cancellation without overwriting a job that already completed."""
    completed_at = datetime.now(UTC)
    _validate_owner_pair(worker_id, worker_instance_id)
    statement = update(Job).where(
        Job.id == job.id,
        Job.state.in_([JobState.RUNNING.value, JobState.CANCEL_REQUESTED.value]),
    )
    if worker_id is not None and worker_instance_id is not None:
        statement = statement.where(
            Job.claimed_by_worker_id == worker_id,
            Job.claimed_by_instance_id == worker_instance_id,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > completed_at,
        )
    updated_id = db.execute(
        statement.values(
            state=JobState.CANCELLED.value,
            current_stage="cancelled",
            completed_at=completed_at,
            lease_expires_at=None,
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


def mark_failed(
    db: Session,
    job: Job,
    error_info: dict[str, object],
    *,
    worker_id: str | None = None,
    worker_instance_id: str | None = None,
) -> JobState:
    """Fail only a still-running job; a concurrent cancellation or success remains authoritative."""
    completed_at = datetime.now(UTC)
    _validate_owner_pair(worker_id, worker_instance_id)
    statement = update(Job).where(Job.id == job.id, Job.state == JobState.RUNNING.value)
    if worker_id is not None and worker_instance_id is not None:
        statement = statement.where(
            Job.claimed_by_worker_id == worker_id,
            Job.claimed_by_instance_id == worker_instance_id,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > completed_at,
        )
    updated_id = db.execute(
        statement.values(
            state=JobState.FAILED.value,
            current_stage="failed",
            error_info=error_info,
            completed_at=completed_at,
            lease_expires_at=None,
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


def retry(
    db: Session,
    job: Job,
    max_retries: int,
    *,
    acknowledge_unsafe_orphan: bool = False,
) -> None:
    if job.state not in {JobState.FAILED.value, JobState.CANCELLED.value}:
        raise ValueError(f"Cannot retry job in state {job.state}")
    if job.attempt_number > max_retries:
        raise ValueError("Maximum retry count reached")
    if job.error_info.get("retry_safe") is False and not acknowledge_unsafe_orphan:
        raise ValueError(
            "The prior worker lease expired while its provider process could still be active; "
            "explicit orphan-risk acknowledgement is required"
        )
    job.state = JobState.QUEUED.value
    job.error_info = {}
    job.progress = 0
    job.current_stage = "queued for retry"
    job.started_at = None
    job.completed_at = None
    job.claimed_by_worker_id = None
    job.claimed_by_instance_id = None
    job.lease_heartbeat_at = None
    job.lease_expires_at = None
    db.commit()


def reconcile_expired_job_leases(
    db: Session,
    *,
    checked_at: datetime | None = None,
) -> list[JobLeaseRecovery]:
    """Make expired owned Jobs terminal without assuming their provider process stopped.

    Expired work is deliberately not requeued. A hosted request or detached local child may still
    be running, so retry requires an explicit operator acknowledgement.
    """
    timestamp = checked_at or datetime.now(UTC)
    candidates = list(
        db.scalars(
            select(Job).where(
                Job.state.in_([JobState.RUNNING.value, JobState.CANCEL_REQUESTED.value]),
                Job.claimed_by_worker_id.is_not(None),
                Job.claimed_by_instance_id.is_not(None),
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at <= timestamp,
            )
        )
    )
    recovered: list[JobLeaseRecovery] = []
    for job in candidates:
        if (
            job.claimed_by_worker_id is None
            or job.claimed_by_instance_id is None
            or job.lease_expires_at is None
        ):
            continue
        previous_state = JobState(job.state)
        recovered_state = (
            JobState.CANCELLED if previous_state is JobState.CANCEL_REQUESTED else JobState.FAILED
        )
        error_info: dict[str, object] = {
            **(job.error_info or {}),
            "type": "JobLeaseExpired",
            "failure_kind": "orphaned_worker_lease",
            "message": (
                "The owning worker stopped renewing its Job lease; its external process state "
                "is unknown"
            ),
            "retry_safe": False,
            "claimed_by_worker_id": job.claimed_by_worker_id,
            "claimed_by_instance_id": job.claimed_by_instance_id,
            "lease_expired_at": job.lease_expires_at.isoformat(),
            "reconciled_at": timestamp.isoformat(),
        }
        updated_id = db.execute(
            update(Job)
            .where(
                Job.id == job.id,
                Job.state == previous_state.value,
                Job.claimed_by_worker_id == job.claimed_by_worker_id,
                Job.claimed_by_instance_id == job.claimed_by_instance_id,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at <= timestamp,
            )
            .values(
                state=recovered_state.value,
                current_stage=(
                    "cancelled after worker lease expiry"
                    if recovered_state is JobState.CANCELLED
                    else "failed: worker lease expired"
                ),
                error_info=error_info,
                completed_at=timestamp,
                lease_expires_at=None,
            )
            .returning(Job.id)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if updated_id is None:
            continue
        clip_state = "cancelled" if recovered_state is JobState.CANCELLED else "failed"
        db.execute(
            update(VideoChainClip)
            .where(VideoChainClip.job_id == job.id)
            .values(state=clip_state, failure_info=error_info)
        )
        recovered.append(
            JobLeaseRecovery(
                job_id=job.id,
                previous_state=previous_state,
                recovered_state=recovered_state,
                worker_id=job.claimed_by_worker_id,
                worker_instance_id=job.claimed_by_instance_id,
                expired_at=job.lease_expires_at,
            )
        )
    db.commit()
    db.expire_all()
    return recovered


def _validate_owner_pair(worker_id: str | None, worker_instance_id: str | None) -> None:
    if (worker_id is None) is not (worker_instance_id is None):
        raise ValueError("worker_id and worker_instance_id must be provided together")


__all__ = [
    "JobLeaseRecovery",
    "claim_next",
    "has_queued",
    "mark_cancelled",
    "mark_failed",
    "mark_succeeded",
    "reconcile_expired_job_leases",
    "request_cancellation",
    "retry",
]
