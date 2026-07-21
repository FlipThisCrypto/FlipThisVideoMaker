import asyncio
import shutil
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from flipthis_video_maker.contracts.video_generation import GenerationCategory
from flipthis_video_maker.media.ffmpeg import (
    MediaCancelled,
    MediaCommandError,
    MediaError,
    run,
)
from flipthis_video_maker.media.video_delivery import inspect_frame_timing
from flipthis_video_maker.providers.base.errors import (
    ProviderCleanupResult,
    ProviderExecutionError,
    ProviderFailureKind,
    ProviderOutOfMemoryError,
)
from flipthis_video_maker.providers.base.models import Capability, ProviderInfo


class RifeCliInterpolationProvider:
    """Isolated Practical-RIFE 4.25 CLI adapter; model dependencies stay external."""

    def __init__(
        self,
        python: Path,
        script: Path,
        model_directory: Path,
        *,
        provider_id: str = "rife-local",
        timeout_seconds: float = 1800,
        oom_exit_codes: set[int] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.python = python
        self.script = script
        self.model_directory = model_directory
        self.timeout_seconds = timeout_seconds
        self.oom_exit_codes = oom_exit_codes or set()
        self.cancel_requested = cancel_requested

    def info(self) -> ProviderInfo:
        available = (
            all(path.is_file() for path in (self.python, self.script))
            and self.model_directory.is_dir()
        )
        return ProviderInfo(
            id=self.provider_id,
            name="Practical-RIFE temporal interpolation",
            model_identity="Practical-RIFE-4.25",
            capabilities={Capability.INTERPOLATION},
            available=available,
            supported_inputs={"video/mp4", "target_fps"},
            generation_category=GenerationCategory.FRAME_INTERPOLATION,
            cancellation_supported=True,
            progress_supported=False,
            notes=(
                "Runs in an administrator-managed Python <=3.11 environment on the one GPU "
                "visible to its worker. It does not generate scene motion."
            ),
        )

    async def health(self) -> dict[str, object]:
        info = self.info()
        return {
            "ok": info.available,
            "status": "ready" if info.available else "missing_external_runtime",
            "python": shutil.which(str(self.python)) is not None or self.python.is_file(),
            "script": self.script.is_file(),
            "model_directory": self.model_directory.is_dir(),
        }

    async def process(self, video: Path, output: Path, *, target_fps: int) -> Path:
        if output.exists():
            raise FileExistsError(f"Refusing to overwrite completed RIFE output: {output}")
        if not self.info().available:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="interpolate",
                failure_kind=ProviderFailureKind.INVALID_INPUT,
                backend_code="runtime_unavailable",
            )
        if target_fps < 1 or target_fps > 120:
            raise ValueError("RIFE target FPS must be between 1 and 120")
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}")
        args = [
            str(self.python),
            str(self.script),
            "--video",
            str(video),
            "--output",
            str(partial),
            "--model",
            str(self.model_directory),
            "--fps",
            str(target_fps),
        ]
        try:
            await asyncio.to_thread(
                run,
                args,
                self.timeout_seconds,
                cancel_requested=self.cancel_requested,
            )
            if not partial.is_file():
                raise ProviderExecutionError(
                    provider_id=self.provider_id,
                    operation="interpolate",
                    failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                    backend_code="missing_output",
                )
            facts = inspect_frame_timing(
                partial,
                cancel_requested=self.cancel_requested,
            )
            if abs(facts["average_frame_rate"] - target_fps) > 0.05:
                raise ProviderExecutionError(
                    provider_id=self.provider_id,
                    operation="interpolate",
                    failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                    backend_code="wrong_frame_rate",
                )
            partial.replace(output)
        except MediaCancelled as error:
            partial.unlink(missing_ok=True)
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="interpolate",
                failure_kind=ProviderFailureKind.CANCELLED,
                backend_code="child_process_cancelled",
            ) from error
        except subprocess.TimeoutExpired as error:
            partial.unlink(missing_ok=True)
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="interpolate",
                failure_kind=ProviderFailureKind.TIMEOUT,
                retryable=True,
                backend_code="child_process_timeout",
            ) from error
        except MediaCommandError as error:
            partial.unlink(missing_ok=True)
            if error.return_code in self.oom_exit_codes:
                raise ProviderOutOfMemoryError(
                    provider_id=self.provider_id,
                    operation="interpolate",
                    backend_code=f"exit_code:{error.return_code}",
                ) from error
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="interpolate",
                backend_code=f"exit_code:{error.return_code}",
            ) from error
        except (MediaError, ValueError) as error:
            partial.unlink(missing_ok=True)
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="interpolate",
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="invalid_interpolation_output",
            ) from error
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return output

    async def cleanup_after_oom(
        self,
        _error: ProviderOutOfMemoryError,
    ) -> ProviderCleanupResult:
        return ProviderCleanupResult(
            provider_id=self.provider_id,
            completed=True,
            retry_safe=True,
            action_code="child_process_reaped_and_partial_removed",
        )


__all__ = ["RifeCliInterpolationProvider"]
