import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flipthis_video_maker.contracts.video_generation import (
    ChainClipState,
    ChainState,
    ContinuationMode,
    FirstLastFrameGenerationRequest,
)
from flipthis_video_maker.domain.enums import JobState
from flipthis_video_maker.domain.models import Asset, Job, Project, VideoChain, VideoChainClip
from flipthis_video_maker.media.chain_assembly import assemble_shared_boundary_clips
from flipthis_video_maker.services.asset_inputs import validated_asset_input
from flipthis_video_maker.storage.assets import register_asset

FLF_REQUEST_KEY = "first_last_frame_generation_v1"
CHAIN_CLIP_ID_KEY = "video_chain_clip_id"
SUPPORTED_KEYFRAME_MIME_TYPES = frozenset({"image/png", "image/jpeg"})
SUPPORTED_AUDIO_MIME_TYPES = frozenset({"audio/wav", "audio/mpeg"})


class VideoChainConflict(ValueError):
    pass


def create_video_chain(
    db: Session,
    project: Project,
    *,
    name: str,
    description: str = "",
    continuation_mode: ContinuationMode = ContinuationMode.PLANNED_TARGET,
    buffer_target_seconds: float = 30,
) -> VideoChain:
    chain = VideoChain(
        project_id=project.id,
        name=name,
        description=description,
        continuation_mode=continuation_mode.value,
        state=ChainState.DRAFT.value,
        buffer_target_seconds=buffer_target_seconds,
        stream_state={
            "published_segments": 0,
            "buffer_depth_seconds": 0.0,
            "sustainable_real_time_factor": None,
        },
    )
    db.add(chain)
    db.commit()
    return chain


def enqueue_chain_clip(
    db: Session,
    project: Project,
    chain: VideoChain,
    request: FirstLastFrameGenerationRequest,
    *,
    predecessor: VideoChainClip | None = None,
    new_lineage: bool = False,
    gpu_assignment: str = "cpu",
    additional_job_payload: dict[str, Any] | None = None,
) -> tuple[VideoChainClip, Job]:
    if chain.project_id != project.id:
        raise VideoChainConflict("Video chain belongs to another project")
    if chain.state in {
        ChainState.PAUSED.value,
        ChainState.COMPLETE.value,
        ChainState.CANCELLED.value,
    }:
        raise VideoChainConflict("Video chain cannot be extended in its current state")

    start_asset, _ = validated_asset_input(
        db,
        project,
        request.start_frame_asset_id,
        allowed_mime_types=SUPPORTED_KEYFRAME_MIME_TYPES,
    )
    end_asset, _ = validated_asset_input(
        db,
        project,
        request.target_end_frame_asset_id,
        allowed_mime_types=SUPPORTED_KEYFRAME_MIME_TYPES,
    )
    input_asset_ids = [start_asset.id, end_asset.id]
    for identity_asset_id in request.identity_reference_asset_ids:
        identity_asset, _ = validated_asset_input(
            db,
            project,
            identity_asset_id,
            allowed_mime_types=SUPPORTED_KEYFRAME_MIME_TYPES,
        )
        input_asset_ids.append(identity_asset.id)
    if request.audio_reference_asset_id is not None:
        audio_asset, _ = validated_asset_input(
            db,
            project,
            request.audio_reference_asset_id,
            allowed_mime_types=SUPPORTED_AUDIO_MIME_TYPES,
        )
        if (
            audio_asset.duration is None
            or abs(audio_asset.duration - request.duration_seconds) > 0.1
        ):
            raise VideoChainConflict(
                "Standard lip-sync audio must be within 100 ms of the clip duration"
            )
        input_asset_ids.append(audio_asset.id)
    input_asset_ids = list(dict.fromkeys(input_asset_ids))

    lineage_version = chain.active_lineage_version
    if predecessor is None:
        existing = db.scalar(
            select(VideoChainClip.id)
            .where(VideoChainClip.chain_id == chain.id)
            .where(VideoChainClip.lineage_version == lineage_version)
            .limit(1)
        )
        if existing is not None:
            raise VideoChainConflict("An initial clip already exists in the active lineage")
        if request.retry_continuation.predecessor_clip_id is not None:
            raise VideoChainConflict("Initial clip cannot name a predecessor")
        sequence_number = 1
    else:
        if predecessor.chain_id != chain.id:
            raise VideoChainConflict("Predecessor belongs to another video chain")
        if predecessor.state != ChainClipState.ACCEPTED.value:
            raise VideoChainConflict("Only an accepted clip can be continued")
        if predecessor.actual_last_frame_asset_id is None:
            raise VideoChainConflict("Accepted predecessor has no decoded actual final frame")
        if request.start_frame_asset_id != predecessor.actual_last_frame_asset_id:
            raise VideoChainConflict(
                "Successor start Asset must be the predecessor's decoded actual final frame"
            )
        if request.retry_continuation.predecessor_clip_id != predecessor.id:
            raise VideoChainConflict("Request continuation lineage does not match predecessor")
        sequence_number = predecessor.sequence_number + 1
        if new_lineage:
            lineage_version += 1
            chain.active_lineage_version = lineage_version
        elif predecessor.lineage_version != lineage_version:
            raise VideoChainConflict("Predecessor is outside the active lineage")

    clip = VideoChainClip(
        id=str(uuid.uuid4()),
        chain_id=chain.id,
        sequence_number=sequence_number,
        revision=1,
        lineage_version=lineage_version,
        predecessor_clip_id=predecessor.id if predecessor else None,
        planned_start_frame_asset_id=start_asset.id,
        target_end_frame_asset_id=end_asset.id,
        state=ChainClipState.QUEUED.value,
        request_snapshot=request.model_dump(mode="json"),
        request_digest=request.digest(),
    )
    job = Job(
        id=str(uuid.uuid4()),
        job_type="video_chain_clip_generation",
        project_id=project.id,
        provider=request.provider_id,
        gpu_assignment=gpu_assignment,
        state=JobState.QUEUED.value,
        input_asset_ids=input_asset_ids,
        payload={
            **(additional_job_payload or {}),
            FLF_REQUEST_KEY: request.model_dump(mode="json"),
            CHAIN_CLIP_ID_KEY: clip.id,
        },
    )
    clip.job_id = job.id
    chain.state = ChainState.ACTIVE.value
    db.add_all([clip, job])
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise VideoChainConflict(
            "A conflicting successor was queued for this chain lineage"
        ) from error
    return clip, job


def accept_chain_clip(db: Session, clip: VideoChainClip) -> VideoChainClip:
    if clip.state != ChainClipState.AWAITING_REVIEW.value:
        raise VideoChainConflict("Only a validated clip awaiting review can be accepted")
    continuity = clip.result_snapshot.get("continuity_qa", {})
    if not isinstance(continuity, dict) or continuity.get("passed") is not True:
        raise VideoChainConflict("Clip cannot be accepted because continuity QA did not pass")
    clip.state = ChainClipState.ACCEPTED.value
    clip.accepted_at = datetime.now(UTC)
    db.commit()
    return clip


def reject_chain_clip(db: Session, clip: VideoChainClip) -> VideoChainClip:
    if clip.state not in {
        ChainClipState.AWAITING_REVIEW.value,
        ChainClipState.DEGRADED.value,
    }:
        raise VideoChainConflict("Only reviewable clips can be rejected")
    clip.state = ChainClipState.REJECTED.value
    clip.rejected_at = datetime.now(UTC)
    db.commit()
    return clip


def request_from_clip(clip: VideoChainClip) -> FirstLastFrameGenerationRequest:
    request = FirstLastFrameGenerationRequest.model_validate(clip.request_snapshot)
    if request.digest() != clip.request_digest:
        raise RuntimeError("Persisted FLF request digest does not match its immutable snapshot")
    return request


def request_input_asset_ids(request: FirstLastFrameGenerationRequest) -> list[str]:
    return list(
        dict.fromkeys(
            [
                request.start_frame_asset_id,
                request.target_end_frame_asset_id,
                *request.identity_reference_asset_ids,
                *(
                    [request.audio_reference_asset_id]
                    if request.audio_reference_asset_id is not None
                    else []
                ),
            ]
        )
    )


def active_lineage_clips(db: Session, chain: VideoChain) -> list[VideoChainClip]:
    """Resolve the active branch through predecessor links, including inherited prefix clips."""
    tail = db.scalar(
        select(VideoChainClip)
        .where(VideoChainClip.chain_id == chain.id)
        .where(VideoChainClip.lineage_version == chain.active_lineage_version)
        .order_by(VideoChainClip.sequence_number.desc(), VideoChainClip.revision.desc())
        .limit(1)
    )
    if tail is None:
        return []
    reversed_path: list[VideoChainClip] = []
    seen: set[str] = set()
    current: VideoChainClip | None = tail
    while current is not None:
        if current.id in seen or current.chain_id != chain.id:
            raise VideoChainConflict("Video chain predecessor lineage is invalid")
        seen.add(current.id)
        reversed_path.append(current)
        current = (
            db.get(VideoChainClip, current.predecessor_clip_id)
            if current.predecessor_clip_id is not None
            else None
        )
    path = list(reversed(reversed_path))
    if [clip.sequence_number for clip in path] != list(range(1, len(path) + 1)):
        raise VideoChainConflict("Active video-chain predecessor path is not contiguous")
    return path


def assemble_video_chain(db: Session, project: Project, chain: VideoChain) -> Asset:
    if chain.project_id != project.id:
        raise VideoChainConflict("Video chain belongs to another project")
    clips = active_lineage_clips(db, chain)
    if not clips or any(clip.state != ChainClipState.ACCEPTED.value for clip in clips):
        raise VideoChainConflict("Every active-lineage clip must be accepted before assembly")
    paths: list[Path] = []
    parent_ids: list[str] = []
    for clip in clips:
        if clip.delivery_video_asset_id is None:
            raise VideoChainConflict("Accepted clip has no delivery Asset")
        asset, path = validated_asset_input(
            db,
            project,
            clip.delivery_video_asset_id,
            allowed_mime_types=frozenset({"video/mp4"}),
        )
        parent_ids.append(asset.id)
        paths.append(path)
    output = (
        Path(project.root_asset_directory)
        / "chains"
        / chain.id
        / f"lineage-{chain.active_lineage_version}"
        / f"assembled-{uuid.uuid4().hex}.mp4"
    )
    _, facts = assemble_shared_boundary_clips(paths, output)
    asset = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind="assembled_video_chain",
        path=output,
        provider="ffmpeg",
        model="shared-boundary-trim-av-v2",
        parents=parent_ids,
        generation_parameters=facts,
    )
    chain.assembled_asset_id = asset.id
    db.commit()
    return asset


__all__ = [
    "CHAIN_CLIP_ID_KEY",
    "FLF_REQUEST_KEY",
    "VideoChainConflict",
    "accept_chain_clip",
    "active_lineage_clips",
    "assemble_video_chain",
    "create_video_chain",
    "enqueue_chain_clip",
    "reject_chain_clip",
    "request_input_asset_ids",
    "request_from_clip",
]
