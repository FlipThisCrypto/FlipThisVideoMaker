import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from flipthis_video_maker.providers.base.models import Capability, ProviderInfo

ProgressCallback = Callable[[str], Awaitable[None]]


class WanGPHeadlessProvider:
    """External WanGP adapter for the documented `wgp.py --process` interface."""

    def __init__(
        self,
        python: Path,
        wgp_script: Path,
        *,
        timeout: float = 7200,
    ) -> None:
        self.python = python
        self.wgp_script = wgp_script
        self.timeout = timeout

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="wangp-headless",
            name="WanGP headless provider",
            model_identity="administrator-selected WanGP model",
            capabilities={
                Capability.IMAGE_GENERATION,
                Capability.VIDEO_GENERATION,
                Capability.FIRST_LAST_FRAME_VIDEO,
            },
            available=self.python.is_file() and self.wgp_script.is_file(),
            supported_inputs={"WanGP queue JSON", "WanGP queue ZIP"},
            notes=(
                "Uses the official external wgp.py --process contract; "
                "model support is discovered by WanGP"
            ),
        )

    async def health(self) -> dict[str, object]:
        return {
            "ok": self.python.is_file() and self.wgp_script.is_file(),
            "python_exists": self.python.is_file(),
            "script_exists": self.wgp_script.is_file(),
        }

    def argv(self, queue_file: Path, output_directory: Path) -> list[str]:
        if queue_file.suffix.lower() not in {".json", ".zip"}:
            raise ValueError("WanGP input must be an exported queue JSON or ZIP")
        return [
            str(self.python),
            str(self.wgp_script),
            "--process",
            str(queue_file),
            "--output-dir",
            str(output_directory),
        ]

    async def process(
        self,
        queue_file: Path,
        output_directory: Path,
        progress: ProgressCallback | None = None,
    ) -> list[Path]:
        if not queue_file.is_file():
            raise FileNotFoundError(queue_file)
        output_directory.mkdir(parents=True, exist_ok=True)
        before = {path.resolve() for path in output_directory.rglob("*") if path.is_file()}
        process = await asyncio.create_subprocess_exec(
            *self.argv(queue_file, output_directory),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except (TimeoutError, asyncio.CancelledError):
            process.terminate()
            await process.wait()
            raise
        if progress is not None and stdout:
            await progress(stdout.decode(errors="replace")[-2000:])
        if process.returncode:
            error = stderr.decode(errors="replace")[-2000:]
            raise RuntimeError(f"WanGP process failed ({process.returncode}): {error}")
        outputs = [
            path
            for path in output_directory.rglob("*")
            if path.is_file() and path.resolve() not in before
        ]
        if not outputs:
            raise RuntimeError("WanGP reported success but produced no new output files")
        return outputs
