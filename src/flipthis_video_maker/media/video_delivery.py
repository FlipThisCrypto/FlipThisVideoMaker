import json
import math
import statistics
import uuid
from collections.abc import Callable
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import Any, TypedDict

from PIL import Image, ImageChops, ImageDraw, ImageStat

from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.contracts.video_generation import FirstLastFrameGenerationRequest
from flipthis_video_maker.media.ffmpeg import run

CancelCheck = Callable[[], bool]
INSPECTION_FRAME_INDEXES = (0, 1, 60, 150, 300, 450, 598, 599)


class FrameTiming(TypedDict):
    duration_seconds: float
    average_frame_rate: float
    nominal_frame_rate: float
    decoded_frame_count: int
    declared_frame_count: int
    constant_frame_rate: bool
    width: int
    height: int


class DuplicateEvidence(TypedDict):
    decoded_hash_count: int
    unique_hash_count: int
    exact_adjacent_duplicate_count: int
    longest_consecutive_run: int


def mock_motion_interpolate(
    native_video: Path,
    output: Path,
    *,
    duration_seconds: float,
    delivery_fps: int,
    cancel_requested: CancelCheck | None = None,
) -> Path:
    """CPU test conversion; never described as the production RIFE path."""
    _require_new_output(output)
    expected_frames = _integral_frame_count(duration_seconds, delivery_fps)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}")
    try:
        run(
            [
                get_settings().ffmpeg_path,
                "-y",
                "-v",
                "error",
                "-i",
                str(native_video),
                "-vf",
                (
                    f"trim=duration={duration_seconds + 1 / delivery_fps:g},"
                    "tpad=stop_mode=clone:stop_duration=0.1,"
                    f"minterpolate=fps={delivery_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir,"
                    f"setpts=N/({delivery_fps}*TB)"
                ),
                "-frames:v",
                str(expected_frames),
                "-fps_mode",
                "cfr",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-an",
                str(partial),
            ],
            timeout=600,
            cancel_requested=cancel_requested,
        )
        partial.replace(output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return output


def normalize_interpolated_delivery(
    interpolated_video: Path,
    output: Path,
    *,
    duration_seconds: float,
    delivery_fps: int,
    cancel_requested: CancelCheck | None = None,
) -> Path:
    """Encode already-interpolated unique frames onto an exact CFR delivery timeline."""
    _require_new_output(output)
    expected_frames = _integral_frame_count(duration_seconds, delivery_fps)
    input_facts = inspect_frame_timing(interpolated_video)
    if abs(input_facts["average_frame_rate"] - delivery_fps) > 0.05:
        raise ValueError("Interpolator output frame rate does not match requested delivery FPS")
    if input_facts["decoded_frame_count"] < expected_frames:
        raise ValueError("Interpolator returned too few decoded frames for exact delivery")
    selection = _uniform_frame_selection(
        input_facts["decoded_frame_count"],
        expected_frames,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}")
    try:
        run(
            [
                get_settings().ffmpeg_path,
                "-y",
                "-v",
                "error",
                "-i",
                str(interpolated_video),
                "-map",
                "0:v:0",
                "-vf",
                f"{selection}setpts=N/({delivery_fps}*TB)",
                "-frames:v",
                str(expected_frames),
                "-fps_mode",
                "cfr",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "12",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-an",
                str(partial),
            ],
            timeout=600,
            cancel_requested=cancel_requested,
        )
        partial.replace(output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return output


def _uniform_frame_selection(input_frames: int, output_frames: int) -> str:
    """Drop surplus internal frames evenly while preserving both boundary frames."""
    if input_frames < output_frames or output_frames < 2:
        raise ValueError("Frame selection requires at least two output frames and enough input")
    surplus = input_frames - output_frames
    if surplus == 0:
        return ""
    dropped = {
        round(position * (input_frames - 1) / (surplus + 1)) for position in range(1, surplus + 1)
    }
    if len(dropped) != surplus or 0 in dropped or input_frames - 1 in dropped:
        raise ValueError("Could not derive an endpoint-preserving frame selection")
    predicates = "+".join(f"eq(n\\,{index})" for index in sorted(dropped))
    return f"select=not({predicates}),"


def mux_exact_delivery_audio(
    video: Path,
    audio: Path,
    output: Path,
    *,
    duration_seconds: float,
    cancel_requested: CancelCheck | None = None,
) -> Path:
    """Mux a persisted speech Asset without changing the validated video timeline."""
    _require_new_output(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}")
    try:
        run(
            [
                get_settings().ffmpeg_path,
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
                "-af",
                f"apad,atrim=duration={duration_seconds:g}",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-t",
                f"{duration_seconds:g}",
                "-movflags",
                "+faststart",
                str(partial),
            ],
            timeout=600,
            cancel_requested=cancel_requested,
        )
        partial.replace(output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return output


def inspect_delivery_contract(
    video: Path,
    *,
    request: FirstLastFrameGenerationRequest,
    required_start: Path,
    target_end: Path,
    evidence_directory: Path,
    cancel_requested: CancelCheck | None = None,
) -> dict[str, Any]:
    evidence_directory.mkdir(parents=True, exist_ok=True)
    timing = inspect_frame_timing(video, cancel_requested=cancel_requested)
    expected_frames = request.expected_delivery_frames
    frame_paths: dict[int, Path] = {}
    for frame_index in INSPECTION_FRAME_INDEXES:
        if frame_index >= expected_frames:
            continue
        path = evidence_directory / f"frame-{frame_index:04d}.png"
        extract_frame_at_index(
            video,
            path,
            frame_index,
            cancel_requested=cancel_requested,
        )
        frame_paths[frame_index] = path
    first_metrics = image_similarity(required_start, frame_paths[0])
    end_metrics = image_similarity(target_end, frame_paths[expected_frames - 1])
    penultimate_metrics = image_similarity(target_end, frame_paths[expected_frames - 2])
    hashes = decoded_frame_hashes(video, cancel_requested=cancel_requested)
    duplicate = duplicate_frame_evidence(hashes)
    last_step = image_similarity(
        frame_paths[expected_frames - 2],
        frame_paths[expected_frames - 1],
    )
    final_improvement = end_metrics["ssim"] - penultimate_metrics["ssim"]
    checks = {
        "duration_exact": abs(timing["duration_seconds"] - request.duration_seconds) <= 0.002,
        "constant_frame_rate": timing["constant_frame_rate"] is True,
        "delivery_fps_exact": abs(timing["average_frame_rate"] - request.delivery_fps) <= 0.001,
        "displayed_frame_count_exact": timing["decoded_frame_count"] == expected_frames,
        "start_boundary_passed": first_metrics["normalized_mae"] <= 0.02
        and first_metrics["ssim"] >= 0.97,
        "end_boundary_passed": end_metrics["normalized_mae"] <= 0.10
        and end_metrics["ssim"] >= 0.80
        and end_metrics["dhash_similarity"] >= 0.80,
        "no_obvious_last_frame_snap": final_improvement <= 0.15
        and last_step["normalized_mae"] <= 0.15,
        "no_frozen_duplicate_run": duplicate["longest_consecutive_run"]
        <= max(2, round(request.delivery_fps * 0.1)),
    }
    contact_sheet = evidence_directory / "contact-sheet.png"
    create_contact_sheet(frame_paths, contact_sheet)
    return {
        "version": 1,
        "passed": all(checks.values()),
        "checks": checks,
        "timing": timing,
        "expected": {
            "duration_seconds": request.duration_seconds,
            "delivery_fps": request.delivery_fps,
            "displayed_frames": expected_frames,
        },
        "boundaries": {
            "start": first_metrics,
            "end": end_metrics,
            "target_similarity_at_penultimate": penultimate_metrics,
            "final_improvement_in_ssim": final_improvement,
            "penultimate_to_final": last_step,
            "lpips": {
                "available": False,
                "reason": "LPIPS is not installed in the lightweight core environment",
            },
            "identity_similarity": {
                "available": False,
                "reason": "No privacy-reviewed identity model is installed",
            },
        },
        "duplicates": duplicate,
        "inspection_frames": {
            str(index): str(path.relative_to(evidence_directory))
            for index, path in frame_paths.items()
        },
        "contact_sheet": str(contact_sheet.relative_to(evidence_directory)),
    }


def write_qa_report(report: dict[str, Any], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.name}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        partial.replace(output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return output


def inspect_frame_timing(
    video: Path,
    *,
    cancel_requested: CancelCheck | None = None,
) -> FrameTiming:
    completed = run(
        [
            get_settings().ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_frames",
            "-show_entries",
            "stream=avg_frame_rate,r_frame_rate,nb_read_frames,nb_frames,duration,width,height",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video),
        ],
        cancel_requested=cancel_requested,
    )
    data = json.loads(completed.stdout)
    streams = data.get("streams", [])
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise ValueError("ffprobe did not return a decoded video stream")
    stream = streams[0]
    avg = _rate(str(stream.get("avg_frame_rate", "0/0")))
    nominal = _rate(str(stream.get("r_frame_rate", "0/0")))
    decoded = _integer(stream.get("nb_read_frames"))
    declared = _integer(stream.get("nb_frames"))
    format_data = data.get("format", {})
    duration = float(stream.get("duration") or format_data.get("duration") or 0)
    frames = data.get("frames", [])
    timestamps = (
        [
            float(frame["best_effort_timestamp_time"])
            for frame in frames
            if isinstance(frame, dict) and "best_effort_timestamp_time" in frame
        ]
        if isinstance(frames, list)
        else []
    )
    return {
        "duration_seconds": duration,
        "average_frame_rate": avg,
        "nominal_frame_rate": nominal,
        "decoded_frame_count": decoded,
        "declared_frame_count": declared,
        "constant_frame_rate": abs(avg - nominal) <= 0.001
        and _constant_timestamp_cadence(timestamps, decoded, avg),
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
    }


def extract_frame_at_index(
    video: Path,
    output: Path,
    frame_index: int,
    *,
    cancel_requested: CancelCheck | None = None,
) -> Path:
    if frame_index < 0:
        raise ValueError("Frame index cannot be negative")
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            get_settings().ffmpeg_path,
            "-y",
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            f"select=eq(n\\,{frame_index})",
            "-vsync",
            "0",
            "-frames:v",
            "1",
            str(output),
        ],
        cancel_requested=cancel_requested,
    )
    if not output.is_file():
        raise ValueError(f"Decoded frame {frame_index} was not produced")
    return output


def decoded_frame_hashes(
    video: Path,
    *,
    cancel_requested: CancelCheck | None = None,
) -> list[str]:
    completed = run(
        [
            get_settings().ffmpeg_path,
            "-v",
            "error",
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-f",
            "framemd5",
            "-",
        ],
        timeout=600,
        cancel_requested=cancel_requested,
    )
    hashes: list[str] = []
    for line in completed.stdout.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 6:
            hashes.append(parts[-1])
    return hashes


def duplicate_frame_evidence(hashes: list[str]) -> DuplicateEvidence:
    longest = 0
    current = 0
    previous: str | None = None
    exact_adjacent_duplicates = 0
    for digest in hashes:
        if digest == previous:
            current += 1
            exact_adjacent_duplicates += 1
        else:
            current = 1
        longest = max(longest, current)
        previous = digest
    return {
        "decoded_hash_count": len(hashes),
        "unique_hash_count": len(set(hashes)),
        "exact_adjacent_duplicate_count": exact_adjacent_duplicates,
        "longest_consecutive_run": longest,
    }


def image_similarity(reference_path: Path, actual_path: Path) -> dict[str, float]:
    with Image.open(reference_path) as reference_source, Image.open(actual_path) as actual_source:
        actual = actual_source.convert("RGB")
        reference = reference_source.convert("RGB").resize(actual.size, Image.Resampling.LANCZOS)
        difference = ImageChops.difference(reference, actual)
        normalized_mae = sum(ImageStat.Stat(difference).mean) / (3 * 255)
        normalized_rmse = (
            math.sqrt(sum(value * value for value in ImageStat.Stat(difference).rms) / 3) / 255
        )
        ssim = _global_ssim(reference, actual)
        dhash_similarity = _dhash_similarity(reference, actual)
    return {
        "normalized_mae": normalized_mae,
        "normalized_rmse": normalized_rmse,
        "ssim": ssim,
        "dhash_similarity": dhash_similarity,
    }


def create_contact_sheet(frame_paths: dict[int, Path], output: Path) -> Path:
    thumbnails: list[tuple[int, Image.Image]] = []
    for frame_index, path in sorted(frame_paths.items()):
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail((320, 180), Image.Resampling.LANCZOS)
            thumbnails.append((frame_index, image.copy()))
    if not thumbnails:
        raise ValueError("Contact sheet requires at least one frame")
    width = 640
    height = math.ceil(len(thumbnails) / 2) * 220
    sheet = Image.new("RGB", (width, height), "#111827")
    draw = ImageDraw.Draw(sheet)
    for position, (frame_index, image) in enumerate(thumbnails):
        x = (position % 2) * 320
        y = (position // 2) * 220
        sheet.paste(image, (x, y + 24))
        draw.text((x + 8, y + 4), f"Frame {frame_index}", fill="white")
    sheet.save(output, "PNG")
    return output


def _global_ssim(reference: Image.Image, actual: Image.Image) -> float:
    first = reference.convert("L").resize((256, 256), Image.Resampling.LANCZOS)
    second = actual.convert("L").resize((256, 256), Image.Resampling.LANCZOS)
    values_a = list(first.getdata())
    values_b = list(second.getdata())
    mean_a = statistics.fmean(values_a)
    mean_b = statistics.fmean(values_b)
    variance_a = statistics.fmean((value - mean_a) ** 2 for value in values_a)
    variance_b = statistics.fmean((value - mean_b) ** 2 for value in values_b)
    covariance = statistics.fmean(
        (value_a - mean_a) * (value_b - mean_b)
        for value_a, value_b in zip(values_a, values_b, strict=True)
    )
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    denominator = (mean_a**2 + mean_b**2 + c1) * (variance_a + variance_b + c2)
    if denominator == 0:
        return 1.0
    return max(
        -1.0,
        min(
            1.0,
            ((2 * mean_a * mean_b + c1) * (2 * covariance + c2)) / denominator,
        ),
    )


def _dhash_similarity(reference: Image.Image, actual: Image.Image) -> float:
    return 1 - ((_dhash(reference) ^ _dhash(actual)).bit_count() / 64)


def _dhash(image: Image.Image) -> int:
    pixels = list(image.convert("L").resize((9, 8), Image.Resampling.LANCZOS).getdata())
    value = 0
    for row in range(8):
        for column in range(8):
            left = pixels[row * 9 + column]
            right = pixels[row * 9 + column + 1]
            value = (value << 1) | int(left > right)
    return value


def _rate(value: str) -> float:
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _integer(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _integral_frame_count(duration_seconds: float, fps: int) -> int:
    frames = duration_seconds * fps
    rounded = round(frames)
    if abs(frames - rounded) > 1e-9:
        raise ValueError("Duration and FPS must produce an integral frame count")
    return rounded


def _require_new_output(output: Path) -> None:
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite completed media output: {output}")


def _constant_timestamp_cadence(
    timestamps: list[float],
    decoded_frame_count: int,
    average_frame_rate: float,
) -> bool:
    if decoded_frame_count <= 0 or len(timestamps) != decoded_frame_count:
        return False
    if decoded_frame_count == 1:
        return True
    if average_frame_rate <= 0:
        return False
    expected_step = 1 / average_frame_rate
    tolerance = max(0.00001, expected_step * 0.005)
    return all(
        abs((current - previous) - expected_step) <= tolerance
        for previous, current in pairwise(timestamps)
    )


__all__ = [
    "INSPECTION_FRAME_INDEXES",
    "create_contact_sheet",
    "decoded_frame_hashes",
    "duplicate_frame_evidence",
    "extract_frame_at_index",
    "image_similarity",
    "inspect_delivery_contract",
    "inspect_frame_timing",
    "mock_motion_interpolate",
    "normalize_interpolated_delivery",
    "write_qa_report",
]
