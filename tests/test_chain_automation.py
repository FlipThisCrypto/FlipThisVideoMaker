from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from flipthis_video_maker.api.router import pause_chain, resume_chain
from flipthis_video_maker.contracts.video_generation import (
    ChainAutomationConfiguration,
    ChainClipState,
    ContinuationMode,
    FirstLastFrameGenerationRequest,
    InterpolationMode,
)
from flipthis_video_maker.domain.enums import JobState
from flipthis_video_maker.domain.models import Asset, Job, Project, VideoChain, VideoChainClip
from flipthis_video_maker.services.chain_automation import (
    auto_accept_publish_and_replenish,
    configure_chain_automation,
    schedule_chain_replenishment,
    update_playback_position,
)
from flipthis_video_maker.services.video_chains import create_video_chain, enqueue_chain_clip
from flipthis_video_maker.storage.assets import register_asset
from flipthis_video_maker.workers.main import process_next


def _asset(
    db: Session,
    project: Project,
    name: str,
    color: str,
    *,
    parents: list[str] | None = None,
) -> Asset:
    path = Path(project.root_asset_directory) / "keyframes" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1280, 720), color).save(path, "PNG")
    asset = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind="keyframe",
        path=path,
        parents=parents,
    )
    db.commit()
    return asset


def _accepted_initial_clip(
    db: Session,
    tmp_path: Path,
    *,
    buffer_target_seconds: float = 30,
) -> tuple[Project, VideoChain, VideoChainClip]:
    project = Project(
        name="Automated chain",
        root_asset_directory=str(tmp_path / "project"),
        resolution_profile="standard",
    )
    db.add(project)
    db.commit()
    start = _asset(db, project, "start.png", "navy")
    end = _asset(db, project, "end.png", "teal")
    chain = create_video_chain(
        db,
        project,
        name="Automatic",
        continuation_mode=ContinuationMode.AUTO_GENERATE_TARGET,
        buffer_target_seconds=buffer_target_seconds,
    )
    request = FirstLastFrameGenerationRequest(
        provider_id="fixture-flf",
        provider_model="fixture-motion-v1",
        start_frame_asset_id=start.id,
        target_end_frame_asset_id=end.id,
        prompt="A performer continues walking as the camera follows naturally.",
        duration_seconds=10,
        native_requested_fps=24,
        delivery_fps=60,
        width=1280,
        height=720,
        aspect_ratio="16:9",
        interpolation_mode=InterpolationMode.MOCK_FFMPEG_MINTERPOLATE,
        interpolation_provider_id=None,
    )
    clip, clip_job = enqueue_chain_clip(
        db,
        project,
        chain,
        request,
        gpu_assignment="cpu",
    )
    clip.state = ChainClipState.ACCEPTED.value
    clip.actual_last_frame_asset_id = end.id
    clip.actual_start_frame_asset_id = start.id
    clip.result_snapshot = {"continuity_qa": {"passed": True}}
    clip_job.state = JobState.SUCCEEDED.value
    db.commit()
    return project, chain, clip


def _configuration() -> ChainAutomationConfiguration:
    return ChainAutomationConfiguration(
        target_provider_id="mock-image",
        target_provider_model="mock-pattern-v1",
        target_prompt="Create the next coherent arcade action beat.",
        gpu_assignment="gpu0",
    )


@pytest.mark.asyncio
async def test_replenishment_owns_one_target_slot_and_queues_successor_from_actual_boundary(
    db: Session,
    tmp_path: Path,
) -> None:
    project, chain, predecessor = _accepted_initial_clip(db, tmp_path)
    configure_chain_automation(db, chain, _configuration())

    first_job = schedule_chain_replenishment(db, project, chain)
    second_call = schedule_chain_replenishment(db, project, chain)

    assert first_job is not None and second_call is not None
    assert first_job.id == second_call.id == chain.replenishment_job_id
    assert first_job.job_type == "video_chain_target_generation"
    assert await process_next(db, "gpu0")

    db.refresh(first_job)
    db.refresh(chain)
    assert first_job.state == JobState.SUCCEEDED.value
    assert chain.replenishment_job_id is None
    successors = [clip for clip in chain.clips if clip.predecessor_clip_id == predecessor.id]
    assert len(successors) == 1
    successor = successors[0]
    assert successor.planned_start_frame_asset_id == predecessor.actual_last_frame_asset_id
    assert successor.target_end_frame_asset_id == first_job.output_asset_ids[0]
    assert successor.request_snapshot["retry_continuation"]["continuation_mode"] == (
        "auto_generate_target"
    )


def test_playback_position_triggers_replenishment_only_below_remaining_buffer(
    db: Session,
    tmp_path: Path,
) -> None:
    project, chain, _clip = _accepted_initial_clip(
        db,
        tmp_path,
        buffer_target_seconds=10,
    )
    configure_chain_automation(db, chain, _configuration())
    chain.stream_state = {**chain.stream_state, "published_duration_seconds": 10.0}
    db.commit()

    assert schedule_chain_replenishment(db, project, chain) is None
    assert chain.stream_state["automation_status"] == "buffer_target_met"

    job = update_playback_position(db, project, chain, 1)

    assert job is not None
    assert chain.replenishment_job_id == job.id
    assert chain.stream_state["remaining_buffer_seconds"] == 9


def test_automation_configuration_requires_explicit_auto_continuation_mode(
    db: Session,
    tmp_path: Path,
) -> None:
    project, chain, _clip = _accepted_initial_clip(db, tmp_path)
    chain.continuation_mode = ContinuationMode.MANUAL_TARGET.value
    db.commit()

    with pytest.raises(ValueError, match="not automatic"):
        configure_chain_automation(db, chain, _configuration())


def test_stale_concurrent_controller_cannot_claim_a_second_replenishment_slot(
    db: Session,
    tmp_path: Path,
) -> None:
    project, chain, _clip = _accepted_initial_clip(db, tmp_path)
    configure_chain_automation(db, chain, _configuration())
    factory = sessionmaker(db.get_bind(), expire_on_commit=False, class_=Session)

    with factory() as first, factory() as stale:
        first_project = first.get(Project, project.id)
        first_chain = first.get(VideoChain, chain.id)
        stale_project = stale.get(Project, project.id)
        stale_chain = stale.get(VideoChain, chain.id)
        assert first_project and first_chain and stale_project and stale_chain

        claimed = schedule_chain_replenishment(first, first_project, first_chain)
        assert claimed is not None
        with pytest.raises(ValueError, match="already owns"):
            schedule_chain_replenishment(stale, stale_project, stale_chain)

    assert db.query(Job).filter(Job.job_type == "video_chain_target_generation").count() == 1


def test_terminal_target_job_is_reconciled_without_generating_a_duplicate_target(
    db: Session,
    tmp_path: Path,
) -> None:
    project, chain, predecessor = _accepted_initial_clip(db, tmp_path)
    configure_chain_automation(db, chain, _configuration())
    target_job = schedule_chain_replenishment(db, project, chain)
    assert target_job is not None
    assert predecessor.actual_last_frame_asset_id is not None
    target = _asset(
        db,
        project,
        "generated.png",
        "purple",
        parents=[predecessor.actual_last_frame_asset_id],
    )
    target_job.state = JobState.SUCCEEDED.value
    target_job.output_asset_ids = [target.id]
    db.commit()

    successor_job = schedule_chain_replenishment(db, project, chain)

    assert successor_job is not None
    successors = list(
        db.query(VideoChainClip).filter(VideoChainClip.predecessor_clip_id == predecessor.id)
    )
    assert len(successors) == 1
    assert successors[0].target_end_frame_asset_id == target.id
    assert chain.replenishment_job_id is None
    assert db.query(Job).filter(Job.job_type == "video_chain_target_generation").count() == 1


def test_failed_target_requires_operator_retry_instead_of_unbounded_resubmission(
    db: Session,
    tmp_path: Path,
) -> None:
    project, chain, _predecessor = _accepted_initial_clip(db, tmp_path)
    configure_chain_automation(db, chain, _configuration())
    failed = schedule_chain_replenishment(db, project, chain)
    assert failed is not None
    failed.state = JobState.FAILED.value
    failed.error_info = {"failure_kind": "execution_failed", "retry_safe": False}
    db.commit()

    result = schedule_chain_replenishment(db, project, chain)

    assert result is not None and result.id == failed.id
    assert "operator_action_required" in chain.stream_state["automation_status"]
    assert db.query(Job).filter(Job.job_type == "video_chain_target_generation").count() == 1


def test_pause_cancels_queued_replenishment_and_resume_recreates_one_slot(
    db: Session,
    tmp_path: Path,
) -> None:
    project, chain, _predecessor = _accepted_initial_clip(db, tmp_path)
    configure_chain_automation(db, chain, _configuration())
    cancelled = schedule_chain_replenishment(db, project, chain)
    assert cancelled is not None

    paused = pause_chain(chain.id, db)

    db.refresh(cancelled)
    assert paused.state == "paused"
    assert cancelled.state == JobState.CANCELLED.value
    assert paused.replenishment_job_id is None

    resumed = resume_chain(chain.id, db)

    assert resumed.state == "active"
    assert resumed.replenishment_job_id is not None
    assert resumed.replenishment_job_id != cancelled.id
    assert db.query(Job).filter(Job.job_type == "video_chain_target_generation").count() == 2


def test_auto_accept_requires_passed_qa_then_publishes_and_replenishes(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chain, clip = _accepted_initial_clip(db, tmp_path)
    clip.state = ChainClipState.AWAITING_REVIEW.value
    db.commit()
    configure_chain_automation(db, chain, _configuration())
    published: list[str] = []
    monkeypatch.setattr(
        "flipthis_video_maker.services.chain_automation.publish_hls_buffer",
        lambda _db, _project, current: published.append(current.id),
    )

    job = auto_accept_publish_and_replenish(db, project, chain, clip)

    assert clip.state == ChainClipState.ACCEPTED.value
    assert published == [chain.id]
    assert job is not None and job.id == chain.replenishment_job_id
