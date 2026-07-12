from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy.orm import Session

from flipthis_video_maker.domain.models import Asset
from flipthis_video_maker.media.ffmpeg import checksum, probe


def register_asset(
    db: Session,
    *,
    project_id: str,
    shot_id: str | None,
    kind: str,
    path: Path,
    provider: str = "mock",
    model: str = "",
    prompt: str = "",
    seed: int | None = None,
    parents: list[str] | None = None,
) -> Asset:
    info = probe(path) if path.suffix.lower() in {".mp4", ".wav", ".mkv", ".mov"} else {}
    video: dict[str, Any] = next(
        (s for s in info.get("streams", []) if s.get("codec_type") == "video"), {}
    )
    image_width: int | None = None
    image_height: int | None = None
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        with Image.open(path) as image:
            image.verify()
            image_width, image_height = image.size
    asset = Asset(
        project_id=project_id,
        shot_id=shot_id,
        type=kind,
        file_path=str(path),
        mime_type={
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".mp4": "video/mp4",
            ".srt": "application/x-subrip",
            ".json": "application/json",
        }.get(path.suffix.lower(), "application/octet-stream"),
        checksum=checksum(path),
        width=video.get("width", image_width),
        height=video.get("height", image_height),
        duration=float(info.get("format", {}).get("duration", 0)) or None,
        frame_rate=_rate(video.get("r_frame_rate")),
        source_provider=provider,
        model_identifier=model,
        prompt=prompt,
        seed=seed,
        parent_asset_ids=parents or [],
    )
    db.add(asset)
    db.flush()
    return asset


def _rate(value: str | None) -> float | None:
    if not value:
        return None
    numerator, denominator = value.split("/")
    return float(numerator) / float(denominator)
