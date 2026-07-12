from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Capability(StrEnum):
    STORY_PLANNING = "story_planning"
    IMAGE_GENERATION = "image_generation"
    IMAGE_EDITING = "image_editing"
    TTS = "text_to_speech"
    VOICE_CLONING = "voice_cloning"
    VIDEO_GENERATION = "video_generation"
    FIRST_LAST_FRAME_VIDEO = "first_last_frame_video"
    AUDIO_DRIVEN_AVATAR = "audio_driven_avatar"
    LIP_SYNC = "lip_sync"
    INTERPOLATION = "frame_interpolation"
    UPSCALING = "spatial_upscaling"
    AUDIO_GENERATION = "audio_generation"
    QUALITY_ANALYSIS = "quality_analysis"
    MEDIA_RENDERING = "media_rendering"


class ProviderInfo(BaseModel):
    id: str
    name: str
    model_identity: str
    capabilities: set[Capability]
    available: bool
    supported_inputs: set[str] = Field(default_factory=set)
    max_duration_seconds: float | None = None
    max_width: int | None = None
    max_height: int | None = None
    notes: str = ""


class ImageRequest(BaseModel):
    prompt: str
    output_path: Path
    width: int = 854
    height: int = 480
    seed: int = 42
    label: str = "keyframe"
    characters: list[str] = Field(default_factory=list)


class TTSRequest(BaseModel):
    text: str
    output_path: Path
    voice: str = "default"
    speed: float = 1


class VideoRequest(BaseModel):
    prompt: str
    output_path: Path
    start_frame: Path
    end_frame: Path
    duration: float = 3
    fps: int = 24
    audio_path: Path | None = None
    seed: int = 42
    width: int = 854
    height: int = 480
    settings: dict[str, Any] = Field(default_factory=dict)


class PlannerShot(BaseModel):
    scene_number: int
    sequence_number: int
    shot_type: str
    duration: float
    prompt: str
    negative_prompt: str = ""
    dialogue: str = ""
    narration: str = ""
    speaker: str | None = None
    camera_framing: str = "medium"
    camera_movement: str = "static"
    character_positions: dict[str, str] = Field(default_factory=dict)
    character_actions: dict[str, str] = Field(default_factory=dict)
    lighting: str = ""
    mood: str = ""
    start_frame_description: str
    end_frame_description: str
    video_generation_prompt: str
    negative_video_prompt: str = ""
    sound_requirements: list[str] = Field(default_factory=list)
    transition_intent: str = "hard_cut"


class StoryPlan(BaseModel):
    project_title: str
    story_summary: str
    characters: list[dict[str, Any]]
    locations: list[str]
    props: list[str]
    continuity_rules: list[str]
    shots: list[PlannerShot]
