import uuid
from collections.abc import Callable
from pathlib import Path

from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.media.ffmpeg import probe, run
from flipthis_video_maker.media.video_delivery import inspect_frame_timing


def assemble_shared_boundary_clips(
    clips: list[Path],
    output: Path,
    *,
    fps: int = 60,
    frames_per_clip: int = 600,
    cancel_requested: Callable[[], bool] | None = None,
) -> tuple[Path, dict[str, object]]:
    if not clips:
        raise ValueError("At least one clip is required")
    if fps <= 0 or frames_per_clip <= 1:
        raise ValueError("Invalid assembly frame contract")
    clip_has_audio: list[bool] = []
    for clip in clips:
        facts = inspect_frame_timing(clip, cancel_requested=cancel_requested)
        if facts["decoded_frame_count"] != frames_per_clip:
            raise ValueError("Every chain clip must satisfy its exact frame-count contract")
        if abs(facts["average_frame_rate"] - fps) > 0.001:
            raise ValueError("Every chain clip must have the same exact delivery FPS")
        media = probe(clip, cancel_requested=cancel_requested)
        streams = media.get("streams", [])
        clip_has_audio.append(
            isinstance(streams, list)
            and any(
                isinstance(stream, dict) and stream.get("codec_type") == "audio"
                for stream in streams
            )
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}")
    args = [get_settings().ffmpeg_path, "-y", "-v", "error"]
    for clip in clips:
        args.extend(["-i", str(clip)])
    filters: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []
    include_audio = any(clip_has_audio)
    for index in range(len(clips)):
        start = 0 if index == 0 else 1
        video_label = f"v{index}"
        filters.append(
            f"[{index}:v]trim=start_frame={start}:end_frame={frames_per_clip},"
            f"setpts=N/({fps}*TB)[{video_label}]"
        )
        video_labels.append(f"[{video_label}]")
        if include_audio:
            audio_label = f"a{index}"
            duration = (frames_per_clip - start) / fps
            if clip_has_audio[index]:
                filters.append(
                    f"[{index}:a]atrim=start={start / fps:g}:duration={duration:g},"
                    "asetpts=PTS-STARTPTS,aresample=48000,"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo[{audio_label}]"
                )
            else:
                filters.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:g},"
                    f"asetpts=PTS-STARTPTS[{audio_label}]"
                )
            audio_labels.append(f"[{audio_label}]")
    if include_audio:
        concat_inputs = "".join(
            value for pair in zip(video_labels, audio_labels, strict=True) for value in pair
        )
        filters.append(f"{concat_inputs}concat=n={len(clips)}:v=1:a=1[outv][outa]")
    else:
        filters.append(f"{''.join(video_labels)}concat=n={len(clips)}:v=1:a=0[outv]")
    expected_frames = frames_per_clip + (len(clips) - 1) * (frames_per_clip - 1)
    args.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
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
        ]
    )
    if include_audio:
        args.extend(["-map", "[outa]", "-c:a", "aac", "-ar", "48000", "-ac", "2"])
    else:
        args.append("-an")
    args.append(str(partial))
    try:
        run(args, timeout=1200, cancel_requested=cancel_requested)
        facts = inspect_frame_timing(partial, cancel_requested=cancel_requested)
        if facts["decoded_frame_count"] != expected_frames:
            raise ValueError("Assembled chain has an incorrect decoded frame count")
        if abs(facts["average_frame_rate"] - fps) > 0.001:
            raise ValueError("Assembled chain is not constant at the delivery FPS")
        output_media = probe(partial, cancel_requested=cancel_requested)
        output_streams = output_media.get("streams", [])
        output_has_audio = isinstance(output_streams, list) and any(
            isinstance(stream, dict) and stream.get("codec_type") == "audio"
            for stream in output_streams
        )
        if output_has_audio is not include_audio:
            raise ValueError("Assembled chain did not preserve its audio contract")
        partial.replace(output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return output, {
        **facts,
        "source_clip_count": len(clips),
        "shared_boundary_frames_removed": len(clips) - 1,
        "expected_frame_count": expected_frames,
        "assembly_transition": "shared_frame_trim_without_crossfade",
        "encoding_preset": "medium",
        "encoding_crf": 12,
        "audio_preserved": include_audio,
    }


__all__ = ["assemble_shared_boundary_clips"]
