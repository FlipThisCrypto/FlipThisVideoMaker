from collections.abc import Generator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from flipthis_video_maker.config.render_finalization import (
    RENDER_FINALIZATION_EXECUTION_KEY,
    RenderFinalizationExecution,
)
from flipthis_video_maker.config.render_profiles import RENDER_PROFILE_EXECUTION_KEY
from flipthis_video_maker.config.settings import Settings, get_settings
from flipthis_video_maker.database.session import get_db
from flipthis_video_maker.domain.models import Job, Project
from flipthis_video_maker.main import create_app


def _write_profiles(path: Path, *, quality_width: int = 640) -> Path:
    path.write_text(
        f"""\
version: 1
profiles:
  preview:
    width: 320
    height: 180
    fps: 12
    video_codec: libx264
    audio_codec: aac
    fallback_profile: null
  quality:
    width: {quality_width}
    height: 360
    fps: 24
    video_codec: libx264
    audio_codec: aac
    fallback_profile: preview
""",
        encoding="utf-8",
    )
    return path


def _test_app(db: Session, settings: Settings) -> FastAPI:
    app = create_app()

    def database_override() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    return app


@pytest.mark.asyncio
async def test_render_profile_discovery_and_project_validation(
    db: Session,
    tmp_path: Path,
) -> None:
    profile_path = _write_profiles(tmp_path / "profiles.yaml")
    settings = Settings(
        data_dir=tmp_path / "projects",
        render_profile_config=profile_path,
        default_render_profile="preview",
    )
    transport = httpx.ASGITransport(app=_test_app(db, settings))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        discovery = await client.get("/api/v1/render-profiles")
        assert discovery.status_code == 200
        assert discovery.json() == {
            "default_profile": "preview",
            "profiles": [
                {
                    "name": "preview",
                    "width": 320,
                    "height": 180,
                    "fps": 12,
                    "video_codec": "libx264",
                    "audio_codec": "aac",
                    "fallback_profile": None,
                },
                {
                    "name": "quality",
                    "width": 640,
                    "height": 360,
                    "fps": 24,
                    "video_codec": "libx264",
                    "audio_codec": "aac",
                    "fallback_profile": "preview",
                },
            ],
        }

        created = await client.post("/api/v1/projects", json={"name": "Uses configured default"})
        assert created.status_code == 201
        assert created.json()["resolution_profile"] == "preview"

        invalid_create = await client.post(
            "/api/v1/projects",
            json={"name": "Invalid", "resolution_profile": "not-configured"},
        )
        assert invalid_create.status_code == 422
        assert "Unknown render profile" in invalid_create.json()["detail"]

        project_id = created.json()["id"]
        name_only_update = await client.patch(
            f"/api/v1/projects/{project_id}",
            json={"name": "Profile remains unchanged"},
        )
        assert name_only_update.status_code == 200
        assert name_only_update.json()["resolution_profile"] == "preview"
        invalid_update = await client.patch(
            f"/api/v1/projects/{project_id}",
            json={"name": "Must not persist", "resolution_profile": "not-configured"},
        )
        assert invalid_update.status_code == 422
        persisted = await client.get(f"/api/v1/projects/{project_id}")
        assert persisted.json()["name"] == "Profile remains unchanged"
        assert persisted.json()["resolution_profile"] == "preview"

    assert len(list(db.scalars(select(Project)))) == 1


@pytest.mark.asyncio
async def test_render_and_regeneration_jobs_capture_immutable_profile_execution(
    db: Session,
    tmp_path: Path,
) -> None:
    profile_path = _write_profiles(tmp_path / "profiles.yaml")
    settings = Settings(
        data_dir=tmp_path / "projects",
        render_profile_config=profile_path,
        default_render_profile="preview",
    )
    transport = httpx.ASGITransport(app=_test_app(db, settings))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project = (
            await client.post(
                "/api/v1/projects",
                json={"name": "Snapshot test", "resolution_profile": "preview"},
            )
        ).json()
        scene = (
            await client.post(
                f"/api/v1/projects/{project['id']}/scenes",
                json={"number": 1, "title": "Snapshot scene"},
            )
        ).json()
        shot = (
            await client.post(
                f"/api/v1/scenes/{scene['id']}/shots",
                json={"sequence_number": 1, "duration": 2, "prompt": "Snapshot shot"},
            )
        ).json()
        approved = await client.post(f"/api/v1/shots/{shot['id']}/approve")
        assert approved.status_code == 200

        invalid_render = await client.post(
            f"/api/v1/projects/{project['id']}/render",
            json={"render_profile": "not-configured"},
        )
        assert invalid_render.status_code == 422

        render_response = await client.post(
            f"/api/v1/projects/{project['id']}/render",
            json={"render_profile": "quality"},
        )
        assert render_response.status_code == 202
        render_job_id = render_response.json()["id"]

        default_render_response = await client.post(f"/api/v1/projects/{project['id']}/render")
        assert default_render_response.status_code == 202
        default_render_job_id = default_render_response.json()["id"]

        invalid_regeneration = await client.post(
            f"/api/v1/shots/{shot['id']}/regenerate",
            json={"render_profile": "not-configured"},
        )
        assert invalid_regeneration.status_code == 422

        regeneration_response = await client.post(
            f"/api/v1/shots/{shot['id']}/regenerate",
            json={
                "same_seed": False,
                "prompt": "Only this setting changes",
                "generation_settings": {"steps": 8},
                "render_profile": "quality",
            },
        )
        assert regeneration_response.status_code == 202
        regeneration_job_id = regeneration_response.json()["id"]

    render_job = db.get(Job, render_job_id)
    default_render_job = db.get(Job, default_render_job_id)
    regeneration_job = db.get(Job, regeneration_job_id)
    assert render_job is not None
    assert default_render_job is not None
    assert regeneration_job is not None

    expected_execution = {
        "version": 1,
        "requested_profile": "quality",
        "effective_profile": "quality",
        "profile": {
            "width": 640,
            "height": 360,
            "fps": 24,
            "video_codec": "libx264",
            "audio_codec": "aac",
            "fallback_profile": "preview",
        },
        "fallback_chain": [
            {
                "name": "quality",
                "profile": {
                    "width": 640,
                    "height": 360,
                    "fps": 24,
                    "video_codec": "libx264",
                    "audio_codec": "aac",
                    "fallback_profile": "preview",
                },
            },
            {
                "name": "preview",
                "profile": {
                    "width": 320,
                    "height": 180,
                    "fps": 12,
                    "video_codec": "libx264",
                    "audio_codec": "aac",
                    "fallback_profile": None,
                },
            },
        ],
        "fallback_history": [],
    }
    expected_finalization = RenderFinalizationExecution.compatibility_default().model_dump(
        mode="json"
    )
    assert render_job.payload == {
        RENDER_PROFILE_EXECUTION_KEY: expected_execution,
        RENDER_FINALIZATION_EXECUTION_KEY: expected_finalization,
    }
    default_execution = default_render_job.payload[RENDER_PROFILE_EXECUTION_KEY]
    assert default_execution["requested_profile"] == "preview"
    assert default_execution["effective_profile"] == "preview"
    assert default_execution["profile"]["width"] == 320
    assert regeneration_job.payload == {
        "same_seed": False,
        "prompt": "Only this setting changes",
        "generation_settings": {"steps": 8},
        RENDER_PROFILE_EXECUTION_KEY: expected_execution,
    }

    _write_profiles(profile_path, quality_width=800)
    db.expire_all()
    persisted_render = db.get(Job, render_job_id)
    persisted_default_render = db.get(Job, default_render_job_id)
    persisted_regeneration = db.get(Job, regeneration_job_id)
    assert persisted_render is not None
    assert persisted_default_render is not None
    assert persisted_regeneration is not None
    assert persisted_render.payload[RENDER_PROFILE_EXECUTION_KEY]["profile"]["width"] == 640
    assert persisted_default_render.payload[RENDER_PROFILE_EXECUTION_KEY]["profile"]["width"] == 320
    assert persisted_regeneration.payload[RENDER_PROFILE_EXECUTION_KEY]["profile"]["width"] == 640


@pytest.mark.asyncio
async def test_malformed_job_profile_snapshot_is_reported_without_hiding_other_jobs(
    db: Session,
    tmp_path: Path,
) -> None:
    profile_path = _write_profiles(tmp_path / "profiles.yaml")
    settings = Settings(
        data_dir=tmp_path / "projects",
        render_profile_config=profile_path,
        default_render_profile="preview",
    )
    project = Project(
        name="Malformed snapshot fixture",
        root_asset_directory=str(tmp_path / "project"),
        resolution_profile="preview",
    )
    db.add(project)
    db.flush()
    malformed = Job(
        job_type="mock_project_render",
        project_id=project.id,
        payload={RENDER_PROFILE_EXECUTION_KEY: {}},
    )
    legacy = Job(job_type="mock_project_render", project_id=project.id, payload={})
    db.add_all([malformed, legacy])
    db.commit()
    transport = httpx.ASGITransport(app=_test_app(db, settings))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        collection = await client.get("/api/v1/jobs")
        detail = await client.get(f"/api/v1/jobs/{malformed.id}")

    assert collection.status_code == 200
    records = {item["id"]: item for item in collection.json()}
    assert set(records) == {malformed.id, legacy.id}
    assert records[malformed.id]["render_profile_execution"] is None
    assert records[malformed.id]["render_profile_execution_error"] == "invalid_snapshot"
    assert records[legacy.id]["render_profile_execution"] is None
    assert records[legacy.id]["render_profile_execution_error"] is None
    assert detail.status_code == 200
    assert detail.json()["render_profile_execution_error"] == "invalid_snapshot"
