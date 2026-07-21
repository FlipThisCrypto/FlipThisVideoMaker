import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def _module() -> ModuleType:
    path = Path("scripts/record-video-visual-review.py").resolve()
    spec = importlib.util.spec_from_file_location("video_visual_review", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(tmp_path: Path) -> tuple[Path, argparse.Namespace]:
    directory = tmp_path / "acceptance"
    (directory / "evidence").mkdir(parents=True)
    delivery = directory / "delivery-600.mp4"
    delivery.write_bytes(b"delivery")
    for name in ("contact-sheet.png", "native-contact-sheet.png"):
        (directory / "evidence" / name).write_bytes(name.encode())
    manifest = {
        "version": 1,
        "passed_technical_qa": True,
        "checksums": {"delivery": hashlib.sha256(b"delivery").hexdigest()},
    }
    (directory / "acceptance-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    args = argparse.Namespace(
        acceptance_directory=directory,
        verdict="passed",
        notes="Coherent continuous motion with stable subjects.",
        reviewer="test_reviewer",
        meaningful_continuous_motion=True,
        no_slideshow_or_crossfade=True,
        no_obvious_morph_or_duplicate_subject=True,
        identity_and_scene_coherent=True,
        no_obvious_final_frame_snap=True,
    )
    return directory, args


def test_visual_review_records_checked_immutable_artifacts(tmp_path: Path) -> None:
    review = _module()
    directory, args = _run(tmp_path)

    payload = review._record(args)

    assert payload["production_visual_acceptance"] is True
    assert all(payload["checks"].values())
    assert set(payload["reviewed_artifacts"]) == {
        "manifest",
        "delivery",
        "contract_contact_sheet",
        "native_contact_sheet",
    }
    with pytest.raises(FileExistsError, match="overwrite"):
        review._record(args)
    assert json.loads((directory / "visual-review.json").read_text()) == payload


def test_visual_review_cannot_pass_missing_check_or_changed_delivery(tmp_path: Path) -> None:
    review = _module()
    directory, args = _run(tmp_path)
    args.identity_and_scene_coherent = False

    with pytest.raises(ValueError, match="every visual check"):
        review._record(args)

    args.verdict = "failed"
    (directory / "delivery-600.mp4").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        review._record(args)


def test_visual_review_binds_optional_face_contact_sheet(tmp_path: Path) -> None:
    review = _module()
    directory, args = _run(tmp_path)
    face_sheet = directory / "evidence" / "face-contact-sheet.png"
    face_sheet.write_bytes(b"lip sync face evidence")

    payload = review._record(args)

    reviewed = payload["reviewed_artifacts"]
    assert reviewed["face_contact_sheet"]["path"] == "evidence/face-contact-sheet.png"
    assert reviewed["face_contact_sheet"]["sha256"] == review._sha256(face_sheet)
