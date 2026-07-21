import math
import uuid
from collections.abc import Callable
from pathlib import Path

from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.media.ffmpeg import probe, run
from flipthis_video_maker.media.video_delivery import FrameTiming, inspect_frame_timing


class HlsSegmentFacts(FrameTiming):
    source_frames: int
    shared_boundary_frame_trimmed: bool


def create_validated_hls_segment(
    source: Path,
    output: Path,
    *,
    trim_shared_first_frame: bool,
    fps: int = 60,
    source_frames: int = 600,
    cancel_requested: Callable[[], bool] | None = None,
) -> HlsSegmentFacts:
    source_facts = inspect_frame_timing(source, cancel_requested=cancel_requested)
    if source_facts["decoded_frame_count"] != source_frames:
        raise ValueError("HLS source does not satisfy the validated clip frame contract")
    start_frame = 1 if trim_shared_first_frame else 0
    expected_frames = source_frames - start_frame
    source_media = probe(source, cancel_requested=cancel_requested)
    streams = source_media.get("streams", [])
    source_has_audio = isinstance(streams, list) and any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.is_file():
        partial = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}")
        try:
            args = [
                get_settings().ffmpeg_path,
                "-y",
                "-v",
                "error",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-vf",
                (f"trim=start_frame={start_frame}:end_frame={source_frames},setpts=N/({fps}*TB)"),
                "-frames:v",
                str(expected_frames),
                "-fps_mode",
                "cfr",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-g",
                str(fps),
                "-keyint_min",
                str(fps),
                "-sc_threshold",
                "0",
            ]
            if source_has_audio:
                args.extend(
                    [
                        "-map",
                        "0:a:0",
                        "-af",
                        (
                            f"atrim=start={start_frame / fps:g}:"
                            f"duration={expected_frames / fps:g},asetpts=PTS-STARTPTS"
                        ),
                        "-c:a",
                        "aac",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                    ]
                )
            else:
                args.append("-an")
            args.extend(["-f", "mpegts", str(partial)])
            run(
                args,
                timeout=600,
                cancel_requested=cancel_requested,
            )
            partial.replace(output)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    facts = inspect_frame_timing(output, cancel_requested=cancel_requested)
    if facts["decoded_frame_count"] != expected_frames:
        raise ValueError("HLS segment has an invalid decoded frame count")
    if abs(facts["average_frame_rate"] - fps) > 0.001:
        raise ValueError("HLS segment is not constant at the delivery FPS")
    output_media = probe(output, cancel_requested=cancel_requested)
    output_streams = output_media.get("streams", [])
    output_has_audio = isinstance(output_streams, list) and any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio"
        for stream in output_streams
    )
    if output_has_audio is not source_has_audio:
        raise ValueError("HLS segment audio presence differs from its validated source")
    return {
        **facts,
        "source_frames": source_frames,
        "shared_boundary_frame_trimmed": trim_shared_first_frame,
    }


def write_atomic_event_playlist(
    segments: list[tuple[Path, float]],
    playlist: Path,
    *,
    closed: bool = False,
) -> Path:
    if not segments:
        raise ValueError("An HLS playlist requires at least one validated segment")
    playlist.parent.mkdir(parents=True, exist_ok=True)
    target_duration = math.ceil(max(duration for _path, duration in segments))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    for segment, duration in segments:
        relative = segment.relative_to(playlist.parent)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("HLS segment must be beneath its playlist directory")
        lines.extend([f"#EXTINF:{duration:.6f},", relative.as_posix()])
    if closed:
        lines.append("#EXT-X-ENDLIST")
    partial = playlist.with_name(f".{playlist.name}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_text("\n".join(lines) + "\n", encoding="utf-8")
        partial.replace(playlist)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return playlist


__all__ = ["create_validated_hls_segment", "write_atomic_event_playlist"]
