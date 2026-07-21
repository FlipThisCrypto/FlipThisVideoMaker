import asyncio
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from flipthis_video_maker.media.ffmpeg import MediaCancelled, MediaCommandError, run
from flipthis_video_maker.providers.base.errors import (
    ProviderExecutionError,
    ProviderFailureKind,
)
from flipthis_video_maker.providers.base.models import Capability, ProviderInfo

ALEXNET_SHA256 = "7be5be791159472b1fbf3c69796f7cb30dca7ad8466c2df70058c37116cdee02"
LPIPS_ALEX_SHA256 = "df73285e35b22355a2df87cdb6b70b343713b667eddbda73e1977e0c860835c0"


class LpipsMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    metric: Literal["lpips"]
    network: Literal["alex"]
    distance: float = Field(ge=-0.1, le=10)


class LpipsHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: Literal[True]
    metric: Literal["lpips"]
    version: Literal["0.1"]
    package_version: Literal["0.1.4"]
    network: Literal["alex"]
    torch_version: str = Field(min_length=1, max_length=80)
    torchvision_version: str = Field(min_length=1, max_length=80)
    alexnet_sha256: Literal["7be5be791159472b1fbf3c69796f7cb30dca7ad8466c2df70058c37116cdee02"]
    lpips_alex_sha256: Literal["df73285e35b22355a2df87cdb6b70b343713b667eddbda73e1977e0c860835c0"]


class LpipsCliMetricProvider:
    """Isolated official LPIPS 0.1/AlexNet metric with a strict JSON boundary."""

    def __init__(
        self,
        provider_id: str,
        python: Path,
        script: Path,
        cache_directory: Path,
        *,
        timeout_seconds: float = 300,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.python = python
        self.script = script
        self.cache_directory = cache_directory
        self.timeout_seconds = timeout_seconds
        self.cancel_requested = cancel_requested

    def info(self) -> ProviderInfo:
        available = (
            self.python.is_file() and self.script.is_file() and self.cache_directory.is_dir()
        )
        return ProviderInfo(
            id=self.provider_id,
            name="Local LPIPS perceptual metric",
            model_identity="lpips-0.1-alex",
            capabilities={Capability.QUALITY_ANALYSIS},
            available=available,
            supported_inputs={"image/png", "image/jpeg"},
            cancellation_supported=True,
            notes=(
                "Runs the BSD-2-Clause LPIPS 0.1 AlexNet metric in an isolated local CPU "
                "environment. Lower distance means greater perceptual similarity."
            ),
        )

    async def health(self) -> dict[str, object]:
        if not self.info().available:
            return {"ok": False, "status": "missing_external_runtime"}
        try:
            completed = await asyncio.to_thread(
                run,
                [str(self.python), str(self.script), "--health"],
                self.timeout_seconds,
                cancel_requested=self.cancel_requested,
                env=self._environment(),
            )
            health = LpipsHealth.model_validate_json(completed.stdout)
        except MediaCancelled:
            return {"ok": False, "status": "cancelled"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "status": "timeout"}
        except (MediaCommandError, ValueError, json.JSONDecodeError):
            return {"ok": False, "status": "invalid_or_failed_runtime"}
        return {"status": "ready", **health.model_dump()}

    def measure(self, reference: Path, candidate: Path) -> dict[str, object]:
        for path in (reference, candidate):
            if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                raise ProviderExecutionError(
                    provider_id=self.provider_id,
                    operation="measure_lpips",
                    failure_kind=ProviderFailureKind.INVALID_INPUT,
                    backend_code="invalid_image_input",
                )
        try:
            completed = run(
                [
                    str(self.python),
                    str(self.script),
                    "--reference",
                    str(reference),
                    "--candidate",
                    str(candidate),
                ],
                self.timeout_seconds,
                cancel_requested=self.cancel_requested,
                env=self._environment(),
            )
            measurement = LpipsMeasurement.model_validate_json(completed.stdout)
        except MediaCancelled as error:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="measure_lpips",
                failure_kind=ProviderFailureKind.CANCELLED,
                backend_code="child_process_cancelled",
            ) from error
        except subprocess.TimeoutExpired as error:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="measure_lpips",
                failure_kind=ProviderFailureKind.TIMEOUT,
                retryable=True,
                backend_code="child_process_timeout",
            ) from error
        except MediaCommandError as error:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="measure_lpips",
                backend_code=f"exit_code:{error.return_code}",
            ) from error
        except (ValueError, json.JSONDecodeError) as error:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="measure_lpips",
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="invalid_metric_output",
            ) from error
        return {
            **measurement.model_dump(),
            "provider_id": self.provider_id,
            "model": "lpips-0.1-alex",
        }

    def _environment(self) -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONNOUSERSITE": "1",
            "TORCH_HOME": str(self.cache_directory),
        }


__all__ = [
    "ALEXNET_SHA256",
    "LPIPS_ALEX_SHA256",
    "LpipsCliMetricProvider",
    "LpipsHealth",
    "LpipsMeasurement",
]
