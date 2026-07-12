import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from flipthis_video_maker.api.schemas import (
    AssetRead,
    CandidateRating,
    CandidateRead,
    CharacterPatch,
    CharacterRead,
    SceneCreate,
    ScenePatch,
    SceneRead,
    ShotCreate,
    ShotRead,
    ShotRegenerateRequest,
    VoiceProfilePatch,
    VoiceProfileRead,
)
from flipthis_video_maker.config.settings import Settings, get_settings
from flipthis_video_maker.database.session import get_db
from flipthis_video_maker.domain.models import (
    Asset,
    Candidate,
    Character,
    Job,
    Project,
    Scene,
    Shot,
    VoiceProfile,
)
from flipthis_video_maker.providers.base.models import StoryPlan, TTSRequest
from flipthis_video_maker.providers.mock.providers import MockTTSProvider
from flipthis_video_maker.services.planning import apply_story_plan
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


@router.get("/characters/{character_id}", response_model=CharacterRead)
def get_character(character_id: str, db: DB) -> Character:
    return require(db, Character, character_id)


@router.patch("/characters/{character_id}", response_model=CharacterRead)
def patch_character(character_id: str, body: CharacterPatch, db: DB) -> Character:
    character = require(db, Character, character_id)
    changes = body.model_dump(exclude_unset=True)
    voice_id = changes.get("default_voice_profile_id")
    if voice_id is not None:
        voice = require(db, VoiceProfile, voice_id)
        if voice.character_id != character.id:
            raise HTTPException(400, "Default voice profile belongs to another character")
    for key, value in changes.items():
        setattr(character, key, value)
    db.commit()
    return character


@router.delete("/characters/{character_id}", status_code=204)
def delete_character(character_id: str, db: DB) -> None:
    db.delete(require(db, Character, character_id))
    db.commit()


@router.post("/characters/{character_id}/references", response_model=AssetRead, status_code=201)
async def upload_character_reference(
    character_id: str,
    db: DB,
    settings: Config,
    file: Annotated[UploadFile, File()],
) -> Asset:
    character = require(db, Character, character_id)
    project = require(db, Project, character.project_id)
    if file.content_type not in {"image/png", "image/jpeg"}:
        raise HTTPException(415, "Character references must be PNG or JPEG")
    content = await _read_upload(file, settings)
    suffix = ".png" if file.content_type == "image/png" else ".jpg"
    destination = (
        Path(project.root_asset_directory)
        / "characters"
        / character.id
        / "references"
        / f"{uuid.uuid4().hex}{suffix}"
    )
    _store(content, file.content_type, destination)
    asset = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind="character_reference",
        path=destination,
        provider="upload",
    )
    character.reference_images = [*character.reference_images, asset.id]
    db.commit()
    return asset


@router.get("/characters/{character_id}/voice-profiles", response_model=list[VoiceProfileRead])
def list_voice_profiles(character_id: str, db: DB) -> list[VoiceProfile]:
    require(db, Character, character_id)
    return list(
        db.scalars(
            select(VoiceProfile)
            .where(VoiceProfile.character_id == character_id)
            .order_by(VoiceProfile.created_at)
        )
    )


@router.get("/voice-profiles/{voice_id}", response_model=VoiceProfileRead)
def get_voice_profile(voice_id: str, db: DB) -> VoiceProfile:
    return require(db, VoiceProfile, voice_id)


@router.patch("/voice-profiles/{voice_id}", response_model=VoiceProfileRead)
def patch_voice_profile(voice_id: str, body: VoiceProfilePatch, db: DB) -> VoiceProfile:
    voice = require(db, VoiceProfile, voice_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(voice, key, value)
    db.commit()
    return voice


@router.delete("/voice-profiles/{voice_id}", status_code=204)
def delete_voice_profile(voice_id: str, db: DB) -> None:
    voice = require(db, VoiceProfile, voice_id)
    character = require(db, Character, voice.character_id)
    if character.default_voice_profile_id == voice.id:
        character.default_voice_profile_id = None
    db.delete(voice)
    db.commit()


@router.post("/voice-profiles/{voice_id}/reference-audio", response_model=AssetRead)
async def upload_voice_reference(
    voice_id: str,
    db: DB,
    settings: Config,
    file: Annotated[UploadFile, File()],
    consent_acknowledged: bool = False,
) -> Asset:
    voice = require(db, VoiceProfile, voice_id)
    character = require(db, Character, voice.character_id)
    project = require(db, Project, character.project_id)
    if not consent_acknowledged:
        raise HTTPException(400, "Voice-reference consent must be acknowledged")
    if file.content_type not in {"audio/wav", "audio/mpeg"}:
        raise HTTPException(415, "Voice reference must be WAV or MP3")
    content = await _read_upload(file, settings)
    suffix = ".wav" if file.content_type == "audio/wav" else ".mp3"
    destination = (
        Path(project.root_asset_directory)
        / "characters"
        / character.id
        / "voice"
        / f"{uuid.uuid4().hex}{suffix}"
    )
    _store(content, file.content_type, destination)
    asset = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind="voice_reference",
        path=destination,
        provider="upload",
    )
    voice.reference_audio = asset.id
    voice.consent_acknowledged = True
    db.commit()
    return asset


@router.post("/voice-profiles/{voice_id}/preview", response_model=AssetRead)
async def preview_voice(voice_id: str, text: str, db: DB) -> Asset:
    voice = require(db, VoiceProfile, voice_id)
    if voice.provider != "mock":
        raise HTTPException(409, "Voice preview is implemented only for the mock provider")
    character = require(db, Character, voice.character_id)
    project = require(db, Project, character.project_id)
    destination = (
        Path(project.root_asset_directory)
        / "characters"
        / character.id
        / "voice"
        / "previews"
        / f"{uuid.uuid4().hex}.wav"
    )
    await MockTTSProvider().synthesize(
        TTSRequest(text=text, output_path=destination, voice=character.name, speed=voice.speed)
    )
    asset = register_asset(
        db,
        project_id=project.id,
        shot_id=None,
        kind="voice_preview",
        path=destination,
        provider="mock-tts",
        model="mock-tone-v1",
    )
    db.commit()
    return asset


@router.post("/projects/{project_id}/scenes", response_model=SceneRead, status_code=201)
def create_scene(project_id: str, body: SceneCreate, db: DB) -> Scene:
    require(db, Project, project_id)
    scene = Scene(project_id=project_id, **body.model_dump())
    db.add(scene)
    db.commit()
    return scene


@router.get("/projects/{project_id}/scenes", response_model=list[SceneRead])
def list_scenes(project_id: str, db: DB) -> list[Scene]:
    require(db, Project, project_id)
    return list(
        db.scalars(select(Scene).where(Scene.project_id == project_id).order_by(Scene.number))
    )


@router.get("/scenes/{scene_id}", response_model=SceneRead)
def get_scene(scene_id: str, db: DB) -> Scene:
    return require(db, Scene, scene_id)


@router.patch("/scenes/{scene_id}", response_model=SceneRead)
def patch_scene(scene_id: str, body: ScenePatch, db: DB) -> Scene:
    scene = require(db, Scene, scene_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(scene, key, value)
    db.commit()
    return scene


@router.delete("/scenes/{scene_id}", status_code=204)
def delete_scene(scene_id: str, db: DB) -> None:
    db.delete(require(db, Scene, scene_id))
    db.commit()


@router.post("/scenes/{scene_id}/shots", response_model=ShotRead, status_code=201)
def create_shot(scene_id: str, body: ShotCreate, db: DB) -> Shot:
    require(db, Scene, scene_id)
    shot = Shot(scene_id=scene_id, **body.model_dump())
    db.add(shot)
    db.commit()
    return shot


@router.get("/shots/{shot_id}", response_model=ShotRead)
def get_shot(shot_id: str, db: DB) -> Shot:
    return require(db, Shot, shot_id)


@router.delete("/shots/{shot_id}", status_code=204)
def delete_shot(shot_id: str, db: DB) -> None:
    db.delete(require(db, Shot, shot_id))
    db.commit()


@router.post("/shots/{shot_id}/regenerate", status_code=202)
def regenerate_shot(shot_id: str, body: ShotRegenerateRequest, db: DB) -> dict[str, str]:
    shot = require(db, Shot, shot_id)
    scene = require(db, Scene, shot.scene_id)
    job = Job(
        job_type="mock_shot_regeneration",
        project_id=scene.project_id,
        scene_id=scene.id,
        shot_id=shot.id,
        provider="mock",
        gpu_assignment="cpu",
        payload=body.model_dump(exclude_none=True),
    )
    db.add(job)
    db.commit()
    return {"id": job.id, "state": job.state}


@router.post("/projects/{project_id}/plan/manual", response_model=list[ShotRead])
def manual_plan(project_id: str, body: StoryPlan, db: DB, auto_approve: bool = False) -> list[Shot]:
    project = require(db, Project, project_id)
    try:
        return apply_story_plan(db, project, body, auto_approve=auto_approve)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/projects/{project_id}/assets", response_model=list[AssetRead])
def list_assets(project_id: str, db: DB, asset_type: str | None = None) -> list[Asset]:
    require(db, Project, project_id)
    query = select(Asset).where(Asset.project_id == project_id)
    if asset_type:
        query = query.where(Asset.type == asset_type)
    return list(db.scalars(query.order_by(Asset.created_at.desc())))


@router.get("/assets/{asset_id}", response_model=AssetRead)
def get_asset(asset_id: str, db: DB) -> Asset:
    return require(db, Asset, asset_id)


@router.get("/assets/{asset_id}/file")
def asset_file(asset_id: str, db: DB) -> FileResponse:
    asset = require(db, Asset, asset_id)
    path = Path(asset.file_path)
    if not path.is_file():
        raise HTTPException(410, "Asset file is missing")
    return FileResponse(path, media_type=asset.mime_type, filename=path.name)


@router.get("/shots/{shot_id}/candidates", response_model=list[CandidateRead])
def list_candidates(shot_id: str, db: DB) -> list[Candidate]:
    require(db, Shot, shot_id)
    return list(
        db.scalars(
            select(Candidate)
            .where(Candidate.shot_id == shot_id)
            .order_by(Candidate.created_at.desc())
        )
    )


@router.get("/candidates/{candidate_id}", response_model=CandidateRead)
def get_candidate(candidate_id: str, db: DB) -> Candidate:
    return require(db, Candidate, candidate_id)


@router.post("/candidates/{candidate_id}/reject", response_model=CandidateRead)
def reject_candidate(candidate_id: str, db: DB) -> Candidate:
    candidate = require(db, Candidate, candidate_id)
    candidate.disposition = "rejected"
    shot = require(db, Shot, candidate.shot_id)
    if candidate.id not in shot.rejected_candidate_ids:
        shot.rejected_candidate_ids = [*shot.rejected_candidate_ids, candidate.id]
    if shot.selected_candidate_id == candidate.id:
        shot.selected_candidate_id = None
    db.commit()
    return candidate


@router.post("/candidates/{candidate_id}/rating", response_model=CandidateRead)
def rate_candidate(candidate_id: str, body: CandidateRating, db: DB) -> Candidate:
    candidate = require(db, Candidate, candidate_id)
    candidate.user_rating = body.rating
    db.commit()
    return candidate


async def _read_upload(file: UploadFile, settings: Settings) -> bytes:
    maximum = settings.max_upload_mb * 1024 * 1024
    content = await file.read(maximum + 1)
    if len(content) > maximum:
        raise HTTPException(413, "Upload too large")
    return content


def _store(content: bytes, content_type: str | None, destination: Path) -> None:
    if content_type is None:
        raise HTTPException(415, "Upload content type is required")
    try:
        store_validated_upload(
            content=content,
            content_type=content_type,
            destination=destination,
        )
    except UploadValidationError as error:
        raise HTTPException(400, str(error)) from error
