import asyncio
import hashlib
import math
import shutil
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from flipthis_video_maker.config.settings import get_settings
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

MODEL_FILES = {
    "flownet.pkl": "6615790efd627772917205db291f51cd392528a157ecbb2ecaeec3bff8eb6de2",
    "IFNet_HDv3.py": "655b4c772b037967b86c2dd31c8fa3b5323b79dd9a0e0088708d89149bbc8a32",
    "RIFE_HDv3.py": "81bbd0648e499de79e44768d284005d9d57d0f6eb7c30adae407f22675055730",
    "refine.py": "0c5698b4a05b9f6ab551740575c1c35e248e5b1829bab6445186081ebe15f032",
}


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
        model_verified = info.available and _model_manifest_verified(self.model_directory)
        runtime_verified = False
        cuda_available = False
        if info.available and model_verified:
            try:
                completed = await asyncio.to_thread(
                    run,
                    [
                        str(self.python),
                        "-c",
                        (
                            "import cv2,numpy,skvideo.io,torch;"
                            "print('cuda=' + str(torch.cuda.is_available()).lower())"
                        ),
                    ],
                    30,
                    cwd=self.script.parent,
                )
                runtime_verified = True
                cuda_available = "cuda=true" in completed.stdout
            except (MediaError, subprocess.TimeoutExpired, OSError):
                pass
        healthy = info.available and model_verified and runtime_verified and cuda_available
        return {
            "ok": healthy,
            "status": "ready" if healthy else "missing_or_invalid_external_runtime",
            "python": shutil.which(str(self.python)) is not None or self.python.is_file(),
            "script": self.script.is_file(),
            "model_directory": self.model_directory.is_dir(),
            "model_verified": model_verified,
            "runtime_verified": runtime_verified,
            "cuda_available": cuda_available,
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
        try:
            input_facts = inspect_frame_timing(video, cancel_requested=self.cancel_requested)
        except MediaCancelled as error:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="inspect_interpolation_input",
                failure_kind=ProviderFailureKind.CANCELLED,
                backend_code="input_inspection_cancelled",
            ) from error
        except subprocess.TimeoutExpired as error:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="inspect_interpolation_input",
                failure_kind=ProviderFailureKind.TIMEOUT,
                retryable=True,
                backend_code="input_inspection_timeout",
            ) from error
        except (MediaError, ValueError) as error:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="inspect_interpolation_input",
                failure_kind=ProviderFailureKind.INVALID_INPUT,
                backend_code="invalid_native_video",
            ) from error
        native_fps = input_facts["average_frame_rate"]
        if native_fps <= 0 or target_fps <= native_fps:
            raise ValueError("RIFE target FPS must be greater than the measured native FPS")
        multiplier = math.ceil(target_fps / native_fps)
        if multiplier < 2 or multiplier > 16:
            raise ValueError("RIFE interpolation multiplier must be between 2 and 16")
        minimum_frames = (
            math.ceil((input_facts["decoded_frame_count"] - 1) * target_fps / native_fps) + 1
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        partial = output.with_name(f".{output.stem}-{token}.partial{output.suffix}")
        workspace = output.parent / f".rife-work-{token}"
        workspace.mkdir()
        frame_directory = workspace / "vid_out"
        args = [
            str(self.python),
            str(self.script),
            "--video",
            str(video),
            "--model",
            str(self.model_directory),
            "--fps",
            str(target_fps),
            "--multi",
            str(multiplier),
            "--png",
        ]
        try:
            await asyncio.to_thread(
                run,
                args,
                self.timeout_seconds,
                cancel_requested=self.cancel_requested,
                cwd=workspace,
            )
            frame_paths = sorted(frame_directory.glob("*.png"))
            if len(frame_paths) < minimum_frames or any(
                path.name != f"{index:07d}.png" for index, path in enumerate(frame_paths)
            ):
                raise ProviderExecutionError(
                    provider_id=self.provider_id,
                    operation="interpolate",
                    failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                    backend_code="missing_or_noncontiguous_png_frames",
                )
            # Practical-RIFE's PNG route converts every decoded source frame through RGB.
            # Splice only its interior frames between boundaries decoded directly from the
            # immutable native video. Keeping those boundaries in FFmpeg's YUV domain avoids
            # an otherwise measurable second colour conversion at conditioned endpoints.
            last_native_frame = input_facts["decoded_frame_count"] - 1
            last_interpolated_frame = len(frame_paths) - 1
            filter_complex = (
                "[0:v]select=eq(n\\,0),"
                f"setpts=N/({target_fps}*TB)[first];"
                f"[1:v]select=between(n\\,1\\,{last_interpolated_frame - 1}),"
                f"setpts=N/({target_fps}*TB)[middle];"
                f"[0:v]select=eq(n\\,{last_native_frame}),"
                f"setpts=N/({target_fps}*TB)[last];"
                "[first][middle][last]concat=n=3:v=1:a=0[outv]"
            )
            await asyncio.to_thread(
                run,
                [
                    get_settings().ffmpeg_path,
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(video),
                    "-framerate",
                    str(target_fps),
                    "-start_number",
                    "0",
                    "-i",
                    str(frame_directory / "%07d.png"),
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[outv]",
                    "-frames:v",
                    str(len(frame_paths)),
                    "-r",
                    str(target_fps),
                    "-fps_mode",
                    "cfr",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "8",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    "-an",
                    str(partial),
                ],
                self.timeout_seconds,
                cancel_requested=self.cancel_requested,
            )
            facts = inspect_frame_timing(
                partial,
                cancel_requested=self.cancel_requested,
            )
            if (
                abs(facts["average_frame_rate"] - target_fps) > 0.05
                or not facts["constant_frame_rate"]
                or facts["decoded_frame_count"] < minimum_frames
                or facts["width"] != input_facts["width"]
                or facts["height"] != input_facts["height"]
            ):
                raise ProviderExecutionError(
                    provider_id=self.provider_id,
                    operation="interpolate",
                    failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                    backend_code="invalid_interpolated_timing_or_resolution",
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
        finally:
            shutil.rmtree(workspace)
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_manifest_verified(model_directory: Path) -> bool:
    try:
        return all(
            (model_directory / filename).is_file()
            and _sha256(model_directory / filename) == checksum
            for filename, checksum in MODEL_FILES.items()
        )
    except OSError:
        return False


__all__ = ["RifeCliInterpolationProvider"]
