import uuid

from sqlalchemy.orm import Session

from flipthis_video_maker.contracts.video_generation import TargetFrameGenerationRequest
from flipthis_video_maker.domain.enums import JobState
from flipthis_video_maker.domain.models import Job, Project, VideoChain, VideoChainClip
from flipthis_video_maker.services.asset_inputs import validated_asset_input
from flipthis_video_maker.services.video_chains import (
    SUPPORTED_KEYFRAME_MIME_TYPES,
    VideoChainConflict,
)

TARGET_FRAME_REQUEST_KEY = "target_frame_generation_v1"
TARGET_FRAME_REQUEST_DIGEST_KEY = "target_frame_request_digest"
TARGET_FRAME_ASSET_ID_KEY = "generated_target_frame_asset_id"


def enqueue_target_frame_generation(
    db: Session,
    project: Project,
    chain: VideoChain,
    request: TargetFrameGenerationRequest,
    *,
    gpu_assignment: str,
) -> Job:
    if chain.project_id != project.id or request.chain_id != chain.id:
        raise VideoChainConflict("Target-frame request belongs to another chain or project")
    if request.predecessor_clip_id is not None:
        predecessor = db.get(VideoChainClip, request.predecessor_clip_id)
        if (
            predecessor is None
            or predecessor.chain_id != chain.id
            or predecessor.actual_last_frame_asset_id != request.continuity_source_asset_id
        ):
            raise VideoChainConflict(
                "Target-frame continuity source must be the predecessor's decoded final frame"
            )
    source, _ = validated_asset_input(
        db,
        project,
        request.continuity_source_asset_id,
        allowed_mime_types=SUPPORTED_KEYFRAME_MIME_TYPES,
    )
    job = Job(
        id=str(uuid.uuid4()),
        job_type="video_chain_target_generation",
        project_id=project.id,
        provider=request.provider_id,
        gpu_assignment=gpu_assignment,
        state=JobState.QUEUED.value,
        input_asset_ids=[source.id],
        payload={
            TARGET_FRAME_REQUEST_KEY: request.model_dump(mode="json"),
            TARGET_FRAME_REQUEST_DIGEST_KEY: request.digest(),
        },
    )
    db.add(job)
    db.commit()
    return job


def target_request_from_job(job: Job) -> TargetFrameGenerationRequest:
    request = TargetFrameGenerationRequest.model_validate(
        (job.payload or {}).get(TARGET_FRAME_REQUEST_KEY)
    )
    digest = (job.payload or {}).get(TARGET_FRAME_REQUEST_DIGEST_KEY)
    if digest != request.digest():
        raise RuntimeError("Persisted target-frame request digest does not match its snapshot")
    return request


__all__ = [
    "TARGET_FRAME_ASSET_ID_KEY",
    "TARGET_FRAME_REQUEST_DIGEST_KEY",
    "TARGET_FRAME_REQUEST_KEY",
    "enqueue_target_frame_generation",
    "target_request_from_job",
]
