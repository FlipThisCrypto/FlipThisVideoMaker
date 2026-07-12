import asyncio
import math
import shutil
import struct
import uuid
import wave
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw

from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.media.ffmpeg import run
from flipthis_video_maker.providers.base.models import (
    Capability,
    ImageRequest,
    ProviderInfo,
    TTSRequest,
    VideoRequest,
)


class MockImageProvider:
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="mock-image",
            name="Deterministic Mock Image",
            model_identity="mock-pattern-v1",
            capabilities={Capability.IMAGE_GENERATION},
            available=True,
            supported_inputs={"text", "seed"},
            max_width=1920,
            max_height=1080,
        )

    async def health(self) -> dict[str, object]:
        return {"ok": True}

    async def generate(self, request: ImageRequest) -> Path:
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = request.output_path.with_name(
            f".{request.output_path.stem}-{uuid.uuid4().hex}.partial{request.output_path.suffix}"
        )
        base = (
            (request.seed * 37) % 205 + 30,
            (request.seed * 67) % 205 + 30,
            (request.seed * 97) % 205 + 30,
        )
        image = Image.new("RGB", (request.width, request.height), base)
        draw = ImageDraw.Draw(image)
        step = max(20, request.width // 12)
        for i in range(0, request.width + request.height, step):
            color = ((base[0] + i) % 255, (base[1] + 2 * i) % 255, (base[2] + 3 * i) % 255)
            draw.line((i, 0, 0, i), fill=color, width=max(2, step // 8))
        text = (
            f"{request.label}\n"
            f"Characters: {', '.join(request.characters) or 'none'}\n"
            f"Seed: {request.seed}\n{request.prompt[:100]}"
        )
        draw.rounded_rectangle(
            (24, 24, min(request.width - 24, 700), 160), radius=12, fill=(0, 0, 0, 180)
        )
        draw.multiline_text((42, 42), text, fill="white", spacing=6)
        image.save(temporary, "PNG")
        temporary.replace(request.output_path)
        return request.output_path


class MockTTSProvider:
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="mock-tts",
            name="Deterministic Mock TTS",
            model_identity="mock-tone-v1",
            capabilities={Capability.TTS},
            available=True,
            supported_inputs={"text"},
        )

    async def health(self) -> dict[str, object]:
        return {"ok": True}

    async def synthesize(self, request: TTSRequest) -> Path:
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = request.output_path.with_name(
            f".{request.output_path.stem}-{uuid.uuid4().hex}.partial{request.output_path.suffix}"
        )
        seconds = max(0.8, len(request.text.split()) * 0.32 / request.speed)
        rate, frequency = 24000, 220 + sum(request.voice.encode()) % 220
        with wave.open(str(temporary), "wb") as wav:
            wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            for i in range(int(seconds * rate)):
                envelope = min(1.0, i / (rate * 0.03), (seconds * rate - i) / (rate * 0.03))
                sample = int(3500 * envelope * math.sin(2 * math.pi * frequency * i / rate))
                wav.writeframesraw(struct.pack("<h", sample))
        temporary.replace(request.output_path)
        return request.output_path


class MockVideoProvider:
    def __init__(self, cancel_requested: Callable[[], bool] | None = None) -> None:
        self.cancel_requested = cancel_requested

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="mock-video",
            name="Deterministic Mock FLF Video",
            model_identity="ffmpeg-xfade-v1",
            capabilities={Capability.VIDEO_GENERATION, Capability.FIRST_LAST_FRAME_VIDEO},
            available=True,
            supported_inputs={"first_frame", "last_frame", "audio"},
            max_duration_seconds=60,
            max_width=1920,
            max_height=1080,
        )

    async def health(self) -> dict[str, object]:
        return {"ok": shutil.which(get_settings().ffmpeg_path) is not None}

    async def generate(self, request: VideoRequest) -> Path:
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = request.output_path.with_name(
            f".{request.output_path.stem}-{uuid.uuid4().hex}.partial{request.output_path.suffix}"
        )
        ffmpeg = get_settings().ffmpeg_path
        frames = max(2, round(request.duration * request.fps))
        vf = (
            f"[0:v]scale={request.width}:{request.height},format=yuv420p[a];"
            f"[1:v]scale={request.width}:{request.height},format=yuv420p[b];"
            f"[a][b]xfade=transition=fade:duration={request.duration}:offset=0,"
            f"trim=duration={request.duration},fps={request.fps}[v]"
        )
        args = [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-loop",
            "1",
            "-i",
            str(request.start_frame),
            "-loop",
            "1",
            "-i",
            str(request.end_frame),
        ]
        if request.audio_path:
            args += ["-i", str(request.audio_path)]
        else:
            args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        args += ["-filter_complex", vf, "-map", "[v]"]
        args += [
            "-map",
            "2:a",
            "-af",
            "aresample=48000,aformat=channel_layouts=stereo,apad",
            "-shortest",
        ]
        args += [
            "-frames:v",
            str(frames),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(temporary),
        ]
        await asyncio.to_thread(run, args, cancel_requested=self.cancel_requested)
        temporary.replace(request.output_path)
        return request.output_path


class MockLipSyncProvider:
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="mock-lipsync",
            name="Deterministic mock lip sync",
            model_identity="mock-passthrough-v1",
            capabilities={Capability.LIP_SYNC},
            available=True,
            supported_inputs={"video", "audio"},
        )

    async def health(self) -> dict[str, object]:
        return {"ok": True}

    async def process(self, video: Path, _audio: Path, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video, output)
        return output


class MockInterpolationProvider:
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="mock-interpolation",
            name="Deterministic mock interpolation",
            model_identity="mock-passthrough-v1",
            capabilities={Capability.INTERPOLATION},
            available=True,
            supported_inputs={"video"},
        )

    async def health(self) -> dict[str, object]:
        return {"ok": True}

    async def process(self, video: Path, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video, output)
        return output
