import asyncio
import os
import shutil
import signal
import uuid
from collections.abc import Callable, Collection
from contextlib import suppress
from pathlib import Path
from typing import Any

from PIL import Image

from flipthis_video_maker.media.ffmpeg import probe
from flipthis_video_maker.providers.base.errors import (
    ProviderCleanupResult,
    ProviderExecutionError,
    ProviderFailureKind,
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
        cancel_requested: Callable[[], bool] | None = None,
        model_identity: str = "administrator-configured",
        health_command: list[str] | None = None,
        max_output_mb: int = 2048,
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
        self.cancel_requested = cancel_requested
        self.model_identity = model_identity
        self.health_command = health_command or []
        if max_output_mb <= 0:
            raise ValueError("CLI provider output limit must be positive")
        self.max_output_bytes = max_output_mb * 1024 * 1024
        self._last_oom_partial: Path | None = None

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id=self.provider_id,
            name=f"CLI: {self.provider_id}",
            model_identity=self.model_identity,
            capabilities=self.capabilities,
            available=(
                (Path(self.command[0]).is_file() or shutil.which(self.command[0]) is not None)
                and bool(self.health_command)
            ),
            notes="Availability requires an executable and a successful configured health command",
        )

    async def health(self) -> dict[str, object]:
        if not self.health_command:
            return {"ok": False, "status": "health_command_missing"}
        executable = self.health_command[0]
        if not (Path(executable).is_file() or shutil.which(executable) is not None):
            return {"ok": False, "status": "health_executable_missing"}
        process = await asyncio.create_subprocess_exec(
            *self.health_command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            return_code = await asyncio.wait_for(process.wait(), timeout=min(self.timeout, 30))
        except TimeoutError:
            await _terminate_process(process)
            return {"ok": False, "status": "health_timeout"}
        return {
            "ok": return_code == 0,
            "status": "healthy" if return_code == 0 else "health_failed",
            "backend_code": f"exit_code:{return_code}",
        }

    async def execute(
        self, values: dict[str, Any], *, operation: str = "command_execution"
    ) -> tuple[str, str]:
        argv = [
            part.format_map({key: str(value) for key, value in values.items()})
            for part in self.command
        ]
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        communication = asyncio.create_task(process.communicate())
        deadline = asyncio.get_running_loop().time() + self.timeout
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    await _terminate_process(process)
                    await communication
                    raise ProviderExecutionError(
                        provider_id=self.provider_id,
                        operation=operation,
                        failure_kind=ProviderFailureKind.TIMEOUT,
                        retryable=True,
                        backend_code="local_process_timeout",
                    )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        asyncio.shield(communication), timeout=min(0.25, remaining)
                    )
                    break
                except TimeoutError:
                    if self.cancel_requested is not None and self.cancel_requested():
                        await _terminate_process(process)
                        await communication
                        raise ProviderExecutionError(
                            provider_id=self.provider_id,
                            operation=operation,
                            failure_kind=ProviderFailureKind.CANCELLED,
                            backend_code="local_process_cancelled",
                        ) from None
        except asyncio.CancelledError:
            await _terminate_process(process)
            communication.cancel()
            with suppress(asyncio.CancelledError):
                await communication
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
        if request.output_path.exists() or request.output_path.is_symlink():
            raise ValueError("Image provider output path must be a new immutable path")
        output = _partial_path(request.output_path)
        await self.execute(
            {
                "prompt": request.prompt,
                "negative_prompt": request.negative_prompt,
                "output": output,
                "reference_image": request.reference_image or "",
                "width": request.width,
                "height": request.height,
                "seed": request.seed,
            },
            operation="image_generation",
        )
        if output.is_symlink() or not output.is_file():
            raise RuntimeError("CLI image provider did not produce a regular file")
        if output.stat().st_size > self.max_output_bytes:
            raise RuntimeError("CLI image provider output exceeds the configured size limit")
        with Image.open(output) as image:
            image.verify()
        with Image.open(output) as image:
            if image.size != (request.width, request.height):
                raise RuntimeError("CLI image provider output dimensions do not match the request")
            if image.format != "PNG":
                raise RuntimeError("CLI image provider must produce PNG for the target contract")
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
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        await process.wait()
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
