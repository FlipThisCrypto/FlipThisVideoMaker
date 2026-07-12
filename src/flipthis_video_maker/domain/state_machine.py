from flipthis_video_maker.domain.enums import ShotStatus

TRANSITIONS: dict[ShotStatus, set[ShotStatus]] = {
    ShotStatus.DRAFT: {ShotStatus.PLANNED, ShotStatus.CANCELLED},
    ShotStatus.PLANNED: {ShotStatus.AWAITING_APPROVAL, ShotStatus.APPROVED, ShotStatus.DRAFT},
    ShotStatus.AWAITING_APPROVAL: {ShotStatus.APPROVED, ShotStatus.DRAFT},
    ShotStatus.APPROVED: {ShotStatus.AUDIO_PENDING, ShotStatus.KEYFRAMES_PENDING},
    ShotStatus.AUDIO_PENDING: {ShotStatus.AUDIO_READY, ShotStatus.FAILED},
    ShotStatus.AUDIO_READY: {ShotStatus.KEYFRAMES_PENDING},
    ShotStatus.KEYFRAMES_PENDING: {ShotStatus.KEYFRAMES_READY, ShotStatus.FAILED},
    ShotStatus.KEYFRAMES_READY: {ShotStatus.VIDEO_PENDING},
    ShotStatus.VIDEO_PENDING: {ShotStatus.VIDEO_GENERATING, ShotStatus.FAILED},
    ShotStatus.VIDEO_GENERATING: {ShotStatus.VIDEO_READY, ShotStatus.FAILED},
    ShotStatus.VIDEO_READY: {
        ShotStatus.LIPSYNC_PENDING,
        ShotStatus.CONTINUITY_PENDING,
        ShotStatus.QA_PENDING,
    },
    ShotStatus.LIPSYNC_PENDING: {ShotStatus.LIPSYNC_READY, ShotStatus.FAILED},
    ShotStatus.LIPSYNC_READY: {ShotStatus.CONTINUITY_PENDING, ShotStatus.QA_PENDING},
    ShotStatus.CONTINUITY_PENDING: {ShotStatus.CONTINUITY_READY, ShotStatus.FAILED},
    ShotStatus.CONTINUITY_READY: {ShotStatus.QA_PENDING},
    ShotStatus.QA_PENDING: {ShotStatus.COMPLETE, ShotStatus.QA_FAILED, ShotStatus.FAILED},
    ShotStatus.QA_FAILED: {ShotStatus.VIDEO_PENDING, ShotStatus.COMPLETE},
    ShotStatus.FAILED: {
        ShotStatus.AUDIO_PENDING,
        ShotStatus.KEYFRAMES_PENDING,
        ShotStatus.VIDEO_PENDING,
    },
    ShotStatus.COMPLETE: {ShotStatus.VIDEO_PENDING},
    ShotStatus.CANCELLED: {ShotStatus.PLANNED},
}


def validate_transition(current: ShotStatus, target: ShotStatus) -> None:
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError(f"Invalid shot state transition: {current} -> {target}")
