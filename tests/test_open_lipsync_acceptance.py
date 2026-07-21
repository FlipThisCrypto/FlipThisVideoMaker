import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


def _load_script() -> ModuleType:
    path = Path("scripts/run-open-lipsync-acceptance.py").resolve()
    spec = importlib.util.spec_from_file_location("open_lipsync_acceptance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(tmp_path: Path) -> argparse.Namespace:
    source = tmp_path / "source"
    source.mkdir()
    (source / "chain-run.json").write_text(
        json.dumps({"version": 1, "database": "chain.db", "clip2_id": "clip-2"})
    )
    (source / "two-clip-acceptance.json").write_text(json.dumps({"version": 1, "passed": True}))
    audio = tmp_path / "audio.wav"
    audio.touch()
    runtime = tmp_path / "latentsync"
    (runtime / "checkpoints" / "auxiliary").mkdir(parents=True)
    (runtime / "checkpoints" / "latentsync_unet.pt").touch()
    (runtime / "checkpoints" / "auxiliary" / "syncnet_v2.model").touch()
    rife = tmp_path / "rife"
    lpips = tmp_path / "lpips"
    rife.mkdir()
    lpips.mkdir()
    return argparse.Namespace(
        source_chain_directory=source,
        audio=audio,
        output_directory=tmp_path / "output",
        latentsync_runtime=runtime,
        rife_runtime=rife,
        lpips_runtime=lpips,
        physical_gpu=1,
        seed=43122,
        timeout_seconds=3600,
    )


def test_validate_requires_absolute_paths(tmp_path: Path) -> None:
    module = _load_script()
    args = _args(tmp_path)
    args.audio = Path("relative.wav")

    with pytest.raises(ValueError, match="absolute"):
        module._validate(args)


def test_validate_refuses_existing_output(tmp_path: Path) -> None:
    module = _load_script()
    args = _args(tmp_path)
    args.output_directory.mkdir()

    with pytest.raises(FileExistsError, match="overwrite"):
        module._validate(args)


def test_validate_pins_source_runtime_weights_and_gpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    args = _args(tmp_path)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    monkeypatch.setattr(
        module,
        "_sha256",
        Mock(side_effect=[module.LATENTSYNC_WEIGHT_SHA256, module.SYNCNET_WEIGHT_SHA256]),
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        Mock(return_value=Mock(stdout=f"{module.LATENTSYNC_COMMIT}\n")),
    )

    run_record, chain_report = module._validate(args)

    assert run_record["clip2_id"] == "clip-2"
    assert chain_report["passed"] is True


def test_validate_rejects_unselected_gpu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script()
    args = _args(tmp_path)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    monkeypatch.setattr(
        module,
        "_sha256",
        Mock(side_effect=[module.LATENTSYNC_WEIGHT_SHA256, module.SYNCNET_WEIGHT_SHA256]),
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        Mock(return_value=Mock(stdout=f"{module.LATENTSYNC_COMMIT}\n")),
    )

    with pytest.raises(ValueError, match="expose only"):
        module._validate(args)


def test_atomic_json_cleans_partial_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    output = tmp_path / "result.json"
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("disk failure")))

    with pytest.raises(OSError, match="disk failure"):
        module._atomic_json({"version": 1}, output)

    assert list(tmp_path.iterdir()) == []


def test_contained_file_rejects_escape_and_missing_file(tmp_path: Path) -> None:
    module = _load_script()
    root = tmp_path / "acceptance"
    root.mkdir()
    inside = root / "asset.mp4"
    inside.touch()
    outside = tmp_path / "private.mp4"
    outside.touch()

    assert module._contained_file(root, "asset.mp4", "Asset") == inside
    with pytest.raises(ValueError, match="inside"):
        module._contained_file(root, outside, "Asset")
    with pytest.raises(ValueError, match="inside"):
        module._contained_file(root, "missing.mp4", "Asset")
