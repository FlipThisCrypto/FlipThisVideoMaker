from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from flipthis_video_maker.contracts.video_generation import FirstLastFrameGenerationRequest


class ResolvedFirstLastFrameRequest(BaseModel):
    """Immutable provider-neutral snapshot plus server-resolved Asset paths."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    snapshot: FirstLastFrameGenerationRequest
    start_frame_path: Path
    start_frame_mime_type: str
    end_frame_path: Path
    end_frame_mime_type: str
    output_path: Path


class ProviderRunOutput(BaseModel):
    """Normalized provider execution facts consumed by orchestration."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    output_path: Path
    provider_job_id: str
    actual_model: str
    api_version: str | None = None
    submitted_at: datetime
    completed_at: datetime
    provider_seconds: float = Field(ge=0)
    download_seconds: float = Field(ge=0)
    actual_settings: dict[str, object] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()


__all__ = ["ProviderRunOutput", "ResolvedFirstLastFrameRequest"]
