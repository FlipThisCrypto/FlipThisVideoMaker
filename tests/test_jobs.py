from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from flipthis_video_maker.domain.enums import JobState
from flipthis_video_maker.domain.models import Asset, Candidate, Job, Scene, Shot
from flipthis_video_maker.media.ffmpeg import checksum
from flipthis_video_maker.pipeline.mock_pipeline import (
    MockPipeline,
    PipelineCancelled,
    create_sample,
)
from flipthis_video_maker.services.jobs import claim_next, request_cancellation, retry
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
