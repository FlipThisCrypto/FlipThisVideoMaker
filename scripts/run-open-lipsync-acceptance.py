#!/usr/bin/env python3
"""Exercise local LatentSync on a persisted real generated clip and retain evidence."""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from flipthis_video_maker.database.session import make_engine
from flipthis_video_maker.domain.models import Asset, Project, VideoChainClip
from flipthis_video_maker.media.ffmpeg import checksum, probe, run
from flipthis_video_maker.media.video_delivery import (
    inspect_delivery_contract,
    inspect_frame_timing,
    mux_exact_delivery_audio,
    normalize_interpolated_delivery,
    write_qa_report,
)
from flipthis_video_maker.providers.latentsync.cli import LatentSyncCliProvider
from flipthis_video_maker.providers.lpips.cli import LpipsCliMetricProvider
from flipthis_video_maker.providers.rife.cli import RifeCliInterpolationProvider
from flipthis_video_maker.scheduler.gpu import GPUTelemetryRecorder
from flipthis_video_maker.services.video_chains import request_from_clip
from flipthis_video_maker.storage.assets import register_asset

LATENTSYNC_COMMIT = "a229c3948406bc2cf6eaf4873e662e70c6a04746"
LATENTSYNC_WEIGHT_SHA256 = "6440b49a7ccceff56cdc001f5f17605216337f5bbd66fa360139768926e23f51"
SYNCNET_WEIGHT_SHA256 = "961e8696f888fce4f3f3a6c3d5b3267cf5b343100b238e79b2659bff2c605442"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_chain_directory", type=Path)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--latentsync-runtime", type=Path, required=True)
    parser.add_argument("--rife-runtime", type=Path, required=True)
    parser.add_argument("--lpips-runtime", type=Path, required=True)
    parser.add_argument("--physical-gpu", type=int, choices=(0, 1), default=1)
    parser.add_argument("--seed", type=int, default=43122)
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is missing or invalid") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError(f"{label} contract is invalid")
    return payload


def _atomic_json(payload: dict[str, Any], output: Path) -> None:
    partial = output.with_name(f".{output.name}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        partial.replace(output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _copy_new(source: Path, destination: Path) -> Path:
    if not source.is_file() or destination.exists():
        raise ValueError("Import source is missing or destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def _contained_file(root: Path, value: str | Path, label: str) -> Path:
    resolved_root = root.resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    candidate = candidate.resolve()
    if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
        raise ValueError(f"{label} must be a file inside the source acceptance directory")
    return candidate


def _validate(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    paths = (
        args.source_chain_directory,
        args.audio,
        args.output_directory,
        args.latentsync_runtime,
        args.rife_runtime,
        args.lpips_runtime,
    )
    if not all(path.is_absolute() for path in paths):
        raise ValueError("All filesystem paths must be absolute")
    if args.output_directory.exists():
        raise FileExistsError("Refusing to overwrite a lip-sync acceptance directory")
    if not args.audio.is_file() or args.timeout_seconds <= 0:
        raise ValueError("Audio input and timeout must be valid")
    run_record = _json_object(args.source_chain_directory / "chain-run.json", "Chain run")
    chain_report = _json_object(
        args.source_chain_directory / "two-clip-acceptance.json",
        "Two-clip acceptance",
    )
    if chain_report.get("passed") is not True:
        raise ValueError("Source chain has not passed real two-clip acceptance")
    runtime_commit = subprocess.run(
        ["git", "-C", str(args.latentsync_runtime), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if runtime_commit != LATENTSYNC_COMMIT:
        raise ValueError("LatentSync runtime is not at the acceptance-pinned commit")
    weights = (
        args.latentsync_runtime / "checkpoints" / "latentsync_unet.pt",
        args.latentsync_runtime / "checkpoints" / "auxiliary" / "syncnet_v2.model",
    )
    if tuple(_sha256(path) if path.is_file() else None for path in weights) != (
        LATENTSYNC_WEIGHT_SHA256,
        SYNCNET_WEIGHT_SHA256,
    ):
        raise ValueError("LatentSync acceptance weights are missing or changed")
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    if visible != str(args.physical_gpu):
        raise ValueError("CUDA_VISIBLE_DEVICES must expose only the selected physical GPU")
    return run_record, chain_report


def _new_database(path: Path) -> tuple[Session, Engine]:
    url = f"sqlite:///{path}"
    alembic = Config("alembic.ini")
    alembic.attributes["database_url"] = url
    command.upgrade(alembic, "head")
    engine = make_engine(url)
    return sessionmaker(engine, expire_on_commit=False, class_=Session)(), engine


def _register(
    db: Session,
    project: Project,
    path: Path,
    kind: str,
    provider: str,
    model: str,
    *,
    parents: list[str] | None = None,
    generation_parameters: dict[str, Any] | None = None,
) -> Asset:
    asset = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind=kind,
        path=path,
        provider=provider,
        model=model,
        parents=parents,
        generation_parameters=generation_parameters,
    )
    db.commit()
    return asset


def _contact_sheet(video: Path, output: Path, *, face_crop: bool = False) -> None:
    filters = "fps=2.5,"
    if face_crop:
        filters += "crop=340:260:0:0,scale=510:390,"
    else:
        filters += "scale=424:240,"
    run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            f"{filters}tile=5x5",
            "-frames:v",
            "1",
            str(output),
        ]
    )


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    run_record, _ = _validate(args)
    root = args.output_directory
    root.mkdir(parents=True)
    evidence = root / "evidence"
    inputs = root / "inputs"
    stages = root / "stages"
    for directory in (evidence, inputs, stages):
        directory.mkdir()

    source_db_path = _contained_file(
        args.source_chain_directory,
        str(run_record["database"]),
        "Source database",
    )
    source_engine = make_engine(f"sqlite:///{source_db_path}")
    try:
        with Session(source_engine) as source_db:
            clip = source_db.get(VideoChainClip, str(run_record["clip2_id"]))
            if clip is None or clip.native_video_asset_id is None:
                raise ValueError("Accepted source Clip 2 is missing")
            request = request_from_clip(clip)
            native_source = source_db.get(Asset, clip.native_video_asset_id)
            start_source = source_db.get(Asset, request.start_frame_asset_id)
            target_source = source_db.get(Asset, request.target_end_frame_asset_id)
            if native_source is None or start_source is None or target_source is None:
                raise ValueError("Accepted source Clip 2 lineage is incomplete")
            native_path = _copy_new(
                _contained_file(
                    args.source_chain_directory,
                    native_source.file_path,
                    "Native source Asset",
                ),
                inputs / "native.mp4",
            )
            start_path = _copy_new(
                _contained_file(
                    args.source_chain_directory,
                    start_source.file_path,
                    "Start-frame Asset",
                ),
                inputs / "required-start.png",
            )
            target_path = _copy_new(
                _contained_file(
                    args.source_chain_directory,
                    target_source.file_path,
                    "Target-frame Asset",
                ),
                inputs / "target-end.png",
            )
    finally:
        source_engine.dispose()

    audio_path = root / "audio-10s.wav"
    run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(args.audio),
            "-af",
            "apad",
            "-t",
            "10",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]
    )
    if abs(inspect_frame_timing(native_path)["duration_seconds"] - 10.125) > 0.01:
        raise ValueError("Acceptance source is not the exercised 81-frame native clip")

    db, engine = _new_database(root / "acceptance.db")
    telemetry = GPUTelemetryRecorder(args.physical_gpu).start()
    started = time.monotonic()
    try:
        project = Project(name="Open local LatentSync acceptance", root_asset_directory=str(root))
        db.add(project)
        db.commit()
        start_asset = _register(
            db, project, start_path, "acceptance_start_frame", "import", "source"
        )
        target_asset = _register(
            db, project, target_path, "acceptance_target_frame", "import", "source"
        )
        native_asset = _register(
            db,
            project,
            native_path,
            "native_generative_video",
            "wan22-flf-gpu1",
            "wan2.2-i2v-a14b-fp8",
            parents=[start_asset.id, target_asset.id],
        )
        audio_asset = _register(
            db,
            project,
            audio_path,
            "dialogue_audio",
            "operator-upload",
            "rights-reviewed-audio",
        )
        rife = RifeCliInterpolationProvider(
            args.rife_runtime / ".venv/bin/python",
            args.rife_runtime / "inference_video.py",
            args.rife_runtime / "train_log",
            timeout_seconds=1800,
        )
        if (await rife.health()).get("ok") is not True:
            raise RuntimeError("Practical-RIFE is not healthy")
        telemetry.set_stage("preparing_exact_25fps")
        raw_25 = stages / "rife-raw-25fps.mp4"
        await rife.process(native_path, raw_25, target_fps=25)
        raw_25_asset = _register(
            db,
            project,
            raw_25,
            "lip_sync_source_interpolated_raw_video",
            "rife-local",
            "Practical-RIFE-4.25",
            parents=[native_asset.id],
        )
        exact_25 = stages / "lip-sync-source-exact-250.mp4"
        normalize_interpolated_delivery(raw_25, exact_25, duration_seconds=10, delivery_fps=25)
        exact_facts = inspect_frame_timing(exact_25)
        if (
            exact_facts["decoded_frame_count"] != 250
            or exact_facts["constant_frame_rate"] is not True
        ):
            raise RuntimeError("LatentSync source is not exactly 250 CFR frames")
        exact_25_asset = _register(
            db,
            project,
            exact_25,
            "lip_sync_source_video",
            "flipthis-delivery",
            "exact-cfr-v1",
            parents=[native_asset.id, raw_25_asset.id],
            generation_parameters={"fps": 25, "frames": 250, "duration_seconds": 10},
        )
        latentsync = LatentSyncCliProvider(
            "latentsync-local",
            args.latentsync_runtime / ".venv/bin/python",
            args.latentsync_runtime,
            args.latentsync_runtime / "configs/unet/stage2.yaml",
            args.latentsync_runtime / "checkpoints/latentsync_unet.pt",
            args.latentsync_runtime / "checkpoints/auxiliary/syncnet_v2.model",
            timeout_seconds=args.timeout_seconds,
            inference_steps=20,
            guidance_scale=1.5,
            enable_deepcache=True,
        )
        health = await latentsync.health()
        if health.get("ok") is not True:
            raise RuntimeError("LatentSync is not healthy")
        telemetry.set_stage("lip_syncing")
        lip_path = stages / "latentsync-25fps.mp4"
        lip_result = await latentsync.process(exact_25, audio_path, lip_path, seed=args.seed)
        lip_asset = _register(
            db,
            project,
            lip_path,
            "performance_conditioned_video",
            "latentsync-local",
            lip_result.actual_model,
            parents=[exact_25_asset.id, audio_asset.id],
            generation_parameters=lip_result.model_dump(mode="json"),
        )
        telemetry.set_stage("interpolating_and_encoding")
        raw_60 = stages / "rife-raw-60fps.mp4"
        await rife.process(lip_path, raw_60, target_fps=60)
        raw_60_asset = _register(
            db,
            project,
            raw_60,
            "temporally_interpolated_video",
            "rife-local",
            "Practical-RIFE-4.25",
            parents=[lip_asset.id],
        )
        video_only = stages / "delivery-video-only-600.mp4"
        normalize_interpolated_delivery(raw_60, video_only, duration_seconds=10, delivery_fps=60)
        delivery = root / "delivery-600.mp4"
        mux_exact_delivery_audio(video_only, audio_path, delivery, duration_seconds=10)
        delivery_asset = _register(
            db,
            project,
            delivery,
            "lip_synced_delivery_video",
            "flipthis-delivery",
            "h264-cfr60-aac",
            parents=[native_asset.id, lip_asset.id, raw_60_asset.id, audio_asset.id],
        )
        telemetry.set_stage("validating")
        lpips = LpipsCliMetricProvider(
            "lpips-local",
            args.lpips_runtime / ".venv/bin/python",
            Path("scripts/lpips_metric.py").resolve(),
            args.lpips_runtime / "cache",
        )
        qa = inspect_delivery_contract(
            delivery,
            request=request,
            required_start=start_path,
            target_end=target_path,
            evidence_directory=evidence,
            perceptual_metric=lpips,
        )
        streams = probe(delivery).get("streams", [])
        qa["lip_sync"] = lip_result.model_dump(mode="json")
        qa["checks"]["audio_stream_present"] = isinstance(streams, list) and any(
            isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams
        )
        qa["checks"]["lip_sync_qa_passed"] = lip_result.sync_qa_passed
        qa["passed"] = all(qa["checks"].values())
        qa_path = write_qa_report(qa, root / "qa-report.json")
        qa_asset = _register(
            db,
            project,
            qa_path,
            "video_qa_report",
            "flipthis-qa",
            "delivery-contract-v1",
            parents=[delivery_asset.id, start_asset.id, target_asset.id],
            generation_parameters={"passed": qa["passed"]},
        )
        _contact_sheet(lip_path, evidence / "native-contact-sheet.png")
        _contact_sheet(delivery, evidence / "face-contact-sheet.png", face_crop=True)
        wall_seconds = time.monotonic() - started
        manifest = {
            "version": 1,
            "passed_technical_qa": qa["passed"],
            "visual_review": {"status": "pending_human_review", "production_passed": False},
            "checksums": {
                "delivery": checksum(delivery),
                "qa_report": checksum(qa_path),
                "native_contact_sheet": checksum(evidence / "native-contact-sheet.png"),
                "face_contact_sheet": checksum(evidence / "face-contact-sheet.png"),
            },
            "assets": {
                "project_id": project.id,
                "audio_asset_id": audio_asset.id,
                "native_asset_id": native_asset.id,
                "raw_25fps_asset_id": raw_25_asset.id,
                "exact_25fps_asset_id": exact_25_asset.id,
                "lip_sync_asset_id": lip_asset.id,
                "raw_60fps_asset_id": raw_60_asset.id,
                "delivery_asset_id": delivery_asset.id,
                "qa_asset_id": qa_asset.id,
            },
            "lip_sync": lip_result.model_dump(mode="json"),
            "runtime": {
                "latentsync_commit": LATENTSYNC_COMMIT,
                "weight_sha256": LATENTSYNC_WEIGHT_SHA256,
                "syncnet_sha256": SYNCNET_WEIGHT_SHA256,
                "code_license": "Apache-2.0",
                "weight_license": "OpenRAIL++",
                "wall_seconds": wall_seconds,
                "health": health,
            },
        }
        _atomic_json(manifest, root / "acceptance-manifest.json")
    finally:
        telemetry_result = telemetry.stop()
        db.close()
        engine.dispose()
    manifest["telemetry"] = telemetry_result.model_dump(mode="json")
    _atomic_json(manifest, root / "acceptance-manifest.json")
    return manifest


def main() -> int:
    args = _parser().parse_args()
    print(json.dumps(asyncio.run(_run(args)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
