from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SubtitleMode = Literal["sidecar", "soft", "burned"]
SupportedMusicMime = Literal["audio/wav", "audio/mpeg"]


class SubtitleFinalizationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    mode: SubtitleMode = "sidecar"
    language: str = Field(default="eng", pattern=r"^[A-Za-z0-9_-]{2,16}$")
    title: str = Field(default="Subtitles", min_length=1, max_length=200)
    default: bool = True
    forced: bool = False

    @field_validator("title")
    @classmethod
    def reject_nul_in_title(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Subtitle title cannot contain NUL characters")
        return value


class AudioFinalizationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    normalize: bool = False
    integrated_lufs: float = Field(default=-16, ge=-70, le=-5)
    loudness_range_lu: float = Field(default=11, ge=1, le=50)
    true_peak_dbfs: float = Field(default=-1.5, ge=-9, le=0)


class MusicFinalizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    asset_id: str = Field(min_length=1, max_length=100)
    gain_db: float = Field(default=-18, ge=-60, le=12)
    loop: bool = True
    threshold: float = Field(default=0.03, ge=0.000_975_63, le=1)
    ratio: float = Field(default=8, ge=1, le=20)
    attack_ms: float = Field(default=20, ge=0.01, le=2000)
    release_ms: float = Field(default=300, ge=0.01, le=9000)


class MusicFinalizationSnapshot(MusicFinalizationRequest):
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: SupportedMusicMime


class RenderFinalizationRequest(BaseModel):
    """Administrator-safe media intent accepted before a render Job is queued."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    subtitle: SubtitleFinalizationSettings = Field(default_factory=SubtitleFinalizationSettings)
    audio: AudioFinalizationSettings = Field(default_factory=AudioFinalizationSettings)
    music: MusicFinalizationRequest | None = None

    @model_validator(mode="after")
    def music_requires_normalization(self) -> Self:
        if self.music is not None and not self.audio.normalize:
            raise ValueError("Background music requires audio normalization")
        return self


class RenderFinalizationExecution(BaseModel):
    """Immutable final-media settings and input identity captured in a persistent Job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    subtitle: SubtitleFinalizationSettings
    audio: AudioFinalizationSettings
    music: MusicFinalizationSnapshot | None = None

    @model_validator(mode="after")
    def music_requires_normalization(self) -> Self:
        if self.music is not None and not self.audio.normalize:
            raise ValueError("Background music requires audio normalization")
        return self

    @classmethod
    def capture(
        cls,
        request: RenderFinalizationRequest,
        *,
        music_checksum: str | None = None,
        music_mime_type: SupportedMusicMime | None = None,
    ) -> Self:
        if request.music is None:
            if music_checksum is not None or music_mime_type is not None:
                raise ValueError("Music identity was supplied without a music request")
            music = None
        else:
            if music_checksum is None or music_mime_type is None:
                raise ValueError("Music checksum and MIME type are required")
            music = MusicFinalizationSnapshot(
                **request.music.model_dump(),
                checksum=music_checksum,
                mime_type=music_mime_type,
            )
        return cls(subtitle=request.subtitle, audio=request.audio, music=music)

    @classmethod
    def compatibility_default(cls) -> Self:
        return cls.capture(RenderFinalizationRequest())


RENDER_FINALIZATION_EXECUTION_KEY = "render_finalization_execution"


def render_finalization_execution_from_payload(
    payload: dict[str, object],
) -> RenderFinalizationExecution:
    raw = payload.get(RENDER_FINALIZATION_EXECUTION_KEY)
    if not isinstance(raw, dict):
        raise ValueError("Job payload has no render-finalization execution snapshot")
    return RenderFinalizationExecution.model_validate(raw)


__all__ = [
    "AudioFinalizationSettings",
    "MusicFinalizationRequest",
    "MusicFinalizationSnapshot",
    "RENDER_FINALIZATION_EXECUTION_KEY",
    "RenderFinalizationExecution",
    "RenderFinalizationRequest",
    "SubtitleFinalizationSettings",
    "SubtitleMode",
    "SupportedMusicMime",
    "render_finalization_execution_from_payload",
]
