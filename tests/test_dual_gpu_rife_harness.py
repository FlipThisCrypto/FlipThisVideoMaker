import argparse
import importlib.util
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest


def _module() -> ModuleType:
    path = Path("scripts/verify-dual-gpu-rife.py").resolve()
    spec = importlib.util.spec_from_file_location("dual_gpu_rife_harness", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime(tmp_path: Path) -> tuple[Path, Path, Path]:
    runtime = tmp_path / "runtime"
    (runtime / ".venv/bin").mkdir(parents=True)
    (runtime / ".venv/bin/python").touch()
    (runtime / "inference_video.py").touch()
    (runtime / "train_log").mkdir()
    source = tmp_path / "native.mp4"
    source.touch()
    output = tmp_path / "output"
    return runtime, source, output


def test_dual_gpu_harness_requires_absolute_valid_non_overwriting_paths(
    tmp_path: Path,
) -> None:
    harness = _module()
    runtime, source, output = _runtime(tmp_path)

    harness._validate_paths(runtime, source, output)
    assert output.is_dir()
    (output / "rife-gpu0.mp4").touch()

    with pytest.raises(FileExistsError, match="overwrite"):
        harness._validate_paths(runtime, source, output)
    with pytest.raises(ValueError, match="absolute"):
        harness._validate_paths(Path("runtime"), source, output)


def test_dual_gpu_child_environment_is_minimal_and_device_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _module()
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("PRIVATE_API_TOKEN", "must-not-reach-child")

    environment = harness._child_environment(1)

    assert environment["CUDA_VISIBLE_DEVICES"] == "1"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PATH"] == "/usr/bin"
    assert "PRIVATE_API_TOKEN" not in environment


def test_dual_gpu_worker_argv_uses_distinct_physical_assignment(tmp_path: Path) -> None:
    harness = _module()
    runtime, source, output = _runtime(tmp_path)
    args = argparse.Namespace(
        runtime=runtime,
        input_video=source,
        output_directory=output,
        timeout_seconds=123.0,
    )

    gpu0 = harness._worker_args(args, 0)
    gpu1 = harness._worker_args(args, 1)

    assert gpu0[:-1] == gpu1[:-1]
    assert gpu0[-2:] == ["--worker-gpu", "0"]
    assert gpu1[-2:] == ["--worker-gpu", "1"]
    assert "123.0" in gpu0


def test_dual_gpu_worker_result_rejects_wrong_device_or_output(tmp_path: Path) -> None:
    harness = _module()
    output = tmp_path / "output"
    output.mkdir()
    expected = output / "rife-gpu0.mp4"
    expected.touch()
    valid = {
        "gpu": 0,
        "output": str(expected),
        "telemetry": {"physical_gpu": 0},
    }

    assert harness._validate_worker_result(valid, 0, output) == valid
    with pytest.raises(ValueError, match="physical-device"):
        harness._validate_worker_result({**valid, "telemetry": {"physical_gpu": 1}}, 0, output)
    with pytest.raises(ValueError, match="output path"):
        harness._validate_worker_result({**valid, "output": str(tmp_path / "other.mp4")}, 0, output)


def test_dual_gpu_overlap_requires_simultaneous_two_worker_windows() -> None:
    harness = _module()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    first = {"telemetry": {"started_at": start.isoformat(), "elapsed_seconds": 10}}
    second = {
        "telemetry": {
            "started_at": start.replace(second=5).isoformat(),
            "elapsed_seconds": 10,
        }
    }

    assert harness._overlap_seconds([first, second]) == 5
    with pytest.raises(ValueError, match="Exactly two"):
        harness._overlap_seconds([first])


def test_dual_gpu_stop_group_escalates_after_term_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _module()

    class FakeProcess:
        pid = 4321
        waits = 0

        def poll(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            self.waits += 1
            if timeout is not None:
                raise subprocess.TimeoutExpired("probe", timeout)
            return 0

    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(harness.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    harness._stop_group(FakeProcess())

    assert signals == [(4321, signal.SIGTERM), (4321, signal.SIGKILL)]
