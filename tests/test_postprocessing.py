from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from flipthis_video_maker.domain.enums import ShotStatus
from flipthis_video_maker.domain.models import Asset, Candidate, Project, Scene, Shot
from flipthis_video_maker.media.ffmpeg import MediaCancelled, checksum
from flipthis_video_maker.pipeline.mock_pipeline import MockPipeline
from flipthis_video_maker.pipeline.postprocessing import (
    decide_interpolation,
    decide_lip_sync,
)
from flipthis_video_maker.pipeline.shot_regeneration import MockShotRegenerator
from flipthis_video_maker.providers.mock.providers import (
    MockInterpolationProvider,
    MockLipSyncProvider,
)


@pytest.mark.parametrize(
    ("settings", "reason"),
    [
        ({"dialogue_visibility": "off-camera"}, "off_camera_dialogue"),
        ({"dialogue_visibility": "mouth-hidden"}, "mouth_hidden"),
        ({"lip_sync_mode": "integrated"}, "integrated_in_video_provider"),
        ({"lip_sync_mode": "skip"}, "explicit_skip"),
    ],
)
def test_lip_sync_decision_bypasses_nonqualifying_dialogue(
    settings: dict[str, object],
    reason: str,
) -> None:
    decision = decide_lip_sync("A visible line", "Ash", settings)

    assert decision.apply is False
    assert decision.reason == reason


def test_postprocessing_decisions_require_visible_dialogue_or_explicit_interpolation() -> None:
    assert decide_lip_sync("A visible line", "Ash", {}).apply is True
    assert decide_lip_sync("", "Ash", {}).reason == "no_dialogue"
    assert decide_lip_sync("Narrated", None, {}).reason == "no_visible_speaker"
    assert decide_interpolation("hard_cut", {}).apply is False
    assert decide_interpolation("interpolated_bridge", {}).apply is True
    assert decide_interpolation("hard_cut", {"interpolation": True}).apply is True
    assert (
        decide_interpolation("interpolated_bridge", {"interpolation": False}).reason
        == "explicit_skip"
    )


@pytest.mark.asyncio
async def test_mock_passthrough_is_atomic_and_removes_cancelled_partials(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    audio = tmp_path / "dialogue.wav"
    source.write_bytes(b"video" * 512)
    audio.write_bytes(b"audio")

    lip_output = tmp_path / "lip" / "output.mp4"
    assert await MockLipSyncProvider().process(source, audio, lip_output) == lip_output
    assert lip_output.read_bytes() == source.read_bytes()
    assert not list(lip_output.parent.glob("*.partial*"))

    checks = 0

    def cancel_during_copy() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    interpolation_output = tmp_path / "interpolation" / "output.mp4"
    with pytest.raises(MediaCancelled, match="interpolation cancelled"):
        await MockInterpolationProvider(cancel_during_copy).process(source, interpolation_output)
    assert not interpolation_output.exists()
    assert not list(interpolation_output.parent.glob("*.partial*"))


@pytest.mark.asyncio
async def test_render_and_regeneration_use_final_postprocessed_assets(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = Project(
        name="Post-processing project",
        root_asset_directory=str(tmp_path / "project"),
        resolution_profile="draft",
    )
    scene = Scene(number=1, title="Visible speaker", characters=["Ash"])
    shot = Shot(
        sequence_number=1,
        duration=1.2,
        prompt="Ash speaks while the camera slowly moves",
        dialogue="We should leave now.",
        speaker="Ash",
        transition_type="interpolated_bridge",
        generation_settings={"steps": 4},
        status=ShotStatus.APPROVED.value,
        approval_state="approved",
    )
    scene.shots.append(shot)
    project.scenes.append(scene)
    db.add(project)
    db.commit()

    observed_states: list[tuple[str, str]] = []
    real_lip_sync = MockLipSyncProvider.process
    real_interpolation = MockInterpolationProvider.process

    async def observe_lip_sync(
        provider: MockLipSyncProvider,
        video: Path,
        audio: Path,
        output: Path,
    ) -> Path:
        observed_states.append(("lip_sync", shot.status))
        return await real_lip_sync(provider, video, audio, output)

    async def observe_interpolation(
        provider: MockInterpolationProvider,
        video: Path,
        output: Path,
    ) -> Path:
        observed_states.append(("interpolation", shot.status))
        return await real_interpolation(provider, video, output)

    monkeypatch.setattr(MockLipSyncProvider, "process", observe_lip_sync)
    monkeypatch.setattr(MockInterpolationProvider, "process", observe_interpolation)

    render = await MockPipeline(db).run(project.id)
    assert observed_states == [
        ("lip_sync", ShotStatus.LIPSYNC_PENDING.value),
        ("interpolation", ShotStatus.CONTINUITY_PENDING.value),
    ]
    assert shot.status == ShotStatus.COMPLETE.value

    assets = list(db.scalars(select(Asset).where(Asset.shot_id == shot.id)))
    by_type = {asset.type: asset for asset in assets}
    base_video = by_type["video_candidate"]
    dialogue_audio = by_type["dialogue_audio"]
    lip_synced = by_type["lipsynced_video"]
    interpolated = by_type["interpolated_video"]
    actual_start = by_type["actual_start_frame"]
    actual_end = by_type["actual_end_frame"]
    selected = db.get(Candidate, shot.selected_candidate_id)
    assert selected is not None
    assert lip_synced.parent_asset_ids == [base_video.id, dialogue_audio.id]
    assert interpolated.parent_asset_ids == [lip_synced.id]
    assert selected.output_asset_id == interpolated.id
    assert actual_start.parent_asset_ids == [interpolated.id]
    assert actual_end.parent_asset_ids == [interpolated.id]
    assert selected.first_frame_asset_id == actual_start.id
    assert selected.last_frame_asset_id == actual_end.id
    assert selected.settings["postprocessing"]["lip_sync"]["applied"] is True
    assert selected.settings["postprocessing"]["interpolation"]["applied"] is True
    assert (
        shot.continuity_packet["generation"]["postprocessing"]
        == selected.settings["postprocessing"]
    )
    assert checksum(Path(base_video.file_path)) == checksum(Path(lip_synced.file_path))
    assert checksum(Path(lip_synced.file_path)) == checksum(Path(interpolated.file_path))

    final_asset = db.get(Asset, render.creation_metadata["output_asset_id"])
    assert final_asset is not None
    assert final_asset.parent_asset_ids == [interpolated.id]

    selected_id = shot.selected_candidate_id
    selected_start_id = shot.actual_start_frame_id
    selected_end_id = shot.actual_end_frame_id
    selected_checksum = checksum(Path(interpolated.file_path))
    lip_sync_count = len([asset for asset in assets if asset.type == "lipsynced_video"])
    observed_states.clear()

    regenerated = await MockShotRegenerator(db).run(
        shot.id,
        same_seed=False,
        generation_settings={
            "dialogue_visibility": "mouth-hidden",
            "interpolation": True,
        },
    )
    assert observed_states == [("interpolation", ShotStatus.CONTINUITY_PENDING.value)]
    assert regenerated.disposition == "pending"
    assert regenerated.id != selected.id
    assert shot.selected_candidate_id == selected_id
    assert shot.actual_start_frame_id == selected_start_id
    assert shot.actual_end_frame_id == selected_end_id
    assert shot.status == ShotStatus.COMPLETE.value
    assert checksum(Path(interpolated.file_path)) == selected_checksum

    regenerated_output = db.get(Asset, regenerated.output_asset_id)
    regenerated_start = db.get(Asset, regenerated.first_frame_asset_id)
    regenerated_end = db.get(Asset, regenerated.last_frame_asset_id)
    assert regenerated_output is not None
    assert regenerated_start is not None
    assert regenerated_end is not None
    assert regenerated_output.type == "interpolated_video"
    assert regenerated_start.parent_asset_ids == [regenerated_output.id]
    assert regenerated_end.parent_asset_ids == [regenerated_output.id]
    assert regenerated.settings["postprocessing"]["lip_sync"] == {
        "applied": False,
        "reason": "mouth_hidden",
        "provider": "mock-lipsync",
        "model": "mock-passthrough-v1",
        "asset_id": None,
    }
    assert regenerated.settings["steps"] == 4
    final_assets = list(db.scalars(select(Asset).where(Asset.shot_id == shot.id)))
    assert (
        len([asset for asset in final_assets if asset.type == "lipsynced_video"]) == lip_sync_count
    )

    candidate_count = len(list(db.scalars(select(Candidate).where(Candidate.shot_id == shot.id))))

    async def cancel_lip_sync(
        _provider: MockLipSyncProvider,
        _video: Path,
        _audio: Path,
        _output: Path,
    ) -> Path:
        assert shot.status == ShotStatus.LIPSYNC_PENDING.value
        raise MediaCancelled("cancelled lip-sync fixture")

    monkeypatch.setattr(MockLipSyncProvider, "process", cancel_lip_sync)
    with pytest.raises(MediaCancelled, match="cancelled lip-sync fixture"):
        await MockShotRegenerator(db).run(
            shot.id,
            same_seed=False,
            generation_settings={"interpolation": False},
        )
    db.refresh(shot)
    assert shot.status == ShotStatus.FAILED.value
    assert shot.selected_candidate_id == selected_id
    assert shot.actual_start_frame_id == selected_start_id
    assert shot.actual_end_frame_id == selected_end_id
    assert (
        len(list(db.scalars(select(Candidate).where(Candidate.shot_id == shot.id))))
        == candidate_count
    )
