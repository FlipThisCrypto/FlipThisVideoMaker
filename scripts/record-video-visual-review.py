#!/usr/bin/env python3
"""Record an immutable visual-review decision for a completed acceptance run."""

import argparse
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHECKS = (
    "meaningful_continuous_motion",
    "no_slideshow_or_crossfade",
    "no_obvious_morph_or_duplicate_subject",
    "identity_and_scene_coherent",
    "no_obvious_final_frame_snap",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("acceptance_directory", type=Path)
    parser.add_argument("--verdict", choices=("passed", "failed"), required=True)
    parser.add_argument("--notes", required=True)
    parser.add_argument("--reviewer", default="codex_agent_visual_inspection")
    for check in CHECKS:
        parser.add_argument(f"--{check.replace('_', '-')}", action="store_true")
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_run(directory: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    if not directory.is_absolute():
        raise ValueError("Acceptance directory must be absolute")
    manifest_path = directory / "acceptance-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Acceptance manifest is missing or invalid") from error
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("Acceptance manifest contract is invalid")
    paths = {
        "manifest": manifest_path,
        "delivery": directory / "delivery-600.mp4",
        "contract_contact_sheet": directory / "evidence/contact-sheet.png",
        "native_contact_sheet": directory / "evidence/native-contact-sheet.png",
    }
    face_contact_sheet = directory / "evidence/face-contact-sheet.png"
    if face_contact_sheet.exists():
        paths["face_contact_sheet"] = face_contact_sheet
    if not all(path.is_file() for path in paths.values()):
        raise ValueError("Acceptance review artifact is missing")
    expected_delivery = manifest.get("checksums", {}).get("delivery")
    if not isinstance(expected_delivery, str) or _sha256(paths["delivery"]) != expected_delivery:
        raise ValueError("Delivery checksum does not match the acceptance manifest")
    return manifest, paths


def _record(args: argparse.Namespace) -> dict[str, Any]:
    manifest, paths = _load_run(args.acceptance_directory)
    checks = {check: bool(getattr(args, check)) for check in CHECKS}
    if args.verdict == "passed" and (
        not manifest.get("passed_technical_qa") or not all(checks.values())
    ):
        raise ValueError("A passing review requires technical QA and every visual check")
    output = args.acceptance_directory / "visual-review.json"
    if output.exists():
        raise FileExistsError("Refusing to overwrite an existing visual review")
    payload: dict[str, Any] = {
        "version": 1,
        "verdict": args.verdict,
        "production_visual_acceptance": args.verdict == "passed",
        "reviewer": args.reviewer,
        "reviewed_at": datetime.now(UTC).isoformat(),
        "notes": args.notes,
        "checks": checks,
        "reviewed_artifacts": {
            name: {
                "path": str(path.relative_to(args.acceptance_directory)),
                "sha256": _sha256(path),
            }
            for name, path in paths.items()
        },
    }
    partial = output.with_name(f".{output.name}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        partial.replace(output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return payload


def main() -> int:
    args = _parser().parse_args()
    if not args.notes.strip() or not args.reviewer.strip():
        raise ValueError("Review notes and reviewer must be non-empty")
    print(json.dumps(_record(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
