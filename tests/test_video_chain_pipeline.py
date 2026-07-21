import json
import math
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
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
from flipthis_video_maker.domain.models import Asset, Project
from flipthis_video_maker.media.ffmpeg import probe, run
from flipthis_video_maker.media.video_delivery import (
    extract_frame_at_index,
    image_similarity,
    inspect_frame_timing,
    mock_motion_interpolate,
)
from flipthis_video_maker.pipeline import video_chain as video_chain_module
from flipthis_video_maker.pipeline.video_chain import VideoChainPipeline
from flipthis_video_maker.providers.base.video_generation import (
    ProviderRunOutput,
    ResolvedFirstLastFrameRequest,
)
from flipthis_video_maker.providers.latentsync.cli import LipSyncRunOutput
from flipthis_video_maker.services.video_chains import (
    accept_chain_clip,
    assemble_video_chain,
    create_video_chain,
    enqueue_chain_clip,
)
from flipthis_video_maker.services.video_streaming import publish_hls_buffer
from flipthis_video_maker.storage.assets import register_asset


class FixtureMotionProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(
        self,
        request: ResolvedFirstLastFrameRequest,
        **_kwargs: object,
    ) -> ProviderRunOutput:
        self.calls += 1
        with tempfile.TemporaryDirectory(prefix="flipthis-fixture-motion-") as directory:
            frame_directory = Path(directory)
            with Image.open(request.start_frame_path) as source:
                start = source.convert("RGB")
            with Image.open(request.end_frame_path) as source:
                end = source.convert("RGB").resize(start.size)
            for index in range(241):
                progress = index / 240
                if index == 0:
                    frame = start.copy()
                elif index == 240:
                    frame = end.copy()
                else:
                    frame = Image.blend(start, end, progress)
                    draw = ImageDraw.Draw(frame)
                    x = round(8 + progress * (start.width - 16))
                    y = round(start.height / 2 + 12 * math.sin(progress * 6.28))
                    radius = max(1, round(5 * min(progress * 12, (1 - progress) * 12, 1)))
                    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="white")
                frame.save(frame_directory / f"{index:04d}.png")
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-framerate",
                    "24",
                    "-i",
                    str(frame_directory / "%04d.png"),
                    "-frames:v",
                    "241",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(request.output_path),
                ]
            )
        now = datetime.now(UTC)
        return ProviderRunOutput(
            output_path=request.output_path,
            provider_job_id=f"fixture-{request.snapshot.retry_continuation.attempt}",
            actual_model="fixture-motion-v1",
            api_version="fixture-1",
            submitted_at=now,
            completed_at=now,
            provider_seconds=0,
            download_seconds=0,
            actual_settings={"native_frames": 241},
        )


class FixtureInterpolationProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def process(self, video: Path, output: Path, *, target_fps: int) -> Path:
        self.calls += 1
        return mock_motion_interpolate(
            video,
            output,
            duration_seconds=10,
            delivery_fps=target_fps,
        )


class FixtureLipSyncProvider:
    async def process(
        self,
        video: Path,
        audio: Path,
        output: Path,
        *,
        seed: int | None,
    ) -> LipSyncRunOutput:
        run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(video),
                "-i",
                str(audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-t",
                "10",
                str(output),
            ]
        )
        return LipSyncRunOutput(
            provider_id="latentsync-local",
            actual_model="LatentSync-1.5-fixture",
            output_path=output,
            sync_confidence=4.5,
            av_offset_frames=0,
            sync_qa_passed=True,
            actual_settings={"seed": seed if seed is not None else -1},
        )


def _endpoint(path: Path, *, color: tuple[int, int, int], x: int) -> None:
    image = Image.new("RGB", (128, 72), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((x, 26, x + 18, 46), fill="white")
    image.save(path)


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
        prompt="A camera follows a moving subject through a continuously changing scene.",
        duration_seconds=10,
        native_requested_fps=24,
        delivery_fps=60,
        width=128,
        height=72,
        aspect_ratio="16:9",
        interpolation_mode=InterpolationMode.MOCK_FFMPEG_MINTERPOLATE,
        interpolation_provider_id=None,
        retry_continuation=RetryContinuation(predecessor_clip_id=predecessor_id),
    )


@pytest.mark.asyncio
async def test_two_clip_pipeline_reuses_actual_last_frame_and_trims_shared_boundary(
    db: Session,
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    source = root / "source"
    source.mkdir(parents=True)
    project = Project(name="Continuous", root_asset_directory=str(root))
    db.add(project)
    db.commit()
    paths = [source / name for name in ("a.png", "b.png", "c.png")]
    _endpoint(paths[0], color=(30, 20, 80), x=8)
    _endpoint(paths[1], color=(20, 100, 60), x=54)
    _endpoint(paths[2], color=(100, 30, 20), x=100)
    assets = []
    for path in paths:
        assets.append(
            register_asset(
                db,
                project_id=project.id,
                shot_id=None,
                kind="upload",
                path=path,
                provider="upload",
            )
        )
    db.commit()
    chain = create_video_chain(db, project, name="A to B to C")
    first, _ = enqueue_chain_clip(
        db,
        project,
        chain,
        _request(assets[0].id, assets[1].id),
    )
    await VideoChainPipeline(db, provider=FixtureMotionProvider()).run(project, first)

    assert first.state == ChainClipState.AWAITING_REVIEW.value
    assert first.native_video_asset_id is not None
    assert first.delivery_video_asset_id is not None
    assert first.actual_last_frame_asset_id is not None
    first_delivery = db.get(Asset, first.delivery_video_asset_id)
    assert first_delivery is not None
    first_facts = inspect_frame_timing(Path(first_delivery.file_path))
    assert first_facts["duration_seconds"] == pytest.approx(10, abs=0.002)
    assert first_facts["average_frame_rate"] == 60
    assert first_facts["decoded_frame_count"] == 600
    accept_chain_clip(db, first)

    second_request = _request(
        first.actual_last_frame_asset_id,
        assets[2].id,
        predecessor_id=first.id,
    )
    second, _ = enqueue_chain_clip(
        db,
        project,
        chain,
        second_request,
        predecessor=first,
    )
    assert second.planned_start_frame_asset_id == first.actual_last_frame_asset_id
    await VideoChainPipeline(db, provider=FixtureMotionProvider()).run(project, second)
    assert second.state == ChainClipState.AWAITING_REVIEW.value
    accept_chain_clip(db, second)

    assembled = assemble_video_chain(db, project, chain)
    assembled_facts = inspect_frame_timing(Path(assembled.file_path))
    assert assembled_facts["decoded_frame_count"] == 1199
    assert assembled_facts["average_frame_rate"] == 60
    assert assembled.generation_parameters["shared_boundary_frames_removed"] == 1
    before = tmp_path / "assembled-599.png"
    after = tmp_path / "assembled-600.png"
    extract_frame_at_index(Path(assembled.file_path), before, 599)
    extract_frame_at_index(Path(assembled.file_path), after, 600)
    boundary_step = image_similarity(before, after)
    assert boundary_step["normalized_mae"] > 0
    assert boundary_step["ssim"] < 1

    playlist_asset = publish_hls_buffer(db, project, chain)
    playlist = Path(playlist_asset.file_path).read_text(encoding="utf-8")
    assert "#EXT-X-PLAYLIST-TYPE:EVENT" in playlist
    assert "#EXT-X-ENDLIST" not in playlist
    assert "segments/segment-v2-00001-" in playlist
    assert "segments/segment-v2-00002-" in playlist
    segment_root = Path(playlist_asset.file_path).parent / "segments"
    segment_paths = sorted(segment_root.glob("segment-v2-*.ts"))
    assert inspect_frame_timing(segment_paths[0])["decoded_frame_count"] == 600
    assert inspect_frame_timing(segment_paths[1])["decoded_frame_count"] == 599
    assert chain.stream_state["published_segments"] == 2
    assert chain.stream_state["exhaustion_policy"] == "pause_playback_and_rebuffer"


@pytest.mark.asyncio
async def test_pipeline_restart_resumes_from_immutable_native_asset(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "resume-project"
    source = root / "source"
    source.mkdir(parents=True)
    project = Project(name="Resume", root_asset_directory=str(root))
    db.add(project)
    db.commit()
    start_path = source / "start.png"
    end_path = source / "end.png"
    _endpoint(start_path, color=(20, 30, 60), x=8)
    _endpoint(end_path, color=(60, 90, 20), x=92)
    start = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind="upload",
        path=start_path,
        provider="upload",
    )
    end = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind="upload",
        path=end_path,
        provider="upload",
    )
    db.commit()
    chain = create_video_chain(db, project, name="Restart")
    clip, _ = enqueue_chain_clip(db, project, chain, _request(start.id, end.id))
    provider = FixtureMotionProvider()
    original_interpolation = video_chain_module.mock_motion_interpolate

    def fail_after_native(*_args: object, **_kwargs: object) -> Path:
        raise RuntimeError("simulated worker restart after native completion")

    monkeypatch.setattr(video_chain_module, "mock_motion_interpolate", fail_after_native)
    with pytest.raises(RuntimeError, match="simulated worker restart"):
        await VideoChainPipeline(db, provider=provider).run(project, clip)

    assert provider.calls == 1
    assert clip.native_video_asset_id is not None
    native = db.get(Asset, clip.native_video_asset_id)
    assert native is not None
    native_path = native.file_path
    native_checksum = native.checksum
    monkeypatch.setattr(
        video_chain_module,
        "mock_motion_interpolate",
        original_interpolation,
    )

    await VideoChainPipeline(db, provider=provider).run(project, clip)

    assert provider.calls == 1
    assert clip.state == ChainClipState.AWAITING_REVIEW.value
    persisted_native = db.get(Asset, clip.native_video_asset_id)
    assert persisted_native is not None
    assert persisted_native.file_path == native_path
    assert persisted_native.checksum == native_checksum


@pytest.mark.asyncio
async def test_pipeline_restart_resumes_from_immutable_interpolated_asset(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "interpolation-resume-project"
    source = root / "source"
    source.mkdir(parents=True)
    project = Project(name="Interpolation resume", root_asset_directory=str(root))
    db.add(project)
    db.commit()
    start_path = source / "start.png"
    end_path = source / "end.png"
    _endpoint(start_path, color=(20, 30, 60), x=8)
    _endpoint(end_path, color=(60, 90, 20), x=92)
    start = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind="upload",
        path=start_path,
        provider="upload",
    )
    end = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind="upload",
        path=end_path,
        provider="upload",
    )
    db.commit()
    values = _request(start.id, end.id).model_dump()
    values.update(
        interpolation_mode=InterpolationMode.RIFE,
        interpolation_provider_id="fixture-rife",
    )
    request = FirstLastFrameGenerationRequest.model_validate(values)
    chain = create_video_chain(db, project, name="Resume interpolation")
    clip, _ = enqueue_chain_clip(db, project, chain, request)
    provider = FixtureMotionProvider()
    interpolator = FixtureInterpolationProvider()
    original_normalize = video_chain_module.normalize_interpolated_delivery

    def fail_after_interpolation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated worker restart after interpolation")

    monkeypatch.setattr(
        video_chain_module,
        "normalize_interpolated_delivery",
        fail_after_interpolation,
    )
    with pytest.raises(RuntimeError, match="simulated worker restart"):
        await VideoChainPipeline(
            db,
            provider=provider,
            interpolation_provider=interpolator,
        ).run(project, clip)

    interpolated_asset_id = clip.result_snapshot.get("interpolated_asset_id")
    assert isinstance(interpolated_asset_id, str)
    interpolated = db.get(Asset, interpolated_asset_id)
    assert interpolated is not None
    interpolated_path = interpolated.file_path
    interpolated_checksum = interpolated.checksum
    monkeypatch.setattr(
        video_chain_module,
        "normalize_interpolated_delivery",
        original_normalize,
    )

    await VideoChainPipeline(
        db,
        provider=provider,
        interpolation_provider=interpolator,
    ).run(project, clip)

    assert provider.calls == 1
    assert interpolator.calls == 1
    assert clip.state == ChainClipState.AWAITING_REVIEW.value
    persisted = db.get(Asset, interpolated_asset_id)
    assert persisted is not None
    assert persisted.file_path == interpolated_path
    assert persisted.checksum == interpolated_checksum


@pytest.mark.asyncio
async def test_optional_lip_sync_preserves_asset_lineage_audio_and_boundary_qa(
    db: Session,
    tmp_path: Path,
) -> None:
    root = tmp_path / "lip-project"
    source = root / "source"
    source.mkdir(parents=True)
    project = Project(name="Lip sync", root_asset_directory=str(root))
    db.add(project)
    db.commit()
    start_path = source / "start.png"
    end_path = source / "end.png"
    audio_path = source / "dialogue.wav"
    _endpoint(start_path, color=(20, 30, 60), x=8)
    _endpoint(end_path, color=(60, 90, 20), x=92)
    run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=10",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]
    )
    start = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind="upload",
        path=start_path,
        provider="upload",
    )
    end = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind="upload",
        path=end_path,
        provider="upload",
    )
    audio = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind="dialogue_audio",
        path=audio_path,
        provider="upload",
    )
    db.commit()
    values = _request(start.id, end.id).model_dump()
    values.update(
        interpolation_mode=InterpolationMode.RIFE,
        interpolation_provider_id="fixture-rife",
        audio_reference_asset_id=audio.id,
        lip_sync_mode=LipSyncMode.LATENTSYNC,
        lip_sync_provider_id="latentsync-local",
        lip_sync_settings=LipSyncSettings(
            eligibility=LipSyncEligibility.SPEAKING_FACE_VISIBLE,
            speaker_label="speaker-1",
        ).model_dump(),
    )
    request = FirstLastFrameGenerationRequest.model_validate(values)
    chain = create_video_chain(db, project, name="Speaking clip")
    clip, job = enqueue_chain_clip(db, project, chain, request)

    assert job.input_asset_ids == [start.id, end.id, audio.id]
    output = await VideoChainPipeline(
        db,
        provider=FixtureMotionProvider(),
        interpolation_provider=FixtureInterpolationProvider(),
        lip_sync_provider=FixtureLipSyncProvider(),
    ).run(project, clip)

    facts = inspect_frame_timing(Path(output.file_path))
    assert facts["decoded_frame_count"] == 600
    assert facts["average_frame_rate"] == 60
    streams = probe(Path(output.file_path))["streams"]
    assert any(stream["codec_type"] == "audio" for stream in streams)
    assert clip.state == ChainClipState.AWAITING_REVIEW.value
    assert clip.qa_report_asset_id is not None
    qa_asset = db.get(Asset, clip.qa_report_asset_id)
    assert qa_asset is not None
    report = json.loads(Path(qa_asset.file_path).read_text(encoding="utf-8"))
    assert report["checks"]["lip_sync_qa_passed"] is True
    assert report["checks"]["audio_stream_present"] is True
    assert report["lip_sync"]["sync_confidence"] == 4.5
    assert report["asset_lineage"]["performance_conditioned_video_asset_id"]
    accept_chain_clip(db, clip)
    playlist = publish_hls_buffer(db, project, chain)
    segment = next((Path(playlist.file_path).parent / "segments").glob("segment-v2-*.ts"))
    assert any(stream["codec_type"] == "audio" for stream in probe(segment)["streams"])
    assembled = assemble_video_chain(db, project, chain)
    assert any(
        stream["codec_type"] == "audio" for stream in probe(Path(assembled.file_path))["streams"]
    )
