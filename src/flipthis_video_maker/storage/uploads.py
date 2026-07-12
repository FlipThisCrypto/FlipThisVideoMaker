import json
import uuid
import wave
from pathlib import Path

from PIL import Image


class UploadValidationError(ValueError):
    pass


def store_validated_upload(*, content: bytes, content_type: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.stem}-{uuid.uuid4().hex}.partial{destination.suffix}"
    )
    temporary.write_bytes(content)
    try:
        _validate_file(temporary, content_type)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _validate_file(path: Path, content_type: str) -> None:
    try:
        if content_type in {"image/png", "image/jpeg"}:
            with Image.open(path) as image:
                image.verify()
                expected = "PNG" if content_type == "image/png" else "JPEG"
                if image.format != expected:
                    raise UploadValidationError(
                        f"Upload content is {image.format}, expected {expected}"
                    )
        elif content_type == "audio/wav":
            with wave.open(str(path), "rb") as audio:
                if audio.getnchannels() < 1 or audio.getframerate() < 8000:
                    raise UploadValidationError("WAV audio has unsupported parameters")
        elif content_type == "application/json":
            json.loads(path.read_text(encoding="utf-8"))
        elif content_type in {"text/plain", "text/markdown"}:
            path.read_text(encoding="utf-8")
        elif content_type in {"audio/mpeg"}:
            if path.stat().st_size < 128:
                raise UploadValidationError("Audio upload is too small to be valid")
        else:
            raise UploadValidationError(f"Unsupported upload type: {content_type}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UploadValidationError(
            f"Upload content failed validation: {type(error).__name__}"
        ) from error
