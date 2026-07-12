import asyncio
import shutil
import uuid
from collections.abc import Collection
from pathlib import Path
from typing import Any

from PIL import Image

from flipthis_video_maker.media.ffmpeg import probe
from flipthis_video_maker.providers.base.errors import (
    ProviderCleanupResult,
    ProviderExecutionError,
    ProviderOutOfMemoryError,
)
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
        oom_exit_codes: Collection[int] = (),
    ) -> None:
        if not command:
            raise ValueError("CLI provider command cannot be empty")
        if 0 in oom_exit_codes:
            raise ValueError("CLI provider OOM exit codes cannot include successful exit code 0")
        self.provider_id, self.command, self.capabilities, self.timeout = (
            provider_id,
            command,
            capabilities,
            timeout,
        )
        self.oom_exit_codes = frozenset(oom_exit_codes)
        self._last_oom_partial: Path | None = None

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

    async def execute(
        self, values: dict[str, Any], *, operation: str = "command_execution"
    ) -> tuple[str, str]:
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
            await _terminate_process(process)
            raise
        if process.returncode in self.oom_exit_codes:
            raw_output = values.get("output")
            self._last_oom_partial = Path(raw_output) if raw_output is not None else None
            raise ProviderOutOfMemoryError(
                provider_id=self.provider_id,
                operation=operation,
                backend_code=f"exit_code:{process.returncode}",
            )
        if process.returncode:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation=operation,
                backend_code=f"exit_code:{process.returncode}",
            )
        return stdout.decode(), stderr.decode()

    async def cleanup_after_oom(self, error: ProviderOutOfMemoryError) -> ProviderCleanupResult:
        if error.provider_id != self.provider_id:
            raise ValueError("Cannot clean up an OOM from a different provider")
        partial = self._last_oom_partial
        self._last_oom_partial = None
        try:
            if partial is not None:
                partial.unlink(missing_ok=True)
        except OSError:
            return ProviderCleanupResult(
                provider_id=self.provider_id,
                completed=False,
                retry_safe=False,
                action_code="reaped_process_partial_cleanup_failed",
            )
        return ProviderCleanupResult(
            provider_id=self.provider_id,
            completed=True,
            retry_safe=True,
            action_code="reaped_process_partial_removed",
        )


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
            },
            operation="image_generation",
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
            },
            operation="text_to_speech",
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
            },
            operation="video_generation",
        )
        data = probe(output)
        if not any(stream.get("codec_type") == "video" for stream in data.get("streams", [])):
            raise RuntimeError("CLI video output has no decodable video stream")
        output.replace(request.output_path)
        return request.output_path


def _partial_path(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    return output.with_name(f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}")


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        await process.wait()
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
