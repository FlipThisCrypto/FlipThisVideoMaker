import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def discover_gpus() -> list[dict[str, Any]]:
    if not shutil.which("nvidia-smi"):
        return []
    query = "index,name,temperature.gpu,utilization.gpu,memory.total,memory.used,memory.free"
    result = subprocess.run(
        ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        return []
    keys = [
        "index",
        "name",
        "temperature_c",
        "utilization_percent",
        "memory_total_mb",
        "memory_used_mb",
        "memory_free_mb",
    ]
    return [
        {
            key: (int(value.strip()) if key != "name" else value.strip())
            for key, value in zip(keys, line.split(","), strict=True)
        }
        for line in result.stdout.splitlines()
        if line.strip()
    ]


class GPULock:
    def __init__(self, gpu: str, directory: Path = Path("data/gpu-locks")) -> None:
        self.path = directory / f"gpu-{gpu}.lock"
        self.handle: Any = None

    def __enter__(self) -> "GPULock":
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("w")
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        self.handle.write(json.dumps({"gpu": self.path.stem}))
        self.handle.flush()
        return self

    def __exit__(self, *_args: object) -> None:
        import fcntl

        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()
