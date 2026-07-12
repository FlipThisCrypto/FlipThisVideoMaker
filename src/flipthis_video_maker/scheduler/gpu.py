import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GPUMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    name: str = Field(min_length=1)
    temperature_c: int
    utilization_percent: int = Field(ge=0)
    memory_total_mb: int = Field(ge=0)
    memory_used_mb: int = Field(ge=0)
    memory_free_mb: int = Field(ge=0)


class GPUProbeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    metrics: tuple[GPUMetrics, ...] = ()
    error: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "GPUProbeResult":
        if self.error is not None and self.metrics:
            raise ValueError("A failed GPU probe cannot contain metrics")
        indexes = [metric.index for metric in self.metrics]
        if len(indexes) != len(set(indexes)):
            raise ValueError("GPU probe returned duplicate physical indexes")
        return self


AdmissionReason = Literal[
    "admitted",
    "probe_failed",
    "physical_gpu_not_found",
    "insufficient_free_vram",
]


class VRAMAdmissionDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    admitted: bool
    physical_gpu: int = Field(ge=0)
    minimum_free_vram_mb: int = Field(ge=0)
    observed_free_vram_mb: int | None = Field(default=None, ge=0)
    reason: AdmissionReason
    detail: str = ""


def probe_gpus() -> GPUProbeResult:
    """Strictly query and validate metrics for every visible physical NVIDIA GPU."""
    if not shutil.which("nvidia-smi"):
        return GPUProbeResult(error="nvidia-smi is unavailable")

    query = "index,name,temperature.gpu,utilization.gpu,memory.total,memory.used,memory.free"
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return GPUProbeResult(error="nvidia-smi timed out")
    except OSError as error:
        return GPUProbeResult(error=f"nvidia-smi could not be executed: {type(error).__name__}")

    if result.returncode:
        detail = result.stderr.strip()[-2000:]
        suffix = f": {detail}" if detail else ""
        return GPUProbeResult(error=f"nvidia-smi failed with exit code {result.returncode}{suffix}")

    try:
        metrics = tuple(
            _parse_metrics_line(line) for line in result.stdout.splitlines() if line.strip()
        )
        if not metrics:
            raise ValueError("nvidia-smi returned no GPU records")
        return GPUProbeResult(metrics=metrics)
    except ValueError as error:
        return GPUProbeResult(error=f"Invalid nvidia-smi output: {error}")


def evaluate_vram_admission(
    probe: GPUProbeResult,
    *,
    physical_gpu: int,
    minimum_free_vram_mb: int,
) -> VRAMAdmissionDecision:
    """Evaluate one physical GPU without borrowing or summing memory from another device."""
    if physical_gpu < 0:
        raise ValueError("physical_gpu must be non-negative")
    if minimum_free_vram_mb < 0:
        raise ValueError("minimum_free_vram_mb must be non-negative")

    if probe.error is not None:
        return VRAMAdmissionDecision(
            admitted=False,
            physical_gpu=physical_gpu,
            minimum_free_vram_mb=minimum_free_vram_mb,
            reason="probe_failed",
            detail=probe.error,
        )

    metric = next((item for item in probe.metrics if item.index == physical_gpu), None)
    if metric is None:
        return VRAMAdmissionDecision(
            admitted=False,
            physical_gpu=physical_gpu,
            minimum_free_vram_mb=minimum_free_vram_mb,
            reason="physical_gpu_not_found",
            detail=f"Physical GPU {physical_gpu} was not reported by nvidia-smi",
        )

    admitted = metric.memory_free_mb >= minimum_free_vram_mb
    return VRAMAdmissionDecision(
        admitted=admitted,
        physical_gpu=physical_gpu,
        minimum_free_vram_mb=minimum_free_vram_mb,
        observed_free_vram_mb=metric.memory_free_mb,
        reason="admitted" if admitted else "insufficient_free_vram",
        detail=(
            "Minimum free VRAM is available"
            if admitted
            else (
                f"Physical GPU {physical_gpu} has {metric.memory_free_mb} MB free; "
                f"{minimum_free_vram_mb} MB is required"
            )
        ),
    )


def discover_gpus() -> list[dict[str, Any]]:
    """Return the existing JSON-compatible discovery shape, or an empty list on probe failure."""
    probe = probe_gpus()
    return [metric.model_dump() for metric in probe.metrics] if probe.error is None else []


def _parse_metrics_line(line: str) -> GPUMetrics:
    values = [value.strip() for value in line.split(",")]
    if len(values) != 7:
        raise ValueError(f"expected 7 columns, received {len(values)}")
    return GPUMetrics(
        index=int(values[0]),
        name=values[1],
        temperature_c=int(values[2]),
        utilization_percent=int(values[3]),
        memory_total_mb=int(values[4]),
        memory_used_mb=int(values[5]),
        memory_free_mb=int(values[6]),
    )


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
