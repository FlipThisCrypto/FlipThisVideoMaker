#!/usr/bin/env python3
"""Exercise the local Wan/RIFE/QA stack with a pinned open live-action corpus."""

import argparse
import asyncio
import hashlib
import json
import signal
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from flipthis_video_maker.contracts.video_generation import (
    FirstLastFrameGenerationRequest,
    InterpolationMode,
)
from flipthis_video_maker.media.ffmpeg import run
from flipthis_video_maker.media.video_delivery import (
    INSPECTION_FRAME_INDEXES,
    image_similarity,
    inspect_delivery_contract,
    inspect_frame_timing,
    normalize_interpolated_delivery,
    write_qa_report,
)
from flipthis_video_maker.providers.base.video_generation import (
    ResolvedFirstLastFrameRequest,
)
from flipthis_video_maker.providers.comfyui.wan_flf import (
    MODEL_IDENTITY,
    ComfyUIWanFirstLastFrameProvider,
)
from flipthis_video_maker.providers.lpips.cli import LpipsCliMetricProvider
from flipthis_video_maker.providers.rife.cli import RifeCliInterpolationProvider
from flipthis_video_maker.scheduler.gpu import GPUTelemetryRecorder

CORPUS_URL = "https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov"
CORPUS_SHA256 = "efa9062d9cdb7a338e40ad530dfdf234806743f29ae6a1a136b97ece4e588e8f"
CORPUS_LICENSE = "Creative Commons Attribution 3.0"
CORPUS_ATTRIBUTION = "Tears of Steel — (CC) Blender Foundation | mango.blender.org"
START_SECONDS = 120.5
END_SECONDS = 130.5
WIDTH = 848
HEIGHT = 480


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_video", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8189")
    parser.add_argument(
        "--workflow",
        type=Path,
        default=Path("config/comfyui-workflows/wan2.2-flf-api-v1.json").resolve(),
    )
    parser.add_argument("--rife-runtime", type=Path, required=True)
    parser.add_argument("--lpips-runtime", type=Path)
    parser.add_argument("--physical-gpu", type=int, choices=(0, 1), default=1)
    parser.add_argument("--seed", type=int, default=43121)
    parser.add_argument("--timeout-seconds", type=float, default=7200)
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate(args: argparse.Namespace) -> None:
    paths = [args.source_video, args.output_directory, args.workflow, args.rife_runtime]
    if args.lpips_runtime is not None:
        paths.append(args.lpips_runtime)
    if not all(path.is_absolute() for path in paths):
        raise ValueError("All filesystem paths must be absolute")
    if args.timeout_seconds <= 0 or args.seed < 0 or args.seed > 4_294_967_295:
        raise ValueError("Timeout and seed are outside their valid ranges")
    if not args.source_video.is_file() or _sha256(args.source_video) != CORPUS_SHA256:
        raise ValueError("Source is not the pinned Tears of Steel 720p corpus")
    if not args.workflow.is_file():
        raise ValueError("Wan workflow is missing")
    for path in (
        args.rife_runtime / ".venv/bin/python",
        args.rife_runtime / "inference_video.py",
        args.rife_runtime / "train_log",
    ):
        if not path.exists():
            raise ValueError(f"RIFE runtime component is missing: {path.name}")
    if args.lpips_runtime is not None:
        for path in (
            args.lpips_runtime / ".venv/bin/python",
            args.lpips_runtime / "cache",
        ):
            if not path.exists():
                raise ValueError(f"LPIPS runtime component is missing: {path.name}")
    if args.output_directory.exists():
        raise FileExistsError("Refusing to overwrite an acceptance output directory")


def _extract_frame(source: Path, output: Path, seconds: float) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            str(seconds),
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT}",
            str(output),
        ]
    )
    if not output.is_file():
        raise RuntimeError("FFmpeg did not extract a corpus frame")


def _request(args: argparse.Namespace) -> FirstLastFrameGenerationRequest:
    provider_id = f"wan22-flf-gpu{args.physical_gpu}"
    return FirstLastFrameGenerationRequest(
        provider_id=provider_id,
        provider_model=MODEL_IDENTITY,
        start_frame_asset_id="open-corpus-start",
        target_end_frame_asset_id="open-corpus-end",
        prompt=(
            "A continuous locked-camera cinematic shot. The older grey-haired man naturally "
            "wakes, breathes, shifts his arms and torso, and sits upright. The armed man in the "
            "background remains alert with subtle natural body motion. Clothing, hair, candle "
            "flames, and atmosphere move coherently. Preserve both men's identity, anatomy, "
            "wardrobe, lighting, architecture, and spatial layout throughout."
        ),
        negative_prompt=(
            "cut, edit, transition, crossfade, dissolve, morph, duplicate person, extra person, "
            "teleportation, sliding body, warped face, deformed hands, frozen image, camera shake"
        ),
        duration_seconds=10,
        native_requested_fps=8,
        delivery_fps=60,
        width=WIDTH,
        height=HEIGHT,
        aspect_ratio="16:9",
        seed=args.seed,
        camera_direction="locked tripod; no pan, tilt, zoom, dolly, or cut",
        interpolation_mode=InterpolationMode.RIFE,
        interpolation_provider_id="rife-local",
        provider_settings={
            provider_id: {
                "steps": 20,
                "cfg": 4.0,
                "shift": 8.0,
                "high_noise_end_step": 10,
            }
        },
    )


def _atomic_json(payload: dict[str, Any], output: Path) -> None:
    partial = output.with_name(f".{output.name}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        partial.replace(output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _reference_trajectory(
    source: Path, evidence: Path, generated_evidence: Path
) -> dict[str, object]:
    comparisons: dict[str, object] = {}
    for frame_index in INSPECTION_FRAME_INDEXES:
        reference = evidence / f"reference-{frame_index:04d}.png"
        seconds = START_SECONDS + frame_index / 60
        _extract_frame(source, reference, seconds)
        generated = generated_evidence / f"frame-{frame_index:04d}.png"
        comparisons[str(frame_index)] = image_similarity(reference, generated)
    return {
        "reference_is_ground_truth_motion_not_a_generation_requirement": True,
        "frames": comparisons,
    }


def _native_contact_sheet(native: Path, output: Path) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(native),
            "-vf",
            "scale=212:120,tile=9x9",
            "-frames:v",
            "1",
            str(output),
        ]
    )
    if not output.is_file():
        raise RuntimeError("FFmpeg did not create the native-frame contact sheet")


async def _execute(args: argparse.Namespace, cancelled: threading.Event) -> dict[str, Any]:
    output = args.output_directory
    output.mkdir(parents=True)
    boundaries = output / "boundaries"
    evidence = output / "evidence"
    reference_evidence = output / "reference-evidence"
    boundaries.mkdir()
    evidence.mkdir()
    reference_evidence.mkdir()
    start = boundaries / "start.png"
    end = boundaries / "end.png"
    _extract_frame(args.source_video, start, START_SECONDS)
    _extract_frame(args.source_video, end, END_SECONDS)

    request = _request(args)
    provider = ComfyUIWanFirstLastFrameProvider(
        request.provider_id,
        endpoint=args.endpoint,
        workflow_template=args.workflow,
        gpu_assignment=f"gpu{args.physical_gpu}",
        timeout_seconds=args.timeout_seconds,
    )
    health = await provider.health()
    if health.get("ok") is not True:
        raise RuntimeError(f"Wan provider is not healthy: {health.get('status', 'unknown')}")

    telemetry = GPUTelemetryRecorder(args.physical_gpu).start()
    started = time.monotonic()
    try:
        telemetry.set_stage("wan_generation")
        native = output / "native.mp4"
        run_output = await provider.generate(
            ResolvedFirstLastFrameRequest(
                snapshot=request,
                start_frame_path=start,
                start_frame_mime_type="image/png",
                end_frame_path=end,
                end_frame_mime_type="image/png",
                output_path=native,
            ),
            cancel_requested=cancelled.is_set,
        )
        native_contact_sheet = evidence / "native-contact-sheet.png"
        _native_contact_sheet(native, native_contact_sheet)
        telemetry.set_stage("rife_interpolation")
        rife = RifeCliInterpolationProvider(
            args.rife_runtime / ".venv/bin/python",
            args.rife_runtime / "inference_video.py",
            args.rife_runtime / "train_log",
            timeout_seconds=1800,
            cancel_requested=cancelled.is_set,
        )
        interpolated = output / "interpolated-60fps.mp4"
        await rife.process(native, interpolated, target_fps=60)
        telemetry.set_stage("delivery_encoding")
        delivery = output / "delivery-600.mp4"
        normalize_interpolated_delivery(
            interpolated,
            delivery,
            duration_seconds=10,
            delivery_fps=60,
            cancel_requested=cancelled.is_set,
        )
        metric = None
        metric_health = None
        if args.lpips_runtime is not None:
            metric = LpipsCliMetricProvider(
                "lpips-local",
                args.lpips_runtime / ".venv/bin/python",
                Path("scripts/lpips_metric.py").resolve(),
                args.lpips_runtime / "cache",
                cancel_requested=cancelled.is_set,
            )
            metric_health = await metric.health()
            if metric_health.get("ok") is not True:
                raise RuntimeError("LPIPS runtime is not healthy")
        telemetry.set_stage("quality_validation")
        qa = inspect_delivery_contract(
            delivery,
            request=request,
            required_start=start,
            target_end=end,
            evidence_directory=evidence,
            cancel_requested=cancelled.is_set,
            perceptual_metric=metric,
        )
        qa["reference_trajectory"] = _reference_trajectory(
            args.source_video, reference_evidence, evidence
        )
        write_qa_report(qa, evidence / "qa-report.json")
    finally:
        telemetry_result = telemetry.stop()

    manifest: dict[str, Any] = {
        "version": 1,
        "passed_technical_qa": qa["passed"],
        "visual_review": {
            "status": "pending_human_review",
            "production_passed": False,
        },
        "corpus": {
            "title": "Tears of Steel",
            "source_url": CORPUS_URL,
            "source_sha256": CORPUS_SHA256,
            "license": CORPUS_LICENSE,
            "attribution": CORPUS_ATTRIBUTION,
            "start_seconds": START_SECONDS,
            "end_seconds": END_SECONDS,
            "transform": f"cover-scale-and-center-crop-{WIDTH}x{HEIGHT}",
            "start_sha256": _sha256(start),
            "end_sha256": _sha256(end),
        },
        "request": request.model_dump(mode="json"),
        "request_digest": request.digest(),
        "provider": run_output.model_dump(mode="json"),
        "provider_health": health,
        "lpips_health": metric_health,
        "timing": {
            "wall_seconds": time.monotonic() - started,
            "native": inspect_frame_timing(native),
            "interpolated": inspect_frame_timing(interpolated),
            "delivery": inspect_frame_timing(delivery),
        },
        "checksums": {
            "native": _sha256(native),
            "interpolated": _sha256(interpolated),
            "delivery": _sha256(delivery),
            "qa_report": _sha256(evidence / "qa-report.json"),
            "native_contact_sheet": _sha256(native_contact_sheet),
        },
        "telemetry": telemetry_result.model_dump(mode="json"),
    }
    _atomic_json(manifest, output / "acceptance-manifest.json")
    return manifest


def main() -> int:
    args = _parser().parse_args()
    _validate(args)
    cancelled = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signum, _frame: cancelled.set())
    result = asyncio.run(_execute(args, cancelled))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
