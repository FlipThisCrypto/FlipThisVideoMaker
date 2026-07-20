import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from flipthis_video_maker.config.render_finalization import (
    RenderFinalizationExecution,
    RenderFinalizationRequest,
)
from flipthis_video_maker.config.render_profiles import (
    RenderProfileConfigurationFile,
    RenderProfileExecution,
)
from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.domain.enums import ShotStatus
from flipthis_video_maker.domain.models import Asset, Project, Render, Scene, Shot
from flipthis_video_maker.media.ffmpeg import MediaCancelled, checksum, probe, run
from flipthis_video_maker.pipeline import mock_pipeline as mock_pipeline_module
from flipthis_video_maker.pipeline.mock_pipeline import MockPipeline
from flipthis_video_maker.storage.assets import register_asset


def _profile_execution() -> RenderProfileExecution:
    profiles = RenderProfileConfigurationFile.model_validate(
        {
            "profiles": {
                "test": {
                    "width": 320,
                    "height": 180,
                    "fps": 12,
                    "video_codec": "libx264",
                    "audio_codec": "aac",
                }
            }
        }
    )
    return RenderProfileExecution.resolve(profiles, "test")


def _project(db: Session, root: Path) -> Project:
    project = Project(
        name="Finalization fixture",
        root_asset_directory=str(root),
        resolution_profile="test",
    )
    scene = Scene(number=1, title="One speaking shot", characters=["Ash"])
    scene.shots = [
        Shot(
            sequence_number=1,
            duration=1.5,
            prompt="Ash turns toward a pulsing blue light",
            dialogue="The signal is stable now.",
            speaker="Ash",
            status=ShotStatus.APPROVED.value,
            approval_state="approved",
            seed=700,
        )
    ]
    project.scenes = [scene]
    db.add(project)
    db.commit()
    return project


def _execution(payload: dict[str, object]) -> RenderFinalizationExecution:
    return RenderFinalizationExecution.capture(RenderFinalizationRequest.model_validate(payload))


def _asset(db: Session, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    assert asset is not None
    return asset


def _stream_types(path: Path) -> set[str]:
    return {stream["codec_type"] for stream in probe(path)["streams"]}


@pytest.mark.asyncio
async def test_sidecar_default_registers_complete_asset_graph_and_rerenders_immutably(
    db: Session,
    tmp_path: Path,
) -> None:
    project = _project(db, tmp_path / "project")
    execution = RenderFinalizationExecution.compatibility_default()

    first = await MockPipeline(
        db,
        render_profile_execution=_profile_execution(),
        render_finalization_execution=execution,
    ).run(project.id)
    first_output = Path(first.output_path)
    first_checksum = checksum(first_output)
    first_asset = _asset(db, str(first.creation_metadata["output_asset_id"]))
    subtitle_asset = _asset(db, str(first.creation_metadata["subtitle_asset_id"]))
    thumbnail_asset = _asset(db, str(first.creation_metadata["thumbnail_asset_id"]))
    contact_asset = _asset(db, str(first.creation_metadata["contact_sheet_asset_id"]))
    manifest_asset = _asset(db, str(first.creation_metadata["manifest_asset_id"]))
    manifest = json.loads(Path(manifest_asset.file_path).read_text(encoding="utf-8"))

    assert first_output.name == "final.mp4"
    assert _stream_types(first_output) == {"video", "audio"}
    assert first_asset.type == "final_render"
    assert first_asset.parent_asset_ids
    assert first_asset.generation_parameters["finalization_stage"] == "assembly"
    assert first_asset.generation_parameters[
        "render_finalization_execution"
    ] == execution.model_dump(mode="json")
    assert subtitle_asset.type == "subtitle_sidecar"
    assert subtitle_asset.parent_asset_ids == first_asset.parent_asset_ids
    assert thumbnail_asset.parent_asset_ids == [first_asset.id]
    assert contact_asset.parent_asset_ids == [first_asset.id]
    assert manifest_asset.parent_asset_ids == [
        first_asset.id,
        subtitle_asset.id,
        thumbnail_asset.id,
        contact_asset.id,
    ]
    assert manifest["version"] == 2
    assert manifest["asset_ids"]["render"] == first_asset.id
    assert manifest["render_finalization_execution"] == execution.model_dump(mode="json")
    assert manifest["finalization_stages"][-1] == {
        "stage": "subtitles",
        "applied": False,
        "reason": "sidecar_only",
        "asset_id": None,
    }
    assert first.creation_metadata["final_quality"]["checks"]["decodable_video"] is True

    second = await MockPipeline(
        db,
        render_profile_execution=_profile_execution(),
        render_finalization_execution=execution,
    ).run(project.id)

    assert Path(second.output_path) != first_output
    assert first_output.is_file()
    assert checksum(first_output) == first_checksum
    paths = list(db.scalars(select(Asset.file_path).where(Asset.project_id == project.id)))
    assert len(paths) == len(set(paths))


@pytest.mark.parametrize(
    ("mode", "expected_streams", "expected_model"),
    [
        ("soft", {"video", "audio", "subtitle"}, "subtitle-mux-v1"),
        ("burned", {"video", "audio"}, "subtitle-burn-v1"),
    ],
)
@pytest.mark.asyncio
async def test_subtitle_modes_publish_a_distinct_final_asset(
    db: Session,
    tmp_path: Path,
    mode: str,
    expected_streams: set[str],
    expected_model: str,
) -> None:
    project = _project(db, tmp_path / mode)
    execution = _execution({"subtitle": {"mode": mode, "language": "eng"}})

    render = await MockPipeline(
        db,
        render_profile_execution=_profile_execution(),
        render_finalization_execution=execution,
    ).run(project.id)

    final_asset = _asset(db, str(render.creation_metadata["output_asset_id"]))
    subtitle_asset = _asset(db, str(render.creation_metadata["subtitle_asset_id"]))
    assembly_asset = _asset(db, final_asset.parent_asset_ids[0])
    assert _stream_types(Path(render.output_path)) == expected_streams
    assert final_asset.type == "final_render"
    assert final_asset.model_identifier == expected_model
    assert final_asset.parent_asset_ids == [assembly_asset.id, subtitle_asset.id]
    assert assembly_asset.type == "assembled_render"
    assert checksum(Path(final_asset.file_path)) == final_asset.checksum
    assert render.subtitle_configuration["mode"] == mode
    assert render.subtitle_configuration["result"]["mode"] == mode


@pytest.mark.parametrize("with_music", [False, True])
@pytest.mark.asyncio
async def test_audio_normalization_and_ducked_music_preserve_duration_and_provenance(
    db: Session,
    tmp_path: Path,
    with_music: bool,
) -> None:
    project = _project(db, tmp_path / f"audio-{with_music}")
    music_asset: Asset | None = None
    request_payload: dict[str, object] = {
        "audio": {"normalize": True, "integrated_lufs": -18},
    }
    if with_music:
        music_path = Path(project.root_asset_directory) / "source" / "music.wav"
        music_path.parent.mkdir(parents=True, exist_ok=True)
        run(
            [
                get_settings().ffmpeg_path,
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:sample_rate=48000:duration=0.3",
                "-c:a",
                "pcm_s16le",
                str(music_path),
            ]
        )
        music_asset = register_asset(
            db,
            project_id=project.id,
            shot_id=None,
            kind="background_music",
            path=music_path,
            provider="upload",
        )
        db.commit()
        request_payload["music"] = {
            "asset_id": music_asset.id,
            "gain_db": -14,
        }

    request = RenderFinalizationRequest.model_validate(request_payload)
    execution = RenderFinalizationExecution.capture(
        request,
        music_checksum=music_asset.checksum if music_asset is not None else None,
        music_mime_type="audio/wav" if music_asset is not None else None,
    )
    render = await MockPipeline(
        db,
        render_profile_execution=_profile_execution(),
        render_finalization_execution=execution,
        music_asset=music_asset,
    ).run(project.id)

    final_asset = _asset(db, str(render.creation_metadata["output_asset_id"]))
    assembly_asset = _asset(db, final_asset.parent_asset_ids[0])
    assert final_asset.type == "final_render"
    assert final_asset.model_identifier == ("loudnorm-ducking-v1" if with_music else "loudnorm-v1")
    assert assembly_asset.type == "assembled_render"
    assert float(probe(Path(render.output_path))["format"]["duration"]) == pytest.approx(
        1.5,
        abs=0.2,
    )
    assert _stream_types(Path(render.output_path)) == {"video", "audio"}
    assert render.audio_configuration["normalize"] is True
    result = render.audio_configuration["result"]
    assert result["target"]["integrated_lufs"] == -18
    assert result["music_ducking_applied"] is with_music
    if music_asset is not None:
        assert final_asset.parent_asset_ids == [assembly_asset.id, music_asset.id]
        assert render.audio_configuration["music_asset_id"] == music_asset.id


@pytest.mark.asyncio
async def test_cancelled_audio_stage_retains_committed_inputs_and_retry_uses_new_paths(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(db, tmp_path / "cancel")
    execution = _execution({"audio": {"normalize": True}})
    real_normalize = mock_pipeline_module.normalize_audio

    def cancel_audio(*_args: object, **_kwargs: object) -> None:
        raise MediaCancelled("cancelled final audio fixture")

    monkeypatch.setattr(mock_pipeline_module, "normalize_audio", cancel_audio)
    with pytest.raises(MediaCancelled, match="cancelled final audio fixture"):
        await MockPipeline(
            db,
            render_profile_execution=_profile_execution(),
            render_finalization_execution=execution,
        ).run(project.id)

    retained = list(
        db.scalars(
            select(Asset).where(
                Asset.project_id == project.id,
                Asset.type.in_(["assembled_render", "subtitle_sidecar"]),
            )
        )
    )
    assert {asset.type for asset in retained} == {"assembled_render", "subtitle_sidecar"}
    retained_checksums = {asset.id: checksum(Path(asset.file_path)) for asset in retained}
    assert not list(db.scalars(select(Render).where(Render.project_id == project.id)))
    assert not list(Path(project.root_asset_directory).rglob("*.partial*"))

    monkeypatch.setattr(mock_pipeline_module, "normalize_audio", real_normalize)
    render = await MockPipeline(
        db,
        render_profile_execution=_profile_execution(),
        render_finalization_execution=execution,
    ).run(project.id)

    assert Path(render.output_path).is_file()
    for asset in retained:
        assert Path(asset.file_path).is_file()
        assert checksum(Path(asset.file_path)) == retained_checksums[asset.id]


@pytest.mark.asyncio
async def test_pipeline_rejects_missing_music_asset_before_generation(
    db: Session,
    tmp_path: Path,
) -> None:
    project = _project(db, tmp_path / "missing-music")
    request = RenderFinalizationRequest.model_validate(
        {
            "audio": {"normalize": True},
            "music": {"asset_id": "missing"},
        }
    )
    execution = RenderFinalizationExecution.capture(
        request,
        music_checksum="a" * 64,
        music_mime_type="audio/wav",
    )

    with pytest.raises(ValueError, match="requires its immutable Asset"):
        await MockPipeline(
            db,
            render_profile_execution=_profile_execution(),
            render_finalization_execution=execution,
        ).run(project.id)

    assert not list(Path(project.root_asset_directory).rglob("*.mp4"))
