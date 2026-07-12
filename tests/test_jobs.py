import asyncio
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from flipthis_video_maker.config.render_profiles import (
    RENDER_PROFILE_EXECUTION_KEY,
    RenderProfileExecution,
    RenderProfileFallbackRecord,
    load_render_profile_configuration,
    render_profile_execution_from_payload,
)
from flipthis_video_maker.domain.enums import JobState, ShotStatus
from flipthis_video_maker.domain.models import Asset, Candidate, Job, Scene, Shot
from flipthis_video_maker.media.ffmpeg import checksum, run
from flipthis_video_maker.pipeline import mock_pipeline as mock_pipeline_module
from flipthis_video_maker.pipeline.mock_pipeline import (
    MockPipeline,
    PipelineCancelled,
    create_sample,
)
from flipthis_video_maker.providers.base.models import VideoRequest
from flipthis_video_maker.providers.mock.providers import MockVideoProvider
from flipthis_video_maker.services.jobs import (
    claim_next,
    mark_succeeded,
    request_cancellation,
    retry,
)
from flipthis_video_maker.workers import main as worker_main
from flipthis_video_maker.workers.main import process_next


def test_claims_only_the_workers_exact_queue(db: Session, tmp_path: Path) -> None:
    project = create_sample(db, tmp_path / "project")
    cpu_job = Job(job_type="render", project_id=project.id, gpu_assignment="cpu")
    gpu_job = Job(job_type="render", project_id=project.id, gpu_assignment="gpu0")
    db.add_all([gpu_job, cpu_job])
    db.commit()

    claimed = claim_next(db, "cpu")

    assert claimed is not None
    assert claimed.id == cpu_job.id
    assert claimed.attempt_number == 1
    assert gpu_job.state == JobState.QUEUED.value


def test_queued_job_can_be_cancelled_and_retried(db: Session, tmp_path: Path) -> None:
    project = create_sample(db, tmp_path / "project")
    job = Job(job_type="mock_project_render", project_id=project.id, gpu_assignment="cpu")
    db.add(job)
    db.commit()

    assert request_cancellation(db, job) is JobState.CANCELLED
    retry(db, job, max_retries=2)
    assert job.state == JobState.QUEUED.value


def test_stale_cancellation_cannot_overwrite_atomic_success(db: Session, tmp_path: Path) -> None:
    project = create_sample(db, tmp_path / "project")
    job = Job(job_type="mock_project_render", project_id=project.id, gpu_assignment="cpu")
    db.add(job)
    db.commit()
    claimed = claim_next(db, "cpu")
    assert claimed is not None
    bind = db.get_bind()

    with Session(bind) as stale_api:
        stale_job = stale_api.get(Job, job.id)
        assert stale_job is not None
        with Session(bind) as worker:
            running_job = worker.get(Job, job.id)
            assert running_job is not None
            assert mark_succeeded(worker, running_job, ["output-asset"]) is JobState.SUCCEEDED
        with pytest.raises(ValueError, match="Cannot cancel job in state succeeded"):
            request_cancellation(stale_api, stale_job)

    db.refresh(job)
    assert job.state == JobState.SUCCEEDED.value
    assert job.output_asset_ids == ["output-asset"]


def test_atomic_cancellation_wins_over_job_success(db: Session, tmp_path: Path) -> None:
    project = create_sample(db, tmp_path / "project")
    job = Job(job_type="mock_project_render", project_id=project.id, gpu_assignment="cpu")
    db.add(job)
    db.commit()
    claimed = claim_next(db, "cpu")
    assert claimed is not None
    bind = db.get_bind()

    with Session(bind) as api:
        running_job = api.get(Job, job.id)
        assert running_job is not None
        assert request_cancellation(api, running_job) is JobState.CANCEL_REQUESTED
    with Session(bind) as worker:
        cancelled_job = worker.get(Job, job.id)
        assert cancelled_job is not None
        assert (
            mark_succeeded(worker, cancelled_job, ["must-not-be-recorded"])
            is JobState.CANCEL_REQUESTED
        )
        assert cancelled_job.output_asset_ids == []


@pytest.mark.asyncio
async def test_worker_uses_the_immutable_enqueued_render_profile(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = load_render_profile_configuration(Path("config/render-profiles.yaml"))
    execution = RenderProfileExecution.resolve(profiles, "standard")
    project = create_sample(db, tmp_path / "project")
    project.resolution_profile = "draft"
    job = Job(
        job_type="mock_project_render",
        project_id=project.id,
        gpu_assignment="cpu",
        payload={RENDER_PROFILE_EXECUTION_KEY: execution.model_dump(mode="json")},
    )
    db.add(job)
    db.commit()
    observed: list[RenderProfileExecution] = []

    async def capture_profile(pipeline: MockPipeline, _project_id: str) -> SimpleNamespace:
        assert pipeline.render_profile_execution is not None
        observed.append(pipeline.render_profile_execution)
        return SimpleNamespace(creation_metadata={"output_asset_id": "standard-output"})

    profiles.profiles["standard"] = profiles.require("draft")
    monkeypatch.setattr(MockPipeline, "run", capture_profile)

    assert await process_next(db, "cpu", render_profiles=profiles)
    db.refresh(job)
    persisted = render_profile_execution_from_payload(job.payload)
    assert job.state == JobState.SUCCEEDED.value
    assert observed[0].effective_profile == "standard"
    assert observed[0].profile.width == 1280
    assert persisted == execution


@pytest.mark.asyncio
async def test_worker_snapshots_a_legacy_job_once_at_claim(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = load_render_profile_configuration(Path("config/render-profiles.yaml"))
    project = create_sample(db, tmp_path / "project")
    project.resolution_profile = "standard"
    job = Job(job_type="mock_project_render", project_id=project.id, gpu_assignment="cpu")
    db.add(job)
    db.commit()

    async def finish(pipeline: MockPipeline, _project_id: str) -> SimpleNamespace:
        assert pipeline.render_profile_execution is not None
        assert pipeline.render_profile_execution.profile.width == 1280
        return SimpleNamespace(creation_metadata={"output_asset_id": "legacy-output"})

    monkeypatch.setattr(MockPipeline, "run", finish)
    assert await process_next(db, "cpu", render_profiles=profiles)
    db.refresh(job)
    execution = render_profile_execution_from_payload(job.payload)
    assert execution.requested_profile == "standard"
    assert execution.effective_profile == "standard"
    assert execution.profile.width == 1280


@pytest.mark.asyncio
async def test_manual_retry_resumes_the_persisted_effective_fallback_profile(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = load_render_profile_configuration(Path("config/render-profiles.yaml"))
    execution = RenderProfileExecution.resolve(profiles, "final").advance(
        RenderProfileFallbackRecord(
            occurred_at=datetime(2026, 7, 12, tzinfo=UTC),
            provider_id="fixture-video",
            operation="video_generation",
            from_profile="final",
            to_profile="standard",
            job_attempt=1,
            gpu_assignment="gpu0",
            backend_code="fixture_oom",
            cleanup_action="fixture_process_reaped",
            cleanup_completed=True,
            cleanup_retry_safe=True,
        )
    )
    project = create_sample(db, tmp_path / "project")
    job = Job(
        job_type="mock_project_render",
        project_id=project.id,
        gpu_assignment="cpu",
        state=JobState.FAILED.value,
        attempt_number=1,
        payload={RENDER_PROFILE_EXECUTION_KEY: execution.model_dump(mode="json")},
    )
    db.add(job)
    db.commit()
    job_id = job.id
    bind = db.get_bind()
    db.close()
    observed: list[RenderProfileExecution] = []

    async def capture_profile(pipeline: MockPipeline, _project_id: str) -> SimpleNamespace:
        assert pipeline.render_profile_execution is not None
        observed.append(pipeline.render_profile_execution)
        return SimpleNamespace(creation_metadata={"output_asset_id": "retry-output"})

    monkeypatch.setattr(MockPipeline, "run", capture_profile)
    with Session(bind) as api:
        failed = api.get(Job, job_id)
        assert failed is not None
        retry(api, failed, max_retries=2)
    with Session(bind) as worker:
        assert await process_next(worker, "cpu", render_profiles=profiles)
    with Session(bind) as check:
        succeeded = check.get(Job, job_id)
        assert succeeded is not None
        persisted = render_profile_execution_from_payload(succeeded.payload)
        assert succeeded.state == JobState.SUCCEEDED.value
        assert succeeded.attempt_number == 2
        assert persisted.effective_profile == "standard"
        assert len(persisted.fallback_history) == 1
        assert observed == [persisted]


@pytest.mark.asyncio
async def test_failed_persistent_job_retries_to_a_final_asset(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = create_sample(db, tmp_path / "project")
    job = Job(job_type="mock_project_render", project_id=project.id, gpu_assignment="cpu")
    db.add(job)
    db.commit()
    job_id = job.id
    bind = db.get_bind()
    db.close()
    real_run = MockPipeline.run

    async def fail_once(_pipeline: MockPipeline, _project_id: str) -> None:
        raise RuntimeError("intentional first-attempt failure")

    monkeypatch.setattr(MockPipeline, "run", fail_once)
    with Session(bind) as restarted_worker:
        assert await process_next(restarted_worker, "cpu")
        failed_job = restarted_worker.get(Job, job_id)
        assert failed_job is not None
        assert failed_job.state == JobState.FAILED.value
        assert failed_job.attempt_number == 1

    monkeypatch.setattr(MockPipeline, "run", real_run)
    with Session(bind) as restarted_api:
        failed_job = restarted_api.get(Job, job_id)
        assert failed_job is not None
        retry(restarted_api, failed_job, max_retries=2)
    with Session(bind) as second_worker:
        assert await process_next(second_worker, "cpu")
    with Session(bind) as restarted_api:
        succeeded_job = restarted_api.get(Job, job_id)
        assert succeeded_job is not None
        assert succeeded_job.state == JobState.SUCCEEDED.value
        assert succeeded_job.attempt_number == 2
        assert len(succeeded_job.output_asset_ids) == 1
        assert succeeded_job.progress == 1
        assert succeeded_job.log_path is not None
        assert Path(succeeded_job.log_path).is_file()
        assert "job_succeeded" in Path(succeeded_job.log_path).read_text(encoding="utf-8")
        output = restarted_api.get(Asset, succeeded_job.output_asset_ids[0])
        assert output is not None
        assert output.type == "final_render"
        assert Path(output.file_path).is_file()
        first_shot = restarted_api.scalar(
            select(Shot)
            .join(Scene)
            .where(Scene.project_id == succeeded_job.project_id)
            .order_by(Shot.sequence_number)
            .limit(1)
        )
        assert first_shot is not None
        selected_candidate_id = first_shot.selected_candidate_id
        selected_candidate = restarted_api.get(Candidate, selected_candidate_id)
        assert selected_candidate is not None
        selected_asset = restarted_api.get(Asset, selected_candidate.output_asset_id)
        assert selected_asset is not None
        selected_checksum = checksum(Path(selected_asset.file_path))
        regeneration = Job(
            job_type="mock_shot_regeneration",
            project_id=succeeded_job.project_id,
            scene_id=first_shot.scene_id,
            shot_id=first_shot.id,
            gpu_assignment="cpu",
            payload={"same_seed": False, "prompt": "A changed camera angle"},
        )
        restarted_api.add(regeneration)
        restarted_api.commit()
        regeneration_id = regeneration.id
        first_shot_id = first_shot.id

    with Session(bind) as regeneration_worker:
        assert await process_next(regeneration_worker, "cpu")
    with Session(bind) as final_api:
        regeneration = final_api.get(Job, regeneration_id)
        shot = final_api.get(Shot, first_shot_id)
        assert regeneration is not None
        assert shot is not None
        assert regeneration.state == JobState.SUCCEEDED.value
        assert shot.selected_candidate_id == selected_candidate_id
        candidates = list(
            final_api.scalars(select(Candidate).where(Candidate.shot_id == first_shot_id))
        )
        assert len(candidates) == 2
        regenerated = next(item for item in candidates if item.id != selected_candidate_id)
        assert regenerated.disposition == "pending"
        assert regenerated.settings["render_profile_execution"]["effective_profile"] == "draft"
        regenerated_asset = final_api.get(Asset, regenerated.output_asset_id)
        assert regenerated_asset is not None
        assert (
            regenerated_asset.generation_parameters["render_profile_execution"]["effective_profile"]
            == "draft"
        )
        selected_asset = final_api.get(
            Asset,
            next(item for item in candidates if item.id == selected_candidate_id).output_asset_id,
        )
        assert selected_asset is not None
        assert checksum(Path(selected_asset.file_path)) == selected_checksum


@pytest.mark.asyncio
async def test_worker_observes_cancellation_after_claim(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = create_sample(db, tmp_path / "project")
    job = Job(job_type="mock_project_render", project_id=project.id, gpu_assignment="cpu")
    db.add(job)
    db.commit()
    bind = db.get_bind()

    async def cancel_during_run(pipeline: MockPipeline, _project_id: str) -> None:
        with Session(bind) as api_session:
            running_job = api_session.get(Job, job.id)
            assert running_job is not None
            assert request_cancellation(api_session, running_job) is JobState.CANCEL_REQUESTED
        assert pipeline.cancel_requested is not None
        assert pipeline.cancel_requested()
        raise PipelineCancelled("cancelled by integration test")

    monkeypatch.setattr(MockPipeline, "run", cancel_during_run)
    assert await process_next(db, "cpu")
    db.refresh(job)
    assert job.state == JobState.CANCELLED.value
    assert job.current_stage == "cancelled"


@pytest.mark.asyncio
async def test_worker_shutdown_cancels_the_claimed_job(db: Session, tmp_path: Path) -> None:
    project = create_sample(db, tmp_path / "project")
    job = Job(job_type="mock_project_render", project_id=project.id, gpu_assignment="cpu")
    db.add(job)
    db.commit()

    assert await process_next(db, "cpu", shutdown_requested=lambda: True)
    db.refresh(job)
    assert job.state == JobState.CANCELLED.value


@pytest.mark.asyncio
async def test_generic_provider_error_cannot_overwrite_cancellation(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = create_sample(db, tmp_path / "project")
    job = Job(job_type="mock_project_render", project_id=project.id, gpu_assignment="cpu")
    db.add(job)
    db.commit()
    bind = db.get_bind()

    async def cancel_then_fail(_pipeline: MockPipeline, _project_id: str) -> None:
        with Session(bind) as api:
            running = api.get(Job, job.id)
            assert running is not None
            assert request_cancellation(api, running) is JobState.CANCEL_REQUESTED
        raise RuntimeError("provider failed after cancellation")

    monkeypatch.setattr(MockPipeline, "run", cancel_then_fail)
    assert await process_next(db, "cpu")
    db.refresh(job)
    assert job.state == JobState.CANCELLED.value


@pytest.mark.asyncio
async def test_log_setup_failure_does_not_leave_claimed_job_running(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = create_sample(db, tmp_path / "project")
    job = Job(job_type="mock_project_render", project_id=project.id, gpu_assignment="cpu")
    db.add(job)
    db.commit()

    def fail_log(*_args: object, **_kwargs: object) -> None:
        raise OSError("log volume is read-only")

    monkeypatch.setattr(worker_main, "append_job_log", fail_log)
    assert await process_next(db, "cpu")
    db.refresh(job)
    assert job.state == JobState.FAILED.value


@pytest.mark.asyncio
async def test_success_log_failure_cannot_overwrite_success(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = create_sample(db, tmp_path / "project")
    job = Job(job_type="mock_project_render", project_id=project.id, gpu_assignment="cpu")
    db.add(job)
    db.commit()
    real_append = worker_main.append_job_log

    async def finish_immediately(_pipeline: MockPipeline, _project_id: str) -> SimpleNamespace:
        return SimpleNamespace(creation_metadata={"output_asset_id": "output-asset"})

    def fail_success_log(path: Path, event: str, **data: object) -> None:
        if event == "job_succeeded":
            raise OSError("log volume became read-only")
        real_append(path, event, **data)

    monkeypatch.setattr(MockPipeline, "run", finish_immediately)
    monkeypatch.setattr(worker_main, "append_job_log", fail_success_log)
    assert await process_next(db, "cpu")
    db.refresh(job)
    assert job.state == JobState.SUCCEEDED.value
    assert job.output_asset_ids == ["output-asset"]


@pytest.mark.asyncio
async def test_worker_terminates_active_mock_ffmpeg_when_cancelled(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = create_sample(db, tmp_path / "project")
    job = Job(job_type="mock_project_render", project_id=project.id, gpu_assignment="cpu")
    db.add(job)
    db.commit()
    bind = db.get_bind()
    ready = tmp_path / "ffmpeg-ready"
    terminated = tmp_path / "ffmpeg-terminated"
    script = """
import signal
import sys
import time
from pathlib import Path

ready = Path(sys.argv[1])
terminated = Path(sys.argv[2])

def stop(_signum, _frame):
    terminated.touch()
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
ready.touch()
while True:
    time.sleep(0.1)
"""

    async def generate_slowly(self: MockVideoProvider, _request: VideoRequest) -> Path:
        await asyncio.to_thread(
            run,
            [sys.executable, "-c", script, str(ready), str(terminated)],
            timeout=10,
            cancel_requested=self.cancel_requested,
            poll_interval=0.02,
            terminate_grace_seconds=1,
        )
        raise AssertionError("cancelled process unexpectedly completed")

    real_generate = MockVideoProvider.generate
    monkeypatch.setattr(MockVideoProvider, "generate", generate_slowly)
    worker = asyncio.create_task(process_next(db, "cpu"))
    async with asyncio.timeout(5):
        while not ready.is_file():
            await asyncio.sleep(0.01)
    with Session(bind) as api_session:
        running_job = api_session.get(Job, job.id)
        assert running_job is not None
        assert request_cancellation(api_session, running_job) is JobState.CANCEL_REQUESTED

    assert await asyncio.wait_for(worker, timeout=5)
    db.refresh(job)
    assert job.state == JobState.CANCELLED.value
    assert job.current_stage == "cancelled"
    assert job.output_asset_ids == []
    assert terminated.is_file()
    assert not list(
        db.scalars(
            select(Asset).where(
                Asset.project_id == project.id,
                Asset.type == "video_candidate",
            )
        )
    )
    first_shot = db.scalar(
        select(Shot)
        .join(Scene)
        .where(Scene.project_id == project.id)
        .order_by(Shot.sequence_number)
        .limit(1)
    )
    assert first_shot is not None
    assert first_shot.status == ShotStatus.FAILED.value

    monkeypatch.setattr(MockVideoProvider, "generate", real_generate)
    retry(db, job, max_retries=2)
    assert await process_next(db, "cpu")
    db.refresh(job)
    assert job.state == JobState.SUCCEEDED.value
    assert job.output_asset_ids


@pytest.mark.asyncio
async def test_worker_accepts_cancellation_during_frame_extraction(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = create_sample(db, tmp_path / "project")
    job = Job(job_type="mock_project_render", project_id=project.id, gpu_assignment="cpu")
    db.add(job)
    db.commit()
    bind = db.get_bind()
    ready = tmp_path / "extract-ready"
    terminated = tmp_path / "extract-terminated"
    real_extract = mock_pipeline_module.extract_frame
    errors: list[BaseException] = []
    script = """
import signal
import sys
import time
from pathlib import Path

ready = Path(sys.argv[1])
terminated = Path(sys.argv[2])

def stop(_signum, _frame):
    terminated.touch()
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
ready.touch()
while True:
    time.sleep(0.1)
"""

    def slow_last_frame(
        video: Path,
        output: Path,
        *,
        last: bool = False,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> Path:
        if not last:
            return real_extract(video, output, cancel_requested=cancel_requested)
        run(
            [sys.executable, "-c", script, str(ready), str(terminated)],
            timeout=10,
            cancel_requested=cancel_requested,
            poll_interval=0.02,
            terminate_grace_seconds=1,
        )
        raise AssertionError("cancelled extraction unexpectedly completed")

    def cancel_when_extraction_starts() -> None:
        try:
            deadline = time.monotonic() + 8
            while not ready.is_file() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not ready.is_file():
                raise TimeoutError("frame extraction did not start")
            with Session(bind) as api_session:
                running_job = api_session.get(Job, job.id)
                assert running_job is not None
                assert request_cancellation(api_session, running_job) is JobState.CANCEL_REQUESTED
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(mock_pipeline_module, "extract_frame", slow_last_frame)
    cancellation = threading.Thread(target=cancel_when_extraction_starts)
    cancellation.start()
    assert await process_next(db, "cpu")
    cancellation.join(timeout=2)

    assert not cancellation.is_alive()
    assert not errors
    db.refresh(job)
    assert job.state == JobState.CANCELLED.value
    assert terminated.is_file()
    assert not list(
        db.scalars(
            select(Asset).where(
                Asset.project_id == project.id,
                Asset.type == "video_candidate",
            )
        )
    )
