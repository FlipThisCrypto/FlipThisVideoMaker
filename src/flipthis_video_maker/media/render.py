import json
import uuid
from collections.abc import Callable
from pathlib import Path

from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.media.ffmpeg import run


def write_subtitles(entries: list[tuple[float, float, str]], output: Path) -> Path:
    def timestamp(seconds: float) -> str:
        millis = round(seconds * 1000)
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        secs, millis = divmod(millis, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "\n".join(
            f"{index}\n{timestamp(start)} --> {timestamp(end)}\n{text}\n"
            for index, (start, end, text) in enumerate(entries, 1)
        ),
        encoding="utf-8",
    )
    return output


def concatenate(
    clips: list[Path],
    output: Path,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    listing = output.with_suffix(".concat.txt")
    listing.write_text("".join(f"file '{clip.resolve()}'\n" for clip in clips), encoding="utf-8")
    run(
        [
            get_settings().ffmpeg_path,
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ],
        cancel_requested=cancel_requested,
    )
    return output


def assemble_with_transitions(
    clips: list[Path],
    transitions: list[tuple[str, int]],
    output: Path,
    *,
    fps: int,
    cancel_requested: Callable[[], bool] | None = None,
) -> tuple[Path, list[dict[str, float | int | str]]]:
    """Assemble normalized A/V clips, applying each transition before its destination clip."""
    if not clips:
        raise ValueError("At least one clip is required")
    if len(transitions) != len(clips):
        raise ValueError("Each clip requires transition metadata")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}")
    ffmpeg = get_settings().ffmpeg_path
    args = [ffmpeg, "-y", "-v", "error"]
    for clip in clips:
        args.extend(["-i", str(clip)])

    filters: list[str] = []
    durations: list[float] = []
    from flipthis_video_maker.media.ffmpeg import duration

    for index, clip in enumerate(clips):
        durations.append(duration(clip, cancel_requested=cancel_requested))
        filters.extend(
            [
                f"[{index}:v]fps={fps},format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[v{index}]",
                (
                    f"[{index}:a]aresample=48000,"
                    "aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"asetpts=PTS-STARTPTS[a{index}]"
                ),
            ]
        )

    video_label = "v0"
    audio_label = "a0"
    assembled_duration = durations[0]
    applied: list[dict[str, float | int | str]] = []
    for index in range(1, len(clips)):
        transition_type, overlap_frames = transitions[index]
        next_video = f"vout{index}"
        next_audio = f"aout{index}"
        if transition_type == "crossfade":
            transition_seconds = max(1 / fps, overlap_frames / fps)
            transition_seconds = min(
                transition_seconds, durations[index] / 2, assembled_duration / 2
            )
            offset = assembled_duration - transition_seconds
            filters.append(
                f"[{video_label}][v{index}]xfade=transition=fade:"
                f"duration={transition_seconds:.6f}:offset={offset:.6f}[{next_video}]"
            )
            filters.append(
                f"[{audio_label}][a{index}]acrossfade=d={transition_seconds:.6f}:"
                f"c1=tri:c2=tri[{next_audio}]"
            )
            assembled_duration += durations[index] - transition_seconds
            applied.append(
                {
                    "boundary": index,
                    "type": "crossfade",
                    "frames": overlap_frames,
                    "duration": transition_seconds,
                }
            )
        else:
            trim_seconds = overlap_frames / fps if transition_type == "shared_frame" else 0.0
            incoming_video = f"vtrim{index}"
            incoming_audio = f"atrim{index}"
            if trim_seconds:
                filters.extend(
                    [
                        f"[v{index}]trim=start={trim_seconds:.6f},"
                        f"setpts=PTS-STARTPTS[{incoming_video}]",
                        f"[a{index}]atrim=start={trim_seconds:.6f},"
                        f"asetpts=PTS-STARTPTS[{incoming_audio}]",
                    ]
                )
            else:
                incoming_video = f"v{index}"
                incoming_audio = f"a{index}"
            filters.extend(
                [
                    f"[{video_label}][{incoming_video}]concat=n=2:v=1:a=0[{next_video}]",
                    f"[{audio_label}][{incoming_audio}]concat=n=2:v=0:a=1[{next_audio}]",
                ]
            )
            assembled_duration += durations[index] - trim_seconds
            applied.append(
                {
                    "boundary": index,
                    "type": transition_type,
                    "frames": overlap_frames,
                    "duration": trim_seconds,
                }
            )
        video_label, audio_label = next_video, next_audio

    args.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"[{video_label}]",
            "-map",
            f"[{audio_label}]",
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
            "-movflags",
            "+faststart",
            str(temporary),
        ]
    )
    run(args, cancel_requested=cancel_requested)
    temporary.replace(output)
    return output, applied


def thumbnail(
    video: Path,
    output: Path,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            get_settings().ffmpeg_path,
            "-y",
            "-v",
            "error",
            "-ss",
            "0.2",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(output),
        ],
        cancel_requested=cancel_requested,
    )
    return output


def contact_sheet(frames: list[Path], output: Path) -> Path:
    from PIL import Image, ImageDraw

    images = [Image.open(frame).convert("RGB").resize((320, 180)) for frame in frames]
    sheet = Image.new("RGB", (640, ((len(images) + 1) // 2) * 210), "#111827")
    draw = ImageDraw.Draw(sheet)
    for index, image in enumerate(images):
        x, y = (index % 2) * 320, (index // 2) * 210
        sheet.paste(image, (x, y))
        draw.text((x + 8, y + 184), f"Shot {index + 1}", fill="white")
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    return output


def write_manifest(output: Path, data: dict[str, object]) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    temp.replace(output)
    return output
