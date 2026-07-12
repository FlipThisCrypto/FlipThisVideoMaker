import json
import subprocess
from pathlib import Path
from typing import Any

from flipthis_video_maker.config.settings import get_settings


class MediaError(RuntimeError):
    pass


def run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise MediaError(
            f"Command failed ({result.returncode}): {' '.join(args[:3])}\n{result.stderr[-2000:]}"
        )
    return result


def probe(path: Path) -> dict[str, Any]:
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
        ]
    )
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise MediaError(f"ffprobe returned invalid JSON for {path}")
    return data


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def extract_frame(video: Path, output: Path, *, last: bool = False) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [get_settings().ffmpeg_path, "-y", "-v", "error", "-i", str(video)]
    if last:
        args += ["-vf", "reverse"]
    args += ["-frames:v", "1", str(output)]
    run(args)
    return output


def checksum(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
