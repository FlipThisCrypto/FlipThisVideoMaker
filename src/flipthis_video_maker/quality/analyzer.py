import re
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.media.ffmpeg import probe, run


class MediaQAThresholds(BaseModel):
    """Practical, bounded defaults for deterministic FFmpeg media analysis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    black_min_duration_seconds: float = Field(default=1.0, gt=0, le=60)
    black_pixel_threshold: float = Field(default=0.10, ge=0, le=1)
    freeze_min_duration_seconds: float = Field(default=2.0, gt=0, le=60)
    freeze_noise_db: float = Field(default=-50.0, ge=-100, le=0)
    silence_min_duration_seconds: float = Field(default=2.0, gt=0, le=60)
    silence_noise_db: float = Field(default=-50.0, ge=-100, le=0)
    command_timeout_seconds: float = Field(default=120.0, gt=0, le=600)


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_BLACK_SEGMENT = re.compile(
    rf"black_start:(?P<start>{_NUMBER})\s+"
    rf"black_end:(?P<end>{_NUMBER})\s+"
    rf"black_duration:(?P<duration>{_NUMBER})"
)
_FREEZE_EVENT = re.compile(
    rf"lavfi\.freezedetect\.(?P<kind>freeze_start|freeze_end|freeze_duration):\s*"
    rf"(?P<value>{_NUMBER})"
)
_SILENCE_EVENT = re.compile(
    rf"(?P<kind>silence_start|silence_end|silence_duration):\s*(?P<value>{_NUMBER})"
)


def analyze_video(
    path: Path,
    expected_duration: float,
    width: int,
    height: int,
    expected_fps: int,
    audio_expected: bool,
    *,
    cancel_requested: Callable[[], bool] | None = None,
    audible_audio_expected: bool = False,
    thresholds: MediaQAThresholds | None = None,
) -> dict[str, Any]:
    """Inspect one candidate without mutating it.

    ``audio_expected`` preserves the existing stream-presence contract. Callers that expect
    dialogue or other audible content opt into silence failure with ``audible_audio_expected``.
    """

    result: dict[str, Any] = {"exists": path.is_file(), "checks": {}}
    if not path.is_file():
        result["passed"] = False
        return result
    configured = thresholds or MediaQAThresholds()
    data = probe(path, cancel_requested=cancel_requested)
    streams = data.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    actual_duration = float(data["format"]["duration"])
    frame_rate = str(video.get("r_frame_rate", "0/0")) if video else "0/0"
    try:
        actual_fps = float(Fraction(frame_rate))
    except (ValueError, ZeroDivisionError):
        actual_fps = 0.0
    black_segments: list[dict[str, float]] = []
    frozen_segments: list[dict[str, float]] = []
    silent_segments: list[dict[str, float]] = []
    if video is not None or audio is not None:
        detector_output = _run_detectors(
            path,
            has_video=video is not None,
            has_audio=audio is not None,
            thresholds=configured,
            cancel_requested=cancel_requested,
        )
        black_segments = _parse_black_segments(detector_output)
        frozen_segments = _parse_boundary_events(
            detector_output,
            _FREEZE_EVENT,
            prefix="freeze",
            media_duration=actual_duration,
        )
        silent_segments = _parse_boundary_events(
            detector_output,
            _SILENCE_EVENT,
            prefix="silence",
            media_duration=actual_duration,
        )
    result["checks"] = {
        "decodable_video": video is not None,
        "duration_in_tolerance": abs(actual_duration - expected_duration)
        <= max(0.25, expected_duration * 0.1),
        "dimensions_correct": bool(
            video and video.get("width") == width and video.get("height") == height
        ),
        "frame_rate_valid": actual_fps > 0,
        "frame_rate_correct": abs(actual_fps - expected_fps) <= 0.01,
        "audio_present_when_expected": not audio_expected or audio is not None,
        "no_long_black_segment": video is not None and not black_segments,
        "no_long_frozen_segment": video is not None and not frozen_segments,
        "audible_audio_when_expected": not audible_audio_expected
        or (audio is not None and not silent_segments),
    }
    result.update(
        {
            "passed": all(result["checks"].values()),
            "duration": actual_duration,
            "frame_rate": actual_fps,
            "measurements": {
                "black": _measurement(
                    black_segments,
                    analyzed=video is not None,
                    threshold_seconds=configured.black_min_duration_seconds,
                    detector_threshold=configured.black_pixel_threshold,
                ),
                "freeze": _measurement(
                    frozen_segments,
                    analyzed=video is not None,
                    threshold_seconds=configured.freeze_min_duration_seconds,
                    detector_threshold=configured.freeze_noise_db,
                ),
                "silence": {
                    **_measurement(
                        silent_segments,
                        analyzed=audio is not None,
                        threshold_seconds=configured.silence_min_duration_seconds,
                        detector_threshold=configured.silence_noise_db,
                    ),
                    "audible_audio_expected": audible_audio_expected,
                },
            },
        }
    )
    return result


def _run_detectors(
    path: Path,
    *,
    has_video: bool,
    has_audio: bool,
    thresholds: MediaQAThresholds,
    cancel_requested: Callable[[], bool] | None,
) -> str:
    args = [
        get_settings().ffmpeg_path,
        "-hide_banner",
        "-nostats",
        "-v",
        "info",
        "-i",
        str(path),
    ]
    if has_video:
        args.extend(
            [
                "-vf",
                (
                    "blackdetect="
                    f"d={thresholds.black_min_duration_seconds:g}:"
                    f"pix_th={thresholds.black_pixel_threshold:g},"
                    "freezedetect="
                    f"n={thresholds.freeze_noise_db:g}dB:"
                    f"d={thresholds.freeze_min_duration_seconds:g}"
                ),
            ]
        )
    if has_audio:
        args.extend(
            [
                "-af",
                (
                    "silencedetect="
                    f"n={thresholds.silence_noise_db:g}dB:"
                    f"d={thresholds.silence_min_duration_seconds:g}"
                ),
            ]
        )
    args.extend(["-f", "null", "-"])
    completed = run(
        args,
        timeout=thresholds.command_timeout_seconds,
        cancel_requested=cancel_requested,
    )
    return completed.stderr


def _parse_black_segments(output: str) -> list[dict[str, float]]:
    return [
        {
            "start": float(match.group("start")),
            "end": float(match.group("end")),
            "duration": float(match.group("duration")),
        }
        for match in _BLACK_SEGMENT.finditer(output)
    ]


def _parse_boundary_events(
    output: str,
    pattern: re.Pattern[str],
    *,
    prefix: str,
    media_duration: float,
) -> list[dict[str, float]]:
    segments: list[dict[str, float]] = []
    active_start: float | None = None
    active_duration: float | None = None
    for match in pattern.finditer(output):
        kind = match.group("kind")
        value = float(match.group("value"))
        if kind == f"{prefix}_start":
            active_start = value
            active_duration = None
        elif kind == f"{prefix}_duration":
            active_duration = value
        elif kind == f"{prefix}_end" and active_start is not None:
            duration = active_duration if active_duration is not None else value - active_start
            segments.append(_segment(active_start, value, duration))
            active_start = None
            active_duration = None
    if active_start is not None:
        end = max(active_start, media_duration)
        duration = active_duration if active_duration is not None else end - active_start
        segments.append(_segment(active_start, end, duration))
    return segments


def _segment(start: float, end: float, duration: float) -> dict[str, float]:
    return {
        "start": max(0.0, start),
        "end": max(0.0, end),
        "duration": max(0.0, duration),
    }


def _measurement(
    segments: list[dict[str, float]],
    *,
    analyzed: bool,
    threshold_seconds: float,
    detector_threshold: float,
) -> dict[str, object]:
    durations = [segment["duration"] for segment in segments]
    return {
        "analyzed": analyzed,
        "threshold_seconds": threshold_seconds,
        "detector_threshold": detector_threshold,
        "segment_count": len(segments),
        "longest_seconds": max(durations, default=0.0),
        "total_seconds": sum(durations),
        "segments": segments,
    }


__all__ = ["MediaQAThresholds", "analyze_video"]
