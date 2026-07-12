import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flipthis_video_maker.database.session import Base
from flipthis_video_maker.domain.enums import JobState, ProjectStatus, ShotStatus, WorkerState


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    target_duration: Mapped[float] = mapped_column(Float, default=30)
    aspect_ratio: Mapped[str] = mapped_column(String(20), default="16:9")
    resolution_profile: Mapped[str] = mapped_column(String(30), default="draft")
    fps: Mapped[float] = mapped_column(Float, default=24)
    status: Mapped[str] = mapped_column(String(30), default=ProjectStatus.DRAFT.value)
    root_asset_directory: Mapped[str] = mapped_column(Text)
    default_providers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    global_visual_style: Mapped[str] = mapped_column(Text, default="")
    global_negative_prompt: Mapped[str] = mapped_column(Text, default="")
    seed_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=lambda: {"mode": "fixed", "base": 42}
    )
    original_story: Mapped[str] = mapped_column(Text, default="")
    characters: Mapped[list["Character"]] = relationship(cascade="all, delete-orphan")
    scenes: Mapped[list["Scene"]] = relationship(
        cascade="all, delete-orphan", order_by="Scene.number"
    )


class Character(Base, TimestampMixin):
    __tablename__ = "characters"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    canonical_appearance: Mapped[str] = mapped_column(Text, default="")
    personality_notes: Mapped[str] = mapped_column(Text, default="")
    wardrobe_rules: Mapped[str] = mapped_column(Text, default="")
    color_palette: Mapped[list[str]] = mapped_column(JSON, default=list)
    reference_images: Mapped[list[str]] = mapped_column(JSON, default=list)
    expression_references: Mapped[list[str]] = mapped_column(JSON, default=list)
    pose_references: Mapped[list[str]] = mapped_column(JSON, default=list)
    negative_identity_traits: Mapped[str] = mapped_column(Text, default="")
    model_references: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    consent_provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    default_voice_profile_id: Mapped[str | None] = mapped_column(String(36))
    voice_profiles: Mapped[list["VoiceProfile"]] = relationship(cascade="all, delete-orphan")


class VoiceProfile(Base, TimestampMixin):
    __tablename__ = "voice_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    character_id: Mapped[str] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(80), default="mock")
    model: Mapped[str] = mapped_column(String(120), default="mock-tone-v1")
    reference_audio: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(20), default="en")
    speaking_style: Mapped[str] = mapped_column(String(100), default="neutral")
    speed: Mapped[float] = mapped_column(Float, default=1)
    pitch: Mapped[float] = mapped_column(Float, default=0)
    emotion_defaults: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    consent_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class Scene(Base, TimestampMixin):
    __tablename__ = "scenes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200), default="")
    location: Mapped[str] = mapped_column(Text, default="")
    time_of_day: Mapped[str] = mapped_column(String(80), default="")
    lighting: Mapped[str] = mapped_column(Text, default="")
    characters: Mapped[list[str]] = mapped_column(JSON, default=list)
    props: Mapped[list[str]] = mapped_column(JSON, default=list)
    environment: Mapped[str] = mapped_column(Text, default="")
    continuity_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    shots: Mapped[list["Shot"]] = relationship(
        cascade="all, delete-orphan", order_by="Shot.sequence_number"
    )


class Shot(Base, TimestampMixin):
    __tablename__ = "shots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    shot_type: Mapped[str] = mapped_column(String(80), default="medium")
    duration: Mapped[float] = mapped_column(Float, default=3)
    prompt: Mapped[str] = mapped_column(Text, default="")
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    dialogue: Mapped[str] = mapped_column(Text, default="")
    narration: Mapped[str] = mapped_column(Text, default="")
    speaker: Mapped[str | None] = mapped_column(String(120))
    camera: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    character_positions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    character_actions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    planned_start_frame_id: Mapped[str | None] = mapped_column(String(36))
    planned_end_frame_id: Mapped[str | None] = mapped_column(String(36))
    actual_start_frame_id: Mapped[str | None] = mapped_column(String(36))
    actual_end_frame_id: Mapped[str | None] = mapped_column(String(36))
    continuity_source_frame_id: Mapped[str | None] = mapped_column(String(36))
    continuity_target_frame_id: Mapped[str | None] = mapped_column(String(36))
    continuity_source_shot_id: Mapped[str | None] = mapped_column(String(36))
    transition_type: Mapped[str] = mapped_column(String(40), default="hard_cut")
    overlap_frame_count: Mapped[int] = mapped_column(Integer, default=0)
    seed: Mapped[int] = mapped_column(Integer, default=42)
    provider: Mapped[str] = mapped_column(String(80), default="mock")
    model: Mapped[str] = mapped_column(String(120), default="mock-video-v1")
    generation_settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default=ShotStatus.DRAFT.value)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    approval_state: Mapped[str] = mapped_column(String(30), default="pending")
    selected_candidate_id: Mapped[str | None] = mapped_column(String(36))
    rejected_candidate_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    continuity_packet: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    candidates: Mapped[list["Candidate"]] = relationship(cascade="all, delete-orphan")


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    shot_id: Mapped[str | None] = mapped_column(
        ForeignKey("shots.id", ondelete="SET NULL"), index=True
    )
    type: Mapped[str] = mapped_column(String(80))
    file_path: Mapped[str] = mapped_column(Text, unique=True)
    mime_type: Mapped[str] = mapped_column(String(120))
    checksum: Mapped[str] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[float | None] = mapped_column(Float)
    frame_rate: Mapped[float | None] = mapped_column(Float)
    source_provider: Mapped[str] = mapped_column(String(80), default="")
    model_identifier: Mapped[str] = mapped_column(String(120), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    seed: Mapped[int | None] = mapped_column(Integer)
    generation_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    parent_asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


class Candidate(Base, TimestampMixin):
    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    prompt: Mapped[str] = mapped_column(Text)
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    seed: Mapped[int] = mapped_column(Integer)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generation_seconds: Mapped[float] = mapped_column(Float, default=0)
    gpu: Mapped[str] = mapped_column(String(20), default="cpu")
    input_asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_asset_id: Mapped[str | None] = mapped_column(String(36))
    first_frame_asset_id: Mapped[str | None] = mapped_column(String(36))
    last_frame_asset_id: Mapped[str | None] = mapped_column(String(36))
    qa_results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    user_rating: Mapped[int | None] = mapped_column(Integer)
    disposition: Mapped[str] = mapped_column(String(20), default="pending")


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_type: Mapped[str] = mapped_column(String(80))
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    scene_id: Mapped[str | None] = mapped_column(String(36))
    shot_id: Mapped[str | None] = mapped_column(String(36))
    provider: Mapped[str] = mapped_column(String(80), default="mock")
    gpu_assignment: Mapped[str] = mapped_column(String(20), default="cpu")
    state: Mapped[str] = mapped_column(String(30), default=JobState.QUEUED.value, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    attempt_number: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0)
    current_stage: Mapped[str] = mapped_column(String(120), default="queued")
    error_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    log_path: Mapped[str | None] = mapped_column(Text)
    input_asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Worker(Base, TimestampMixin):
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36), unique=True)
    assignment: Mapped[str] = mapped_column(String(20), index=True)
    state: Mapped[str] = mapped_column(String(20), default=WorkerState.STARTING.value)
    hostname: Mapped[str] = mapped_column(String(255))
    pid: Mapped[int] = mapped_column(Integer)
    current_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Render(Base, TimestampMixin):
    __tablename__ = "renders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    render_profile: Mapped[str] = mapped_column(String(40))
    included_scene_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    included_shot_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_path: Mapped[str] = mapped_column(Text)
    codec: Mapped[str] = mapped_column(String(40), default="libx264")
    resolution: Mapped[str] = mapped_column(String(30), default="854x480")
    frame_rate: Mapped[float] = mapped_column(Float, default=24)
    audio_configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    subtitle_configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    creation_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
