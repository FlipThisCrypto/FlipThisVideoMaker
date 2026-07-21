from pathlib import Path
from typing import Literal

from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from flipthis_video_maker.domain.models import Asset, Project
from flipthis_video_maker.media.ffmpeg import checksum

AssetInputErrorCode = Literal[
    "asset_not_found",
    "asset_project_mismatch",
    "asset_mime_unsupported",
    "asset_outside_project",
    "asset_file_missing",
    "asset_identity_mismatch",
    "asset_media_invalid",
]


class AssetInputError(ValueError):
    def __init__(self, code: AssetInputErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def validated_asset_input(
    db: Session,
    project: Project,
    asset_id: str,
    *,
    allowed_mime_types: frozenset[str],
    expected_checksum: str | None = None,
) -> tuple[Asset, Path]:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise AssetInputError("asset_not_found", "Input Asset was not found")
    if asset.project_id != project.id:
        raise AssetInputError(
            "asset_project_mismatch",
            "Input Asset belongs to another project",
        )
    if asset.mime_type not in allowed_mime_types:
        raise AssetInputError(
            "asset_mime_unsupported",
            "Input Asset has an unsupported media type",
        )

    root = Path(project.root_asset_directory).resolve(strict=False)
    path = Path(asset.file_path).resolve(strict=False)
    if not path.is_relative_to(root):
        raise AssetInputError(
            "asset_outside_project",
            "Input Asset is outside the project data directory",
        )
    if not path.is_file():
        raise AssetInputError("asset_file_missing", "Input Asset file is missing")
    if expected_checksum is not None and asset.checksum != expected_checksum:
        raise AssetInputError(
            "asset_identity_mismatch",
            "Input Asset checksum no longer matches the captured request",
        )
    try:
        actual_checksum = checksum(path)
    except OSError as error:
        raise AssetInputError("asset_file_missing", "Input Asset became unavailable") from error
    if actual_checksum != asset.checksum:
        raise AssetInputError(
            "asset_identity_mismatch",
            "Input Asset bytes no longer match persisted provenance",
        )
    if asset.mime_type in {"image/png", "image/jpeg"}:
        try:
            with Image.open(path) as image:
                image.verify()
        except (OSError, UnidentifiedImageError) as error:
            raise AssetInputError(
                "asset_media_invalid",
                "Input image failed decoded validation",
            ) from error
    return asset, path


__all__ = ["AssetInputError", "validated_asset_input"]
