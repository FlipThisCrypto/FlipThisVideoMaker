from collections.abc import Generator
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy.orm import Session

from flipthis_video_maker.api import router as api_router
from flipthis_video_maker.config.settings import Settings, get_settings
from flipthis_video_maker.database.session import get_db
from flipthis_video_maker.domain.models import Job, VideoChainClip
from flipthis_video_maker.main import create_app
from flipthis_video_maker.providers.base.models import Capability, ProviderInfo


class HealthyGenerationProvider:
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="luma-ray",
            name="Ray fixture",
            model_identity="ray-3.2",
            capabilities={Capability.FIRST_LAST_FRAME_GENERATIVE_VIDEO},
            available=True,
        )

    async def health(self) -> dict[str, object]:
        return {"ok": True, "status": "authenticated"}


class AvailableInterpolator:
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="rife-local",
            name="RIFE fixture",
            model_identity="Practical-RIFE-4.25",
            capabilities={Capability.INTERPOLATION},
            available=True,
        )


class HealthyTargetProvider:
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="target-image-cli",
            name="Target fixture",
            model_identity="target-fixture-v1",
            capabilities={Capability.IMAGE_GENERATION, Capability.IMAGE_EDITING},
            available=True,
        )

    async def health(self) -> dict[str, object]:
        return {"ok": True, "status": "healthy"}


def _png(color: str) -> bytes:
    content = BytesIO()
    Image.new("RGB", (1280, 720), color).save(content, "PNG")
    return content.getvalue()


@pytest.mark.asyncio
async def test_chain_api_configures_honest_automation_and_reports_playback(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api_router,
        "configured_image_provider",
        lambda *_args, **_kwargs: HealthyTargetProvider(),
    )
    app = create_app()

    def database_override() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: Settings(data_dir=tmp_path / "projects")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project = (await client.post("/api/v1/projects", json={"name": "Automated"})).json()
        chain = (
            await client.post(
                f"/api/v1/projects/{project['id']}/video-chains",
                json={"name": "Buffer", "continuation_mode": "auto_generate_target"},
            )
        ).json()
        configured = await client.put(
            f"/api/v1/video-chains/{chain['id']}/automation",
            json={
                "target_provider_id": "target-image-cli",
                "target_provider_model": "target-fixture-v1",
                "target_prompt": "Create a coherent future action target.",
                "auto_accept_qa_passed": True,
                "gpu_assignment": "gpu1",
            },
        )
        playback = await client.post(
            f"/api/v1/video-chains/{chain['id']}/stream/playback",
            json={"position_seconds": 0},
        )

    assert configured.status_code == 200, configured.text
    configured_data = configured.json()
    assert configured_data["automation_config"]["target_provider_model"] == "target-fixture-v1"
    assert configured_data["stream_state"]["automation_status"] == (
        "awaiting_initial_accepted_clip"
    )
    assert playback.status_code == 200
    assert playback.json()["playback_position_seconds"] == 0


@pytest.mark.asyncio
async def test_chain_api_enqueues_an_immutable_automatic_target_job(
    db: Session,
    tmp_path: Path,
) -> None:
    app = create_app()

    def database_override() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: Settings(data_dir=tmp_path / "projects")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project = (await client.post("/api/v1/projects", json={"name": "Auto target"})).json()
        upload = await client.post(
            f"/api/v1/projects/{project['id']}/assets",
            files={"file": ("source.png", _png("navy"), "image/png")},
        )
        assert upload.status_code == 201
        source = upload.json()
        chain = (
            await client.post(
                f"/api/v1/projects/{project['id']}/video-chains",
                json={"name": "Generated targets", "continuation_mode": "auto_generate_target"},
            )
        ).json()
        response = await client.post(
            f"/api/v1/video-chains/{chain['id']}/targets",
            json={
                "continuity_source_asset_id": source["id"],
                "provider_id": "mock-image",
                "provider_model": "mock-pattern-v1",
                "prompt": "Generate the next rain-soaked arcade target frame.",
                "render_profile": "standard",
                "gpu_assignment": "gpu0",
            },
        )

    assert response.status_code == 202, response.text
    assert response.json()["target_frame_generation"]["continuity_source_asset_id"] == source["id"]
    assert response.json()["target_frame_generation_error"] is None
    job = db.get(Job, response.json()["id"])
    assert job is not None
    assert job.job_type == "video_chain_target_generation"
    snapshot = job.payload["target_frame_generation_v1"]
    assert snapshot["continuity_source_asset_id"] == source["id"]
    assert snapshot["width"] == 1280 and snapshot["height"] == 720
    assert job.input_asset_ids == [source["id"]]


@pytest.mark.asyncio
async def test_chain_api_captures_server_validated_immutable_generation_job(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api_router,
        "configured_first_last_frame_provider",
        lambda *_args, **_kwargs: HealthyGenerationProvider(),
    )
    monkeypatch.setattr(
        api_router,
        "configured_interpolation_provider",
        lambda *_args, **_kwargs: AvailableInterpolator(),
    )
    app = create_app()

    def database_override() -> Generator[Session, None, None]:
        yield db

    settings = Settings(data_dir=tmp_path / "projects")
    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project = (await client.post("/api/v1/projects", json={"name": "FLF API"})).json()
        assets = []
        for name, color in (("start.png", "navy"), ("end.png", "teal")):
            response = await client.post(
                f"/api/v1/projects/{project['id']}/assets",
                files={"file": (name, _png(color), "image/png")},
            )
            assert response.status_code == 201
            assets.append(response.json())
        chain_response = await client.post(
            f"/api/v1/projects/{project['id']}/video-chains",
            json={"name": "Endless establishing shot"},
        )
        assert chain_response.status_code == 201
        chain = chain_response.json()
        unsupported = await client.post(
            f"/api/v1/video-chains/{chain['id']}/clips",
            json={
                "start_frame_asset_id": assets[0]["id"],
                "target_end_frame_asset_id": assets[1]["id"],
                "prompt": "A performer crosses a room.",
                "negative_prompt": "camera shake",
                "provider_id": "luma-ray",
                "provider_model": "ray-3.2",
                "render_profile": "standard",
            },
        )
        assert unsupported.status_code == 409
        assert "negative prompt" in unsupported.json()["detail"]
        queued = await client.post(
            f"/api/v1/video-chains/{chain['id']}/clips",
            json={
                "start_frame_asset_id": assets[0]["id"],
                "target_end_frame_asset_id": assets[1]["id"],
                "prompt": "A performer crosses a room while the handheld camera follows.",
                "provider_id": "luma-ray",
                "provider_model": "ray-3.2",
                "render_profile": "standard",
                "gpu_assignment": "gpu1",
            },
        )
        assert queued.status_code == 202, queued.text
        clip_data = queued.json()
        job_response = await client.get(f"/api/v1/jobs/{clip_data['job_id']}")
        assert job_response.status_code == 200

    clip = db.get(VideoChainClip, clip_data["id"])
    job = db.get(Job, clip_data["job_id"])
    assert clip is not None and job is not None
    assert clip.request_digest == clip_data["request_digest"]
    assert clip.request_snapshot["generation_category"] == "first_last_frame_generative_video"
    assert clip.request_snapshot["duration_seconds"] == 10
    assert clip.request_snapshot["native_requested_fps"] == 24
    assert clip.request_snapshot["delivery_fps"] == 60
    assert clip.request_snapshot["captured_render_profile"]["effective_profile"] == "standard"
    assert job.gpu_assignment == "gpu1"
    assert job.input_asset_ids == [assets[0]["id"], assets[1]["id"]]
    exposed = job_response.json()["first_last_frame_generation"]
    assert exposed["start_frame_asset_id"] == assets[0]["id"]
    assert job_response.json()["first_last_frame_generation_error"] is None


@pytest.mark.asyncio
async def test_chain_enqueue_explains_unavailable_provider_instead_of_faking_generation(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api_router,
        "configured_first_last_frame_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("Provider is disabled: luma-ray")
        ),
    )
    app = create_app()

    def database_override() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: Settings(data_dir=tmp_path / "projects")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project = (await client.post("/api/v1/projects", json={"name": "Unavailable"})).json()
        chain = (
            await client.post(
                f"/api/v1/projects/{project['id']}/video-chains",
                json={"name": "Blocked"},
            )
        ).json()
        response = await client.post(
            f"/api/v1/video-chains/{chain['id']}/clips",
            json={
                "start_frame_asset_id": "missing-start",
                "target_end_frame_asset_id": "missing-end",
                "prompt": "Motion",
            },
        )

    assert response.status_code == 409
    assert "Provider is disabled" in response.json()["detail"]
    assert db.query(Job).count() == 0
