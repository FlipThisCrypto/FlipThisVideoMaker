from datetime import datetime
from typing import Any, Literal

from pydantic import (
    AliasPath,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
)

from flipthis_video_maker.config.render_finalization import (
    RENDER_FINALIZATION_EXECUTION_KEY,
    RenderFinalizationExecution,
    RenderFinalizationRequest,
)
from flipthis_video_maker.config.render_profiles import (
    RENDER_PROFILE_EXECUTION_KEY,
    RenderProfileExecution,
)
from flipthis_video_maker.contracts.video_generation import (
    ContinuationMode,
    FirstLastFrameGenerationRequest,
    InterpolationMode,
    LipSyncMode,
    LipSyncSettings,
    SafetySettings,
)
from flipthis_video_maker.services.video_chains import FLF_REQUEST_KEY


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    target_duration: float = Field(default=30, gt=0, le=3600)
    aspect_ratio: str = "16:9"
    resolution_profile: str | None = Field(default=None, min_length=1, max_length=40)
    fps: float = Field(default=24, ge=1, le=120)
    global_visual_style: str = ""
    global_negative_prompt: str = ""


class ProjectRead(ProjectCreate, ORMModel):
    resolution_profile: str
    id: str
    status: str
    root_asset_directory: str
    original_story: str
    created_at: datetime
    updated_at: datetime


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    target_duration: float | None = Field(default=None, gt=0, le=3600)
    aspect_ratio: str | None = None
    resolution_profile: str | None = Field(default=None, min_length=1, max_length=40)
    fps: float | None = Field(default=None, ge=1, le=120)
    global_visual_style: str | None = None
    global_negative_prompt: str | None = None


class ProjectRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    render_profile: str | None = Field(default=None, min_length=1, max_length=40)
    finalization: RenderFinalizationRequest = Field(default_factory=RenderFinalizationRequest)


class VideoChainCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    continuation_mode: ContinuationMode = ContinuationMode.PLANNED_TARGET
    buffer_target_seconds: float = Field(default=30, ge=10, le=3600)


class VideoChainRead(ORMModel):
    id: str
    project_id: str
    name: str
    description: str
    continuation_mode: str
    state: str
    active_lineage_version: int
    buffer_target_seconds: float
    playlist_asset_id: str | None
    assembled_asset_id: str | None
    stream_state: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class VideoChainClipCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    predecessor_clip_id: str | None = Field(default=None, min_length=1, max_length=36)
    regenerate_from_predecessor: bool = False
    provider_id: str = Field(default="ltx-video-pro", min_length=1, max_length=120)
    provider_model: str = Field(default="ltx-2-3-pro", min_length=1, max_length=160)
    start_frame_asset_id: str = Field(min_length=1, max_length=36)
    target_end_frame_asset_id: str = Field(min_length=1, max_length=36)
    prompt: str = Field(min_length=1, max_length=6000)
    negative_prompt: str = Field(default="", max_length=6000)
    duration_seconds: Literal[10] = 10
    native_requested_fps: Literal[24] = 24
    delivery_fps: Literal[60] = 60
    render_profile: str = Field(default="standard", min_length=1, max_length=40)
    seed: int | None = Field(default=None, ge=0, le=4_294_967_295)
    motion_strength: float | None = Field(default=None, ge=0, le=1)
    camera_direction: str = Field(default="natural", min_length=1, max_length=500)
    identity_reference_asset_ids: tuple[str, ...] = ()
    audio_reference_asset_id: str | None = Field(default=None, min_length=1, max_length=36)
    lip_sync_mode: LipSyncMode = LipSyncMode.SKIP
    lip_sync_provider_id: str | None = Field(default=None, min_length=1, max_length=120)
    lip_sync_settings: LipSyncSettings = Field(default_factory=LipSyncSettings)
    interpolation_mode: InterpolationMode = InterpolationMode.RIFE
    interpolation_provider_id: str | None = Field(default="rife-local", max_length=120)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    provider_settings: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    fallback_provider_ids: tuple[str, ...] = ()
    maximum_attempts: int | None = Field(default=None, ge=1, le=10)
    gpu_assignment: Literal["gpu0", "gpu1"] = "gpu0"


class VideoChainClipRead(ORMModel):
    id: str
    chain_id: str
    sequence_number: int
    revision: int
    lineage_version: int
    predecessor_clip_id: str | None
    planned_start_frame_asset_id: str
    target_end_frame_asset_id: str
    actual_start_frame_asset_id: str | None
    actual_last_frame_asset_id: str | None
    native_video_asset_id: str | None
    delivery_video_asset_id: str | None
    qa_report_asset_id: str | None
    job_id: str | None
    state: str
    request_snapshot: dict[str, Any]
    request_digest: str
    result_snapshot: dict[str, Any]
    provider_job_id: str | None
    provider_warnings: list[str]
    failure_info: dict[str, Any]
    accepted_at: datetime | None
    rejected_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RenderProfileRead(BaseModel):
    name: str
    width: int
    height: int
    fps: int
    video_codec: str
    audio_codec: str
    fallback_profile: str | None


class RenderProfileCatalogRead(BaseModel):
    default_profile: str
    profiles: list[RenderProfileRead]


class CharacterCreate(BaseModel):
    name: str
    description: str = ""
    canonical_appearance: str = ""
    personality_notes: str = ""
    wardrobe_rules: str = ""
    color_palette: list[str] = Field(default_factory=list)
    consent_provenance: dict[str, Any] = Field(default_factory=dict)


class CharacterRead(CharacterCreate, ORMModel):
    id: str
    project_id: str
    reference_images: list[str]
    expression_references: list[str]
    pose_references: list[str]
    negative_identity_traits: str
    model_references: dict[str, Any]
    default_voice_profile_id: str | None


class CharacterPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    canonical_appearance: str | None = None
    personality_notes: str | None = None
    wardrobe_rules: str | None = None
    color_palette: list[str] | None = None
    negative_identity_traits: str | None = None
    model_references: dict[str, Any] | None = None
    consent_provenance: dict[str, Any] | None = None
    default_voice_profile_id: str | None = None


class VoiceProfileCreate(BaseModel):
    provider: str = "mock"
    model: str = "mock-tone-v1"
    reference_audio: str | None = None
    language: str = "en"
    speaking_style: str = "neutral"
    speed: float = Field(default=1, gt=0, le=4)
    pitch: float = Field(default=0, ge=-24, le=24)
    emotion_defaults: dict[str, Any] = Field(default_factory=dict)
    consent_acknowledged: bool = False


class VoiceProfilePatch(BaseModel):
    provider: str | None = None
    model: str | None = None
    reference_audio: str | None = None
    language: str | None = None
    speaking_style: str | None = None
    speed: float | None = Field(default=None, gt=0, le=4)
    pitch: float | None = Field(default=None, ge=-24, le=24)
    emotion_defaults: dict[str, Any] | None = None
    consent_acknowledged: bool | None = None


class VoiceProfileRead(VoiceProfileCreate, ORMModel):
    id: str
    character_id: str
    created_at: datetime
    updated_at: datetime


class SceneCreate(BaseModel):
    number: int = Field(ge=1)
    title: str = ""
    location: str = ""
    time_of_day: str = ""
    lighting: str = ""
    characters: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    environment: str = ""
    continuity_state: dict[str, Any] = Field(default_factory=dict)


class ScenePatch(BaseModel):
    number: int | None = Field(default=None, ge=1)
    title: str | None = None
    location: str | None = None
    time_of_day: str | None = None
    lighting: str | None = None
    characters: list[str] | None = None
    props: list[str] | None = None
    environment: str | None = None
    continuity_state: dict[str, Any] | None = None


class SceneRead(SceneCreate, ORMModel):
    id: str
    project_id: str
    created_at: datetime
    updated_at: datetime


class ShotCreate(BaseModel):
    sequence_number: int = Field(ge=1)
    shot_type: str = "medium"
    duration: float = Field(default=3, gt=0, le=60)
    prompt: str = ""
    negative_prompt: str = ""
    dialogue: str = ""
    narration: str = ""
    speaker: str | None = None
    camera: dict[str, Any] = Field(default_factory=dict)
    character_positions: dict[str, Any] = Field(default_factory=dict)
    character_actions: dict[str, Any] = Field(default_factory=dict)
    transition_type: str = "hard_cut"
    overlap_frame_count: int = Field(default=0, ge=0, le=240)
    seed: int = 42
    provider: str = "mock"
    model: str = "mock-video-v1"
    generation_settings: dict[str, Any] = Field(default_factory=dict)


class ShotPatch(BaseModel):
    sequence_number: int | None = Field(default=None, ge=1)
    shot_type: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    dialogue: str | None = None
    narration: str | None = None
    speaker: str | None = None
    duration: float | None = Field(default=None, gt=0, le=60)
    provider: str | None = None
    model: str | None = None
    seed: int | None = None
    transition_type: str | None = None
    overlap_frame_count: int | None = Field(default=None, ge=0, le=240)
    camera: dict[str, Any] | None = None
    character_positions: dict[str, Any] | None = None
    character_actions: dict[str, Any] | None = None
    generation_settings: dict[str, Any] | None = None


class MoveRequest(BaseModel):
    direction: Literal["up", "down"]


class ShotRead(ORMModel):
    id: str
    scene_id: str
    sequence_number: int
    shot_type: str
    duration: float
    prompt: str
    negative_prompt: str
    dialogue: str
    narration: str
    speaker: str | None
    camera: dict[str, Any]
    character_positions: dict[str, Any]
    character_actions: dict[str, Any]
    status: str
    provider: str
    model: str
    seed: int
    transition_type: str
    overlap_frame_count: int
    planned_start_frame_id: str | None
    planned_end_frame_id: str | None
    actual_start_frame_id: str | None
    actual_end_frame_id: str | None
    continuity_source_frame_id: str | None
    continuity_target_frame_id: str | None
    selected_candidate_id: str | None
    continuity_packet: dict[str, Any]
    approval_state: str
    retry_count: int
    generation_settings: dict[str, Any]


class AssetRead(ORMModel):
    id: str
    project_id: str
    shot_id: str | None
    type: str
    file_path: str
    mime_type: str
    checksum: str
    width: int | None
    height: int | None
    duration: float | None
    frame_rate: float | None
    source_provider: str
    model_identifier: str
    prompt: str
    seed: int | None
    generation_parameters: dict[str, Any]
    parent_asset_ids: list[str]
    created_at: datetime


class CandidateRead(ORMModel):
    id: str
    shot_id: str
    provider: str
    model: str
    prompt: str
    negative_prompt: str
    seed: int
    settings: dict[str, Any]
    generation_seconds: float
    gpu: str
    input_asset_ids: list[str]
    output_asset_id: str | None
    first_frame_asset_id: str | None
    last_frame_asset_id: str | None
    qa_results: dict[str, Any]
    user_rating: int | None
    disposition: str
    created_at: datetime


class CandidateRating(BaseModel):
    rating: int = Field(ge=1, le=5)


class ShotRegenerateRequest(BaseModel):
    same_seed: bool = True
    prompt: str | None = None
    negative_prompt: str | None = None
    generation_settings: dict[str, Any] | None = None
    render_profile: str | None = Field(default=None, min_length=1, max_length=40)


class JobRead(ORMModel):
    id: str
    job_type: str
    project_id: str
    shot_id: str | None
    provider: str
    gpu_assignment: str
    state: str
    progress: float
    current_stage: str
    attempt_number: int
    claimed_by_worker_id: str | None
    claimed_by_instance_id: str | None
    lease_heartbeat_at: datetime | None
    lease_expires_at: datetime | None
    error_info: dict[str, Any]
    log_path: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    render_profile_execution: RenderProfileExecution | None = Field(
        default=None,
        validation_alias=AliasPath("payload", RENDER_PROFILE_EXECUTION_KEY),
    )
    render_profile_execution_error: str | None = Field(
        default=None,
        validation_alias=AliasPath("payload", RENDER_PROFILE_EXECUTION_KEY),
    )
    render_finalization_execution: RenderFinalizationExecution | None = Field(
        default=None,
        validation_alias=AliasPath("payload", RENDER_FINALIZATION_EXECUTION_KEY),
    )
    render_finalization_execution_error: str | None = Field(
        default=None,
        validation_alias=AliasPath("payload", RENDER_FINALIZATION_EXECUTION_KEY),
    )
    first_last_frame_generation: FirstLastFrameGenerationRequest | None = Field(
        default=None,
        validation_alias=AliasPath("payload", FLF_REQUEST_KEY),
    )
    first_last_frame_generation_error: str | None = Field(
        default=None,
        validation_alias=AliasPath("payload", FLF_REQUEST_KEY),
    )

    @field_validator("render_profile_execution", mode="before")
    @classmethod
    def tolerate_invalid_profile_execution(cls, value: object) -> RenderProfileExecution | None:
        if value is None:
            return None
        try:
            return RenderProfileExecution.model_validate(value)
        except ValidationError:
            return None

    @field_validator("render_profile_execution_error", mode="before")
    @classmethod
    def report_invalid_profile_execution(cls, value: object) -> str | None:
        if value is None:
            return None
        try:
            RenderProfileExecution.model_validate(value)
        except ValidationError:
            return "invalid_snapshot"
        return None

    @field_validator("render_finalization_execution", mode="before")
    @classmethod
    def tolerate_invalid_finalization_execution(
        cls, value: object
    ) -> RenderFinalizationExecution | None:
        if value is None:
            return None
        try:
            return RenderFinalizationExecution.model_validate(value)
        except ValidationError:
            return None

    @field_validator("render_finalization_execution_error", mode="before")
    @classmethod
    def report_invalid_finalization_execution(cls, value: object) -> str | None:
        if value is None:
            return None
        try:
            RenderFinalizationExecution.model_validate(value)
        except ValidationError:
            return "invalid_snapshot"
        return None

    @field_validator("first_last_frame_generation", mode="before")
    @classmethod
    def tolerate_invalid_flf_generation(
        cls, value: object
    ) -> FirstLastFrameGenerationRequest | None:
        if value is None:
            return None
        try:
            return FirstLastFrameGenerationRequest.model_validate(value)
        except ValidationError:
            return None

    @field_validator("first_last_frame_generation_error", mode="before")
    @classmethod
    def report_invalid_flf_generation(cls, value: object) -> str | None:
        if value is None:
            return None
        try:
            FirstLastFrameGenerationRequest.model_validate(value)
        except ValidationError:
            return "invalid_snapshot"
        return None


class WorkerRead(BaseModel):
    id: str
    assignment: str
    configured: bool
    configured_max_concurrent_jobs: int | None
    physical_gpu: int | None
    runtime_state: str | None
    online: bool
    instance_id: str | None
    hostname: str | None
    pid: int | None
    current_job_id: str | None
    started_at: datetime | None
    last_heartbeat_at: datetime | None
    stopped_at: datetime | None


class RenderRead(ORMModel):
    id: str
    project_id: str
    render_profile: str
    output_path: str
    codec: str
    resolution: str
    frame_rate: float
    audio_configuration: dict[str, Any]
    subtitle_configuration: dict[str, Any]
    creation_metadata: dict[str, Any]
    created_at: datetime


class ErrorResponse(BaseModel):
    error: str
    detail: str
    request_id: str | None = None
