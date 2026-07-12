import json
import math
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Any

from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.media.ffmpeg import (
    CancelCheck,
    MediaCancelled,
    MediaError,
    probe,
    run,
)


class SubtitleMode(StrEnum):
    SOFT = "soft"
    BURNED = "burned"


@dataclass(frozen=True, slots=True)
class SoftSubtitleOptions:
    language: str = "eng"
    title: str = "Subtitles"
    subtitle_codec: str = "mov_text"
    default: bool = True
    forced: bool = False
    timeout_seconds: float = 600

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{2,16}", self.language):
            raise ValueError("Subtitle language must be a 2-16 character language tag")
        if not self.title.strip() or "\x00" in self.title or len(self.title) > 200:
            raise ValueError("Subtitle title must contain 1-200 non-NUL characters")
        _validate_codec_name(self.subtitle_codec, "subtitle_codec")
        _validate_timeout(self.timeout_seconds)


@dataclass(frozen=True, slots=True)
class BurnSubtitleOptions:
    video_codec: str = "libx264"
    audio_codec: str = "copy"
    pixel_format: str = "yuv420p"
    preset: str = "medium"
    crf: int = 18
    timeout_seconds: float = 3600

    def __post_init__(self) -> None:
        _validate_codec_name(self.video_codec, "video_codec")
        _validate_codec_name(self.audio_codec, "audio_codec")
        _validate_codec_name(self.pixel_format, "pixel_format")
        _validate_codec_name(self.preset, "preset")
        if not 0 <= self.crf <= 51:
            raise ValueError("crf must be between 0 and 51")
        _validate_timeout(self.timeout_seconds)


@dataclass(frozen=True, slots=True)
class LoudnessTarget:
    integrated_lufs: float = -16
    loudness_range_lu: float = 11
    true_peak_dbfs: float = -1.5

    def __post_init__(self) -> None:
        if not -70 <= self.integrated_lufs <= -5:
            raise ValueError("integrated_lufs must be between -70 and -5")
        if not 1 <= self.loudness_range_lu <= 50:
            raise ValueError("loudness_range_lu must be between 1 and 50")
        if not -9 <= self.true_peak_dbfs <= 0:
            raise ValueError("true_peak_dbfs must be between -9 and 0")


@dataclass(frozen=True, slots=True)
class AudioFinalizationOptions:
    loudness: LoudnessTarget = field(default_factory=LoudnessTarget)
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    sample_rate: int = 48_000
    channels: int = 2
    timeout_seconds: float = 3600

    def __post_init__(self) -> None:
        _validate_codec_name(self.audio_codec, "audio_codec")
        if self.audio_codec == "copy":
            raise ValueError("Audio normalization requires an encoding codec, not copy")
        if not re.fullmatch(r"[1-9][0-9]*[kKmM]?", self.audio_bitrate):
            raise ValueError("audio_bitrate must look like 192k or 2M")
        if not 8_000 <= self.sample_rate <= 192_000:
            raise ValueError("sample_rate must be between 8000 and 192000")
        if self.channels not in (1, 2):
            raise ValueError("channels must be 1 (mono) or 2 (stereo)")
        _validate_timeout(self.timeout_seconds)


@dataclass(frozen=True, slots=True)
class BackgroundMusicOptions:
    path: Path
    gain_db: float = -18
    threshold: float = 0.03
    ratio: float = 8
    attack_ms: float = 20
    release_ms: float = 300
    loop: bool = True

    def __post_init__(self) -> None:
        if not -60 <= self.gain_db <= 12:
            raise ValueError("gain_db must be between -60 and 12")
        if not 0.000_975_63 <= self.threshold <= 1:
            raise ValueError("threshold must be between 0.00097563 and 1")
        if not 1 <= self.ratio <= 20:
            raise ValueError("ratio must be between 1 and 20")
        if not 0.01 <= self.attack_ms <= 2000:
            raise ValueError("attack_ms must be between 0.01 and 2000")
        if not 0.01 <= self.release_ms <= 9000:
            raise ValueError("release_ms must be between 0.01 and 9000")


@dataclass(frozen=True, slots=True)
class LoudnessMeasurement:
    integrated_lufs: float
    true_peak_dbfs: float
    loudness_range_lu: float
    threshold_lufs: float
    target_offset_lu: float


@dataclass(frozen=True, slots=True)
class SubtitleFinalizationResult:
    output_path: Path
    mode: SubtitleMode
    source_video_duration_seconds: float
    output_video_duration_seconds: float
    width: int
    height: int
    frame_rate: float
    video_codec: str
    audio_codec: str | None
    subtitle_codec: str | None
    subtitle_stream_count: int


@dataclass(frozen=True, slots=True)
class AudioFinalizationResult:
    output_path: Path
    source_video_duration_seconds: float
    output_video_duration_seconds: float
    width: int
    height: int
    frame_rate: float
    video_codec: str
    audio_codec: str
    sample_rate: int
    channels: int
    target: LoudnessTarget
    pre_normalization: LoudnessMeasurement
    music_ducking_applied: bool
    music_path: Path | None


@dataclass(frozen=True, slots=True)
class _VideoSignature:
    duration_seconds: float
    width: int
    height: int
    frame_rate: Fraction
    codec: str


@dataclass(frozen=True, slots=True)
class _AudioSignature:
    duration_seconds: float
    sample_rate: int
    channels: int
    codec: str


def mux_soft_subtitles(
    source: Path,
    subtitles: Path,
    output: Path,
    *,
    options: SoftSubtitleOptions | None = None,
    cancel_requested: CancelCheck | None = None,
) -> SubtitleFinalizationResult:
    """Copy final A/V streams and add one selectable subtitle track."""
    configured = options or SoftSubtitleOptions()
    _validate_inputs(source, output, extra_inputs=(subtitles,))
    source_metadata = probe(source, cancel_requested=cancel_requested)
    source_video = _video_signature(source_metadata, source)
    source_has_audio = bool(_streams(source_metadata, "audio"))
    partial = _partial_path(output)
    disposition = _subtitle_disposition(configured.default, configured.forced)
    args = [
        get_settings().ffmpeg_path,
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-i",
        str(subtitles),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map",
        "1:s:0",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-c:s",
        configured.subtitle_codec,
        "-metadata:s:s:0",
        f"language={configured.language}",
        "-metadata:s:s:0",
        f"title={configured.title}",
        "-disposition:s:0",
        disposition,
        "-movflags",
        "+faststart",
        str(partial),
    ]

    try:
        run(
            args,
            timeout=configured.timeout_seconds,
            cancel_requested=cancel_requested,
        )
        output_metadata = probe(partial, cancel_requested=cancel_requested)
        output_video = _validate_preserved_video(
            source_video,
            output_metadata,
            partial,
            require_same_codec=True,
        )
        _validate_audio_presence(source_has_audio, output_metadata, partial)
        subtitle_streams = _streams(output_metadata, "subtitle")
        if len(subtitle_streams) != 1:
            raise MediaError(
                f"Expected one subtitle stream in {partial}, found {len(subtitle_streams)}"
            )
        subtitle_codec = _required_text(subtitle_streams[0], "codec_name", partial)
        if subtitle_codec != configured.subtitle_codec:
            raise MediaError(
                f"Subtitle codec mismatch in {partial}: {subtitle_codec} != "
                f"{configured.subtitle_codec}"
            )
        subtitle_tags = subtitle_streams[0].get("tags")
        language = subtitle_tags.get("language") if isinstance(subtitle_tags, dict) else None
        if language != configured.language:
            raise MediaError(
                f"Subtitle language mismatch in {partial}: {language!r} != {configured.language!r}"
            )
        _publish_partial(partial, output, cancel_requested)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    audio_streams = _streams(output_metadata, "audio")
    return SubtitleFinalizationResult(
        output_path=output,
        mode=SubtitleMode.SOFT,
        source_video_duration_seconds=source_video.duration_seconds,
        output_video_duration_seconds=output_video.duration_seconds,
        width=output_video.width,
        height=output_video.height,
        frame_rate=float(output_video.frame_rate),
        video_codec=output_video.codec,
        audio_codec=(
            _required_text(audio_streams[0], "codec_name", output) if audio_streams else None
        ),
        subtitle_codec=subtitle_codec,
        subtitle_stream_count=len(subtitle_streams),
    )


def burn_subtitles(
    source: Path,
    subtitles: Path,
    output: Path,
    *,
    options: BurnSubtitleOptions | None = None,
    cancel_requested: CancelCheck | None = None,
) -> SubtitleFinalizationResult:
    """Rasterize subtitles into video while preserving source timing and audio."""
    configured = options or BurnSubtitleOptions()
    _validate_inputs(source, output, extra_inputs=(subtitles,))
    source_metadata = probe(source, cancel_requested=cancel_requested)
    source_video = _video_signature(source_metadata, source)
    source_has_audio = bool(_streams(source_metadata, "audio"))
    partial = _partial_path(output)

    try:
        # The subtitles filter parses its filename through multiple escaping layers.
        # A private, fixed-root copy prevents valid user filenames from becoming
        # filtergraph syntax while keeping the original reference immutable.
        with tempfile.TemporaryDirectory(prefix="ftvm-subtitles-", dir="/tmp") as temporary:
            suffix = subtitles.suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                suffix = ".srt"
            filter_subtitles = Path(temporary) / f"captions{suffix}"
            shutil.copyfile(subtitles, filter_subtitles)
            subtitle_filter = (
                f"subtitles=filename={_quote_filter_value(filter_subtitles)}:charenc=UTF-8"
            )
            run(
                [
                    get_settings().ffmpeg_path,
                    "-nostdin",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a?",
                    "-map_metadata",
                    "0",
                    "-map_chapters",
                    "0",
                    "-vf",
                    subtitle_filter,
                    "-c:v",
                    configured.video_codec,
                    "-preset",
                    configured.preset,
                    "-crf",
                    str(configured.crf),
                    "-pix_fmt",
                    configured.pixel_format,
                    "-c:a",
                    configured.audio_codec,
                    "-movflags",
                    "+faststart",
                    str(partial),
                ],
                timeout=configured.timeout_seconds,
                cancel_requested=cancel_requested,
            )
        output_metadata = probe(partial, cancel_requested=cancel_requested)
        output_video = _validate_preserved_video(
            source_video,
            output_metadata,
            partial,
            require_same_codec=False,
        )
        _validate_audio_presence(source_has_audio, output_metadata, partial)
        subtitle_streams = _streams(output_metadata, "subtitle")
        if subtitle_streams:
            raise MediaError(f"Burned-subtitle output unexpectedly has subtitle streams: {partial}")
        _publish_partial(partial, output, cancel_requested)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    audio_streams = _streams(output_metadata, "audio")
    return SubtitleFinalizationResult(
        output_path=output,
        mode=SubtitleMode.BURNED,
        source_video_duration_seconds=source_video.duration_seconds,
        output_video_duration_seconds=output_video.duration_seconds,
        width=output_video.width,
        height=output_video.height,
        frame_rate=float(output_video.frame_rate),
        video_codec=output_video.codec,
        audio_codec=(
            _required_text(audio_streams[0], "codec_name", output) if audio_streams else None
        ),
        subtitle_codec=None,
        subtitle_stream_count=0,
    )


def measure_loudness(
    source: Path,
    *,
    target: LoudnessTarget | None = None,
    timeout_seconds: float = 600,
    cancel_requested: CancelCheck | None = None,
) -> LoudnessMeasurement:
    """Measure the first audio stream with FFmpeg's EBU R128 loudnorm filter."""
    configured_target = target or LoudnessTarget()
    _validate_timeout(timeout_seconds)
    if not source.is_file():
        raise FileNotFoundError(source)
    metadata = probe(source, cancel_requested=cancel_requested)
    if not _streams(metadata, "audio"):
        raise MediaError(f"Media has no audio stream: {source}")
    result = run(
        [
            get_settings().ffmpeg_path,
            "-nostdin",
            "-v",
            "info",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-af",
            _loudnorm_analysis_filter(configured_target),
            "-f",
            "null",
            "-",
        ],
        timeout=timeout_seconds,
        cancel_requested=cancel_requested,
    )
    return _parse_loudnorm_measurement(result.stderr)


def normalize_audio(
    source: Path,
    output: Path,
    *,
    options: AudioFinalizationOptions | None = None,
    music: BackgroundMusicOptions | None = None,
    cancel_requested: CancelCheck | None = None,
) -> AudioFinalizationResult:
    """Normalize program audio, optionally mix looped ducked music, and copy video unchanged."""
    configured = options or AudioFinalizationOptions()
    extra_inputs = (music.path,) if music else ()
    _validate_inputs(source, output, extra_inputs=extra_inputs)
    source_metadata = probe(source, cancel_requested=cancel_requested)
    source_video = _video_signature(source_metadata, source)
    if not _streams(source_metadata, "audio"):
        raise MediaError(f"Media has no audio stream: {source}")
    if music:
        music_metadata = probe(music.path, cancel_requested=cancel_requested)
        if not _streams(music_metadata, "audio"):
            raise MediaError(f"Background music has no audio stream: {music.path}")

    input_args = _audio_input_args(source, music)
    base_filters, mixed_label = _mix_filters(
        source_video.duration_seconds,
        configured,
        music,
    )
    analysis_label = "loudness_analysis"
    analysis_filters = [
        *base_filters,
        f"[{mixed_label}]{_loudnorm_analysis_filter(configured.loudness)}[{analysis_label}]",
    ]
    analysis = run(
        [
            get_settings().ffmpeg_path,
            "-nostdin",
            "-v",
            "info",
            *input_args,
            "-filter_complex",
            ";".join(analysis_filters),
            "-map",
            f"[{analysis_label}]",
            "-f",
            "null",
            "-",
        ],
        timeout=configured.timeout_seconds,
        cancel_requested=cancel_requested,
    )
    measurement = _parse_loudnorm_measurement(analysis.stderr)

    normalized_label = "normalized_audio"
    render_filters = [
        *base_filters,
        f"[{mixed_label}]{_loudnorm_render_filter(configured.loudness, measurement)}"
        f"[{normalized_label}]",
    ]
    partial = _partial_path(output)
    args = [
        get_settings().ffmpeg_path,
        "-nostdin",
        "-y",
        "-v",
        "error",
        *input_args,
        "-filter_complex",
        ";".join(render_filters),
        "-map",
        "0:v:0",
        "-map",
        f"[{normalized_label}]",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        "-c:v",
        "copy",
        "-c:a",
        configured.audio_codec,
        "-b:a",
        configured.audio_bitrate,
        "-ar",
        str(configured.sample_rate),
        "-ac",
        str(configured.channels),
        "-movflags",
        "+faststart",
        str(partial),
    ]

    try:
        run(
            args,
            timeout=configured.timeout_seconds,
            cancel_requested=cancel_requested,
        )
        output_metadata = probe(partial, cancel_requested=cancel_requested)
        output_video = _validate_preserved_video(
            source_video,
            output_metadata,
            partial,
            require_same_codec=True,
        )
        output_audio = _audio_signature(output_metadata, partial)
        tolerance = max(0.15, 2 / float(source_video.frame_rate))
        if abs(output_audio.duration_seconds - source_video.duration_seconds) > tolerance:
            raise MediaError(
                f"Normalized audio duration changed in {partial}: "
                f"{output_audio.duration_seconds:.6f}s != "
                f"{source_video.duration_seconds:.6f}s"
            )
        if output_audio.sample_rate != configured.sample_rate:
            raise MediaError(
                f"Normalized sample rate mismatch in {partial}: "
                f"{output_audio.sample_rate} != {configured.sample_rate}"
            )
        if output_audio.channels != configured.channels:
            raise MediaError(
                f"Normalized channel count mismatch in {partial}: "
                f"{output_audio.channels} != {configured.channels}"
            )
        _publish_partial(partial, output, cancel_requested)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    return AudioFinalizationResult(
        output_path=output,
        source_video_duration_seconds=source_video.duration_seconds,
        output_video_duration_seconds=output_video.duration_seconds,
        width=output_video.width,
        height=output_video.height,
        frame_rate=float(output_video.frame_rate),
        video_codec=output_video.codec,
        audio_codec=output_audio.codec,
        sample_rate=output_audio.sample_rate,
        channels=output_audio.channels,
        target=configured.loudness,
        pre_normalization=measurement,
        music_ducking_applied=music is not None,
        music_path=music.path if music else None,
    )


def _audio_input_args(source: Path, music: BackgroundMusicOptions | None) -> list[str]:
    args = ["-i", str(source)]
    if music:
        if music.loop:
            args.extend(["-stream_loop", "-1"])
        args.extend(["-i", str(music.path)])
    return args


def _mix_filters(
    duration_seconds: float,
    options: AudioFinalizationOptions,
    music: BackgroundMusicOptions | None,
) -> tuple[list[str], str]:
    layout = "mono" if options.channels == 1 else "stereo"
    duration_text = f"{duration_seconds:.6f}"
    program = (
        f"[0:a:0]aresample={options.sample_rate},"
        f"aformat=sample_fmts=fltp:channel_layouts={layout},"
        f"apad=whole_dur={duration_text},atrim=duration={duration_text},"
        "asetpts=PTS-STARTPTS"
    )
    if music is None:
        return [f"{program}[mixed_program]"], "mixed_program"

    music_filter = (
        f"[1:a:0]aresample={options.sample_rate},"
        f"aformat=sample_fmts=fltp:channel_layouts={layout},"
        f"volume={music.gain_db:.6f}dB,"
        f"apad=whole_dur={duration_text},atrim=duration={duration_text},"
        "asetpts=PTS-STARTPTS[prepared_music]"
    )
    return (
        [
            f"{program},asplit=2[program_sidechain][program_mix]",
            music_filter,
            (
                "[prepared_music][program_sidechain]sidechaincompress="
                f"threshold={music.threshold:.8f}:ratio={music.ratio:.6f}:"
                f"attack={music.attack_ms:.6f}:release={music.release_ms:.6f}"
                "[ducked_music]"
            ),
            (
                "[program_mix][ducked_music]amix=inputs=2:duration=first:"
                "dropout_transition=0:normalize=0[mixed_program]"
            ),
        ],
        "mixed_program",
    )


def _loudnorm_analysis_filter(target: LoudnessTarget) -> str:
    return (
        f"loudnorm=I={target.integrated_lufs:.6f}:"
        f"LRA={target.loudness_range_lu:.6f}:"
        f"TP={target.true_peak_dbfs:.6f}:print_format=json"
    )


def _loudnorm_render_filter(
    target: LoudnessTarget,
    measured: LoudnessMeasurement,
) -> str:
    return (
        f"loudnorm=I={target.integrated_lufs:.6f}:"
        f"LRA={target.loudness_range_lu:.6f}:"
        f"TP={target.true_peak_dbfs:.6f}:"
        f"measured_I={measured.integrated_lufs:.6f}:"
        f"measured_LRA={measured.loudness_range_lu:.6f}:"
        f"measured_TP={measured.true_peak_dbfs:.6f}:"
        f"measured_thresh={measured.threshold_lufs:.6f}:"
        f"offset={measured.target_offset_lu:.6f}:"
        "linear=true:print_format=summary"
    )


def _parse_loudnorm_measurement(stderr: str) -> LoudnessMeasurement:
    candidates = re.findall(r"\{\s*\"input_i\".*?\}", stderr, flags=re.DOTALL)
    if not candidates:
        raise MediaError("FFmpeg loudnorm analysis did not return JSON measurements")
    try:
        raw = json.loads(candidates[-1])
        measurement = LoudnessMeasurement(
            integrated_lufs=_finite_float(raw["input_i"], "input_i"),
            true_peak_dbfs=_finite_float(raw["input_tp"], "input_tp"),
            loudness_range_lu=_finite_float(raw["input_lra"], "input_lra"),
            threshold_lufs=_finite_float(raw["input_thresh"], "input_thresh"),
            target_offset_lu=_finite_float(raw["target_offset"], "target_offset"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise MediaError("FFmpeg loudnorm analysis returned invalid measurements") from error
    return measurement


def _finite_float(value: object, field_name: str) -> float:
    if not isinstance(value, (str, int, float)):
        raise ValueError(f"Loudness field {field_name} is not numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"Loudness field {field_name} is not finite")
    return parsed


def _validate_inputs(source: Path, output: Path, *, extra_inputs: tuple[Path, ...]) -> None:
    for path in (source, *extra_inputs):
        if not path.is_file():
            raise FileNotFoundError(path)
    if source.resolve() == output.resolve():
        raise ValueError("Final-media output must not replace its source")
    if os.path.lexists(output):
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)


def _partial_path(output: Path) -> Path:
    return output.with_name(f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}")


def _publish_partial(
    partial: Path,
    output: Path,
    cancel_requested: CancelCheck | None,
) -> None:
    if cancel_requested and cancel_requested():
        raise MediaCancelled(f"Final-media operation cancelled before publishing {output}")
    if os.path.lexists(output):
        raise FileExistsError(output)
    partial.replace(output)


def _streams(metadata: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    raw_streams = metadata.get("streams")
    if not isinstance(raw_streams, list):
        raise MediaError("ffprobe response has no streams list")
    streams: list[dict[str, Any]] = []
    for stream in raw_streams:
        if isinstance(stream, dict) and stream.get("codec_type") == kind:
            streams.append(stream)
    return streams


def _video_signature(metadata: dict[str, Any], path: Path) -> _VideoSignature:
    video_streams = _streams(metadata, "video")
    if not video_streams:
        raise MediaError(f"Media has no video stream: {path}")
    stream = video_streams[0]
    width = _required_int(stream, "width", path)
    height = _required_int(stream, "height", path)
    frame_rate = _frame_rate(stream, path)
    return _VideoSignature(
        duration_seconds=_stream_duration(metadata, stream, path),
        width=width,
        height=height,
        frame_rate=frame_rate,
        codec=_required_text(stream, "codec_name", path),
    )


def _audio_signature(metadata: dict[str, Any], path: Path) -> _AudioSignature:
    audio_streams = _streams(metadata, "audio")
    if not audio_streams:
        raise MediaError(f"Media has no audio stream: {path}")
    stream = audio_streams[0]
    return _AudioSignature(
        duration_seconds=_stream_duration(metadata, stream, path),
        sample_rate=_required_int(stream, "sample_rate", path),
        channels=_required_int(stream, "channels", path),
        codec=_required_text(stream, "codec_name", path),
    )


def _validate_preserved_video(
    source: _VideoSignature,
    output_metadata: dict[str, Any],
    output: Path,
    *,
    require_same_codec: bool,
) -> _VideoSignature:
    rendered = _video_signature(output_metadata, output)
    if (rendered.width, rendered.height) != (source.width, source.height):
        raise MediaError(
            f"Video dimensions changed in {output}: "
            f"{rendered.width}x{rendered.height} != {source.width}x{source.height}"
        )
    if abs(float(rendered.frame_rate) - float(source.frame_rate)) > 0.01:
        raise MediaError(
            f"Video frame rate changed in {output}: "
            f"{float(rendered.frame_rate):.6f} != {float(source.frame_rate):.6f}"
        )
    tolerance = max(0.1, 2 / float(source.frame_rate))
    if abs(rendered.duration_seconds - source.duration_seconds) > tolerance:
        raise MediaError(
            f"Video duration changed in {output}: "
            f"{rendered.duration_seconds:.6f}s != {source.duration_seconds:.6f}s"
        )
    if require_same_codec and rendered.codec != source.codec:
        raise MediaError(f"Video codec changed in {output}: {rendered.codec} != {source.codec}")
    return rendered


def _validate_audio_presence(
    source_has_audio: bool,
    output_metadata: dict[str, Any],
    output: Path,
) -> None:
    output_has_audio = bool(_streams(output_metadata, "audio"))
    if output_has_audio != source_has_audio:
        raise MediaError(f"Audio stream presence changed in {output}")


def _stream_duration(
    metadata: dict[str, Any],
    stream: dict[str, Any],
    path: Path,
) -> float:
    raw = stream.get("duration")
    if raw in (None, "N/A"):
        media_format = metadata.get("format")
        raw = media_format.get("duration") if isinstance(media_format, dict) else None
    if not isinstance(raw, (str, int, float)):
        raise MediaError(f"Media duration is missing or invalid: {path}")
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise MediaError(f"Media duration is missing or invalid: {path}") from error
    if not math.isfinite(value) or value <= 0:
        raise MediaError(f"Media duration is not positive and finite: {path}")
    return value


def _frame_rate(stream: dict[str, Any], path: Path) -> Fraction:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(key)
        if not isinstance(raw, str):
            continue
        try:
            value = Fraction(raw)
        except (ValueError, ZeroDivisionError):
            continue
        if value > 0:
            return value
    raise MediaError(f"Video frame rate is missing or invalid: {path}")


def _required_int(data: dict[str, Any], key: str, path: Path) -> int:
    try:
        value = int(data[key])
    except (KeyError, TypeError, ValueError) as error:
        raise MediaError(f"Media {key} is missing or invalid: {path}") from error
    if value <= 0:
        raise MediaError(f"Media {key} must be positive: {path}")
    return value


def _required_text(data: dict[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise MediaError(f"Media {key} is missing or invalid: {path}")
    return value


def _subtitle_disposition(default: bool, forced: bool) -> str:
    flags = [name for name, enabled in (("default", default), ("forced", forced)) if enabled]
    return "+".join(flags) if flags else "0"


def _quote_filter_value(path: Path) -> str:
    value = str(path)
    escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    return f"'{escaped}'"


def _validate_codec_name(value: str, field_name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError(f"{field_name} contains unsupported characters")


def _validate_timeout(value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout_seconds must be positive and finite")


__all__ = [
    "AudioFinalizationOptions",
    "AudioFinalizationResult",
    "BackgroundMusicOptions",
    "BurnSubtitleOptions",
    "LoudnessMeasurement",
    "LoudnessTarget",
    "SoftSubtitleOptions",
    "SubtitleFinalizationResult",
    "SubtitleMode",
    "burn_subtitles",
    "measure_loudness",
    "mux_soft_subtitles",
    "normalize_audio",
]
