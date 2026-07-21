import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from flipthis_video_maker.contracts.video_generation import FirstLastFrameGenerationRequest


def _module() -> ModuleType:
    path = Path("scripts/run-open-chain-acceptance.py").resolve()
    spec = importlib.util.spec_from_file_location("open_chain_acceptance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request() -> FirstLastFrameGenerationRequest:
    return FirstLastFrameGenerationRequest(
        provider_id="wan22-flf-gpu1",
        provider_model="wan2.2-i2v-a14b-fp8",
        start_frame_asset_id="start",
        target_end_frame_asset_id="target-b",
        prompt="A continuous live-action shot with natural motion.",
        duration_seconds=10,
        native_requested_fps=8,
        delivery_fps=60,
        width=848,
        height=480,
        aspect_ratio="16:9",
        seed=43121,
        provider_settings={
            "wan22-flf-gpu1": {
                "steps": 20,
                "cfg": 4.0,
                "shift": 8.0,
                "high_noise_end_step": 10,
            }
        },
    )


def _args(tmp_path: Path) -> argparse.Namespace:
    previous = tmp_path / "previous"
    previous.mkdir()
    delivery = previous / "delivery-600.mp4"
    delivery.write_bytes(b"accepted-delivery")
    digest = hashlib.sha256(delivery.read_bytes()).hexdigest()
    (previous / "acceptance-manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "passed_technical_qa": True,
                "checksums": {"delivery": digest},
                "request": _request().model_dump(mode="json"),
            }
        ),
        encoding="utf-8",
    )
    (previous / "visual-review.json").write_text(
        json.dumps(
            {
                "version": 1,
                "production_visual_acceptance": True,
                "reviewed_artifacts": {"delivery": {"sha256": digest}},
            }
        ),
        encoding="utf-8",
    )
    source = tmp_path / "source.mov"
    source.touch()
    workflow = tmp_path / "workflow.json"
    workflow.write_text("{}", encoding="utf-8")
    rife = tmp_path / "rife"
    lpips = tmp_path / "lpips"
    for path in (
        rife / ".venv/bin/python",
        rife / "inference_video.py",
        lpips / ".venv/bin/python",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (rife / "train_log").mkdir()
    (lpips / "cache").mkdir()
    return argparse.Namespace(
        previous_acceptance=previous,
        source_video=source,
        output_directory=tmp_path / "output",
        endpoint="http://127.0.0.1:8189",
        workflow=workflow,
        rife_runtime=rife,
        lpips_runtime=lpips,
        physical_gpu=1,
        seed=43122,
        timeout_seconds=7200,
    )


def test_chain_acceptance_validates_reviewed_previous_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _module()
    args = _args(tmp_path)
    real_sha = harness._sha256
    monkeypatch.setattr(
        harness,
        "_sha256",
        lambda path: harness.CORPUS_SHA256 if path == args.source_video else real_sha(path),
    )

    manifest, review = harness._validate_generate(args)

    assert manifest["passed_technical_qa"] is True
    assert review["production_visual_acceptance"] is True
    args.previous_acceptance.joinpath("delivery-600.mp4").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        harness._validate_generate(args)


def test_chain_acceptance_rejects_unreviewed_or_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _module()
    args = _args(tmp_path)
    real_sha = harness._sha256
    monkeypatch.setattr(
        harness,
        "_sha256",
        lambda path: harness.CORPUS_SHA256 if path == args.source_video else real_sha(path),
    )
    review_path = args.previous_acceptance / "visual-review.json"
    review = json.loads(review_path.read_text())
    review["production_visual_acceptance"] = False
    review_path.write_text(json.dumps(review))

    with pytest.raises(ValueError, match="both technical and visual"):
        harness._validate_generate(args)
    args.output_directory.mkdir()
    with pytest.raises(FileExistsError, match="overwrite"):
        harness._validate_generate(args)


def test_successor_request_uses_only_actual_last_asset_and_predecessor() -> None:
    harness = _module()

    successor = harness._successor_request(
        _request(),
        provider_id="wan22-flf-gpu0",
        start_asset_id="decoded-actual-last",
        target_asset_id="target-c",
        predecessor_id="clip-1",
        seed=43122,
    )

    assert successor.start_frame_asset_id == "decoded-actual-last"
    assert successor.target_end_frame_asset_id == "target-c"
    assert successor.retry_continuation.predecessor_clip_id == "clip-1"
    assert successor.provider_id == "wan22-flf-gpu0"
    assert set(successor.provider_settings) == {"wan22-flf-gpu0"}
    assert successor.seed == 43122


def test_chain_acceptance_copy_and_json_writers_refuse_or_clean_partial(
    tmp_path: Path,
) -> None:
    harness = _module()
    source = tmp_path / "source.bin"
    source.write_bytes(b"immutable")
    copied = tmp_path / "copied.bin"

    assert harness._copy_new(source, copied).read_bytes() == b"immutable"
    with pytest.raises(ValueError, match="destination"):
        harness._copy_new(source, copied)
    report = tmp_path / "report.json"
    harness._atomic_json({"version": 1, "passed": True}, report)
    assert json.loads(report.read_text())["passed"] is True
    assert not list(tmp_path.glob("*.partial"))


def test_chain_finalize_requires_checksum_bound_visual_pass(tmp_path: Path) -> None:
    harness = _module()
    output = tmp_path / "output"
    review = output / "clip2-acceptance"
    review.mkdir(parents=True)
    (output / "chain-run.json").write_text(
        json.dumps(
            {
                "version": 1,
                "review_directory": "clip2-acceptance",
                "clip2_delivery_sha256": "expected",
            }
        ),
        encoding="utf-8",
    )
    (review / "visual-review.json").write_text(
        json.dumps({"version": 1, "production_visual_acceptance": False}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not passed visual review"):
        harness._finalize(output)
