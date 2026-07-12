from enum import StrEnum


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RENDERING = "rendering"
    COMPLETE = "complete"
    FAILED = "failed"


class ShotStatus(StrEnum):
    DRAFT = "draft"
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    AUDIO_PENDING = "audio_pending"
    AUDIO_READY = "audio_ready"
    KEYFRAMES_PENDING = "keyframes_pending"
    KEYFRAMES_READY = "keyframes_ready"
    VIDEO_PENDING = "video_pending"
    VIDEO_GENERATING = "video_generating"
    VIDEO_READY = "video_ready"
    LIPSYNC_PENDING = "lipsync_pending"
    LIPSYNC_READY = "lipsync_ready"
    CONTINUITY_PENDING = "continuity_pending"
    CONTINUITY_READY = "continuity_ready"
    QA_PENDING = "qa_pending"
    QA_FAILED = "qa_failed"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
