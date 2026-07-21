#!/usr/bin/env python3
"""Generate and finalize a persisted real successor to the open-corpus acceptance clip."""

import argparse
import asyncio
import hashlib
import json
import shutil
import signal
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from flipthis_video_maker.contracts.video_generation import (
    ChainClipState,
    FirstLastFrameGenerationRequest,
    RetryContinuation,
)
from flipthis_video_maker.database.session import make_engine
from flipthis_video_maker.domain.enums import JobState
from flipthis_video_maker.domain.models import Asset, Job, Project, VideoChain, VideoChainClip
from flipthis_video_maker.media.ffmpeg import checksum, run
from flipthis_video_maker.media.video_delivery import (
    decoded_frame_hashes,
    duplicate_frame_evidence,
    extract_frame_at_index,
    image_similarity,
    inspect_frame_timing,
)
from flipthis_video_maker.pipeline.video_chain import VideoChainPipeline
from flipthis_video_maker.providers.comfyui.wan_flf import (
    ComfyUIWanFirstLastFrameProvider,
)
from flipthis_video_maker.providers.lpips.cli import LpipsCliMetricProvider
from flipthis_video_maker.providers.rife.cli import RifeCliInterpolationProvider
from flipthis_video_maker.scheduler.gpu import GPUTelemetryRecorder
from flipthis_video_maker.services.video_chains import (
    accept_chain_clip,
    assemble_video_chain,
    create_video_chain,
    enqueue_chain_clip,
)
from flipthis_video_maker.storage.assets import register_asset

CORPUS_SHA256 = "efa9062d9cdb7a338e40ad530dfdf234806743f29ae6a1a136b97ece4e588e8f"
TARGET_SECONDS = 134.0
WIDTH = 848
HEIGHT = 480


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("previous_acceptance", type=Path)
    generate.add_argument("source_video", type=Path)
    generate.add_argument("output_directory", type=Path)
    generate.add_argument("--endpoint", default="http://127.0.0.1:8189")
    generate.add_argument("--workflow", type=Path, required=True)
    generate.add_argument("--rife-runtime", type=Path, required=True)
    generate.add_argument("--lpips-runtime", type=Path, required=True)
    generate.add_argument("--physical-gpu", type=int, choices=(0, 1), default=1)
    generate.add_argument("--seed", type=int, default=43122)
    generate.add_argument("--timeout-seconds", type=float, default=7200)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("output_directory", type=Path)
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(payload: dict[str, Any], output: Path) -> None:
    partial = output.with_name(f".{output.name}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        partial.replace(output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is missing or invalid") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError(f"{label} contract is invalid")
    return payload


def _validate_generate(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    paths = (
        args.previous_acceptance,
        args.source_video,
        args.output_directory,
        args.workflow,
        args.rife_runtime,
        args.lpips_runtime,
    )
    if not all(path.is_absolute() for path in paths):
        raise ValueError("All filesystem paths must be absolute")
    if args.output_directory.exists():
        raise FileExistsError("Refusing to overwrite a chain acceptance directory")
    if not args.source_video.is_file() or _sha256(args.source_video) != CORPUS_SHA256:
        raise ValueError("Source is not the pinned Tears of Steel 720p corpus")
    if not args.workflow.is_file() or args.timeout_seconds <= 0:
        raise ValueError("Workflow and timeout are invalid")
    for path in (
        args.rife_runtime / ".venv/bin/python",
        args.rife_runtime / "inference_video.py",
        args.rife_runtime / "train_log",
        args.lpips_runtime / ".venv/bin/python",
        args.lpips_runtime / "cache",
    ):
        if not path.exists():
            raise ValueError(f"External runtime component is missing: {path.name}")
    manifest = _json_object(
        args.previous_acceptance / "acceptance-manifest.json", "Previous acceptance manifest"
    )
    review = _json_object(args.previous_acceptance / "visual-review.json", "Previous visual review")
    if (
        manifest.get("passed_technical_qa") is not True
        or review.get("production_visual_acceptance") is not True
    ):
        raise ValueError("Previous clip has not passed both technical and visual review")
    delivery = args.previous_acceptance / "delivery-600.mp4"
    expected = manifest.get("checksums", {}).get("delivery")
    if not isinstance(expected, str) or not delivery.is_file() or _sha256(delivery) != expected:
        raise ValueError("Previous delivery checksum is invalid")
    reviewed_delivery = review.get("reviewed_artifacts", {}).get("delivery", {}).get("sha256")
    if reviewed_delivery != expected:
        raise ValueError("Previous visual review does not bind the accepted delivery")
    return manifest, review


def _extract_corpus_frame(source: Path, output: Path, seconds: float) -> None:
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


def _copy_new(source: Path, destination: Path) -> Path:
    if not source.is_file() or destination.exists():
        raise ValueError("Import source is missing or destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def _migrated_session(database: Path) -> tuple[Session, Engine]:
    url = f"sqlite:///{database}"
    alembic = Config("alembic.ini")
    alembic.attributes["database_url"] = url
    command.upgrade(alembic, "head")
    engine = make_engine(url)
    factory = sessionmaker(engine, expire_on_commit=False, class_=Session)
    return factory(), engine


def _register(
    db: Session,
    project: Project,
    path: Path,
    kind: str,
    *,
    parents: list[str] | None = None,
    generation_parameters: dict[str, Any] | None = None,
) -> Asset:
    return register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind=kind,
        path=path,
        provider="open-acceptance-import",
        model="wan2.2-i2v-a14b-fp8",
        parents=parents,
        generation_parameters=generation_parameters,
    )


def _successor_request(
    previous: FirstLastFrameGenerationRequest,
    *,
    provider_id: str,
    start_asset_id: str,
    target_asset_id: str,
    predecessor_id: str,
    seed: int,
) -> FirstLastFrameGenerationRequest:
    return previous.model_copy(
        update={
            "provider_id": provider_id,
            "start_frame_asset_id": start_asset_id,
            "target_end_frame_asset_id": target_asset_id,
            "prompt": (
                "A continuous locked-camera cinematic shot. The older grey-haired man remains "
                "upright, breathes, shifts his weight, lowers his gaze, and naturally leans to "
                "his left. The armed man in the background stays alert with subtle body motion. "
                "Preserve both men's identity, anatomy, wardrobe, lighting, architecture, depth, "
                "and spatial layout throughout."
            ),
            "negative_prompt": (
                "cut, edit, transition, crossfade, dissolve, morph, duplicate person, "
                "extra person, teleportation, sliding body, warped face, deformed hands, "
                "frozen image, camera shake"
            ),
            "seed": seed,
            "retry_continuation": RetryContinuation(predecessor_clip_id=predecessor_id),
            "provider_settings": {
                provider_id: {
                    "steps": 20,
                    "cfg": 4.0,
                    "shift": 8.0,
                    "high_noise_end_step": 10,
                }
            },
        }
    )


async def _generate(args: argparse.Namespace) -> dict[str, Any]:
    previous_manifest, _ = _validate_generate(args)
    root = args.output_directory
    root.mkdir(parents=True)
    imported = root / "imported-clip1"
    target_root = root / "targets"
    review_root = root / "clip2-acceptance"
    evidence = review_root / "evidence"
    for directory in (imported, target_root, evidence):
        directory.mkdir(parents=True)

    clip1_start_path = _copy_new(
        args.previous_acceptance / "boundaries/start.png", imported / "planned-start.png"
    )
    clip1_target_path = _copy_new(
        args.previous_acceptance / "boundaries/end.png", imported / "planned-target.png"
    )
    clip1_native_path = _copy_new(args.previous_acceptance / "native.mp4", imported / "native.mp4")
    clip1_delivery_path = _copy_new(
        args.previous_acceptance / "delivery-600.mp4", imported / "delivery-600.mp4"
    )
    clip1_qa_path = _copy_new(
        args.previous_acceptance / "evidence/qa-report.json", imported / "qa-report.json"
    )
    clip1_actual_start_path = imported / "actual-frame-0000.png"
    clip1_actual_last_path = imported / "actual-frame-0599.png"
    extract_frame_at_index(clip1_delivery_path, clip1_actual_start_path, 0)
    extract_frame_at_index(clip1_delivery_path, clip1_actual_last_path, 599)
    previous_final = args.previous_acceptance / "evidence/frame-0599.png"
    if not previous_final.is_file() or _sha256(clip1_actual_last_path) != _sha256(previous_final):
        raise ValueError("Freshly decoded Clip 1 final frame does not match its accepted evidence")
    target_c_path = target_root / "target-c.png"
    _extract_corpus_frame(args.source_video, target_c_path, TARGET_SECONDS)

    db, engine = _migrated_session(root / "chain.db")
    cancelled = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signum, _frame: cancelled.set())
    telemetry = GPUTelemetryRecorder(args.physical_gpu).start()
    try:
        project = Project(name="Open real two-clip acceptance", root_asset_directory=str(root))
        db.add(project)
        db.commit()
        start_asset = _register(db, project, clip1_start_path, "acceptance_start_frame")
        target_b_asset = _register(db, project, clip1_target_path, "acceptance_target_frame")
        target_c_asset = _register(db, project, target_c_path, "acceptance_target_frame")
        db.commit()
        chain = create_video_chain(db, project, name="Tears of Steel real continuation")
        previous_request = FirstLastFrameGenerationRequest.model_validate(
            previous_manifest["request"]
        ).model_copy(
            update={
                "start_frame_asset_id": start_asset.id,
                "target_end_frame_asset_id": target_b_asset.id,
            }
        )
        first, first_job = enqueue_chain_clip(
            db, project, chain, previous_request, gpu_assignment=f"gpu{args.physical_gpu}"
        )
        native_asset = _register(
            db,
            project,
            clip1_native_path,
            "native_generative_video",
            parents=[start_asset.id, target_b_asset.id],
            generation_parameters={
                "imported_acceptance_manifest": _sha256(
                    args.previous_acceptance / "acceptance-manifest.json"
                )
            },
        )
        delivery_asset = _register(
            db,
            project,
            clip1_delivery_path,
            "generated_video_delivery",
            parents=[native_asset.id],
        )
        actual_start_asset = _register(
            db,
            project,
            clip1_actual_start_path,
            "actual_start_frame",
            parents=[delivery_asset.id],
        )
        actual_last_asset = _register(
            db,
            project,
            clip1_actual_last_path,
            "actual_last_frame",
            parents=[delivery_asset.id],
        )
        qa_asset = _register(
            db,
            project,
            clip1_qa_path,
            "video_qa_report",
            parents=[delivery_asset.id, start_asset.id, target_b_asset.id],
        )
        first.native_video_asset_id = native_asset.id
        first.delivery_video_asset_id = delivery_asset.id
        first.actual_start_frame_asset_id = actual_start_asset.id
        first.actual_last_frame_asset_id = actual_last_asset.id
        first.qa_report_asset_id = qa_asset.id
        first.result_snapshot = {
            "continuity_qa": {
                "passed": True,
                "imported_from_exercised_acceptance": True,
            }
        }
        first.state = ChainClipState.AWAITING_REVIEW.value
        first_job.state = JobState.SUCCEEDED.value
        first_job.progress = 1
        first_job.current_stage = "imported_exercised_acceptance"
        first_job.output_asset_ids = [delivery_asset.id]
        first_job.completed_at = datetime.now(UTC)
        db.commit()
        accept_chain_clip(db, first)

        provider_id = f"wan22-flf-gpu{args.physical_gpu}"
        second_request = _successor_request(
            previous_request,
            provider_id=provider_id,
            start_asset_id=actual_last_asset.id,
            target_asset_id=target_c_asset.id,
            predecessor_id=first.id,
            seed=args.seed,
        )
        second, second_job = enqueue_chain_clip(
            db,
            project,
            chain,
            second_request,
            predecessor=first,
            gpu_assignment=f"gpu{args.physical_gpu}",
        )
        provider = ComfyUIWanFirstLastFrameProvider(
            provider_id,
            endpoint=args.endpoint,
            workflow_template=args.workflow,
            gpu_assignment=f"gpu{args.physical_gpu}",
            timeout_seconds=args.timeout_seconds,
        )
        health = await provider.health()
        if health.get("ok") is not True:
            raise RuntimeError(f"Wan provider is not healthy: {health.get('status', 'unknown')}")
        rife = RifeCliInterpolationProvider(
            args.rife_runtime / ".venv/bin/python",
            args.rife_runtime / "inference_video.py",
            args.rife_runtime / "train_log",
            timeout_seconds=1800,
            cancel_requested=cancelled.is_set,
        )
        lpips = LpipsCliMetricProvider(
            "lpips-local",
            args.lpips_runtime / ".venv/bin/python",
            Path("scripts/lpips_metric.py").resolve(),
            args.lpips_runtime / "cache",
            cancel_requested=cancelled.is_set,
        )
        started = time.monotonic()
        await VideoChainPipeline(
            db,
            provider=provider,
            interpolation_provider=rife,
            perceptual_metric_provider=lpips,
            gpu_telemetry=telemetry,
            cancel_requested=cancelled.is_set,
        ).run(project, second)
        wall_seconds = time.monotonic() - started
        if second.state != ChainClipState.AWAITING_REVIEW.value:
            raise RuntimeError("Real successor did not pass technical QA into review")
        second_job.state = JobState.SUCCEEDED.value
        second_job.progress = 1
        second_job.current_stage = "awaiting_review"
        second_job.output_asset_ids = [second.delivery_video_asset_id]
        second_job.completed_at = datetime.now(UTC)
        db.commit()
        second_delivery = db.get(Asset, second.delivery_video_asset_id)
        second_native = db.get(Asset, second.native_video_asset_id)
        if second_delivery is None or second_native is None or second.qa_report_asset_id is None:
            raise RuntimeError("Successor output lineage is incomplete")
        qa_record = db.get(Asset, second.qa_report_asset_id)
        if qa_record is None:
            raise RuntimeError("Successor QA Asset is missing")
        qa = _json_object(Path(qa_record.file_path), "Successor QA report")
        contact_id = qa.get("asset_lineage", {}).get("contact_sheet_asset_id")
        contact_asset = db.get(Asset, contact_id) if isinstance(contact_id, str) else None
        if contact_asset is None:
            raise RuntimeError("Successor contact-sheet Asset is missing")
        reviewed_delivery = _copy_new(
            Path(second_delivery.file_path), review_root / "delivery-600.mp4"
        )
        _copy_new(Path(contact_asset.file_path), evidence / "contact-sheet.png")
        native_contact = evidence / "native-contact-sheet.png"
        _native_contact_sheet(Path(second_native.file_path), native_contact)
        review_manifest = {
            "version": 1,
            "passed_technical_qa": True,
            "visual_review": {"status": "pending_human_review", "production_passed": False},
            "checksums": {
                "delivery": _sha256(reviewed_delivery),
                "native": _sha256(Path(second_native.file_path)),
                "native_contact_sheet": _sha256(native_contact),
            },
            "chain": {
                "project_id": project.id,
                "chain_id": chain.id,
                "clip1_id": first.id,
                "clip2_id": second.id,
                "clip1_actual_last_frame_asset_id": actual_last_asset.id,
                "clip2_planned_start_frame_asset_id": second.planned_start_frame_asset_id,
                "clip2_target_end_frame_asset_id": target_c_asset.id,
            },
        }
        _atomic_json(review_manifest, review_root / "acceptance-manifest.json")
        run_record = {
            "version": 1,
            "database": "chain.db",
            "project_id": project.id,
            "chain_id": chain.id,
            "clip1_id": first.id,
            "clip2_id": second.id,
            "review_directory": "clip2-acceptance",
            "clip1_actual_last_frame_asset_id": actual_last_asset.id,
            "clip1_actual_last_frame_sha256": actual_last_asset.checksum,
            "clip2_planned_start_frame_asset_id": second.planned_start_frame_asset_id,
            "clip2_state": second.state,
            "clip2_delivery_asset_id": second.delivery_video_asset_id,
            "clip2_delivery_sha256": second_delivery.checksum,
            "clip2_native_asset_id": second.native_video_asset_id,
            "clip2_qa_report_asset_id": second.qa_report_asset_id,
            "target_c_seconds": TARGET_SECONDS,
            "target_c_sha256": target_c_asset.checksum,
            "wall_seconds": wall_seconds,
            "provider_health": health,
        }
        _atomic_json(run_record, root / "chain-run.json")
        db.commit()
    finally:
        telemetry_result = telemetry.stop()
        db.close()
        engine.dispose()
    run_record["telemetry"] = telemetry_result.model_dump(mode="json")
    _atomic_json(run_record, root / "chain-run.json")
    return run_record


def _finalize(output_directory: Path) -> dict[str, Any]:
    if not output_directory.is_absolute():
        raise ValueError("Output directory must be absolute")
    run_record = _json_object(output_directory / "chain-run.json", "Chain run record")
    review_directory = output_directory / str(run_record.get("review_directory"))
    review = _json_object(review_directory / "visual-review.json", "Clip 2 visual review")
    if review.get("production_visual_acceptance") is not True:
        raise ValueError("Clip 2 has not passed visual review")
    reviewed_delivery = review.get("reviewed_artifacts", {}).get("delivery", {}).get("sha256")
    if reviewed_delivery != run_record.get("clip2_delivery_sha256"):
        raise ValueError("Clip 2 visual review does not bind the persisted delivery Asset")

    database = output_directory / str(run_record.get("database"))
    engine = make_engine(f"sqlite:///{database}")
    factory = sessionmaker(engine, expire_on_commit=False, class_=Session)
    try:
        with factory() as db:
            project = db.get(Project, str(run_record["project_id"]))
            chain = db.get(VideoChain, str(run_record["chain_id"]))
            first = db.get(VideoChainClip, str(run_record["clip1_id"]))
            second = db.get(VideoChainClip, str(run_record["clip2_id"]))
            if None in (project, chain, first, second):
                raise ValueError("Persisted chain lineage is missing")
            assert (
                project is not None
                and chain is not None
                and first is not None
                and second is not None
            )
            first_job = db.get(Job, first.job_id)
            second_job = db.get(Job, second.job_id)
            if (
                first.actual_last_frame_asset_id != second.planned_start_frame_asset_id
                or second.predecessor_clip_id != first.id
                or second.state
                not in {
                    ChainClipState.AWAITING_REVIEW.value,
                    ChainClipState.ACCEPTED.value,
                }
                or first_job is None
                or second_job is None
                or first_job.state != JobState.SUCCEEDED.value
                or second_job.state != JobState.SUCCEEDED.value
            ):
                raise ValueError("Persisted successor lineage does not match Clip 1's actual end")
            second_delivery = db.get(Asset, second.delivery_video_asset_id)
            first_delivery = db.get(Asset, first.delivery_video_asset_id)
            if second_delivery is None or first_delivery is None:
                raise ValueError("Persisted delivery Asset is missing")
            if checksum(Path(second_delivery.file_path)) != reviewed_delivery:
                raise ValueError("Persisted Clip 2 delivery changed after visual review")
            if second.state == ChainClipState.AWAITING_REVIEW.value:
                accept_chain_clip(db, second)
            assembled = assemble_video_chain(db, project, chain)
            assembled_path = Path(assembled.file_path)
            facts = inspect_frame_timing(assembled_path)
            if facts["decoded_frame_count"] != 1199 or facts["average_frame_rate"] != 60:
                raise RuntimeError("Assembled real chain failed the 1,199-frame contract")

            evidence = output_directory / "assembled-evidence"
            if evidence.exists():
                evidence = output_directory / f"assembled-evidence-{uuid.uuid4().hex}"
            evidence.mkdir(exist_ok=False)
            paths = {
                "clip1_last": evidence / "clip1-frame-0599.png",
                "clip2_first": evidence / "clip2-frame-0000.png",
                "clip2_second": evidence / "clip2-frame-0001.png",
                "assembled_join": evidence / "assembled-frame-0599.png",
                "assembled_after_join": evidence / "assembled-frame-0600.png",
            }
            extract_frame_at_index(Path(first_delivery.file_path), paths["clip1_last"], 599)
            extract_frame_at_index(Path(second_delivery.file_path), paths["clip2_first"], 0)
            extract_frame_at_index(Path(second_delivery.file_path), paths["clip2_second"], 1)
            extract_frame_at_index(assembled_path, paths["assembled_join"], 599)
            extract_frame_at_index(assembled_path, paths["assembled_after_join"], 600)
            duplicate = duplicate_frame_evidence(decoded_frame_hashes(assembled_path))
            join_step = image_similarity(paths["assembled_join"], paths["assembled_after_join"])
            if duplicate["longest_consecutive_run"] > 6 or join_step["normalized_mae"] <= 0:
                raise RuntimeError("Assembled real chain has a frozen run or duplicate join frame")
            report = {
                "version": 1,
                "passed": True,
                "lineage": {
                    "clip1_id": first.id,
                    "clip2_id": second.id,
                    "clip1_actual_last_frame_asset_id": first.actual_last_frame_asset_id,
                    "clip2_planned_start_frame_asset_id": second.planned_start_frame_asset_id,
                    "predecessor_clip_id": second.predecessor_clip_id,
                },
                "assembly": {
                    "asset_id": assembled.id,
                    "sha256": checksum(assembled_path),
                    "facts": facts,
                    "generation_parameters": assembled.generation_parameters,
                    "duplicates": duplicate,
                },
                "boundary": {
                    "conditioned_shared_frame": image_similarity(
                        paths["clip1_last"], paths["clip2_first"]
                    ),
                    "assembled_frame_599_matches_clip1_last": image_similarity(
                        paths["clip1_last"], paths["assembled_join"]
                    ),
                    "assembled_frame_600_matches_clip2_frame_1": image_similarity(
                        paths["clip2_second"], paths["assembled_after_join"]
                    ),
                    "join_step": join_step,
                },
            }
            _atomic_json(report, output_directory / "two-clip-acceptance.json")
            return report
    finally:
        engine.dispose()


def main() -> int:
    args = _parser().parse_args()
    result = (
        asyncio.run(_generate(args))
        if args.command == "generate"
        else _finalize(args.output_directory)
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
