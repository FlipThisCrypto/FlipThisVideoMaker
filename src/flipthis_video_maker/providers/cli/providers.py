import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

from flipthis_video_maker.media.ffmpeg import probe
from flipthis_video_maker.providers.base.models import (
    Capability,
    ImageRequest,
    ProviderInfo,
    TTSRequest,
    VideoRequest,
)


class ConfiguredCLIProvider:
    """Administrator-configured argv template; user values are arguments, never shell text."""

    def __init__(
        self,
        provider_id: str,
        command: list[str],
        capabilities: set[Capability],
        timeout: int = 1800,
    ) -> None:
        if not command:
            raise ValueError("CLI provider command cannot be empty")
        self.provider_id, self.command, self.capabilities, self.timeout = (
            provider_id,
            command,
            capabilities,
            timeout,
        )

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id=self.provider_id,
            name=f"CLI: {self.provider_id}",
            model_identity="administrator-configured",
            capabilities=self.capabilities,
            available=Path(self.command[0]).is_file() or shutil.which(self.command[0]) is not None,
            notes="Availability is confirmed by health execution when configured",
        )

    async def health(self) -> dict[str, object]:
        return {"ok": self.info().available, "command": self.command[0]}

    async def execute(self, values: dict[str, Any]) -> tuple[str, str]:
        argv = [
            part.format_map({key: str(value) for key, value in values.items()})
            for part in self.command
        ]
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except (TimeoutError, asyncio.CancelledError):
            process.terminate()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError(
                f"CLI provider failed ({process.returncode}): {stderr.decode()[-2000:]}"
            )
        return stdout.decode(), stderr.decode()


class GenericCLIImageProvider(ConfiguredCLIProvider):
    async def generate(self, request: ImageRequest) -> Path:
        output = _partial_path(request.output_path)
        await self.execute(
            {
                "prompt": request.prompt,
                "output": output,
                "width": request.width,
                "height": request.height,
                "seed": request.seed,
            }
        )
        with Image.open(output) as image:
            image.verify()
        output.replace(request.output_path)
        return request.output_path


class GenericCLITTSProvider(ConfiguredCLIProvider):
    async def synthesize(self, request: TTSRequest) -> Path:
        output = _partial_path(request.output_path)
        await self.execute(
            {
                "text": request.text,
                "output": output,
                "voice": request.voice,
                "speed": request.speed,
            }
        )
        data = probe(output)
        if not any(stream.get("codec_type") == "audio" for stream in data.get("streams", [])):
            raise RuntimeError("CLI TTS output has no decodable audio stream")
        output.replace(request.output_path)
        return request.output_path


class GenericCLIVideoProvider(ConfiguredCLIProvider):
    async def generate(self, request: VideoRequest) -> Path:
        output = _partial_path(request.output_path)
        await self.execute(
            {
                "prompt": request.prompt,
                "output": output,
                "start_frame": request.start_frame,
                "end_frame": request.end_frame,
                "audio": request.audio_path or "",
                "duration": request.duration,
                "fps": request.fps,
                "width": request.width,
                "height": request.height,
                "seed": request.seed,
            }
        )
        data = probe(output)
        if not any(stream.get("codec_type") == "video" for stream in data.get("streams", [])):
            raise RuntimeError("CLI video output has no decodable video stream")
        output.replace(request.output_path)
        return request.output_path


def _partial_path(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    return output.with_name(f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}")
