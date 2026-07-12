import asyncio
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from flipthis_video_maker.api.schemas import (
    CharacterCreate,
    CharacterRead,
    JobRead,
    ProjectCreate,
    ProjectRead,
    RenderRead,
    ShotPatch,
    ShotRead,
    VoiceProfileCreate,
    VoiceProfileRead,
)
from flipthis_video_maker.config.settings import Settings, get_settings
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
    VoiceProfile,
)
from flipthis_video_maker.media.ffmpeg import checksum
from flipthis_video_maker.providers.planning.deterministic import DeterministicStoryPlanner
from flipthis_video_maker.providers.registry import (
    configured_story_planner,
    provider_health_records,
    provider_records,
)
from flipthis_video_maker.scheduler.gpu import discover_gpus
from flipthis_video_maker.services.jobs import request_cancellation, retry
from flipthis_video_maker.services.planning import apply_story_plan
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
    project = Project(id=identifier, root_asset_directory=str(root), **body.model_dump())
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
def update_project(project_id: str, body: ProjectCreate, db: DB) -> Project:
    project = require(db, Project, project_id)
    for key, value in body.model_dump().items():
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
def enqueue_render(project_id: str, db: DB) -> JobRead:
    project = require(db, Project, project_id)
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
    job = Job(
        job_type="mock_project_render", project_id=project_id, provider="mock", gpu_assignment="cpu"
    )
    db.add(job)
    db.commit()
    return JobRead.model_validate(job)


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
    return {"state": state.value}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, db: DB, settings: Config) -> dict[str, str]:
    job = require(db, Job, job_id)
    try:
        retry(db, job, settings.max_job_retries)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"state": job.state}


@router.get("/providers")
def providers(settings: Config) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in provider_records(settings.provider_config)]


@router.get("/providers/health")
async def provider_health(settings: Config) -> list[dict[str, Any]]:
    return await provider_health_records(settings.provider_config)


@router.get("/gpus")
def gpus() -> list[dict[str, Any]]:
    return discover_gpus()


@router.get("/workers")
def workers() -> list[dict[str, Any]]:
    return [
        {"id": "cpu", "configured": True},
        {"id": "gpu0", "configured": True},
        {"id": "gpu1", "configured": True},
    ]


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


@router.post("/projects/{project_id}/assets", status_code=201)
async def upload(
    project_id: str, db: DB, settings: Config, file: Annotated[UploadFile, File()]
) -> dict[str, str]:
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
    if file.content_type not in allowed:
        raise HTTPException(415, "Unsupported upload type")
    content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "Upload too large")
    safe_name = Path(file.filename or "upload.bin").name
    path = Path(project.root_asset_directory) / "source" / f"{uuid.uuid4()}-{safe_name}"
    try:
        store_validated_upload(
            content=content,
            content_type=file.content_type or "application/octet-stream",
            destination=path,
        )
    except UploadValidationError as error:
        raise HTTPException(400, str(error)) from error
    asset = Asset(
        project_id=project.id,
        type="upload",
        file_path=str(path),
        mime_type=file.content_type or "application/octet-stream",
        checksum=checksum(path),
        source_provider="upload",
    )
    db.add(asset)
    db.commit()
    return {"id": asset.id, "path": str(path)}
