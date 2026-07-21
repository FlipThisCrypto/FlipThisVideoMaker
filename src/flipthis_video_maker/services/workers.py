import os
import socket
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Self

import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flipthis_video_maker.domain.enums import JobState, WorkerState
from flipthis_video_maker.domain.models import Job, Worker

logger = structlog.get_logger(__name__)
SessionFactory = Callable[[], Session]


def register_worker(
    db: Session,
    worker_id: str,
    assignment: str,
    *,
    instance_id: str | None = None,
    hostname: str | None = None,
    pid: int | None = None,
    registered_at: datetime | None = None,
) -> Worker:
    """Register one process generation, replacing only the logical worker's runtime row."""
    token = instance_id or str(uuid.uuid4())
    timestamp = registered_at or datetime.now(UTC)
    values = {
        "instance_id": token,
        "assignment": assignment,
        "state": WorkerState.STARTING.value,
        "hostname": hostname or socket.gethostname(),
        "pid": pid if pid is not None else os.getpid(),
        "current_job_id": None,
        "started_at": timestamp,
        "last_heartbeat_at": timestamp,
        "stopped_at": None,
    }
    registered_id = db.execute(
        update(Worker).where(Worker.id == worker_id).values(**values).returning(Worker.id)
    ).scalar_one_or_none()
    if registered_id is None:
        db.add(Worker(id=worker_id, **values))
        try:
            db.commit()
        except IntegrityError as error:
            # A simultaneous registration inserted the logical row first. Rotate it to this
            # generation; all later writes remain guarded by the winning boot token.
            db.rollback()
            registered_id = db.execute(
                update(Worker).where(Worker.id == worker_id).values(**values).returning(Worker.id)
            ).scalar_one_or_none()
            if registered_id is None:
                raise RuntimeError(f"Worker registration disappeared: {worker_id}") from error
            db.commit()
    else:
        db.commit()

    worker = db.get(Worker, worker_id)
    if worker is None or worker.instance_id != token:
        raise RuntimeError(f"Worker registration was superseded: {worker_id}")
    return worker


def update_worker_status(
    db: Session,
    worker_id: str,
    instance_id: str,
    *,
    state: WorkerState,
    current_job_id: str | None,
    heartbeat_at: datetime | None = None,
    lease_seconds: float = 30,
) -> bool:
    """Update a worker only when the caller still owns its current boot generation."""
    timestamp = heartbeat_at or datetime.now(UTC)
    if lease_seconds <= 0:
        raise ValueError("Job lease duration must be positive")
    values: dict[str, object] = {
        "state": state.value,
        "current_job_id": current_job_id,
        "last_heartbeat_at": timestamp,
        "stopped_at": timestamp if state is WorkerState.STOPPED else None,
    }
    updated_id = db.execute(
        update(Worker)
        .where(Worker.id == worker_id, Worker.instance_id == instance_id)
        .values(**values)
        .returning(Worker.id)
    ).scalar_one_or_none()
    if updated_id is None:
        db.rollback()
        return False
    if current_job_id is not None:
        if state is not WorkerState.BUSY:
            db.rollback()
            raise ValueError("A worker with a current Job must be busy")
        renewed_id = db.execute(
            update(Job)
            .where(
                Job.id == current_job_id,
                Job.state.in_([JobState.RUNNING.value, JobState.CANCEL_REQUESTED.value]),
                Job.claimed_by_worker_id == worker_id,
                Job.claimed_by_instance_id == instance_id,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at > timestamp,
            )
            .values(
                lease_heartbeat_at=timestamp,
                lease_expires_at=timestamp + timedelta(seconds=lease_seconds),
            )
            .returning(Job.id)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if renewed_id is None:
            db.rollback()
            return False
    db.commit()
    return True


def get_worker(db: Session, worker_id: str) -> Worker | None:
    return db.get(Worker, worker_id)


def list_workers(db: Session) -> list[Worker]:
    return list(db.scalars(select(Worker).order_by(Worker.id)))


def worker_is_online(
    worker: Worker,
    stale_seconds: float,
    *,
    checked_at: datetime | None = None,
) -> bool:
    if stale_seconds <= 0:
        raise ValueError("Worker stale timeout must be positive")
    if worker.state == WorkerState.STOPPED.value:
        return False
    timestamp = checked_at or datetime.now(UTC)
    heartbeat = worker.last_heartbeat_at
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return heartbeat >= timestamp - timedelta(seconds=stale_seconds)


class WorkerHeartbeatReporter:
    """Persist heartbeats from a dedicated thread so blocking media work cannot starve them."""

    def __init__(
        self,
        session_factory: SessionFactory,
        worker_id: str,
        assignment: str,
        *,
        heartbeat_seconds: float = 5,
        lease_seconds: float = 30,
        instance_id: str | None = None,
        hostname: str | None = None,
        pid: int | None = None,
    ) -> None:
        if heartbeat_seconds <= 0:
            raise ValueError("Worker heartbeat interval must be positive")
        if lease_seconds <= heartbeat_seconds:
            raise ValueError("Job lease duration must exceed the heartbeat interval")
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.assignment = assignment
        self.heartbeat_seconds = heartbeat_seconds
        self.lease_seconds = lease_seconds
        self.instance_id = instance_id or str(uuid.uuid4())
        self.hostname = hostname or socket.gethostname()
        self.pid = pid if pid is not None else os.getpid()
        self._state = WorkerState.STARTING
        self._current_job_id: str | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop_requested = threading.Event()
        self._generation_lost = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._last_error: Exception | None = None

    @property
    def generation_lost(self) -> bool:
        return self._generation_lost.is_set()

    @property
    def last_error(self) -> Exception | None:
        with self._lock:
            return self._last_error

    def start(self) -> Worker:
        if self._started:
            raise RuntimeError(f"Worker heartbeat reporter already started: {self.worker_id}")
        with self.session_factory() as db:
            worker = register_worker(
                db,
                self.worker_id,
                self.assignment,
                instance_id=self.instance_id,
                hostname=self.hostname,
                pid=self.pid,
            )
        self._started = True
        self._thread = threading.Thread(
            target=self._run,
            name=f"worker-heartbeat-{self.worker_id}",
            daemon=True,
        )
        self._thread.start()
        self.set_state(WorkerState.IDLE)
        return worker

    def set_state(self, state: WorkerState, current_job_id: str | None = None) -> None:
        if not self._started:
            raise RuntimeError(f"Worker heartbeat reporter is not started: {self.worker_id}")
        with self._lock:
            self._state = state
            self._current_job_id = current_job_id
        self._wake.set()

    def stop(self, timeout: float = 5) -> None:
        thread = self._thread
        if thread is None:
            return
        with self._lock:
            self._state = WorkerState.STOPPED
            self._current_job_id = None
        self._stop_requested.set()
        self._wake.set()
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError(f"Worker heartbeat reporter did not stop: {self.worker_id}")
        self._thread = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()

    def _run(self) -> None:
        while True:
            self._wake.wait(self.heartbeat_seconds)
            self._wake.clear()
            with self._lock:
                state = self._state
                current_job_id = self._current_job_id
            stopping = self._stop_requested.is_set()
            try:
                with self.session_factory() as db:
                    updated = update_worker_status(
                        db,
                        self.worker_id,
                        self.instance_id,
                        state=state,
                        current_job_id=current_job_id,
                        lease_seconds=self.lease_seconds,
                    )
                with self._lock:
                    self._last_error = None
            except Exception as error:
                with self._lock:
                    self._last_error = error
                logger.exception(
                    "worker_heartbeat_failed",
                    worker_id=self.worker_id,
                    instance_id=self.instance_id,
                    error_type=type(error).__name__,
                )
                if stopping:
                    return
                continue
            if not updated:
                self._generation_lost.set()
                logger.error(
                    "worker_heartbeat_generation_lost",
                    worker_id=self.worker_id,
                    instance_id=self.instance_id,
                )
                return
            if stopping:
                return
