from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy.orm import Session

from flipthis_video_maker.config.settings import Settings, get_settings
from flipthis_video_maker.database.session import get_db
from flipthis_video_maker.domain.enums import JobState, WorkerState
from flipthis_video_maker.domain.models import Candidate, Job
from flipthis_video_maker.main import create_app
from flipthis_video_maker.services.workers import register_worker, update_worker_status


@pytest.mark.asyncio
async def test_api_starts_on_migrated_schema_and_persists_project(
    db: Session, tmp_path: Path
) -> None:
    app = create_app()

    def database_override() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: Settings(data_dir=tmp_path / "projects")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/v1/health")
        assert health.status_code == 200
        response = await client.post(
            "/api/v1/projects",
            json={
                "name": "API integration",
                "description": "",
                "target_duration": 30,
                "aspect_ratio": "16:9",
                "resolution_profile": "draft",
                "fps": 24,
                "global_visual_style": "",
                "global_negative_prompt": "",
            },
        )
        assert response.status_code == 201
        project_id = response.json()["id"]
        persisted = await client.get(f"/api/v1/projects/{project_id}")
        assert persisted.status_code == 200


@pytest.mark.asyncio
async def test_worker_status_merges_configuration_with_persisted_liveness(
    db: Session, tmp_path: Path
) -> None:
    app = create_app()
    now = datetime.now(UTC)
    cpu = register_worker(
        db,
        "cpu",
        "cpu",
        instance_id="cpu-generation",
        registered_at=now,
    )
    assert update_worker_status(
        db,
        cpu.id,
        cpu.instance_id,
        state=WorkerState.IDLE,
        current_job_id=None,
        heartbeat_at=now,
    )
    stale = register_worker(
        db,
        "gpu0",
        "gpu0",
        instance_id="stale-generation",
        registered_at=now - timedelta(minutes=5),
    )
    assert update_worker_status(
        db,
        stale.id,
        stale.instance_id,
        state=WorkerState.IDLE,
        current_job_id=None,
        heartbeat_at=now - timedelta(minutes=5),
    )
    register_worker(db, "orphan", "cpu", instance_id="orphan-generation")

    def database_override() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: Settings(
        data_dir=tmp_path / "projects",
        worker_stale_seconds=20,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/workers")

    assert response.status_code == 200
    workers = {item["id"]: item for item in response.json()}
    assert workers["cpu"]["online"] is True
    assert workers["cpu"]["runtime_state"] == WorkerState.IDLE.value
    assert workers["gpu0"]["online"] is False
    assert workers["gpu0"]["runtime_state"] == WorkerState.IDLE.value
    assert workers["gpu1"]["runtime_state"] is None
    assert workers["gpu1"]["configured"] is True
    assert workers["orphan"]["configured"] is False


@pytest.mark.asyncio
async def test_resource_crud_uploads_and_candidate_review(db: Session, tmp_path: Path) -> None:
    app = create_app()

    def database_override() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: Settings(data_dir=tmp_path / "projects")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project = (
            await client.post(
                "/api/v1/projects",
                json={"name": "Resource integration", "target_duration": 30},
            )
        ).json()
        character_response = await client.post(
            f"/api/v1/projects/{project['id']}/characters",
            json={"name": "Ash", "consent_provenance": {"kind": "fictional"}},
        )
        assert character_response.status_code == 201
        character = character_response.json()
        voice_response = await client.post(
            f"/api/v1/characters/{character['id']}/voice-profiles",
            json={"provider": "mock", "consent_acknowledged": True},
        )
        assert voice_response.status_code == 201
        voice = voice_response.json()
        patched = await client.patch(
            f"/api/v1/characters/{character['id']}",
            json={
                "wardrobe_rules": "black hoodie",
                "default_voice_profile_id": voice["id"],
            },
        )
        assert patched.json()["default_voice_profile_id"] == voice["id"]

        image_bytes = BytesIO()
        Image.new("RGB", (64, 48), "navy").save(image_bytes, "PNG")
        reference = await client.post(
            f"/api/v1/characters/{character['id']}/references",
            files={"file": ("reference.png", image_bytes.getvalue(), "image/png")},
        )
        assert reference.status_code == 201
        assert (reference.json()["width"], reference.json()["height"]) == (64, 48)
        invalid = await client.post(
            f"/api/v1/characters/{character['id']}/references",
            files={"file": ("fake.png", b"not an image", "image/png")},
        )
        assert invalid.status_code == 400
        assert invalid.json()["error"] == "request_error"

        preview = await client.post(
            f"/api/v1/voice-profiles/{voice['id']}/preview",
            params={"text": "Preview line"},
        )
        assert preview.status_code == 200
        preview_file = await client.get(f"/api/v1/assets/{preview.json()['id']}/file")
        assert preview_file.status_code == 200
        assert preview_file.headers["content-type"].startswith("audio/wav")

        scene_response = await client.post(
            f"/api/v1/projects/{project['id']}/scenes",
            json={"number": 1, "title": "Manual scene"},
        )
        assert scene_response.status_code == 201
        scene = scene_response.json()
        shot_response = await client.post(
            f"/api/v1/scenes/{scene['id']}/shots",
            json={"sequence_number": 1, "duration": 4, "prompt": "Manual shot"},
        )
        assert shot_response.status_code == 201
        shot = shot_response.json()
        second_shot = (
            await client.post(
                f"/api/v1/scenes/{scene['id']}/shots",
                json={"sequence_number": 2, "duration": 3, "prompt": "Cutaway"},
            )
        ).json()
        moved_shot = await client.post(
            f"/api/v1/shots/{second_shot['id']}/move",
            json={"direction": "up"},
        )
        assert moved_shot.status_code == 200
        assert moved_shot.json()["sequence_number"] == 1
        patched_shot = await client.patch(
            f"/api/v1/shots/{shot['id']}",
            json={
                "narration": "A quiet cutaway",
                "model": "mock-video-v2",
                "generation_settings": {"candidate_count": 2},
            },
        )
        assert patched_shot.json()["narration"] == "A quiet cutaway"
        assert patched_shot.json()["model"] == "mock-video-v2"
        assert patched_shot.json()["generation_settings"] == {"candidate_count": 2}

        second_scene = (
            await client.post(
                f"/api/v1/projects/{project['id']}/scenes",
                json={"number": 2, "title": "Second scene"},
            )
        ).json()
        moved_scene = await client.post(
            f"/api/v1/scenes/{second_scene['id']}/move",
            json={"direction": "up"},
        )
        assert moved_scene.status_code == 200
        assert moved_scene.json()["number"] == 1
        approved = await client.post(f"/api/v1/shots/{shot['id']}/approve")
        assert approved.json()["status"] == "approved"

        candidate = Candidate(
            shot_id=shot["id"],
            provider="mock-video",
            model="test-model",
            prompt="Manual shot",
            seed=42,
        )
        db.add(candidate)
        db.commit()
        selected = await client.post(f"/api/v1/shots/{shot['id']}/candidates/{candidate.id}/select")
        assert selected.status_code == 200
        rated = await client.post(f"/api/v1/candidates/{candidate.id}/rating", json={"rating": 5})
        assert rated.json()["user_rating"] == 5
        rejected = await client.post(f"/api/v1/candidates/{candidate.id}/reject")
        assert rejected.json()["disposition"] == "rejected"

        assets = await client.get(f"/api/v1/projects/{project['id']}/assets")
        assert len(assets.json()) == 2

        log_path = tmp_path / "job.jsonl"
        log_path.write_text('{"event":"job_succeeded"}\n', encoding="utf-8")
        job = Job(
            job_type="mock_project_render",
            project_id=project["id"],
            state=JobState.SUCCEEDED.value,
            progress=1,
            current_stage="complete",
            log_path=str(log_path),
        )
        db.add(job)
        db.commit()
        events = await client.get(f"/api/v1/jobs/{job.id}/events")
        assert events.status_code == 200
        assert "event: job" in events.text
        assert '"state":"succeeded"' in events.text
        log = await client.get(f"/api/v1/jobs/{job.id}/log")
        assert log.status_code == 200
        assert "job_succeeded" in log.text
