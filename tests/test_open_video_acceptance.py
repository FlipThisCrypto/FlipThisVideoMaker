import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def _module() -> ModuleType:
    path = Path("scripts/run-open-video-acceptance.py").resolve()
    spec = importlib.util.spec_from_file_location("open_video_acceptance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(tmp_path: Path) -> argparse.Namespace:
    source = tmp_path / "source.mov"
    source.touch()
    workflow = tmp_path / "workflow.json"
    workflow.write_text("{}", encoding="utf-8")
    rife = tmp_path / "rife"
    (rife / ".venv/bin").mkdir(parents=True)
    (rife / ".venv/bin/python").touch()
    (rife / "inference_video.py").touch()
    (rife / "train_log").mkdir()
    return argparse.Namespace(
        source_video=source,
        output_directory=tmp_path / "output",
        endpoint="http://127.0.0.1:8189",
        workflow=workflow,
        rife_runtime=rife,
        lpips_runtime=None,
        physical_gpu=1,
        seed=43121,
        timeout_seconds=7200,
    )


def test_open_acceptance_validates_pinned_source_and_refuses_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _module()
    args = _args(tmp_path)
    monkeypatch.setattr(harness, "_sha256", lambda _path: harness.CORPUS_SHA256)

    harness._validate(args)
    args.output_directory.mkdir()

    with pytest.raises(FileExistsError, match="overwrite"):
        harness._validate(args)


def test_open_acceptance_rejects_unpinned_or_relative_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _module()
    args = _args(tmp_path)
    monkeypatch.setattr(harness, "_sha256", lambda _path: "wrong")

    with pytest.raises(ValueError, match="pinned"):
        harness._validate(args)
    args.source_video = Path("relative.mov")
    with pytest.raises(ValueError, match="absolute"):
        harness._validate(args)


def test_open_acceptance_request_is_true_flf_and_reproducible(tmp_path: Path) -> None:
    harness = _module()
    args = _args(tmp_path)

    request = harness._request(args)

    assert request.provider_id == "wan22-flf-gpu1"
    assert request.duration_seconds == 10
    assert request.native_requested_fps == 8
    assert request.delivery_fps == 60
    assert request.expected_delivery_frames == 600
    assert request.start_frame_asset_id != request.target_end_frame_asset_id
    assert request.provider_settings[request.provider_id] == {
        "steps": 20,
        "cfg": 4.0,
        "shift": 8.0,
        "high_noise_end_step": 10,
    }
    assert request.digest() == harness._request(args).digest()


def test_open_acceptance_extracts_one_cover_cropped_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _module()
    source = tmp_path / "source.mov"
    source.touch()
    output = tmp_path / "frame.png"
    observed: list[str] = []

    def fake_run(argv: list[str]) -> None:
        observed.extend(argv)
        output.touch()

    monkeypatch.setattr(harness, "run", fake_run)

    harness._extract_frame(source, output, 120.5)

    assert "120.5" in observed
    assert "scale=848:480:force_original_aspect_ratio=increase,crop=848:480" in observed
    assert observed[-1] == str(output)


def test_open_acceptance_manifest_write_is_atomic_and_stable(tmp_path: Path) -> None:
    harness = _module()
    output = tmp_path / "manifest.json"
    payload = {"version": 1, "passed": True}

    harness._atomic_json(payload, output)

    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert not list(tmp_path.glob("*.partial"))


def test_open_acceptance_native_sheet_contains_all_81_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _module()
    native = tmp_path / "native.mp4"
    native.touch()
    output = tmp_path / "native-contact.png"
    observed: list[str] = []

    def fake_run(argv: list[str]) -> None:
        observed.extend(argv)
        output.touch()

    monkeypatch.setattr(harness, "run", fake_run)

    harness._native_contact_sheet(native, output)

    assert "scale=212:120,tile=9x9" in observed
    assert observed[-1] == str(output)
