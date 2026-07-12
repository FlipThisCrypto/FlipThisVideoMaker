import math
from pathlib import Path

import pytest
from PIL import Image, ImageStat

from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.media import finalization
from flipthis_video_maker.media.ffmpeg import MediaCancelled, extract_frame, probe, run
from flipthis_video_maker.media.finalization import (
    AudioFinalizationOptions,
    BackgroundMusicOptions,
    BurnSubtitleOptions,
    LoudnessTarget,
    SoftSubtitleOptions,
    SubtitleMode,
    burn_subtitles,
    measure_loudness,
    mux_soft_subtitles,
    normalize_audio,
)
from flipthis_video_maker.media.render import write_subtitles


def _make_source(path: Path, *, duration: float = 2) -> Path:
    run(
        [
            get_settings().ffmpeg_path,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=320x180:r=24:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-filter:a",
            "volume=0.03",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-shortest",
            str(path),
        ]
    )
    return path


def _make_music(path: Path, *, duration: float = 0.35) -> Path:
    run(
        [
            get_settings().ffmpeg_path,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=880:sample_rate=48000:duration={duration}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )
    return path


def _stream(metadata: dict[str, object], kind: str) -> dict[str, object]:
    streams = metadata["streams"]
    assert isinstance(streams, list)
    return next(item for item in streams if item["codec_type"] == kind)


def test_soft_subtitle_mux_copies_video_and_adds_selectable_track(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source.mp4")
    subtitles = write_subtitles(
        [(0.4, 1.6, "A selectable caption")],
        tmp_path / "captions with spaces.srt",
    )
    output = tmp_path / "soft-subtitles.mp4"

    result = mux_soft_subtitles(
        source,
        subtitles,
        output,
        options=SoftSubtitleOptions(language="eng", title="English captions"),
    )

    metadata = probe(output)
    source_video = _stream(probe(source), "video")
    output_video = _stream(metadata, "video")
    subtitle = _stream(metadata, "subtitle")
    assert result.mode is SubtitleMode.SOFT
    assert result.subtitle_stream_count == 1
    assert result.subtitle_codec == "mov_text"
    assert output_video["codec_name"] == source_video["codec_name"]
    assert output_video["r_frame_rate"] == source_video["r_frame_rate"] == "24/1"
    assert subtitle["codec_name"] == "mov_text"
    assert subtitle["tags"]["language"] == "eng"
    assert subtitle["disposition"]["default"] == 1
    assert result.output_video_duration_seconds == pytest.approx(
        result.source_video_duration_seconds,
        abs=0.1,
    )


def test_burn_subtitles_rasterizes_text_without_changing_video_timing(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source.mp4")
    subtitles = write_subtitles(
        [(0, 1.8, "VISIBLE BURNED CAPTION")],
        tmp_path / "captions:chapter's.srt",
    )
    output = tmp_path / "burned-subtitles.mp4"

    result = burn_subtitles(
        source,
        subtitles,
        output,
        options=BurnSubtitleOptions(preset="ultrafast"),
    )

    metadata = probe(output)
    assert result.mode is SubtitleMode.BURNED
    assert result.subtitle_stream_count == 0
    assert {item["codec_type"] for item in metadata["streams"]} == {"video", "audio"}
    assert result.frame_rate == pytest.approx(24)
    assert result.output_video_duration_seconds == pytest.approx(
        result.source_video_duration_seconds,
        abs=0.1,
    )

    source_frame = extract_frame(source, tmp_path / "source-frame.png")
    burned_frame = extract_frame(output, tmp_path / "burned-frame.png")
    source_brightness = ImageStat.Stat(Image.open(source_frame).convert("L")).mean[0]
    burned_brightness = ImageStat.Stat(Image.open(burned_frame).convert("L")).mean[0]
    assert burned_brightness > source_brightness + 0.5


def test_two_pass_audio_normalization_hits_target_and_copies_video(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source.mp4")
    output = tmp_path / "normalized.mp4"
    target = LoudnessTarget(integrated_lufs=-18, true_peak_dbfs=-2)

    result = normalize_audio(
        source,
        output,
        options=AudioFinalizationOptions(loudness=target),
    )
    measured_output = measure_loudness(output, target=target)

    source_video = _stream(probe(source), "video")
    output_metadata = probe(output)
    output_video = _stream(output_metadata, "video")
    output_audio = _stream(output_metadata, "audio")
    assert not result.music_ducking_applied
    assert math.isfinite(result.pre_normalization.integrated_lufs)
    assert measured_output.integrated_lufs == pytest.approx(-18, abs=1)
    assert output_video["codec_name"] == source_video["codec_name"]
    assert output_video["r_frame_rate"] == source_video["r_frame_rate"]
    assert (output_audio["codec_name"], output_audio["sample_rate"], output_audio["channels"]) == (
        "aac",
        "48000",
        2,
    )
    assert result.output_video_duration_seconds == pytest.approx(
        result.source_video_duration_seconds,
        abs=0.1,
    )


def test_optional_music_ducking_loops_short_music_without_extending_video(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source.mp4")
    music = _make_music(tmp_path / "music.wav")
    output = tmp_path / "normalized-with-music.mp4"
    target = LoudnessTarget(integrated_lufs=-18, true_peak_dbfs=-2)

    result = normalize_audio(
        source,
        output,
        options=AudioFinalizationOptions(loudness=target),
        music=BackgroundMusicOptions(path=music, gain_db=-14, loop=True),
    )
    measured_output = measure_loudness(output, target=target)

    assert result.music_ducking_applied
    assert result.music_path == music
    assert measured_output.integrated_lufs == pytest.approx(-18, abs=1)
    assert result.output_video_duration_seconds == pytest.approx(
        result.source_video_duration_seconds,
        abs=0.1,
    )
    assert float(probe(output)["format"]["duration"]) == pytest.approx(2, abs=0.15)


def test_cancellation_removes_partial_and_never_publishes_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path / "source.mp4", duration=0.5)
    subtitles = write_subtitles([(0, 0.4, "Cancelled")], tmp_path / "captions.srt")
    output = tmp_path / "cancelled.mp4"

    def cancel_after_partial(args: list[str], *args_: object, **kwargs: object) -> object:
        del args_, kwargs
        partial = Path(args[-1])
        partial.write_bytes(b"incomplete")
        raise MediaCancelled("fixture cancellation")

    monkeypatch.setattr(finalization, "run", cancel_after_partial)

    with pytest.raises(MediaCancelled, match="fixture cancellation"):
        mux_soft_subtitles(source, subtitles, output, cancel_requested=lambda: False)

    assert not output.exists()
    assert not list(tmp_path.glob(".*.partial.mp4"))


def test_finalization_refuses_to_overwrite_completed_output(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source.mp4", duration=0.5)
    subtitles = write_subtitles([(0, 0.4, "Immutable")], tmp_path / "captions.srt")
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"completed output")

    with pytest.raises(FileExistsError):
        mux_soft_subtitles(source, subtitles, output)

    assert output.read_bytes() == b"completed output"
