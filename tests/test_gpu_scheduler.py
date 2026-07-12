import subprocess

import pytest

from flipthis_video_maker.scheduler import gpu


def successful_probe(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["nvidia-smi"],
        returncode=0,
        stdout=stdout,
        stderr="",
    )


def install_probe(
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess[str],
) -> None:
    monkeypatch.setattr(gpu.shutil, "which", lambda _command: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(gpu.subprocess, "run", lambda *_args, **_kwargs: result)


def test_probe_and_public_discovery_keep_two_physical_gpus_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_probe(
        monkeypatch,
        successful_probe(
            "0, NVIDIA GeForce RTX 4070, 43, 4, 12282, 717, 11159\n"
            "1, NVIDIA GeForce RTX 4070, 41, 0, 12282, 18, 11858\n"
        ),
    )

    probe = gpu.probe_gpus()

    assert probe.error is None
    assert [metric.index for metric in probe.metrics] == [0, 1]
    assert [metric.memory_free_mb for metric in probe.metrics] == [11159, 11858]
    assert gpu.discover_gpus() == [
        {
            "index": 0,
            "name": "NVIDIA GeForce RTX 4070",
            "temperature_c": 43,
            "utilization_percent": 4,
            "memory_total_mb": 12282,
            "memory_used_mb": 717,
            "memory_free_mb": 11159,
        },
        {
            "index": 1,
            "name": "NVIDIA GeForce RTX 4070",
            "temperature_c": 41,
            "utilization_percent": 0,
            "memory_total_mb": 12282,
            "memory_used_mb": 18,
            "memory_free_mb": 11858,
        },
    ]


def test_admission_uses_only_the_requested_physical_gpu() -> None:
    probe = gpu.GPUProbeResult(
        metrics=(
            gpu.GPUMetrics(
                index=0,
                name="GPU 0",
                temperature_c=40,
                utilization_percent=0,
                memory_total_mb=12000,
                memory_used_mb=10500,
                memory_free_mb=1500,
            ),
            gpu.GPUMetrics(
                index=1,
                name="GPU 1",
                temperature_c=40,
                utilization_percent=0,
                memory_total_mb=12000,
                memory_used_mb=3000,
                memory_free_mb=9000,
            ),
        )
    )

    gpu0 = gpu.evaluate_vram_admission(probe, physical_gpu=0, minimum_free_vram_mb=2000)
    gpu1 = gpu.evaluate_vram_admission(probe, physical_gpu=1, minimum_free_vram_mb=2000)

    assert not gpu0.admitted
    assert gpu0.observed_free_vram_mb == 1500
    assert gpu0.reason == "insufficient_free_vram"
    assert gpu1.admitted
    assert gpu1.observed_free_vram_mb == 9000
    assert gpu1.reason == "admitted"


def test_admission_does_not_sum_free_vram_across_devices() -> None:
    probe = gpu.GPUProbeResult(
        metrics=tuple(
            gpu.GPUMetrics(
                index=index,
                name=f"GPU {index}",
                temperature_c=40,
                utilization_percent=0,
                memory_total_mb=12000,
                memory_used_mb=10800,
                memory_free_mb=1200,
            )
            for index in (0, 1)
        )
    )

    decisions = [
        gpu.evaluate_vram_admission(probe, physical_gpu=index, minimum_free_vram_mb=2000)
        for index in (0, 1)
    ]

    assert all(not decision.admitted for decision in decisions)
    assert [decision.observed_free_vram_mb for decision in decisions] == [1200, 1200]


def test_admission_accepts_exact_threshold() -> None:
    probe = gpu.GPUProbeResult(
        metrics=(
            gpu.GPUMetrics(
                index=1,
                name="GPU 1",
                temperature_c=40,
                utilization_percent=0,
                memory_total_mb=12000,
                memory_used_mb=10000,
                memory_free_mb=2000,
            ),
        )
    )

    decision = gpu.evaluate_vram_admission(probe, physical_gpu=1, minimum_free_vram_mb=2000)

    assert decision.admitted
    assert decision.reason == "admitted"


def test_missing_nvidia_smi_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu.shutil, "which", lambda _command: None)

    probe = gpu.probe_gpus()
    decision = gpu.evaluate_vram_admission(probe, physical_gpu=0, minimum_free_vram_mb=2000)

    assert probe.metrics == ()
    assert probe.error == "nvidia-smi is unavailable"
    assert not decision.admitted
    assert decision.reason == "probe_failed"
    assert gpu.discover_gpus() == []


def test_failing_nvidia_smi_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    install_probe(
        monkeypatch,
        subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=9,
            stdout="",
            stderr="driver unavailable",
        ),
    )

    probe = gpu.probe_gpus()
    decision = gpu.evaluate_vram_admission(probe, physical_gpu=1, minimum_free_vram_mb=2000)

    assert probe.metrics == ()
    assert probe.error is not None
    assert "exit code 9" in probe.error
    assert not decision.admitted
    assert decision.reason == "probe_failed"


@pytest.mark.parametrize(
    "stdout",
    [
        "0, GPU 0, 40, 0, 12000, 1000\n",
        "0, GPU 0, unknown, 0, 12000, 1000, 11000\n",
        "0, GPU 0, 40, 0, 12000, 1000, 11000\n0, Duplicate GPU 0, 41, 1, 12000, 2000, 10000\n",
    ],
)
def test_malformed_probe_output_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    install_probe(monkeypatch, successful_probe(stdout))

    probe = gpu.probe_gpus()

    assert probe.metrics == ()
    assert probe.error is not None
    assert "Invalid nvidia-smi output" in probe.error
    assert gpu.discover_gpus() == []


def test_missing_physical_gpu_fails_closed() -> None:
    probe = gpu.GPUProbeResult(
        metrics=(
            gpu.GPUMetrics(
                index=0,
                name="GPU 0",
                temperature_c=40,
                utilization_percent=0,
                memory_total_mb=12000,
                memory_used_mb=1000,
                memory_free_mb=11000,
            ),
        )
    )

    decision = gpu.evaluate_vram_admission(probe, physical_gpu=1, minimum_free_vram_mb=2000)

    assert not decision.admitted
    assert decision.observed_free_vram_mb is None
    assert decision.reason == "physical_gpu_not_found"
