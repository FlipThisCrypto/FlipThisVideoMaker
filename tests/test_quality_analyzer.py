from pathlib import Path

import pytest

from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.media.ffmpeg import MediaCancelled, checksum, run
from flipthis_video_maker.quality import analyzer as analyzer_module
from flipthis_video_maker.quality.analyzer import MediaQAThresholds, analyze_video


def _make_clip(path: Path, *, video_source: str, audio_source: str, duration: float = 3) -> None:
    run(
        [
            get_settings().ffmpeg_path,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            video_source,
            "-f",
            "lavfi",
            "-i",
            audio_source,
            "-t",
            str(duration),
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
            str(path),
        ]
    )


def test_ffmpeg_qa_accepts_moving_video_with_audible_audio(tmp_path: Path) -> None:
    clip = tmp_path / "healthy.mp4"
    _make_clip(
        clip,
        video_source="testsrc2=s=160x90:r=12:d=3",
        audio_source="sine=frequency=440:sample_rate=48000:duration=3",
    )

    result = analyze_video(
        clip,
        expected_duration=3,
        width=160,
        height=90,
        expected_fps=12,
        audio_expected=True,
        audible_audio_expected=True,
        thresholds=MediaQAThresholds(
            black_min_duration_seconds=0.5,
            freeze_min_duration_seconds=0.5,
            silence_min_duration_seconds=0.5,
        ),
    )

    assert result["passed"]
    assert all(result["checks"].values())
    assert result["measurements"]["black"]["segment_count"] == 0
    assert result["measurements"]["freeze"]["segment_count"] == 0
    assert result["measurements"]["silence"]["segment_count"] == 0


def test_ffmpeg_qa_reports_black_frozen_and_silent_segments_without_mutating_media(
    tmp_path: Path,
) -> None:
    clip = tmp_path / "invalid.mp4"
    _make_clip(
        clip,
        video_source="color=c=black:s=160x90:r=12:d=3",
        audio_source="anullsrc=r=48000:cl=stereo:d=3",
    )
    original_checksum = checksum(clip)

    result = analyze_video(
        clip,
        expected_duration=3,
        width=160,
        height=90,
        expected_fps=12,
        audio_expected=True,
        audible_audio_expected=True,
        thresholds=MediaQAThresholds(
            black_min_duration_seconds=0.5,
            freeze_min_duration_seconds=0.5,
            silence_min_duration_seconds=0.5,
        ),
    )

    assert not result["passed"]
    assert not result["checks"]["no_long_black_segment"]
    assert not result["checks"]["no_long_frozen_segment"]
    assert not result["checks"]["audible_audio_when_expected"]
    for detector in ("black", "freeze", "silence"):
        measurement = result["measurements"][detector]
        assert measurement["segment_count"] >= 1
        assert measurement["longest_seconds"] >= 2.9
        assert measurement["segments"]
    assert clip.is_file()
    assert checksum(clip) == original_checksum


def test_silence_is_measured_but_does_not_fail_when_audible_audio_is_not_expected(
    tmp_path: Path,
) -> None:
    clip = tmp_path / "moving-silent.mp4"
    _make_clip(
        clip,
        video_source="testsrc2=s=160x90:r=12:d=3",
        audio_source="anullsrc=r=48000:cl=stereo:d=3",
    )

    result = analyze_video(
        clip,
        expected_duration=3,
        width=160,
        height=90,
        expected_fps=12,
        audio_expected=True,
        thresholds=MediaQAThresholds(silence_min_duration_seconds=0.5),
    )

    assert result["passed"]
    assert result["checks"]["audio_present_when_expected"]
    assert result["checks"]["audible_audio_when_expected"]
    assert result["measurements"]["silence"]["segment_count"] == 1
    assert not result["measurements"]["silence"]["audible_audio_expected"]


def test_detector_reuses_media_cancellation_before_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = tmp_path / "clip.mp4"
    _make_clip(
        clip,
        video_source="testsrc2=s=160x90:r=12:d=1",
        audio_source="sine=frequency=440:sample_rate=48000:duration=1",
        duration=1,
    )
    real_probe = analyzer_module.probe

    def probe_without_cancellation(path: Path, **_kwargs: object) -> dict[str, object]:
        return real_probe(path)

    monkeypatch.setattr(analyzer_module, "probe", probe_without_cancellation)

    with pytest.raises(MediaCancelled):
        analyze_video(
            clip,
            expected_duration=1,
            width=160,
            height=90,
            expected_fps=12,
            audio_expected=True,
            cancel_requested=lambda: True,
        )

    assert clip.is_file()
