import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class GenerationCategory(StrEnum):
    MOCK_TEST_VIDEO = "mock_test_video"
    STILL_IMAGE_ANIMATION = "still_image_animation"
    FRAME_INTERPOLATION = "frame_interpolation"
    FIRST_FRAME_IMAGE_TO_VIDEO = "first_frame_image_to_video"
    FIRST_LAST_FRAME_GENERATIVE_VIDEO = "first_last_frame_generative_video"
    PERFORMANCE_CONDITIONED_VIDEO = "performance_conditioned_video"
    FRAME_RATE_CONVERSION = "frame_rate_conversion"


class LipSyncMode(StrEnum):
    SKIP = "skip"
    PROVIDER_INTEGRATED = "provider_integrated"
    POST_PROCESS_AUTO = "post_process_auto"
    MUSETALK = "musetalk"
    LATENTSYNC = "latentsync"


class LipSyncEligibility(StrEnum):
    SPEAKING_FACE_VISIBLE = "speaking_face_visible"
    NARRATION_NO_VISIBLE_SPEAKER = "narration_no_visible_speaker"
    MOUTH_HIDDEN = "mouth_hidden"
    MULTIPLE_FACES = "multiple_faces"
    NO_SPEECH = "no_speech"
    EXPLICIT_SKIP = "explicit_skip"


class InterpolationMode(StrEnum):
    DISABLED = "disabled"
    PROVIDER_NATIVE = "provider_native"
    RIFE = "rife"
    MOCK_FFMPEG_MINTERPOLATE = "mock_ffmpeg_minterpolate"


class ContinuationMode(StrEnum):
    PLANNED_TARGET = "planned_target"
    AUTO_GENERATE_TARGET = "auto_generate_target"
    MANUAL_TARGET = "manual_target"
    REGENERATE_FROM_POINT = "regenerate_from_point"
    EXTEND_EXISTING = "extend_existing"


class ChainState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChainClipState(StrEnum):
    PLANNED = "planned"
    QUEUED = "queued"
    GENERATING = "generating"
    INTERPOLATING = "interpolating"
    LIP_SYNCING = "lip_syncing"
    VALIDATING = "validating"
    AWAITING_REVIEW = "awaiting_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class SafetySettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    policy: str = Field(default="provider_default", min_length=1, max_length=100)
    allow_adult_people: bool = True
    disclose_ai_generation: bool = True


class CapturedFallbackPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_chain: tuple[str, ...] = ()
    allow_degraded_generation_category: bool = False
    maximum_attempts: int = Field(default=2, ge=1, le=10)


class RetryContinuation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt: int = Field(default=0, ge=0, le=100)
    continuation_mode: ContinuationMode = ContinuationMode.PLANNED_TARGET
    predecessor_clip_id: str | None = Field(default=None, min_length=1, max_length=36)
    retry_of_job_id: str | None = Field(default=None, min_length=1, max_length=36)


class TargetFrameGenerationRequest(BaseModel):
    """Immutable provider-neutral request for creating a future chain target frame."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    contract_version: Literal[1] = 1
    chain_id: str = Field(min_length=1, max_length=36)
    predecessor_clip_id: str | None = Field(default=None, min_length=1, max_length=36)
    continuity_source_asset_id: str = Field(min_length=1, max_length=36)
    provider_id: str = Field(min_length=1, max_length=120)
    provider_model: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=6000)
    negative_prompt: str = Field(default="", max_length=6000)
    width: int = Field(ge=256, le=8192)
    height: int = Field(ge=256, le=8192)
    seed: int = Field(ge=0, le=4_294_967_295)
    provider_settings: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)

    def digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class ChainAutomationConfiguration(BaseModel):
    """Captured policy for playback-aware target/clip replenishment."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    version: Literal[1] = 1
    enabled: bool = True
    auto_accept_qa_passed: bool = True
    target_provider_id: str = Field(min_length=1, max_length=120)
    target_provider_model: str = Field(min_length=1, max_length=160)
    target_prompt: str = Field(min_length=1, max_length=6000)
    target_negative_prompt: str = Field(default="", max_length=6000)
    target_seed_base: int = Field(default=1000, ge=0, le=4_294_967_295)
    target_provider_settings: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    gpu_assignment: Literal["gpu0", "gpu1"] = "gpu0"


class LipSyncSettings(BaseModel):
    """Provider-neutral speaking-shot intent captured before lip-sync execution."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    eligibility: LipSyncEligibility = LipSyncEligibility.EXPLICIT_SKIP
    speaker_label: str | None = Field(default=None, min_length=1, max_length=200)
    face_index: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if (
            self.eligibility is not LipSyncEligibility.MULTIPLE_FACES
            and self.face_index is not None
        ):
            raise ValueError("Face selection is valid only when multiple faces are declared")
        return self


class FirstLastFrameGenerationRequest(BaseModel):
    """Immutable, versioned intent captured before provider-specific translation."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    version: Literal[1] = 1
    generation_category: Literal[GenerationCategory.FIRST_LAST_FRAME_GENERATIVE_VIDEO] = (
        GenerationCategory.FIRST_LAST_FRAME_GENERATIVE_VIDEO
    )
    provider_id: str = Field(min_length=1, max_length=120)
    provider_model: str = Field(min_length=1, max_length=160)
    provider_version: str | None = Field(default=None, min_length=1, max_length=120)
    start_frame_asset_id: str = Field(min_length=1, max_length=36)
    target_end_frame_asset_id: str = Field(min_length=1, max_length=36)
    prompt: str = Field(min_length=1, max_length=6000)
    negative_prompt: str = Field(default="", max_length=6000)
    duration_seconds: float = Field(default=10, gt=0, le=60)
    native_requested_fps: int = Field(default=24, ge=1, le=120)
    delivery_fps: int = Field(default=60, ge=1, le=120)
    width: int = Field(default=1280, ge=64, le=7680)
    height: int = Field(default=720, ge=64, le=4320)
    aspect_ratio: str = Field(default="16:9", pattern=r"^\d{1,2}:\d{1,2}$")
    seed: int | None = Field(default=None, ge=0, le=4_294_967_295)
    motion_strength: float | None = Field(default=None, ge=0, le=1)
    camera_direction: str = Field(default="natural", min_length=1, max_length=500)
    identity_reference_asset_ids: tuple[str, ...] = ()
    audio_reference_asset_id: str | None = Field(default=None, min_length=1, max_length=36)
    lip_sync_mode: LipSyncMode = LipSyncMode.SKIP
    lip_sync_provider_id: str | None = Field(default=None, min_length=1, max_length=120)
    lip_sync_settings: LipSyncSettings = Field(default_factory=LipSyncSettings)
    interpolation_mode: InterpolationMode = InterpolationMode.RIFE
    interpolation_provider_id: str | None = Field(
        default="rife-local", min_length=1, max_length=120
    )
    safety: SafetySettings = Field(default_factory=SafetySettings)
    provider_settings: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    captured_render_profile: dict[str, JsonValue] = Field(default_factory=dict)
    captured_fallback_policy: CapturedFallbackPolicy = Field(default_factory=CapturedFallbackPolicy)
    retry_continuation: RetryContinuation = Field(default_factory=RetryContinuation)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.start_frame_asset_id == self.target_end_frame_asset_id:
            raise ValueError("Start and target end frames must be distinct Assets")
        if self.native_requested_fps != self.delivery_fps and self.interpolation_mode in {
            InterpolationMode.DISABLED,
            InterpolationMode.PROVIDER_NATIVE,
        }:
            raise ValueError("A real interpolation mode is required when delivery FPS differs")
        if self.interpolation_mode is InterpolationMode.RIFE and not self.interpolation_provider_id:
            raise ValueError("RIFE interpolation requires an interpolation provider")
        if self.interpolation_mode is not InterpolationMode.RIFE and self.interpolation_provider_id:
            raise ValueError("Interpolation provider is valid only for RIFE mode")
        if self.provider_settings and set(self.provider_settings) != {self.provider_id}:
            raise ValueError("Provider settings must use exactly the selected provider namespace")
        if self.lip_sync_mode is not LipSyncMode.SKIP and self.audio_reference_asset_id is None:
            raise ValueError("Lip sync requires a persisted audio reference Asset")
        if self.lip_sync_mode is not LipSyncMode.SKIP and not self.lip_sync_provider_id:
            raise ValueError("Lip sync requires a selected provider")
        if self.lip_sync_mode is LipSyncMode.SKIP and self.lip_sync_provider_id:
            raise ValueError("Skipped lip sync cannot select a provider")
        if self.lip_sync_mode is LipSyncMode.SKIP:
            if self.lip_sync_settings.eligibility not in {
                LipSyncEligibility.NARRATION_NO_VISIBLE_SPEAKER,
                LipSyncEligibility.MOUTH_HIDDEN,
                LipSyncEligibility.MULTIPLE_FACES,
                LipSyncEligibility.NO_SPEECH,
                LipSyncEligibility.EXPLICIT_SKIP,
            }:
                raise ValueError("A visibly speaking face cannot silently skip requested lip sync")
        elif self.lip_sync_settings.eligibility is not LipSyncEligibility.SPEAKING_FACE_VISIBLE:
            raise ValueError("Lip sync is eligible only for one clearly visible speaking face")
        if self.lip_sync_mode is LipSyncMode.LATENTSYNC:
            if self.lip_sync_provider_id != "latentsync-local":
                raise ValueError("LatentSync mode requires the LatentSync provider")
            if self.lip_sync_settings.face_index is not None:
                raise ValueError("LatentSync does not expose deterministic face selection")
        expected_ratio = self.width / self.height
        left, right = (int(value) for value in self.aspect_ratio.split(":"))
        if abs(expected_ratio - left / right) > 0.03:
            raise ValueError("Resolution does not match the declared aspect ratio")
        return self

    @property
    def expected_delivery_frames(self) -> int:
        frames = self.duration_seconds * self.delivery_fps
        rounded = round(frames)
        if abs(frames - rounded) > 1e-9:
            raise ValueError("Duration and delivery FPS do not produce an integral frame count")
        return rounded

    def digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class ProviderTiming(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    submitted_at: datetime
    completed_at: datetime
    provider_seconds: float = Field(ge=0)
    download_seconds: float = Field(ge=0)


class FirstLastFrameGenerationResult(BaseModel):
    """Immutable normalized facts captured after media validation and Asset registration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_job_id: str = Field(min_length=1, max_length=200)
    provider_id: str = Field(min_length=1, max_length=120)
    actual_model: str = Field(min_length=1, max_length=160)
    provider_api_version: str | None = Field(default=None, max_length=120)
    actual_settings: dict[str, JsonValue] = Field(default_factory=dict)
    actual_duration_seconds: float = Field(gt=0)
    actual_native_fps: float = Field(gt=0, le=240)
    actual_width: int = Field(gt=0)
    actual_height: int = Field(gt=0)
    native_video_asset_id: str = Field(min_length=1, max_length=36)
    delivery_video_asset_id: str = Field(min_length=1, max_length=36)
    output_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    actual_first_frame_asset_id: str = Field(min_length=1, max_length=36)
    actual_last_frame_asset_id: str = Field(min_length=1, max_length=36)
    qa_report_asset_id: str = Field(min_length=1, max_length=36)
    provenance: dict[str, JsonValue] = Field(default_factory=dict)
    timing: ProviderTiming
    resource_usage: dict[str, JsonValue] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    continuity_qa: dict[str, JsonValue] = Field(default_factory=dict)


def utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "CapturedFallbackPolicy",
    "ChainAutomationConfiguration",
    "ChainClipState",
    "ChainState",
    "ContinuationMode",
    "FirstLastFrameGenerationRequest",
    "FirstLastFrameGenerationResult",
    "GenerationCategory",
    "InterpolationMode",
    "LipSyncEligibility",
    "LipSyncMode",
    "LipSyncSettings",
    "ProviderTiming",
    "RetryContinuation",
    "SafetySettings",
    "TargetFrameGenerationRequest",
    "utc_now",
]
