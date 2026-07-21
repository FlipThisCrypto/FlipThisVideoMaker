import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
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


class GPUTelemetrySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    available: bool
    physical_gpu: int = Field(ge=0)
    started_at: datetime
    elapsed_seconds: float = Field(ge=0)
    sample_interval_seconds: float = Field(gt=0)
    sample_count: int = Field(ge=0)
    failed_sample_count: int = Field(ge=0)
    observed_mean_period_seconds: float | None = Field(default=None, ge=0)
    sampling_coverage_ratio: float = Field(ge=0, le=1)
    memory_total_mb: int | None = Field(default=None, ge=0)
    baseline_memory_used_mb: int | None = Field(default=None, ge=0)
    peak_memory_used_mb: int | None = Field(default=None, ge=0)
    peak_stage_delta_mb: int | None = Field(default=None, ge=0)
    peak_utilization_percent: int | None = Field(default=None, ge=0)
    peak_temperature_c: int | None = None
    error_code: str | None = None
    stages: dict[str, dict[str, int]] = Field(default_factory=dict)


class GPUTelemetryRecorder:
    """Sample one physical GPU without pooling or remapping device memory."""

    def __init__(
        self,
        physical_gpu: int,
        *,
        sample_interval_seconds: float = 0.1,
        probe: Callable[[], GPUProbeResult] | None = None,
    ) -> None:
        if physical_gpu < 0 or sample_interval_seconds < 0.05:
            raise ValueError("Telemetry requires a physical GPU and interval of at least 0.05 s")
        self.physical_gpu = physical_gpu
        self.sample_interval_seconds = sample_interval_seconds
        self._probe = probe or probe_gpus
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at: datetime | None = None
        self._started_monotonic: float | None = None
        self._stopped_monotonic: float | None = None
        self._samples: list[tuple[str, GPUMetrics]] = []
        self._failed_samples = 0
        self._stage = "initializing"

    def start(self) -> "GPUTelemetryRecorder":
        if self._thread is not None:
            raise RuntimeError("GPU telemetry is already started")
        self._started_at = datetime.now(UTC)
        self._started_monotonic = time.monotonic()
        self.sample_now()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        return self

    def sample_now(self) -> None:
        probe = self._probe()
        metric = next(
            (item for item in probe.metrics if item.index == self.physical_gpu),
            None,
        )
        with self._lock:
            if probe.error is not None or metric is None:
                self._failed_samples += 1
            else:
                self._samples.append((self._stage, metric))

    def set_stage(self, stage: str) -> None:
        if not stage or len(stage) > 80:
            raise ValueError("Telemetry stage must be a non-empty bounded label")
        with self._lock:
            self._stage = stage

    def stop(self) -> GPUTelemetrySnapshot:
        if self._thread is None:
            raise RuntimeError("GPU telemetry was not started")
        self._stop.set()
        self._thread.join(timeout=12)
        if self._thread.is_alive():
            with self._lock:
                self._failed_samples += 1
        self._stopped_monotonic = time.monotonic()
        return self.snapshot()

    def snapshot(self) -> GPUTelemetrySnapshot:
        if self._started_at is None or self._started_monotonic is None:
            raise RuntimeError("GPU telemetry was not started")
        ended = self._stopped_monotonic or time.monotonic()
        with self._lock:
            samples = tuple(self._samples)
            failures = self._failed_samples
        elapsed = max(0, ended - self._started_monotonic)
        attempts = len(samples) + failures
        expected_attempts = max(1, int(elapsed / self.sample_interval_seconds) + 1)
        coverage = min(1.0, attempts / expected_attempts)
        mean_period = elapsed / (attempts - 1) if attempts > 1 else None
        if not samples:
            return GPUTelemetrySnapshot(
                available=False,
                physical_gpu=self.physical_gpu,
                started_at=self._started_at,
                elapsed_seconds=elapsed,
                sample_interval_seconds=self.sample_interval_seconds,
                sample_count=0,
                failed_sample_count=failures,
                observed_mean_period_seconds=mean_period,
                sampling_coverage_ratio=coverage,
                error_code="gpu_probe_failed_or_device_missing",
            )
        metrics = tuple(item for _stage, item in samples)
        baseline = metrics[0].memory_used_mb
        peak = max(item.memory_used_mb for item in metrics)
        stages: dict[str, dict[str, int]] = {}
        for stage in dict.fromkeys(stage for stage, _item in samples):
            stage_metrics = [item for sample_stage, item in samples if sample_stage == stage]
            stages[stage] = {
                "sample_count": len(stage_metrics),
                "peak_memory_used_mb": max(item.memory_used_mb for item in stage_metrics),
                "peak_utilization_percent": max(item.utilization_percent for item in stage_metrics),
                "peak_temperature_c": max(item.temperature_c for item in stage_metrics),
            }
        return GPUTelemetrySnapshot(
            available=True,
            physical_gpu=self.physical_gpu,
            started_at=self._started_at,
            elapsed_seconds=elapsed,
            sample_interval_seconds=self.sample_interval_seconds,
            sample_count=len(samples),
            failed_sample_count=failures,
            observed_mean_period_seconds=mean_period,
            sampling_coverage_ratio=coverage,
            memory_total_mb=metrics[0].memory_total_mb,
            baseline_memory_used_mb=baseline,
            peak_memory_used_mb=peak,
            peak_stage_delta_mb=max(0, peak - baseline),
            peak_utilization_percent=max(item.utilization_percent for item in metrics),
            peak_temperature_c=max(item.temperature_c for item in metrics),
            stages=stages,
        )

    def _sample_loop(self) -> None:
        while not self._stop.wait(self.sample_interval_seconds):
            self.sample_now()


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
