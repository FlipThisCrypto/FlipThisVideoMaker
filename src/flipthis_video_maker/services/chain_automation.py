from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from flipthis_video_maker.config.render_profiles import RENDER_PROFILE_EXECUTION_KEY
from flipthis_video_maker.contracts.video_generation import (
    ChainAutomationConfiguration,
    ChainClipState,
    ChainState,
    ContinuationMode,
    FirstLastFrameGenerationRequest,
    LipSyncMode,
    RetryContinuation,
    TargetFrameGenerationRequest,
)
from flipthis_video_maker.domain.enums import JobState
from flipthis_video_maker.domain.models import Asset, Job, Project, VideoChain, VideoChainClip
from flipthis_video_maker.services.target_frames import (
    enqueue_target_frame_generation,
    target_request_from_job,
)
from flipthis_video_maker.services.video_chains import (
    SUPPORTED_KEYFRAME_MIME_TYPES,
    VideoChainConflict,
    accept_chain_clip,
    active_lineage_clips,
    enqueue_chain_clip,
    request_from_clip,
)
from flipthis_video_maker.services.video_streaming import publish_hls_buffer


def configure_chain_automation(
    db: Session,
    chain: VideoChain,
    configuration: ChainAutomationConfiguration,
) -> VideoChain:
    if chain.continuation_mode != ContinuationMode.AUTO_GENERATE_TARGET.value:
        raise VideoChainConflict("Chain continuation mode is not automatic target generation")
    if chain.state in {ChainState.COMPLETE.value, ChainState.CANCELLED.value}:
        raise VideoChainConflict("A terminal chain cannot enable automation")
    chain.automation_config = configuration.model_dump(mode="json")
    if chain.state == ChainState.DRAFT.value:
        chain.state = ChainState.ACTIVE.value
    chain.stream_state = {
        **(chain.stream_state or {}),
        "automation_status": "configured",
        "automation_enabled": configuration.enabled,
        "auto_accept_qa_passed": configuration.auto_accept_qa_passed,
        "last_automation_error": None,
    }
    db.commit()
    return chain


def schedule_chain_replenishment(
    db: Session,
    project: Project,
    chain: VideoChain,
) -> Job | None:
    if chain.project_id != project.id:
        raise VideoChainConflict("Video chain belongs to another project")
    try:
        configuration = ChainAutomationConfiguration.model_validate(chain.automation_config)
    except ValueError:
        _record_status(db, chain, "not_configured")
        return None
    if (
        not configuration.enabled
        or chain.continuation_mode != ContinuationMode.AUTO_GENERATE_TARGET.value
        or chain.state != ChainState.ACTIVE.value
    ):
        _record_status(db, chain, "paused_or_disabled")
        return None

    clips = active_lineage_clips(db, chain)
    accepted = [clip for clip in clips if clip.state == ChainClipState.ACCEPTED.value]
    if not accepted:
        _record_status(db, chain, "awaiting_initial_accepted_clip")
        return None
    tail = clips[-1]
    if tail.state != ChainClipState.ACCEPTED.value:
        _record_status(db, chain, f"waiting_for_clip:{tail.state}")
        return None
    if tail.actual_last_frame_asset_id is None:
        raise VideoChainConflict("Accepted chain tail has no actual decoded final frame")

    published = (chain.stream_state or {}).get("published_duration_seconds", 0.0)
    total_seconds = float(published) if isinstance(published, int | float) else 0.0
    remaining_seconds = max(0.0, total_seconds - chain.playback_position_seconds)
    if remaining_seconds >= chain.buffer_target_seconds:
        _record_status(
            db,
            chain,
            "buffer_target_met",
            total_seconds=total_seconds,
            remaining_seconds=remaining_seconds,
        )
        return None

    if chain.replenishment_job_id is not None:
        existing = db.get(Job, chain.replenishment_job_id)
        if existing is not None and existing.state in {
            JobState.QUEUED.value,
            JobState.RUNNING.value,
            JobState.CANCEL_REQUESTED.value,
        }:
            _record_status(db, chain, f"target_job:{existing.state}")
            return existing
        if (
            existing is not None
            and existing.state == JobState.SUCCEEDED.value
            and len(existing.output_asset_ids) == 1
        ):
            target_asset = db.get(Asset, existing.output_asset_ids[0])
            if target_asset is not None:
                successor = continue_after_generated_target(
                    db, project, chain, existing, target_asset
                )
                return (
                    db.get(Job, successor.job_id)
                    if successor is not None and successor.job_id is not None
                    else None
                )
        if existing is not None and existing.state in {
            JobState.FAILED.value,
            JobState.CANCELLED.value,
        }:
            _record_status(db, chain, f"target_job:{existing.state}:operator_action_required")
            return existing
        if existing is not None:
            _record_status(db, chain, f"target_job:{existing.state}:invalid_terminal_output")
            return None
        chain.replenishment_job_id = None
        db.commit()

    previous = request_from_clip(tail)
    request = TargetFrameGenerationRequest(
        chain_id=chain.id,
        predecessor_clip_id=tail.id,
        continuity_source_asset_id=tail.actual_last_frame_asset_id,
        provider_id=configuration.target_provider_id,
        provider_model=configuration.target_provider_model,
        prompt=configuration.target_prompt,
        negative_prompt=configuration.target_negative_prompt,
        width=previous.width,
        height=previous.height,
        seed=(configuration.target_seed_base + tail.sequence_number + 1) % 4_294_967_296,
        provider_settings=configuration.target_provider_settings,
    )
    job = enqueue_target_frame_generation(
        db,
        project,
        chain,
        request,
        gpu_assignment=configuration.gpu_assignment,
        claim_replenishment_slot=True,
    )
    _record_status(
        db,
        chain,
        "target_job:queued",
        total_seconds=total_seconds,
        remaining_seconds=remaining_seconds,
        replenishment_job_id=job.id,
    )
    return job


def continue_after_generated_target(
    db: Session,
    project: Project,
    chain: VideoChain,
    target_job: Job,
    target_asset: Asset,
) -> VideoChainClip | None:
    if chain.replenishment_job_id != target_job.id:
        return None
    if (
        target_job.project_id != project.id
        or target_job.job_type != "video_chain_target_generation"
        or target_job.state != JobState.SUCCEEDED.value
        or target_asset.project_id != project.id
        or target_asset.id not in target_job.output_asset_ids
        or target_asset.mime_type not in SUPPORTED_KEYFRAME_MIME_TYPES
    ):
        raise VideoChainConflict("Automatic target Job or Asset provenance is invalid")
    target_request = target_request_from_job(target_job)
    if target_request.continuity_source_asset_id not in target_asset.parent_asset_ids:
        raise VideoChainConflict("Automatic target does not descend from its continuity source")
    predecessor = (
        db.get(VideoChainClip, target_request.predecessor_clip_id)
        if target_request.predecessor_clip_id is not None
        else None
    )
    if predecessor is None or predecessor.chain_id != chain.id:
        raise VideoChainConflict("Automatic target predecessor is unavailable")
    existing = db.scalar(
        select(VideoChainClip).where(
            VideoChainClip.predecessor_clip_id == predecessor.id,
            VideoChainClip.lineage_version == chain.active_lineage_version,
        )
    )
    if existing is not None:
        chain.replenishment_job_id = None
        _record_status(db, chain, f"successor_already_queued:{existing.id}")
        return existing
    if chain.state != ChainState.ACTIVE.value:
        chain.replenishment_job_id = None
        _record_status(db, chain, "target_ready_while_paused")
        return None

    previous = request_from_clip(predecessor)
    if (
        previous.lip_sync_mode is not LipSyncMode.SKIP
        or previous.audio_reference_asset_id is not None
    ):
        chain.replenishment_job_id = None
        _record_status(db, chain, "fresh_dialogue_audio_required")
        return None
    configuration = ChainAutomationConfiguration.model_validate(chain.automation_config)
    next_seed = (previous.seed + 1) % 4_294_967_296 if previous.seed is not None else None
    successor_request = FirstLastFrameGenerationRequest.model_validate(
        {
            **previous.model_dump(mode="json"),
            "start_frame_asset_id": target_request.continuity_source_asset_id,
            "target_end_frame_asset_id": target_asset.id,
            "seed": next_seed,
            "retry_continuation": RetryContinuation(
                continuation_mode=ContinuationMode.AUTO_GENERATE_TARGET,
                predecessor_clip_id=predecessor.id,
            ),
        },
    )
    predecessor_job = db.get(Job, predecessor.job_id) if predecessor.job_id is not None else None
    additional_payload: dict[str, object] = {}
    if predecessor_job is not None and RENDER_PROFILE_EXECUTION_KEY in predecessor_job.payload:
        additional_payload[RENDER_PROFILE_EXECUTION_KEY] = predecessor_job.payload[
            RENDER_PROFILE_EXECUTION_KEY
        ]
    clip, _job = enqueue_chain_clip(
        db,
        project,
        chain,
        successor_request,
        predecessor=predecessor,
        gpu_assignment=configuration.gpu_assignment,
        additional_job_payload=additional_payload,
    )
    chain.replenishment_job_id = None
    _record_status(db, chain, f"successor_queued:{clip.id}")
    return clip


def auto_accept_publish_and_replenish(
    db: Session,
    project: Project,
    chain: VideoChain,
    clip: VideoChainClip,
) -> Job | None:
    try:
        configuration = ChainAutomationConfiguration.model_validate(chain.automation_config)
    except ValueError:
        return None
    if (
        not configuration.enabled
        or not configuration.auto_accept_qa_passed
        or chain.continuation_mode != ContinuationMode.AUTO_GENERATE_TARGET.value
        or chain.state != ChainState.ACTIVE.value
    ):
        return None
    if clip.state != ChainClipState.AWAITING_REVIEW.value:
        _record_status(db, chain, f"automation_blocked_clip:{clip.state}")
        return None
    accept_chain_clip(db, clip)
    publish_hls_buffer(db, project, chain)
    return schedule_chain_replenishment(db, project, chain)


def update_playback_position(
    db: Session,
    project: Project,
    chain: VideoChain,
    position_seconds: float,
) -> Job | None:
    published = (chain.stream_state or {}).get("published_duration_seconds", 0.0)
    total_seconds = float(published) if isinstance(published, int | float) else 0.0
    if position_seconds < 0 or position_seconds > total_seconds + 1:
        raise VideoChainConflict("Playback position is outside the validated published prefix")
    chain.playback_position_seconds = min(position_seconds, total_seconds)
    chain.playback_updated_at = datetime.now(UTC)
    chain.stream_state = {
        **(chain.stream_state or {}),
        "playback_position_seconds": chain.playback_position_seconds,
        "remaining_buffer_seconds": max(0.0, total_seconds - chain.playback_position_seconds),
        "playback_updated_at": chain.playback_updated_at.isoformat(),
    }
    db.commit()
    return schedule_chain_replenishment(db, project, chain)


def _record_status(
    db: Session,
    chain: VideoChain,
    status: str,
    **facts: object,
) -> None:
    chain.stream_state = {
        **(chain.stream_state or {}),
        "automation_status": status,
        "replenishment_job_id": chain.replenishment_job_id,
        **facts,
    }
    db.commit()


__all__ = [
    "auto_accept_publish_and_replenish",
    "configure_chain_automation",
    "continue_after_generated_target",
    "schedule_chain_replenishment",
    "update_playback_position",
]
