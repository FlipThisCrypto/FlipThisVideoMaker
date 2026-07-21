#!/usr/bin/env python3
"""Run the exercised RIFE adapter concurrently on physical GPUs 0 and 1."""

import argparse
import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from math import ceil
from pathlib import Path
from typing import Any

from flipthis_video_maker.media.video_delivery import inspect_frame_timing
from flipthis_video_maker.providers.rife.cli import RifeCliInterpolationProvider
from flipthis_video_maker.scheduler.gpu import GPUTelemetryRecorder


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime", type=Path)
    parser.add_argument("input_video", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--worker-gpu", type=int, choices=(0, 1), help=argparse.SUPPRESS)
    return parser


def _validate_paths(runtime: Path, input_video: Path, output_directory: Path) -> None:
    if not all(path.is_absolute() for path in (runtime, input_video, output_directory)):
        raise ValueError("Runtime, input, and output paths must be absolute")
    if not input_video.is_file() or input_video.suffix.lower() != ".mp4":
        raise ValueError("Input must be an existing MP4")
    for path in (
        runtime / ".venv/bin/python",
        runtime / "inference_video.py",
        runtime / "train_log",
    ):
        if not path.exists():
            raise ValueError(f"RIFE runtime component is missing: {path.name}")
    output_directory.mkdir(parents=True, exist_ok=True)
    if any((output_directory / f"rife-gpu{gpu}.mp4").exists() for gpu in (0, 1)):
        raise FileExistsError("Refusing to overwrite a completed dual-GPU probe output")


def _child_environment(gpu: int) -> dict[str, str]:
    allowed = ("PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update(
        {
            "CUDA_VISIBLE_DEVICES": str(gpu),
            "PYTHONNOUSERSITE": "1",
        }
    )
    return environment


def _worker_args(args: argparse.Namespace, gpu: int) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        str(args.runtime),
        str(args.input_video),
        str(args.output_directory),
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--worker-gpu",
        str(gpu),
    ]


def _stop_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_worker_result(payload: object, gpu: int, output_directory: Path) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("gpu") != gpu:
        raise ValueError(f"GPU {gpu} returned invalid probe JSON")
    expected_output = output_directory / f"rife-gpu{gpu}.mp4"
    if payload.get("output") != str(expected_output) or not expected_output.is_file():
        raise ValueError(f"GPU {gpu} returned an invalid output path")
    telemetry = payload.get("telemetry")
    if not isinstance(telemetry, dict) or telemetry.get("physical_gpu") != gpu:
        raise ValueError(f"GPU {gpu} returned mismatched physical-device telemetry")
    return payload


def _overlap_seconds(results: list[dict[str, Any]]) -> float:
    if len(results) != 2:
        raise ValueError("Exactly two GPU results are required")
    starts = [datetime.fromisoformat(result["telemetry"]["started_at"]) for result in results]
    ends = [
        started + timedelta(seconds=float(result["telemetry"]["elapsed_seconds"]))
        for started, result in zip(starts, results, strict=True)
    ]
    return (min(ends) - max(starts)).total_seconds()


def _run_worker(args: argparse.Namespace) -> int:
    gpu = args.worker_gpu
    if gpu not in (0, 1):
        raise ValueError("Worker GPU must be physical device 0 or 1")
    output = args.output_directory / f"rife-gpu{gpu}.mp4"
    provider = RifeCliInterpolationProvider(
        args.runtime / ".venv/bin/python",
        args.runtime / "inference_video.py",
        args.runtime / "train_log",
        provider_id=f"rife-gpu{gpu}",
        timeout_seconds=args.timeout_seconds,
    )
    telemetry = GPUTelemetryRecorder(gpu).start()
    telemetry.set_stage("rife_interpolation")
    started = time.monotonic()
    try:
        asyncio.run(provider.process(args.input_video, output, target_fps=60))
    finally:
        snapshot = telemetry.stop()
    facts = inspect_frame_timing(output)
    source_facts = inspect_frame_timing(args.input_video)
    expected_frames = (source_facts["decoded_frame_count"] - 1) * ceil(
        60 / source_facts["average_frame_rate"]
    ) + 1
    if (
        not facts["constant_frame_rate"]
        or facts["average_frame_rate"] != 60
        or facts["decoded_frame_count"] != expected_frames
        or facts["width"] != source_facts["width"]
        or facts["height"] != source_facts["height"]
    ):
        raise RuntimeError("RIFE probe output failed timing, count, or resolution validation")
    print(
        json.dumps(
            {
                "gpu": gpu,
                "output": str(output),
                "sha256": _sha256(output),
                "wall_seconds": time.monotonic() - started,
                "timing": facts,
                "telemetry": snapshot.model_dump(mode="json"),
            },
            sort_keys=True,
        )
    )
    return 0


def _run_parent(args: argparse.Namespace) -> int:
    processes = {
        gpu: subprocess.Popen(
            _worker_args(args, gpu),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_child_environment(gpu),
            start_new_session=True,
        )
        for gpu in (0, 1)
    }
    deadline = time.monotonic() + args.timeout_seconds
    results: list[dict[str, Any]] = []
    try:
        for gpu, process in processes.items():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, args.timeout_seconds)
            stdout, _stderr = process.communicate(timeout=remaining)
            if process.returncode != 0:
                raise RuntimeError(f"GPU {gpu} probe failed with exit code {process.returncode}")
            results.append(_validate_worker_result(json.loads(stdout), gpu, args.output_directory))
    except BaseException:
        for process in processes.values():
            _stop_group(process)
        raise
    overlap_seconds = _overlap_seconds(results)
    if overlap_seconds <= 0:
        raise RuntimeError("GPU probe processes did not overlap")
    print(
        json.dumps(
            {
                "version": 1,
                "passed": True,
                "overlap_seconds": overlap_seconds,
                "workers": results,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    args = _parser().parse_args()
    _validate_paths(args.runtime, args.input_video, args.output_directory)
    if args.timeout_seconds <= 0:
        raise ValueError("Timeout must be positive")
    return _run_worker(args) if args.worker_gpu is not None else _run_parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
