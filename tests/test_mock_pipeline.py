import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from flipthis_video_maker.domain.models import Asset, Project, Scene, Shot
from flipthis_video_maker.media.ffmpeg import checksum, probe
from flipthis_video_maker.pipeline.mock_pipeline import MockPipeline, create_sample


def stream_by_type(metadata: dict[str, object], kind: str) -> dict[str, object]:
    streams = metadata["streams"]
    assert isinstance(streams, list)
    return next(stream for stream in streams if stream["codec_type"] == kind)


def test_subtitles_are_recomputed_with_the_final_effective_fps() -> None:
    project = Project(name="Subtitle timing", root_asset_directory="/unused")
    scene = Scene(number=1)
    scene.shots = [
        Shot(sequence_number=1, duration=2, dialogue="First", speaker="Ash"),
        Shot(
            sequence_number=2,
            duration=2,
            dialogue="Second",
            speaker="Mira",
            transition_type="crossfade",
            overlap_frame_count=12,
        ),
    ]
    project.scenes = [scene]

    entries = MockPipeline._subtitle_entries(project, fps=12)

    assert entries[1][:2] == pytest.approx((1.2, 1.8))
    assert entries[1][2] == "Mira: Second"


@pytest.mark.asyncio
async def test_mock_pipeline_is_rerunnable_and_preserves_media(db: Session, tmp_path: Path) -> None:
    root = tmp_path / "project"
    project = create_sample(db, root)

    first = await MockPipeline(db).run(project.id)
    first_path = Path(first.output_path)
    first_checksum = checksum(first_path)
    first_manifest = json.loads(
        Path(str(first.creation_metadata["manifest"])).read_text(encoding="utf-8")
    )

    clip_assets = list(
        db.scalars(
            select(Asset).where(
                Asset.project_id == project.id,
                Asset.type == "video_candidate",
            )
        )
    )
    assert len(clip_assets) == 4
    for asset in clip_assets:
        metadata = probe(Path(asset.file_path))
        video = stream_by_type(metadata, "video")
        audio = stream_by_type(metadata, "audio")
        assert (video["width"], video["height"], video["r_frame_rate"]) == (
            854,
            480,
            "24/1",
        )
        assert (audio["sample_rate"], audio["channels"]) == ("48000", 2)

    final_metadata = probe(first_path)
    assert {stream["codec_type"] for stream in final_metadata["streams"]} == {
        "video",
        "audio",
    }
    assert float(final_metadata["format"]["duration"]) == pytest.approx(31.25, abs=0.15)
    assert [item["type"] for item in first_manifest["transitions"]] == [
        "shared_frame",
        "crossfade",
        "hard_cut",
    ]
    assert first_manifest["transitions"][1]["duration"] == pytest.approx(0.5)
    assert first_manifest["requested_render_profile"] == "draft"
    assert first_manifest["effective_render_profile"] == "draft"
    assert first_manifest["render_profile_execution"]["profile"]["width"] == 854
    assert (
        first_manifest["shots"][2]["continuity_source_frame"]
        == first_manifest["shots"][1]["actual_end_frame"]
    )
    for key in ("subtitles", "thumbnail", "contact_sheet"):
        assert (root / first_manifest[key]).is_file()
    assert all(
        asset.generation_parameters["render_profile_execution"]["effective_profile"] == "draft"
        for asset in clip_assets
    )

    second = await MockPipeline(db).run(project.id)
    second_path = Path(second.output_path)
    assert second_path != first_path
    assert first_path.is_file()
    assert checksum(first_path) == first_checksum
    all_paths = list(db.scalars(select(Asset.file_path).where(Asset.project_id == project.id)))
    assert len(all_paths) == len(set(all_paths))
    assert (
        len(
            list(
                db.scalars(
                    select(Asset).where(
                        Asset.project_id == project.id,
                        Asset.type == "video_candidate",
                    )
                )
            )
        )
        == 8
    )
