from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

from flipthis_video_maker.media.ffmpeg import probe


def analyze_video(
    path: Path,
    expected_duration: float,
    width: int,
    height: int,
    expected_fps: int,
    audio_expected: bool,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"exists": path.is_file(), "checks": {}}
    if not path.is_file():
        result["passed"] = False
        return result
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
    }
    result.update(
        {
            "passed": all(result["checks"].values()),
            "duration": actual_duration,
            "frame_rate": actual_fps,
        }
    )
    return result
