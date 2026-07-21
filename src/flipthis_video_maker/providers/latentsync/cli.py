import asyncio
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from flipthis_video_maker.contracts.video_generation import GenerationCategory
from flipthis_video_maker.media.ffmpeg import (
    MediaCancelled,
    MediaCommandError,
    MediaError,
    probe,
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

_CONFIDENCE = re.compile(r"SyncNet confidence:\s*(-?\d+(?:\.\d+)?)")
_OFFSET = re.compile(r"AV offset:\s*(-?\d+)")


class LipSyncRunOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    actual_model: str
    output_path: Path
    sync_confidence: float
    av_offset_frames: int
    sync_qa_passed: bool
    warnings: tuple[str, ...] = ()
    actual_settings: dict[str, bool | float | int | str] = Field(default_factory=dict)


class LatentSyncCliProvider:
    """Isolated LatentSync 1.5 CLI and its official SyncNet evaluator."""

    def __init__(
        self,
        provider_id: str,
        python: Path,
        repository_directory: Path,
        unet_config_path: Path,
        checkpoint_path: Path,
        syncnet_checkpoint_path: Path,
        *,
        timeout_seconds: float = 1800,
        inference_steps: int = 20,
        guidance_scale: float = 1.5,
        enable_deepcache: bool = True,
        oom_exit_codes: set[int] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        if not 20 <= inference_steps <= 50:
            raise ValueError("LatentSync inference steps must be between 20 and 50")
        if not 1 <= guidance_scale <= 3:
            raise ValueError("LatentSync guidance scale must be between 1 and 3")
        self.provider_id = provider_id
        self.python = python
        self.repository_directory = repository_directory
        self.unet_config_path = unet_config_path
        self.checkpoint_path = checkpoint_path
        self.syncnet_checkpoint_path = syncnet_checkpoint_path
        self.timeout_seconds = timeout_seconds
        self.inference_steps = inference_steps
        self.guidance_scale = guidance_scale
        self.enable_deepcache = enable_deepcache
        self.oom_exit_codes = oom_exit_codes or set()
        self.cancel_requested = cancel_requested
        self._cleanup_targets: tuple[Path, ...] = ()

    def info(self) -> ProviderInfo:
        health_paths = (
            self.python,
            self.repository_directory / "scripts" / "inference.py",
            self.repository_directory / "eval" / "eval_sync_conf.py",
            self.unet_config_path,
            self.checkpoint_path,
            self.syncnet_checkpoint_path,
        )
        available = self.repository_directory.is_dir() and all(
            path.is_file() for path in health_paths
        )
        return ProviderInfo(
            id=self.provider_id,
            name="LatentSync 1.5 post-generation lip sync",
            model_identity="LatentSync-1.5",
            capabilities={Capability.LIP_SYNC},
            available=available,
            supported_inputs={
                "video/mp4",
                "audio/wav",
                "audio/mpeg",
                "single_visible_speaking_face",
            },
            max_duration_seconds=10,
            generation_category=GenerationCategory.PERFORMANCE_CONDITIONED_VIDEO,
            cancellation_supported=True,
            progress_supported=False,
            notes=(
                "Apache-2.0 local post-process. Official documentation reports an 8 GB "
                "minimum for v1.5. It does not expose deterministic multi-face selection."
            ),
        )

    async def health(self) -> dict[str, object]:
        info = self.info()
        return {
            "ok": info.available,
            "status": "ready" if info.available else "missing_external_runtime",
            "python": shutil.which(str(self.python)) is not None or self.python.is_file(),
            "repository": self.repository_directory.is_dir(),
            "inference_script": (self.repository_directory / "scripts" / "inference.py").is_file(),
            "checkpoint": self.checkpoint_path.is_file(),
            "syncnet_checkpoint": self.syncnet_checkpoint_path.is_file(),
        }

    async def process(
        self,
        video: Path,
        audio: Path,
        output: Path,
        *,
        seed: int | None,
    ) -> LipSyncRunOutput:
        if output.exists():
            raise FileExistsError(f"Refusing to overwrite completed LatentSync output: {output}")
        if not self.info().available:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="lip_sync",
                failure_kind=ProviderFailureKind.INVALID_INPUT,
                backend_code="runtime_unavailable",
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}")
        work = output.parent / f".latentsync-{uuid.uuid4().hex}"
        inference_temp = work / "inference"
        evaluation_temp = work / "evaluation"
        work.mkdir(parents=True)
        self._cleanup_targets = (partial, work)
        args = [
            str(self.python),
            "-m",
            "scripts.inference",
            "--unet_config_path",
            str(self.unet_config_path),
            "--inference_ckpt_path",
            str(self.checkpoint_path),
            "--inference_steps",
            str(self.inference_steps),
            "--guidance_scale",
            str(self.guidance_scale),
            "--video_path",
            str(video),
            "--audio_path",
            str(audio),
            "--video_out_path",
            str(partial),
            "--temp_dir",
            str(inference_temp),
            "--seed",
            str(seed if seed is not None else -1),
        ]
        if self.enable_deepcache:
            args.append("--enable_deepcache")
        try:
            await asyncio.to_thread(
                run,
                args,
                self.timeout_seconds,
                cancel_requested=self.cancel_requested,
                cwd=self.repository_directory,
            )
            self._validate_output(partial)
            metrics = await self._evaluate(partial, evaluation_temp)
            partial.replace(output)
            shutil.rmtree(work, ignore_errors=True)
            self._cleanup_targets = ()
        except MediaCancelled as error:
            self._remove_partial_outputs()
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="lip_sync",
                failure_kind=ProviderFailureKind.CANCELLED,
                backend_code="child_process_cancelled",
            ) from error
        except subprocess.TimeoutExpired as error:
            self._remove_partial_outputs()
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="lip_sync",
                failure_kind=ProviderFailureKind.TIMEOUT,
                retryable=True,
                backend_code="child_process_timeout",
            ) from error
        except MediaCommandError as error:
            self._remove_partial_outputs()
            if error.return_code in self.oom_exit_codes:
                raise ProviderOutOfMemoryError(
                    provider_id=self.provider_id,
                    operation="lip_sync",
                    backend_code=f"exit_code:{error.return_code}",
                ) from error
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="lip_sync",
                backend_code=f"exit_code:{error.return_code}",
            ) from error
        except (MediaError, ValueError) as error:
            self._remove_partial_outputs()
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="lip_sync",
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="invalid_lip_sync_output",
            ) from error
        except BaseException:
            self._remove_partial_outputs()
            raise
        return LipSyncRunOutput(
            provider_id=self.provider_id,
            actual_model="LatentSync-1.5",
            output_path=output,
            sync_confidence=metrics["sync_confidence"],
            av_offset_frames=metrics["av_offset_frames"],
            sync_qa_passed=metrics["sync_confidence"] >= 3
            and abs(metrics["av_offset_frames"]) <= 1,
            warnings=()
            if metrics["sync_confidence"] >= 3 and abs(metrics["av_offset_frames"]) <= 1
            else ("Official SyncNet evidence is outside the production threshold",),
            actual_settings={
                "inference_steps": self.inference_steps,
                "guidance_scale": self.guidance_scale,
                "enable_deepcache": self.enable_deepcache,
                "seed": seed if seed is not None else -1,
            },
        )

    async def cleanup_after_oom(
        self,
        _error: ProviderOutOfMemoryError,
    ) -> ProviderCleanupResult:
        self._remove_partial_outputs()
        return ProviderCleanupResult(
            provider_id=self.provider_id,
            completed=True,
            retry_safe=True,
            action_code="child_process_reaped_and_partial_removed",
        )

    async def _evaluate(self, output: Path, temp_dir: Path) -> dict[str, float | int]:
        evaluation_work = temp_dir.parent / "syncnet-work"
        evaluation_work.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            str(self.repository_directory)
            if not existing
            else f"{self.repository_directory}{os.pathsep}{existing}"
        )
        result = await asyncio.to_thread(
            run,
            [
                str(self.python),
                "-m",
                "eval.eval_sync_conf",
                "--initial_model",
                str(self.syncnet_checkpoint_path),
                "--video_path",
                str(output),
                "--temp_dir",
                str(temp_dir),
            ],
            self.timeout_seconds,
            cancel_requested=self.cancel_requested,
            cwd=evaluation_work,
            env=environment,
        )
        confidence = _CONFIDENCE.search(result.stdout)
        offset = _OFFSET.search(result.stdout)
        if confidence is None or offset is None:
            raise ValueError("LatentSync evaluator returned no machine-readable metrics")
        return {
            "sync_confidence": float(confidence.group(1)),
            "av_offset_frames": int(offset.group(1)),
        }

    def _validate_output(self, output: Path) -> None:
        if not output.is_file():
            raise ValueError("LatentSync produced no output")
        facts = inspect_frame_timing(output, cancel_requested=self.cancel_requested)
        if facts["decoded_frame_count"] < 2 or facts["duration_seconds"] <= 0:
            raise ValueError("LatentSync produced an empty video stream")
        media = probe(output, cancel_requested=self.cancel_requested)
        streams = media.get("streams", [])
        if not isinstance(streams, list) or not any(
            isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams
        ):
            raise ValueError("LatentSync output has no audio stream")

    def _remove_partial_outputs(self) -> None:
        for target in self._cleanup_targets:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        self._cleanup_targets = ()


__all__ = ["LatentSyncCliProvider", "LipSyncRunOutput"]
