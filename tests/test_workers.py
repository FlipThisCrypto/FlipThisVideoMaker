import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from flipthis_video_maker.config.workers import (
    WorkerConfigurationFile,
    load_worker_configuration,
)
from flipthis_video_maker.domain.enums import JobState, WorkerState
from flipthis_video_maker.domain.models import Job
from flipthis_video_maker.pipeline.mock_pipeline import create_sample
from flipthis_video_maker.scheduler.gpu import GPUMetrics, GPUProbeResult
from flipthis_video_maker.services.workers import (
    WorkerHeartbeatReporter,
    get_worker,
    list_workers,
    register_worker,
    update_worker_status,
    worker_is_online,
)
from flipthis_video_maker.workers import main as worker_main


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def test_worker_heartbeat_persists_across_sessions(db: Session) -> None:
    started_at = datetime(2026, 7, 12, 12, tzinfo=UTC)
    heartbeat_at = started_at + timedelta(seconds=5)
    worker = register_worker(
        db,
        "cpu",
        "cpu",
        instance_id="first-generation",
        hostname="test-host",
        pid=100,
        registered_at=started_at,
    )

    assert update_worker_status(
        db,
        worker.id,
        worker.instance_id,
        state=WorkerState.IDLE,
        current_job_id=None,
        heartbeat_at=heartbeat_at,
    )
    bind = db.get_bind()
    db.close()

    with Session(bind) as restarted:
        persisted = get_worker(restarted, "cpu")
        assert persisted is not None
        assert persisted.state == WorkerState.IDLE.value
        assert _utc(persisted.last_heartbeat_at) == heartbeat_at
        assert worker_is_online(persisted, 20, checked_at=heartbeat_at + timedelta(seconds=19))
        assert not worker_is_online(
            persisted,
            20,
            checked_at=heartbeat_at + timedelta(seconds=21),
        )
        assert [item.id for item in list_workers(restarted)] == ["cpu"]


def test_stale_worker_generation_cannot_update_new_registration(db: Session) -> None:
    started_at = datetime(2026, 7, 12, 12, tzinfo=UTC)
    register_worker(
        db,
        "gpu0",
        "gpu0",
        instance_id="old-generation",
        registered_at=started_at,
    )
    replacement = register_worker(
        db,
        "gpu0",
        "gpu0",
        instance_id="new-generation",
        registered_at=started_at + timedelta(seconds=1),
    )

    assert not update_worker_status(
        db,
        "gpu0",
        "old-generation",
        state=WorkerState.BUSY,
        current_job_id=None,
        heartbeat_at=started_at + timedelta(seconds=2),
    )
    db.refresh(replacement)
    assert replacement.instance_id == "new-generation"
    assert replacement.state == WorkerState.STARTING.value


def test_heartbeat_thread_progresses_while_calling_thread_waits(db: Session) -> None:
    bind = db.get_bind()
    factory = sessionmaker(bind, expire_on_commit=False, class_=Session)
    reporter = WorkerHeartbeatReporter(
        factory,
        "cpu",
        "cpu",
        heartbeat_seconds=0.02,
        instance_id="thread-generation",
        hostname="thread-test",
        pid=200,
    )
    registered = reporter.start()
    initial_heartbeat = _utc(registered.last_heartbeat_at)
    progressed = False
    deadline = time.monotonic() + 2
    blocker = threading.Event()

    try:
        while time.monotonic() < deadline:
            blocker.wait(0.02)
            with factory() as check:
                worker = get_worker(check, "cpu")
                assert worker is not None
                if _utc(worker.last_heartbeat_at) > initial_heartbeat:
                    progressed = True
                    break
        assert progressed
    finally:
        reporter.stop()

    with factory() as check:
        worker = get_worker(check, "cpu")
        assert worker is not None
        assert worker.state == WorkerState.STOPPED.value
        assert worker.stopped_at is not None
    assert not reporter.generation_lost
    assert reporter.last_error is None


def test_worker_configuration_is_validated() -> None:
    configuration = load_worker_configuration(Path("config/workers.yaml"))
    assert [worker.id for worker in configuration.workers] == ["cpu", "gpu0", "gpu1"]

    try:
        WorkerConfigurationFile.model_validate(
            {
                "version": 1,
                "workers": [
                    {"id": "cpu", "assignment": "cpu"},
                    {"id": "cpu", "assignment": "gpu0"},
                ],
            }
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("Duplicate worker IDs were accepted")

    for invalid in (
        {
            "version": 1,
            "workers": [{"id": "gpu0", "assignment": "gpu0"}],
        },
        {
            "version": 1,
            "workers": [
                {"id": "gpu0", "assignment": "gpu0", "physical_gpu": 0},
                {"id": "gpu-copy", "assignment": "gpu-copy", "physical_gpu": 0},
            ],
        },
    ):
        with pytest.raises(ValidationError):
            WorkerConfigurationFile.model_validate(invalid)


@pytest.mark.asyncio
async def test_vram_denial_leaves_gpu_job_unclaimed(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = create_sample(db, tmp_path / "project")
    job = Job(job_type="mock_project_render", project_id=project.id, gpu_assignment="gpu0")
    db.add(job)
    db.commit()
    inside_lock = False

    class TrackingLock:
        def __init__(self, physical_gpu: str) -> None:
            assert physical_gpu == "0"

        def __enter__(self) -> None:
            nonlocal inside_lock
            inside_lock = True

        def __exit__(self, *_args: object) -> None:
            nonlocal inside_lock
            inside_lock = False

    def probe() -> GPUProbeResult:
        assert inside_lock
        return GPUProbeResult(
            metrics=(
                GPUMetrics(
                    index=0,
                    name="GPU 0",
                    temperature_c=40,
                    utilization_percent=0,
                    memory_total_mb=12000,
                    memory_used_mb=11000,
                    memory_free_mb=1000,
                ),
            )
        )

    monkeypatch.setattr(worker_main, "GPULock", TrackingLock)
    monkeypatch.setattr(worker_main, "probe_gpus", probe)

    assert not await worker_main.process_next(
        db,
        "gpu0",
        physical_gpu=0,
        minimum_free_vram_mb=2000,
    )
    db.refresh(job)
    assert job.state == JobState.QUEUED.value
    assert job.attempt_number == 0
    assert not inside_lock


@pytest.mark.asyncio
async def test_superseded_worker_generation_cannot_claim(db: Session, tmp_path: Path) -> None:
    project = create_sample(db, tmp_path / "project")
    job = Job(job_type="unsupported", project_id=project.id, gpu_assignment="cpu")
    db.add(job)
    db.commit()
    register_worker(db, "cpu", "cpu", instance_id="old-generation")
    register_worker(db, "cpu", "cpu", instance_id="new-generation")

    assert not await worker_main.process_next(
        db,
        "cpu",
        worker_id="cpu",
        worker_instance_id="old-generation",
    )
    db.refresh(job)
    assert job.state == JobState.QUEUED.value
    assert job.attempt_number == 0


@pytest.mark.asyncio
async def test_gpu_lock_and_admission_precede_exact_queue_claim(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = create_sample(db, tmp_path / "project")
    gpu0 = Job(job_type="unsupported", project_id=project.id, gpu_assignment="gpu0")
    gpu1 = Job(job_type="unsupported", project_id=project.id, gpu_assignment="gpu1")
    db.add_all([gpu0, gpu1])
    db.commit()
    events: list[str] = []

    class TrackingLock:
        def __init__(self, _physical_gpu: str) -> None:
            pass

        def __enter__(self) -> None:
            events.append("lock_enter")

        def __exit__(self, *_args: object) -> None:
            events.append("lock_exit")

    real_claim = worker_main.claim_next

    def claim(session: Session, assignment: str, **kwargs: str | None) -> Job | None:
        events.append("claim")
        assert events[0] == "lock_enter"
        return real_claim(session, assignment, **kwargs)

    def probe() -> GPUProbeResult:
        events.append("probe")
        return GPUProbeResult(
            metrics=(
                GPUMetrics(
                    index=0,
                    name="GPU 0",
                    temperature_c=40,
                    utilization_percent=0,
                    memory_total_mb=12000,
                    memory_used_mb=1000,
                    memory_free_mb=11000,
                ),
            )
        )

    statuses: list[tuple[WorkerState, str | None]] = []
    monkeypatch.setattr(worker_main, "GPULock", TrackingLock)
    monkeypatch.setattr(worker_main, "probe_gpus", probe)
    monkeypatch.setattr(worker_main, "claim_next", claim)

    assert await worker_main.process_next(
        db,
        "gpu0",
        physical_gpu=0,
        minimum_free_vram_mb=2000,
        worker_status=lambda state, job_id: statuses.append((state, job_id)),
    )
    db.refresh(gpu0)
    db.refresh(gpu1)
    assert events == ["lock_enter", "probe", "claim", "lock_exit"]
    assert gpu0.state == JobState.FAILED.value
    assert gpu0.attempt_number == 1
    assert gpu1.state == JobState.QUEUED.value
    assert gpu1.attempt_number == 0
    assert statuses == [(WorkerState.BUSY, gpu0.id), (WorkerState.IDLE, None)]


@pytest.mark.asyncio
async def test_cpu_worker_never_probes_nvidia(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_probe() -> GPUProbeResult:
        raise AssertionError("CPU worker attempted an NVIDIA probe")

    monkeypatch.setattr(worker_main, "probe_gpus", unexpected_probe)
    assert not await worker_main.process_next(db, "cpu")
