import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy.orm import Session

from flipthis_video_maker.contracts.video_generation import (
    ChainClipState,
    FirstLastFrameGenerationRequest,
    InterpolationMode,
    LipSyncEligibility,
    LipSyncMode,
    LipSyncSettings,
    RetryContinuation,
)
from flipthis_video_maker.domain.models import Job, Project, VideoChainClip
from flipthis_video_maker.media import video_delivery
from flipthis_video_maker.providers.mock.providers import MockVideoProvider
from flipthis_video_maker.services.jobs import claim_next, reconcile_expired_job_leases
from flipthis_video_maker.services.video_chains import (
    VideoChainConflict,
    accept_chain_clip,
    active_lineage_clips,
    create_video_chain,
    enqueue_chain_clip,
    request_from_clip,
)
from flipthis_video_maker.services.workers import register_worker
from flipthis_video_maker.storage.assets import register_asset


def _request(
    start_id: str,
    end_id: str,
    *,
    predecessor_id: str | None = None,
) -> FirstLastFrameGenerationRequest:
    return FirstLastFrameGenerationRequest(
        provider_id="fixture-flf",
        provider_model="fixture-motion-v1",
        start_frame_asset_id=start_id,
        target_end_frame_asset_id=end_id,
        prompt="A subject walks naturally across the scene while the camera tracks smoothly.",
        duration_seconds=10,
        native_requested_fps=24,
        delivery_fps=60,
        width=128,
        height=72,
        aspect_ratio="16:9",
        seed=7,
        interpolation_mode=InterpolationMode.MOCK_FFMPEG_MINTERPOLATE,
        interpolation_provider_id=None,
        retry_continuation=RetryContinuation(predecessor_clip_id=predecessor_id),
    )


def _project_and_assets(db: Session, tmp_path: Path) -> tuple[Project, list[str]]:
    root = tmp_path / "project"
    source = root / "source"
    source.mkdir(parents=True)
    project = Project(name="FLF", root_asset_directory=str(root))
    db.add(project)
    db.commit()
    ids: list[str] = []
    for index, color in enumerate(("red", "blue", "green", "yellow")):
        path = source / f"frame-{index}.png"
        Image.new("RGB", (128, 72), color).save(path)
        asset = register_asset(
            db,
            project_id=project.id,
            shot_id=None,
            kind="upload",
            path=path,
            provider="upload",
        )
        ids.append(asset.id)
    db.commit()
    return project, ids


def test_contract_is_immutable_versioned_and_calculates_exact_delivery_frames() -> None:
    request = _request("start", "end")

    assert request.version == 1
    assert request.expected_delivery_frames == 600
    assert len(request.digest()) == 64
    with pytest.raises(ValidationError):
        request.prompt = "mutated"  # type: ignore[misc]


def test_contract_rejects_untyped_provider_leaks_and_fake_native_60fps() -> None:
    values = _request("start", "end").model_dump()
    values["provider_settings"] = {"some-other-provider": {"raw": True}}
    with pytest.raises(ValidationError, match="selected provider namespace"):
        FirstLastFrameGenerationRequest.model_validate(values)

    values = _request("start", "end").model_dump()
    values["interpolation_mode"] = "provider_native"
    values["interpolation_provider_id"] = None
    with pytest.raises(ValidationError, match="real interpolation mode"):
        FirstLastFrameGenerationRequest.model_validate(values)


def test_mock_transition_is_not_advertised_as_generative_video() -> None:
    info = MockVideoProvider().info()

    assert info.generation_category == "mock_test_video"
    assert "first_last_frame_generative_video" not in info.capabilities
    assert "crossfades" in info.notes


@pytest.mark.parametrize(
    "eligibility",
    [
        LipSyncEligibility.NARRATION_NO_VISIBLE_SPEAKER,
        LipSyncEligibility.MOUTH_HIDDEN,
        LipSyncEligibility.MULTIPLE_FACES,
        LipSyncEligibility.NO_SPEECH,
        LipSyncEligibility.EXPLICIT_SKIP,
    ],
)
def test_non_speaking_shot_decisions_explicitly_skip_lip_sync(
    eligibility: LipSyncEligibility,
) -> None:
    request = _request("start", "end").model_copy(
        update={"lip_sync_settings": LipSyncSettings(eligibility=eligibility)}
    )

    assert request.lip_sync_mode is LipSyncMode.SKIP


def test_lip_sync_requires_one_visible_speaker_and_persisted_audio() -> None:
    values = _request("start", "end").model_dump()
    values.update(
        lip_sync_mode="latentsync",
        lip_sync_provider_id="latentsync-local",
        audio_reference_asset_id="audio",
        lip_sync_settings={"eligibility": "multiple_faces", "face_index": 1},
    )
    with pytest.raises(ValidationError, match="one clearly visible speaking face"):
        FirstLastFrameGenerationRequest.model_validate(values)

    values["lip_sync_settings"] = {"eligibility": "speaking_face_visible"}
    request = FirstLastFrameGenerationRequest.model_validate(values)
    assert request.lip_sync_mode is LipSyncMode.LATENTSYNC


def test_chain_enforces_actual_last_frame_lineage_and_conflicting_successors(
    db: Session,
    tmp_path: Path,
) -> None:
    project, asset_ids = _project_and_assets(db, tmp_path)
    chain = create_video_chain(db, project, name="Continuous sequence")
    first, _ = enqueue_chain_clip(db, project, chain, _request(asset_ids[0], asset_ids[1]))
    first.actual_last_frame_asset_id = asset_ids[2]
    first.actual_start_frame_asset_id = asset_ids[0]
    first.state = ChainClipState.AWAITING_REVIEW.value
    first.result_snapshot = {"continuity_qa": {"passed": True}}
    db.commit()
    accept_chain_clip(db, first)

    wrong = _request(asset_ids[1], asset_ids[3], predecessor_id=first.id)
    with pytest.raises(VideoChainConflict, match="decoded actual final frame"):
        enqueue_chain_clip(db, project, chain, wrong, predecessor=first)

    successor_request = _request(asset_ids[2], asset_ids[3], predecessor_id=first.id)
    successor, _ = enqueue_chain_clip(
        db,
        project,
        chain,
        successor_request,
        predecessor=first,
    )
    assert successor.planned_start_frame_asset_id == first.actual_last_frame_asset_id
    assert successor.sequence_number == 2

    with pytest.raises(VideoChainConflict, match="conflicting successor"):
        enqueue_chain_clip(
            db,
            project,
            chain,
            successor_request,
            predecessor=first,
        )

    branch_request = _request(asset_ids[2], asset_ids[0], predecessor_id=first.id)
    branch, _ = enqueue_chain_clip(
        db,
        project,
        chain,
        branch_request,
        predecessor=first,
        new_lineage=True,
    )
    assert chain.active_lineage_version == 2
    assert [clip.id for clip in active_lineage_clips(db, chain)] == [first.id, branch.id]


def test_request_digest_detects_persisted_snapshot_tampering(
    db: Session,
    tmp_path: Path,
) -> None:
    project, asset_ids = _project_and_assets(db, tmp_path)
    chain = create_video_chain(db, project, name="Immutable")
    clip, _ = enqueue_chain_clip(db, project, chain, _request(asset_ids[0], asset_ids[1]))
    changed = dict(clip.request_snapshot)
    changed["prompt"] = "tampered"
    clip.request_snapshot = changed
    db.commit()

    with pytest.raises(RuntimeError, match="digest"):
        request_from_clip(db.get(VideoChainClip, clip.id) or clip)


def test_expired_worker_lease_marks_linked_video_clip_failed_without_requeue(
    db: Session,
    tmp_path: Path,
) -> None:
    project, asset_ids = _project_and_assets(db, tmp_path)
    chain = create_video_chain(db, project, name="Lease recovery")
    clip, queued_job = enqueue_chain_clip(
        db,
        project,
        chain,
        _request(asset_ids[0], asset_ids[1]),
    )
    claimed_at = datetime(2026, 7, 20, 12, tzinfo=UTC)
    worker = register_worker(
        db,
        "cpu",
        "cpu",
        instance_id="expired-generation",
        registered_at=claimed_at,
    )
    claimed = claim_next(
        db,
        "cpu",
        worker_id=worker.id,
        worker_instance_id=worker.instance_id,
        lease_seconds=10,
        claimed_at=claimed_at,
    )
    assert claimed is not None and claimed.id == queued_job.id

    recovered = reconcile_expired_job_leases(
        db,
        checked_at=claimed_at + timedelta(seconds=11),
    )

    assert len(recovered) == 1
    db.refresh(clip)
    persisted_job = db.get(Job, queued_job.id)
    assert persisted_job is not None
    assert persisted_job.state == "failed"
    assert clip.state == ChainClipState.FAILED.value
    assert clip.failure_info["failure_kind"] == "orphaned_worker_lease"


def test_frame_timing_rejects_variable_timestamps_even_when_rates_claim_cfr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "streams": [
            {
                "avg_frame_rate": "60/1",
                "r_frame_rate": "60/1",
                "nb_read_frames": "4",
                "nb_frames": "4",
                "duration": "0.066667",
                "width": 1920,
                "height": 1080,
            }
        ],
        "frames": [
            {"best_effort_timestamp_time": value}
            for value in ("0.000000", "0.016667", "0.050000", "0.066667")
        ],
        "format": {"duration": "0.066667"},
    }
    monkeypatch.setattr(
        video_delivery,
        "run",
        lambda *_args, **_kwargs: CompletedProcess([], 0, json.dumps(payload), ""),
    )

    facts = video_delivery.inspect_frame_timing(Path("claimed-cfr.mp4"))

    assert facts["average_frame_rate"] == 60
    assert facts["nominal_frame_rate"] == 60
    assert facts["constant_frame_rate"] is False
