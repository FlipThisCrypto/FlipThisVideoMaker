import asyncio
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from flipthis_video_maker.api.render_profiles import (
    load_configured_render_profiles,
    resolve_render_profile,
)
from flipthis_video_maker.api.schemas import (
    AssetRead,
    CharacterCreate,
    CharacterRead,
    JobRead,
    ProjectCreate,
    ProjectPatch,
    ProjectRead,
    ProjectRenderRequest,
    RenderProfileCatalogRead,
    RenderRead,
    ShotPatch,
    ShotRead,
    VideoChainClipCreate,
    VideoChainClipRead,
    VideoChainCreate,
    VideoChainRead,
    VoiceProfileCreate,
    VoiceProfileRead,
    WorkerRead,
)
from flipthis_video_maker.config.render_finalization import (
    RENDER_FINALIZATION_EXECUTION_KEY,
)
from flipthis_video_maker.config.render_profiles import RENDER_PROFILE_EXECUTION_KEY
from flipthis_video_maker.config.settings import Settings, get_settings
from flipthis_video_maker.config.workers import load_worker_configuration
from flipthis_video_maker.contracts.video_generation import (
    CapturedFallbackPolicy,
    ChainClipState,
    ChainState,
    ContinuationMode,
    FirstLastFrameGenerationRequest,
    LipSyncMode,
    RetryContinuation,
)
from flipthis_video_maker.database.session import get_db
from flipthis_video_maker.domain.enums import JobState, ShotStatus
from flipthis_video_maker.domain.models import (
    Asset,
    Candidate,
    Character,
    Job,
    Project,
    Render,
    Scene,
    Shot,
    VideoChain,
    VideoChainClip,
    VoiceProfile,
    Worker,
)
from flipthis_video_maker.media.ffmpeg import MediaError
from flipthis_video_maker.providers.base.models import Capability
from flipthis_video_maker.providers.planning.deterministic import DeterministicStoryPlanner
from flipthis_video_maker.providers.registry import (
    configured_first_last_frame_provider,
    configured_interpolation_provider,
    configured_lip_sync_provider,
    configured_story_planner,
    provider_health_records,
    provider_records,
)
from flipthis_video_maker.scheduler.gpu import discover_gpus
from flipthis_video_maker.services.asset_inputs import AssetInputError
from flipthis_video_maker.services.jobs import request_cancellation, retry
from flipthis_video_maker.services.planning import apply_story_plan
from flipthis_video_maker.services.render_finalization import (
    RenderFinalizationInputError,
    capture_render_finalization,
)
from flipthis_video_maker.services.video_chains import (
    VideoChainConflict,
    accept_chain_clip,
    assemble_video_chain,
    create_video_chain,
    enqueue_chain_clip,
    reject_chain_clip,
)
from flipthis_video_maker.services.video_streaming import publish_hls_buffer
from flipthis_video_maker.services.workers import list_workers, worker_is_online
from flipthis_video_maker.storage.assets import register_asset
from flipthis_video_maker.storage.uploads import UploadValidationError, store_validated_upload

router = APIRouter(prefix="/api/v1")
DB = Annotated[Session, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]


def require[T](db: Session, model: type[T], identifier: str) -> T:
    value = db.get(model, identifier)
    if value is None:
        raise HTTPException(404, f"{model.__name__} not found")
    return value


@router.get("/health")
def health(db: DB) -> dict[str, object]:
    db.execute(select(Project.id).limit(1))
    return {"status": "ok", "database": "ok", "version": "0.1.0"}


@router.post("/projects", response_model=ProjectRead, status_code=201)
def create_project(body: ProjectCreate, db: DB, settings: Config) -> Project:
    requested_profile = body.resolution_profile or settings.default_render_profile
    execution = resolve_render_profile(settings, requested_profile)
    identifier = str(uuid.uuid4())
    root = settings.data_dir / identifier
    for directory in (
        "source",
        "characters",
        "audio",
        "keyframes",
        "shots",
        "scenes",
        "renders",
        "logs",
        "manifests",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)
    values = body.model_dump()
    values["resolution_profile"] = execution.effective_profile
    project = Project(id=identifier, root_asset_directory=str(root), **values)
    db.add(project)
    db.commit()
    return project


@router.get("/projects", response_model=list[ProjectRead])
def list_projects(db: DB) -> list[Project]:
    return list(db.scalars(select(Project).order_by(Project.updated_at.desc())))


@router.get("/projects/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, db: DB) -> Project:
    return require(db, Project, project_id)


@router.patch("/projects/{project_id}", response_model=ProjectRead)
def update_project(project_id: str, body: ProjectPatch, db: DB, settings: Config) -> Project:
    project = require(db, Project, project_id)
    values = body.model_dump(exclude_unset=True, exclude_none=True)
    if body.resolution_profile is not None:
        execution = resolve_render_profile(settings, body.resolution_profile)
        values["resolution_profile"] = execution.effective_profile
    for key, value in values.items():
        setattr(project, key, value)
    db.commit()
    return project


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, db: DB) -> None:
    db.delete(require(db, Project, project_id))
    db.commit()


@router.post("/projects/{project_id}/story")
def ingest_story(project_id: str, content: dict[str, Any], db: DB) -> dict[str, str]:
    project = require(db, Project, project_id)
    project.original_story = str(content.get("text", ""))
    db.commit()
    return {"project_id": project.id, "status": "ingested"}


@router.post("/projects/{project_id}/plan/deterministic", response_model=list[ShotRead])
async def deterministic_plan(project_id: str, db: DB, auto_approve: bool = False) -> list[Shot]:
    project = require(db, Project, project_id)
    try:
        plan = await DeterministicStoryPlanner().plan(project.original_story)
        return apply_story_plan(db, project, plan, auto_approve=auto_approve)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/projects/{project_id}/plan/{provider_id}", response_model=list[ShotRead])
async def configured_plan(
    project_id: str,
    provider_id: str,
    db: DB,
    settings: Config,
    auto_approve: bool = False,
) -> list[Shot]:
    project = require(db, Project, project_id)
    try:
        planner = configured_story_planner(settings.provider_config, provider_id)
        plan = await planner.plan(project.original_story)
        return apply_story_plan(db, project, plan, auto_approve=auto_approve)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except Exception as error:
        raise HTTPException(502, f"Planner backend failed: {type(error).__name__}") from error


@router.post("/projects/{project_id}/characters", response_model=CharacterRead, status_code=201)
def create_character(project_id: str, body: CharacterCreate, db: DB) -> Character:
    require(db, Project, project_id)
    item = Character(project_id=project_id, **body.model_dump())
    db.add(item)
    db.commit()
    return item


@router.get("/projects/{project_id}/characters", response_model=list[CharacterRead])
def list_characters(project_id: str, db: DB) -> list[Character]:
    return list(db.scalars(select(Character).where(Character.project_id == project_id)))


@router.post(
    "/characters/{character_id}/voice-profiles",
    response_model=VoiceProfileRead,
    status_code=201,
)
def create_voice(character_id: str, body: VoiceProfileCreate, db: DB) -> VoiceProfile:
    require(db, Character, character_id)
    voice = VoiceProfile(character_id=character_id, **body.model_dump())
    db.add(voice)
    db.commit()
    return voice


@router.get("/projects/{project_id}/shots", response_model=list[ShotRead])
def list_shots(project_id: str, db: DB) -> list[Shot]:
    return list(
        db.scalars(
            select(Shot)
            .join(Scene)
            .where(Scene.project_id == project_id)
            .order_by(Scene.number, Shot.sequence_number)
        )
    )


@router.patch("/shots/{shot_id}", response_model=ShotRead)
def patch_shot(shot_id: str, body: ShotPatch, db: DB) -> Shot:
    shot = require(db, Shot, shot_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(shot, key, value)
    db.commit()
    return shot


@router.post("/shots/{shot_id}/approve", response_model=ShotRead)
def approve_shot(shot_id: str, db: DB) -> Shot:
    shot = require(db, Shot, shot_id)
    shot.approval_state = "approved"
    shot.status = ShotStatus.APPROVED.value
    db.commit()
    return shot


@router.post("/shots/{shot_id}/candidates/{candidate_id}/select")
def select_candidate(shot_id: str, candidate_id: str, db: DB) -> dict[str, str]:
    shot, candidate = require(db, Shot, shot_id), require(db, Candidate, candidate_id)
    if candidate.shot_id != shot.id:
        raise HTTPException(400, "Candidate does not belong to shot")
    for item in shot.candidates:
        item.disposition = "rejected"
    candidate.disposition = "selected"
    shot.selected_candidate_id = candidate.id
    shot.actual_start_frame_id = candidate.first_frame_asset_id
    shot.actual_end_frame_id = candidate.last_frame_asset_id
    connected_shots = db.scalars(select(Shot).where(Shot.continuity_source_shot_id == shot.id))
    for connected in connected_shots:
        connected.continuity_source_frame_id = candidate.last_frame_asset_id
    db.commit()
    return {"selected": candidate.id}


@router.post("/projects/{project_id}/render", status_code=202)
def enqueue_render(
    project_id: str,
    db: DB,
    settings: Config,
    body: ProjectRenderRequest | None = None,
) -> JobRead:
    project = require(db, Project, project_id)
    request = body or ProjectRenderRequest()
    project_shots = [shot for scene in project.scenes for shot in scene.shots]
    if not project_shots:
        raise HTTPException(409, "Project has no storyboard shots")
    renderable_states = {
        ShotStatus.APPROVED.value,
        ShotStatus.COMPLETE.value,
        ShotStatus.QA_FAILED.value,
    }
    if any(shot.status not in renderable_states for shot in project_shots):
        raise HTTPException(409, "Every shot must be approved before rendering")
    requested_profile = request.render_profile or project.resolution_profile
    execution = resolve_render_profile(settings, requested_profile)
    try:
        finalization_execution, music_asset = capture_render_finalization(
            db,
            project,
            request.finalization,
        )
    except RenderFinalizationInputError as error:
        status_code = {
            "asset_not_found": 404,
            "asset_project_mismatch": 400,
            "asset_not_audio": 422,
            "asset_outside_project": 409,
            "asset_file_missing": 410,
            "asset_identity_mismatch": 409,
        }[error.code]
        raise HTTPException(status_code, str(error)) from error
    job = Job(
        job_type="mock_project_render",
        project_id=project_id,
        provider="mock",
        gpu_assignment="cpu",
        input_asset_ids=[music_asset.id] if music_asset is not None else [],
        payload={
            RENDER_PROFILE_EXECUTION_KEY: execution.model_dump(mode="json"),
            RENDER_FINALIZATION_EXECUTION_KEY: finalization_execution.model_dump(mode="json"),
        },
    )
    db.add(job)
    db.commit()
    return JobRead.model_validate(job)


@router.post(
    "/projects/{project_id}/video-chains",
    response_model=VideoChainRead,
    status_code=201,
)
def create_chain(project_id: str, body: VideoChainCreate, db: DB) -> VideoChain:
    project = require(db, Project, project_id)
    return create_video_chain(db, project, **body.model_dump())


@router.get("/projects/{project_id}/video-chains", response_model=list[VideoChainRead])
def list_video_chains(project_id: str, db: DB) -> list[VideoChain]:
    require(db, Project, project_id)
    return list(
        db.scalars(
            select(VideoChain)
            .where(VideoChain.project_id == project_id)
            .order_by(VideoChain.created_at.desc())
        )
    )


@router.get("/video-chains/{chain_id}", response_model=VideoChainRead)
def get_video_chain(chain_id: str, db: DB) -> VideoChain:
    return require(db, VideoChain, chain_id)


@router.get("/video-chains/{chain_id}/clips", response_model=list[VideoChainClipRead])
def list_video_chain_clips(chain_id: str, db: DB) -> list[VideoChainClip]:
    require(db, VideoChain, chain_id)
    return list(
        db.scalars(
            select(VideoChainClip)
            .where(VideoChainClip.chain_id == chain_id)
            .order_by(
                VideoChainClip.lineage_version,
                VideoChainClip.sequence_number,
                VideoChainClip.revision,
            )
        )
    )


@router.post(
    "/video-chains/{chain_id}/clips",
    response_model=VideoChainClipRead,
    status_code=202,
)
async def enqueue_video_chain_clip(
    chain_id: str,
    body: VideoChainClipCreate,
    db: DB,
    settings: Config,
) -> VideoChainClip:
    chain = require(db, VideoChain, chain_id)
    project = require(db, Project, chain.project_id)
    predecessor = (
        require(db, VideoChainClip, body.predecessor_clip_id) if body.predecessor_clip_id else None
    )
    try:
        generation_provider = configured_first_last_frame_provider(
            settings.provider_config,
            body.provider_id,
        )
        provider_info = generation_provider.info()
        if Capability.FIRST_LAST_FRAME_GENERATIVE_VIDEO not in provider_info.capabilities:
            raise VideoChainConflict(
                "Selected provider does not advertise true first/last-frame generation"
            )
        unsupported_controls = [
            label
            for value, capability, label in (
                (body.negative_prompt, "negative_prompt", "negative prompt"),
                (body.seed, "seed", "seed"),
                (body.motion_strength, "motion_strength", "motion strength"),
                (
                    body.identity_reference_asset_ids,
                    "identity_reference",
                    "identity references",
                ),
            )
            if value not in {None, "", ()} and capability not in provider_info.supported_inputs
        ]
        if unsupported_controls:
            raise VideoChainConflict(
                "Selected provider does not support: " + ", ".join(unsupported_controls)
            )
        health = await generation_provider.health()
        if health.get("ok") is not True:
            raise VideoChainConflict(
                f"Generation provider health check failed: {health.get('status', 'unknown')}"
            )
        if body.interpolation_provider_id is None:
            raise VideoChainConflict("A production interpolation provider is required")
        interpolator = configured_interpolation_provider(
            settings.provider_config,
            body.interpolation_provider_id,
        )
        interpolation_info = interpolator.info()
        if Capability.INTERPOLATION not in interpolation_info.capabilities:
            raise VideoChainConflict("Selected delivery provider is not an interpolator")
        if not interpolation_info.available:
            raise VideoChainConflict("Interpolation provider runtime is not available")
        if body.lip_sync_mode is not LipSyncMode.SKIP:
            if body.lip_sync_mode is not LipSyncMode.LATENTSYNC:
                raise VideoChainConflict(
                    "Only the implemented LatentSync post-process is selectable"
                )
            if body.lip_sync_provider_id is None:
                raise VideoChainConflict("Lip sync requires a configured provider")
            lip_sync_provider = configured_lip_sync_provider(
                settings.provider_config,
                body.lip_sync_provider_id,
            )
            if Capability.LIP_SYNC not in lip_sync_provider.info().capabilities:
                raise VideoChainConflict("Selected performance provider is not a lip-sync provider")
            lip_sync_health = await lip_sync_provider.health()
            if lip_sync_health.get("ok") is not True:
                raise VideoChainConflict("Lip-sync provider runtime is not available")
        if body.fallback_provider_ids:
            raise VideoChainConflict(
                "Automatic cross-provider fallback is not implemented for generative clips"
            )
        configured_maximum_attempts = settings.max_job_retries + 1
        if (
            body.maximum_attempts is not None
            and body.maximum_attempts != configured_maximum_attempts
        ):
            raise VideoChainConflict(
                "Requested maximum attempts must match the server retry policy"
            )
        execution = resolve_render_profile(settings, body.render_profile)
        if body.provider_id == "luma-ray" and execution.profile.height not in {720, 1080}:
            raise VideoChainConflict("Ray 3.2 production clips require a 720p or 1080p profile")
        if body.provider_id.startswith("ltx-") and (
            execution.profile.width,
            execution.profile.height,
        ) != (1920, 1080):
            raise VideoChainConflict("LTX-2.3 production clips require the 1080p final profile")
        if execution.profile.fps != body.native_requested_fps:
            raise VideoChainConflict("Render profile FPS must match provider-native requested FPS")
        if project.aspect_ratio != "16:9":
            raise VideoChainConflict(
                "Current first/last-frame render profiles support only 16:9 chains"
            )
        continuation_mode = (
            ContinuationMode.REGENERATE_FROM_POINT
            if body.regenerate_from_predecessor
            else ContinuationMode(chain.continuation_mode)
        )
        request = FirstLastFrameGenerationRequest(
            provider_id=body.provider_id,
            provider_model=body.provider_model,
            provider_version=None,
            start_frame_asset_id=body.start_frame_asset_id,
            target_end_frame_asset_id=body.target_end_frame_asset_id,
            prompt=body.prompt,
            negative_prompt=body.negative_prompt,
            duration_seconds=body.duration_seconds,
            native_requested_fps=body.native_requested_fps,
            delivery_fps=body.delivery_fps,
            width=execution.profile.width,
            height=execution.profile.height,
            aspect_ratio=project.aspect_ratio,
            seed=body.seed,
            motion_strength=body.motion_strength,
            camera_direction=body.camera_direction,
            identity_reference_asset_ids=body.identity_reference_asset_ids,
            audio_reference_asset_id=body.audio_reference_asset_id,
            lip_sync_mode=body.lip_sync_mode,
            lip_sync_provider_id=body.lip_sync_provider_id,
            lip_sync_settings=body.lip_sync_settings,
            interpolation_mode=body.interpolation_mode,
            interpolation_provider_id=body.interpolation_provider_id,
            safety=body.safety,
            provider_settings=body.provider_settings,
            captured_render_profile=execution.model_dump(mode="json"),
            captured_fallback_policy=CapturedFallbackPolicy(
                provider_chain=(body.provider_id, *body.fallback_provider_ids),
                allow_degraded_generation_category=False,
                maximum_attempts=configured_maximum_attempts,
            ),
            retry_continuation=RetryContinuation(
                attempt=0,
                continuation_mode=continuation_mode,
                predecessor_clip_id=body.predecessor_clip_id,
            ),
        )
        if request.provider_model != provider_info.model_identity:
            raise VideoChainConflict("Requested model does not match provider capability discovery")
        clip, _job = enqueue_chain_clip(
            db,
            project,
            chain,
            request,
            predecessor=predecessor,
            new_lineage=body.regenerate_from_predecessor,
            gpu_assignment=body.gpu_assignment,
            additional_job_payload={
                RENDER_PROFILE_EXECUTION_KEY: execution.model_dump(mode="json")
            },
        )
        return clip
    except AssetInputError as error:
        status = {
            "asset_not_found": 404,
            "asset_project_mismatch": 400,
            "asset_mime_unsupported": 422,
            "asset_outside_project": 409,
            "asset_file_missing": 410,
            "asset_identity_mismatch": 409,
            "asset_media_invalid": 422,
        }[error.code]
        raise HTTPException(status, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/video-chain-clips/{clip_id}/accept", response_model=VideoChainClipRead)
def accept_video_chain_clip(clip_id: str, db: DB) -> VideoChainClip:
    try:
        return accept_chain_clip(db, require(db, VideoChainClip, clip_id))
    except VideoChainConflict as error:
        raise HTTPException(409, str(error)) from error


@router.post("/video-chain-clips/{clip_id}/reject", response_model=VideoChainClipRead)
def reject_video_chain_clip(clip_id: str, db: DB) -> VideoChainClip:
    try:
        return reject_chain_clip(db, require(db, VideoChainClip, clip_id))
    except VideoChainConflict as error:
        raise HTTPException(409, str(error)) from error


@router.post("/video-chains/{chain_id}/assemble", response_model=AssetRead)
def assemble_chain(chain_id: str, db: DB) -> Asset:
    chain = require(db, VideoChain, chain_id)
    project = require(db, Project, chain.project_id)
    try:
        return assemble_video_chain(db, project, chain)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/video-chains/{chain_id}/stream/publish", response_model=AssetRead)
def publish_chain_stream(chain_id: str, db: DB) -> Asset:
    chain = require(db, VideoChain, chain_id)
    project = require(db, Project, chain.project_id)
    try:
        return publish_hls_buffer(db, project, chain)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/video-chains/{chain_id}/hls/playlist.m3u8")
def chain_playlist(chain_id: str, db: DB) -> FileResponse:
    chain = require(db, VideoChain, chain_id)
    if chain.playlist_asset_id is None:
        raise HTTPException(404, "Video chain has no published playlist")
    asset = require(db, Asset, chain.playlist_asset_id)
    path = Path(asset.file_path)
    if not path.is_file():
        raise HTTPException(410, "Published playlist file is missing")
    return FileResponse(
        path,
        media_type="application/vnd.apple.mpegurl",
        filename="playlist.m3u8",
    )


@router.get("/video-chains/{chain_id}/hls/segments/{filename}")
def chain_segment(chain_id: str, filename: str, db: DB) -> FileResponse:
    chain = require(db, VideoChain, chain_id)
    project = require(db, Project, chain.project_id)
    if Path(filename).name != filename or not filename.endswith(".ts"):
        raise HTTPException(400, "Invalid HLS segment name")
    raw_root = (
        Path(project.root_asset_directory)
        / "chains"
        / chain.id
        / f"lineage-{chain.active_lineage_version}"
        / "hls"
        / "segments"
    )
    root = raw_root.resolve(strict=False)
    raw_path = raw_root / filename
    path = raw_path.resolve(strict=False)
    if not path.is_relative_to(root):
        raise HTTPException(400, "Invalid HLS segment path")
    asset = db.scalar(select(Asset).where(Asset.file_path.in_({str(raw_path), str(path)})))
    if asset is None or asset.project_id != project.id:
        raise HTTPException(404, "HLS segment is not published")
    if not path.is_file():
        raise HTTPException(410, "Published HLS segment file is missing")
    return FileResponse(path, media_type="video/mp2t", filename=filename)


@router.post("/video-chains/{chain_id}/pause", response_model=VideoChainRead)
def pause_chain(chain_id: str, db: DB) -> VideoChain:
    chain = require(db, VideoChain, chain_id)
    if chain.state != ChainState.ACTIVE.value:
        raise HTTPException(409, "Only an active chain can be paused")
    chain.state = ChainState.PAUSED.value
    db.commit()
    return chain


@router.post("/video-chains/{chain_id}/resume", response_model=VideoChainRead)
def resume_chain(chain_id: str, db: DB) -> VideoChain:
    chain = require(db, VideoChain, chain_id)
    if chain.state != ChainState.PAUSED.value:
        raise HTTPException(409, "Only a paused chain can be resumed")
    chain.state = ChainState.ACTIVE.value
    db.commit()
    return chain


@router.post("/video-chains/{chain_id}/cancel", response_model=VideoChainRead)
def cancel_chain(chain_id: str, db: DB) -> VideoChain:
    chain = require(db, VideoChain, chain_id)
    if chain.state in {ChainState.COMPLETE.value, ChainState.CANCELLED.value}:
        raise HTTPException(409, "Video chain is already terminal")
    clips = list(db.scalars(select(VideoChainClip).where(VideoChainClip.chain_id == chain.id)))
    for clip in clips:
        if clip.job_id is None:
            continue
        job = db.get(Job, clip.job_id)
        if job is None or job.state not in {JobState.QUEUED.value, JobState.RUNNING.value}:
            continue
        state = request_cancellation(db, job)
        if state is JobState.CANCELLED:
            clip.state = ChainClipState.CANCELLED.value
    chain.state = ChainState.CANCELLED.value
    db.commit()
    return chain


@router.post("/video-chain-clips/{clip_id}/retry", response_model=JobRead)
def retry_video_chain_clip(clip_id: str, db: DB, settings: Config) -> Job:
    clip = require(db, VideoChainClip, clip_id)
    if clip.job_id is None:
        raise HTTPException(409, "Video chain clip has no Job")
    job = require(db, Job, clip.job_id)
    try:
        retry(db, job, settings.max_job_retries)
        clip.state = ChainClipState.QUEUED.value
        clip.failure_info = {}
        db.commit()
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return job


@router.get("/jobs", response_model=list[JobRead])
def jobs(db: DB) -> list[Job]:
    return list(db.scalars(select(Job).order_by(Job.created_at.desc())))


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: str, db: DB) -> Job:
    return require(db, Job, job_id)


@router.get("/jobs/{job_id}/log")
def job_log(job_id: str, db: DB) -> FileResponse:
    job = require(db, Job, job_id)
    if not job.log_path:
        raise HTTPException(404, "Job log is not available")
    path = Path(job.log_path)
    if not path.is_file():
        raise HTTPException(410, "Job log file is missing")
    return FileResponse(path, media_type="application/x-ndjson", filename=path.name)


@router.get("/jobs/{job_id}/events")
def job_events(job_id: str, request: Request, db: DB) -> StreamingResponse:
    require(db, Job, job_id)
    event_sessions = sessionmaker(db.get_bind(), expire_on_commit=False, class_=Session)

    async def stream() -> AsyncGenerator[str, None]:
        last_payload = ""
        heartbeat = 0
        terminal = {
            JobState.SUCCEEDED.value,
            JobState.FAILED.value,
            JobState.CANCELLED.value,
        }
        while not await request.is_disconnected():
            with event_sessions() as event_db:
                job = event_db.get(Job, job_id)
                if job is None:
                    yield 'event: error\ndata: {"detail":"job not found"}\n\n'
                    return
                payload = JobRead.model_validate(job).model_dump_json()
                state = job.state
            if payload != last_payload:
                yield f"event: job\ndata: {payload}\n\n"
                last_payload = payload
                heartbeat = 0
            elif heartbeat >= 20:
                yield ": heartbeat\n\n"
                heartbeat = 0
            if state in terminal:
                return
            heartbeat += 1
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: DB) -> dict[str, str]:
    job = require(db, Job, job_id)
    try:
        state = request_cancellation(db, job)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    if job.job_type == "video_chain_clip_generation" and state is JobState.CANCELLED:
        clip = db.scalar(select(VideoChainClip).where(VideoChainClip.job_id == job.id))
        if clip is not None:
            clip.state = ChainClipState.CANCELLED.value
            db.commit()
    return {"state": state.value}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, db: DB, settings: Config) -> dict[str, str]:
    job = require(db, Job, job_id)
    try:
        retry(db, job, settings.max_job_retries)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    if job.job_type == "video_chain_clip_generation":
        clip = db.scalar(select(VideoChainClip).where(VideoChainClip.job_id == job.id))
        if clip is not None:
            clip.state = ChainClipState.QUEUED.value
            clip.failure_info = {}
            db.commit()
    return {"state": job.state}


@router.get("/providers")
def providers(settings: Config) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in provider_records(settings.provider_config)]


@router.get("/render-profiles", response_model=RenderProfileCatalogRead)
def render_profiles(settings: Config) -> RenderProfileCatalogRead:
    configured = load_configured_render_profiles(settings)
    return RenderProfileCatalogRead(
        default_profile=settings.default_render_profile,
        profiles=[
            {"name": name, **profile.model_dump(mode="json")}
            for name, profile in configured.profiles.items()
        ],
    )


@router.get("/providers/health")
async def provider_health(settings: Config) -> list[dict[str, Any]]:
    return await provider_health_records(settings.provider_config)


@router.get("/gpus")
def gpus() -> list[dict[str, Any]]:
    return discover_gpus()


@router.get("/workers", response_model=list[WorkerRead])
def workers(db: DB, settings: Config) -> list[dict[str, object]]:
    configured = load_worker_configuration(settings.worker_config).workers
    persisted = {worker.id: worker for worker in list_workers(db)}
    records = [
        _worker_record(
            worker_id=item.id,
            assignment=item.assignment,
            configured=True,
            configured_max_concurrent_jobs=item.max_concurrent_jobs,
            physical_gpu=item.physical_gpu,
            runtime=persisted.pop(item.id, None),
            stale_seconds=settings.worker_stale_seconds,
        )
        for item in configured
    ]
    records.extend(
        _worker_record(
            worker_id=runtime.id,
            assignment=runtime.assignment,
            configured=False,
            configured_max_concurrent_jobs=None,
            physical_gpu=None,
            runtime=runtime,
            stale_seconds=settings.worker_stale_seconds,
        )
        for runtime in persisted.values()
    )
    return records


def _worker_record(
    *,
    worker_id: str,
    assignment: str,
    configured: bool,
    configured_max_concurrent_jobs: int | None,
    physical_gpu: int | None,
    runtime: Worker | None,
    stale_seconds: float,
) -> dict[str, object]:
    return {
        "id": worker_id,
        "assignment": assignment,
        "configured": configured,
        "configured_max_concurrent_jobs": configured_max_concurrent_jobs,
        "physical_gpu": physical_gpu,
        "runtime_state": runtime.state if runtime else None,
        "online": worker_is_online(runtime, stale_seconds) if runtime else False,
        "instance_id": runtime.instance_id if runtime else None,
        "hostname": runtime.hostname if runtime else None,
        "pid": runtime.pid if runtime else None,
        "current_job_id": runtime.current_job_id if runtime else None,
        "started_at": runtime.started_at if runtime else None,
        "last_heartbeat_at": runtime.last_heartbeat_at if runtime else None,
        "stopped_at": runtime.stopped_at if runtime else None,
    }


@router.get("/renders/{render_id}")
def render(render_id: str, db: DB) -> FileResponse:
    item = require(db, Render, render_id)
    return FileResponse(
        item.output_path, media_type="video/mp4", filename=Path(item.output_path).name
    )


@router.get("/renders", response_model=list[RenderRead])
def renders(db: DB) -> list[Render]:
    return list(db.scalars(select(Render).order_by(Render.created_at.desc())))


@router.get("/projects/{project_id}/renders", response_model=list[RenderRead])
def project_renders(project_id: str, db: DB) -> list[Render]:
    require(db, Project, project_id)
    return list(
        db.scalars(
            select(Render).where(Render.project_id == project_id).order_by(Render.created_at.desc())
        )
    )


@router.post(
    "/projects/{project_id}/assets",
    response_model=AssetRead,
    status_code=201,
)
async def upload(
    project_id: str, db: DB, settings: Config, file: Annotated[UploadFile, File()]
) -> Asset:
    project = require(db, Project, project_id)
    allowed = {
        "image/png",
        "image/jpeg",
        "audio/wav",
        "audio/mpeg",
        "text/plain",
        "text/markdown",
        "application/json",
    }
    content_type = file.content_type
    if content_type not in allowed:
        raise HTTPException(415, "Unsupported upload type")
    content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "Upload too large")
    assert content_type is not None
    safe_name = _safe_original_filename(file.filename)
    suffix = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "audio/wav": ".wav",
        "audio/mpeg": ".mp3",
        "text/plain": ".txt",
        "text/markdown": ".md",
        "application/json": ".json",
    }[content_type]
    path = Path(project.root_asset_directory) / "source" / f"{uuid.uuid4().hex}{suffix}"
    try:
        store_validated_upload(
            content=content,
            content_type=content_type,
            destination=path,
        )
    except UploadValidationError as error:
        raise HTTPException(400, str(error)) from error
    try:
        asset = register_asset(
            db,
            project_id=project.id,
            shot_id=None,
            kind="upload",
            path=path,
            provider="upload",
            mime_type=content_type,
            generation_parameters={"original_filename": safe_name},
        )
    except MediaError as error:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "Upload content failed media validation") from error
    db.commit()
    return asset


def _safe_original_filename(filename: str | None) -> str:
    try:
        basename = Path((filename or "upload.bin").replace("\\", "/")).name
    except (OSError, ValueError):
        basename = "upload.bin"
    sanitized = "".join(
        character if character.isprintable() and character not in {"/", "\\"} else "_"
        for character in basename
    ).strip()
    if sanitized in {"", ".", ".."}:
        sanitized = "upload.bin"
    return sanitized[:255]
