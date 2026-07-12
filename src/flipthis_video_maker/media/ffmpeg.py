import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flipthis_video_maker.config.settings import get_settings


class MediaError(RuntimeError):
    pass


class MediaCancelled(MediaError):
    pass


CancelCheck = Callable[[], bool]


def run(
    args: list[str],
    timeout: float = 300,
    *,
    cancel_requested: CancelCheck | None = None,
    poll_interval: float = 0.1,
    terminate_grace_seconds: float = 2,
) -> subprocess.CompletedProcess[str]:
    """Run one media command while retaining ownership of its process lifecycle."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")
    if cancel_requested and cancel_requested():
        raise MediaCancelled(f"Command cancelled before start: {' '.join(args[:3])}")

    process = subprocess.Popen(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    started = time.monotonic()
    try:
        while True:
            if cancel_requested and cancel_requested():
                _stop_process(process, terminate_grace_seconds)
                raise MediaCancelled(f"Command cancelled: {' '.join(args[:3])}")

            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                stdout, stderr = _stop_process(process, terminate_grace_seconds)
                raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr)
            try:
                stdout, stderr = process.communicate(timeout=min(poll_interval, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if cancel_requested and cancel_requested():
            raise MediaCancelled(f"Command cancelled: {' '.join(args[:3])}")
    except BaseException:
        if process.poll() is None:
            _stop_process(process, terminate_grace_seconds)
        raise

    result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    if result.returncode:
        raise MediaError(
            f"Command failed ({result.returncode}): {' '.join(args[:3])}\n{result.stderr[-2000:]}"
        )
    return result


def _stop_process(
    process: subprocess.Popen[str], terminate_grace_seconds: float
) -> tuple[str, str]:
    if process.poll() is None:
        try:
            process.terminate()
        except (ProcessLookupError, OSError):
            pass
    try:
        return process.communicate(timeout=max(0, terminate_grace_seconds))
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass
        return process.communicate()


def probe(path: Path, *, cancel_requested: CancelCheck | None = None) -> dict[str, Any]:
    result = run(
        [
            get_settings().ffprobe_path,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        cancel_requested=cancel_requested,
    )
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise MediaError(f"ffprobe returned invalid JSON for {path}")
    return data


def duration(path: Path, *, cancel_requested: CancelCheck | None = None) -> float:
    return float(probe(path, cancel_requested=cancel_requested)["format"]["duration"])


def extract_frame(
    video: Path,
    output: Path,
    *,
    last: bool = False,
    cancel_requested: CancelCheck | None = None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [get_settings().ffmpeg_path, "-y", "-v", "error", "-i", str(video)]
    if last:
        args += ["-vf", "reverse"]
    args += ["-frames:v", "1", str(output)]
    run(args, cancel_requested=cancel_requested)
    return output


def checksum(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
