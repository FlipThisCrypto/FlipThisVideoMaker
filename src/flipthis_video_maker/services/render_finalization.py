from pathlib import Path
from typing import Literal, cast

from sqlalchemy.orm import Session

from flipthis_video_maker.config.render_finalization import (
    RenderFinalizationExecution,
    RenderFinalizationRequest,
    SupportedMusicMime,
)
from flipthis_video_maker.domain.models import Asset, Project
from flipthis_video_maker.media.ffmpeg import checksum

FinalizationInputErrorCode = Literal[
    "asset_not_found",
    "asset_project_mismatch",
    "asset_not_audio",
    "asset_outside_project",
    "asset_file_missing",
    "asset_identity_mismatch",
]

SUPPORTED_MUSIC_MIME_TYPES: frozenset[str] = frozenset({"audio/wav", "audio/mpeg"})


class RenderFinalizationInputError(ValueError):
    """A safe, classified failure while resolving a persisted media input."""

    def __init__(self, code: FinalizationInputErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def capture_render_finalization(
    db: Session,
    project: Project,
    request: RenderFinalizationRequest,
) -> tuple[RenderFinalizationExecution, Asset | None]:
    """Validate request-time inputs and capture their immutable identity."""
    if request.music is None:
        return RenderFinalizationExecution.capture(request), None

    asset = _validated_music_asset(
        db,
        project,
        asset_id=request.music.asset_id,
    )
    execution = RenderFinalizationExecution.capture(
        request,
        music_checksum=asset.checksum,
        music_mime_type=cast(SupportedMusicMime, asset.mime_type),
    )
    return execution, asset


def revalidate_render_finalization(
    db: Session,
    project: Project,
    execution: RenderFinalizationExecution,
) -> Asset | None:
    """Resolve a queued input and prove that its persisted identity still matches."""
    if execution.music is None:
        return None

    return _validated_music_asset(
        db,
        project,
        asset_id=execution.music.asset_id,
        expected_checksum=execution.music.checksum,
        expected_mime_type=execution.music.mime_type,
    )


def _validated_music_asset(
    db: Session,
    project: Project,
    *,
    asset_id: str,
    expected_checksum: str | None = None,
    expected_mime_type: SupportedMusicMime | None = None,
) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise RenderFinalizationInputError(
            "asset_not_found",
            "Background-music Asset was not found",
        )
    if asset.project_id != project.id:
        raise RenderFinalizationInputError(
            "asset_project_mismatch",
            "Background-music Asset belongs to another project",
        )
    if asset.mime_type not in SUPPORTED_MUSIC_MIME_TYPES:
        raise RenderFinalizationInputError(
            "asset_not_audio",
            "Background-music Asset must be WAV or MP3 audio",
        )
    if expected_mime_type is not None and asset.mime_type != expected_mime_type:
        raise RenderFinalizationInputError(
            "asset_identity_mismatch",
            "Background-music Asset MIME type changed after the render was queued",
        )

    project_root = Path(project.root_asset_directory).resolve(strict=False)
    asset_path = Path(asset.file_path).resolve(strict=False)
    if not asset_path.is_relative_to(project_root):
        raise RenderFinalizationInputError(
            "asset_outside_project",
            "Background-music Asset is outside the project data directory",
        )
    if not asset_path.is_file():
        raise RenderFinalizationInputError(
            "asset_file_missing",
            "Background-music Asset file is missing",
        )

    persisted_checksum = asset.checksum
    if expected_checksum is not None and persisted_checksum != expected_checksum:
        raise RenderFinalizationInputError(
            "asset_identity_mismatch",
            "Background-music Asset checksum changed after the render was queued",
        )
    try:
        actual_checksum = checksum(asset_path)
    except OSError as error:
        raise RenderFinalizationInputError(
            "asset_file_missing",
            "Background-music Asset file became unavailable",
        ) from error
    if actual_checksum != persisted_checksum:
        raise RenderFinalizationInputError(
            "asset_identity_mismatch",
            "Background-music Asset file no longer matches its recorded checksum",
        )
    return asset


__all__ = [
    "RenderFinalizationInputError",
    "capture_render_finalization",
    "revalidate_render_finalization",
]
