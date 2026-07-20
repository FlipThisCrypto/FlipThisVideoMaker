import io
import wave
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from flipthis_video_maker.api.schemas import JobRead
from flipthis_video_maker.config.render_finalization import (
    RENDER_FINALIZATION_EXECUTION_KEY,
    RenderFinalizationExecution,
    RenderFinalizationRequest,
)
from flipthis_video_maker.config.settings import Settings, get_settings
from flipthis_video_maker.database.session import get_db
from flipthis_video_maker.domain.enums import JobState
from flipthis_video_maker.domain.models import Asset, Job
from flipthis_video_maker.main import create_app
from flipthis_video_maker.media.ffmpeg import checksum
from flipthis_video_maker.pipeline.mock_pipeline import create_sample
from flipthis_video_maker.services.jobs import retry
from flipthis_video_maker.services.render_finalization import capture_render_finalization
from flipthis_video_maker.workers import main as worker_main


def _wav_bytes(*, frames: int = 8_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8_000)
        audio.writeframes(b"\x00\x00" * frames)
    return output.getvalue()


def _write_wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_wav_bytes())
    return path


def _test_app(db: Session, tmp_path: Path) -> FastAPI:
    app = create_app()

    def database_override() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: Settings(data_dir=tmp_path / "projects")
    return app


@pytest.mark.asyncio
async def test_upload_returns_typed_asset_and_render_captures_music_identity(
    db: Session,
    tmp_path: Path,
) -> None:
    project = create_sample(db, tmp_path / "project")
    Path(project.root_asset_directory, "source").mkdir(parents=True)
    transport = httpx.ASGITransport(app=_test_app(db, tmp_path))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        uploaded = await client.post(
            f"/api/v1/projects/{project.id}/assets",
            files={"file": ("..\\..\\score.wav", _wav_bytes(), "audio/wav")},
        )
        assert uploaded.status_code == 201
        asset_record = uploaded.json()
        assert asset_record["type"] == "upload"
        assert asset_record["mime_type"] == "audio/wav"
        assert asset_record["duration"] == pytest.approx(1, abs=0.02)
        assert asset_record["generation_parameters"] == {"original_filename": "score.wav"}
        assert "path" not in asset_record
        assert Path(asset_record["file_path"]).parent == Path(
            project.root_asset_directory, "source"
        )

        queued = await client.post(
            f"/api/v1/projects/{project.id}/render",
            json={
                "render_profile": "draft",
                "finalization": {
                    "subtitle": {"mode": "soft", "language": "en"},
                    "audio": {"normalize": True, "integrated_lufs": -18},
                    "music": {"asset_id": asset_record["id"], "gain_db": -14},
                },
            },
        )
        assert queued.status_code == 202
        response_snapshot = queued.json()["render_finalization_execution"]
        assert queued.json()["render_finalization_execution_error"] is None
        assert response_snapshot["subtitle"]["mode"] == "soft"
        assert response_snapshot["music"]["asset_id"] == asset_record["id"]
        assert response_snapshot["music"]["checksum"] == asset_record["checksum"]
        assert "path" not in response_snapshot["music"]

        default_queued = await client.post(f"/api/v1/projects/{project.id}/render")
        assert default_queued.status_code == 202
        assert default_queued.json()[
            "render_finalization_execution"
        ] == RenderFinalizationExecution.compatibility_default().model_dump(mode="json")

    job = db.get(Job, queued.json()["id"])
    default_job = db.get(Job, default_queued.json()["id"])
    assert job is not None
    assert default_job is not None
    assert job.input_asset_ids == [asset_record["id"]]
    assert job.payload[RENDER_FINALIZATION_EXECUTION_KEY] == response_snapshot
    assert RENDER_FINALIZATION_EXECUTION_KEY in default_job.payload


@pytest.mark.asyncio
async def test_render_rejects_invalid_music_asset_boundaries(db: Session, tmp_path: Path) -> None:
    project = create_sample(db, tmp_path / "project")
    other = create_sample(db, tmp_path / "other-project")
    project_root = Path(project.root_asset_directory)
    other_root = Path(other.root_asset_directory)
    project_root.mkdir(parents=True)
    other_root.mkdir(parents=True)

    valid_path = _write_wav(project_root / "source" / "valid.wav")
    other_path = _write_wav(other_root / "source" / "other.wav")
    outside_path = _write_wav(tmp_path / "outside.wav")
    missing_path = project_root / "source" / "missing.wav"
    wrong_type_path = project_root / "source" / "notes.txt"
    wrong_type_path.write_text("notes", encoding="utf-8")
    assets = {
        "other": Asset(
            project_id=other.id,
            type="upload",
            file_path=str(other_path),
            mime_type="audio/wav",
            checksum=checksum(other_path),
        ),
        "wrong_type": Asset(
            project_id=project.id,
            type="upload",
            file_path=str(wrong_type_path),
            mime_type="text/plain",
            checksum=checksum(wrong_type_path),
        ),
        "outside": Asset(
            project_id=project.id,
            type="upload",
            file_path=str(outside_path),
            mime_type="audio/wav",
            checksum=checksum(outside_path),
        ),
        "missing": Asset(
            project_id=project.id,
            type="upload",
            file_path=str(missing_path),
            mime_type="audio/wav",
            checksum="a" * 64,
        ),
        "mismatch": Asset(
            project_id=project.id,
            type="upload",
            file_path=str(valid_path),
            mime_type="audio/wav",
            checksum="b" * 64,
        ),
    }
    db.add_all(assets.values())
    db.commit()
    transport = httpx.ASGITransport(app=_test_app(db, tmp_path))

    expected = {
        "missing-id": 404,
        assets["other"].id: 400,
        assets["wrong_type"].id: 422,
        assets["outside"].id: 409,
        assets["missing"].id: 410,
        assets["mismatch"].id: 409,
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for asset_id, status_code in expected.items():
            response = await client.post(
                f"/api/v1/projects/{project.id}/render",
                json={
                    "finalization": {
                        "audio": {"normalize": True},
                        "music": {"asset_id": asset_id},
                    }
                },
            )
            assert response.status_code == status_code, response.text

        unknown_control = await client.post(
            f"/api/v1/projects/{project.id}/render",
            json={"finalization": {}, "arbitrary": True},
        )
        assert unknown_control.status_code == 422

    assert db.query(Job).count() == 0


@pytest.mark.asyncio
async def test_worker_captures_legacy_default_and_revalidates_music(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = create_sample(db, tmp_path / "project")
    music_path = _write_wav(Path(project.root_asset_directory) / "source" / "music.wav")
    music = Asset(
        project_id=project.id,
        type="upload",
        file_path=str(music_path),
        mime_type="audio/wav",
        checksum=checksum(music_path),
    )
    db.add(music)
    db.flush()
    request = RenderFinalizationRequest.model_validate(
        {"audio": {"normalize": True}, "music": {"asset_id": music.id}}
    )
    execution, _ = capture_render_finalization(db, project, request)
    explicit = Job(
        job_type="mock_project_render",
        project_id=project.id,
        input_asset_ids=[music.id],
        payload={RENDER_FINALIZATION_EXECUTION_KEY: execution.model_dump(mode="json")},
    )
    legacy = Job(job_type="mock_project_render", project_id=project.id)
    db.add_all([explicit, legacy])
    db.commit()
    observed: list[tuple[RenderFinalizationExecution, Asset | None]] = []

    class CapturingPipeline:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            finalization = kwargs["render_finalization_execution"]
            selected_music = kwargs["music_asset"]
            assert isinstance(finalization, RenderFinalizationExecution)
            assert selected_music is None or isinstance(selected_music, Asset)
            observed.append((finalization, selected_music))

        async def run(self, _project_id: str) -> SimpleNamespace:
            return SimpleNamespace(creation_metadata={"output_asset_id": "output"})

    monkeypatch.setattr(worker_main, "MockPipeline", CapturingPipeline)
    assert await worker_main.process_next(db, "cpu")
    assert observed == [(execution, music)]
    assert await worker_main.process_next(db, "cpu")
    assert observed[1] == (RenderFinalizationExecution.compatibility_default(), None)
    db.refresh(legacy)
    assert legacy.payload[
        RENDER_FINALIZATION_EXECUTION_KEY
    ] == RenderFinalizationExecution.compatibility_default().model_dump(mode="json")


@pytest.mark.asyncio
async def test_worker_fails_malformed_or_tampered_snapshot_and_retry_preserves_it(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = create_sample(db, tmp_path / "project")
    music_path = _write_wav(Path(project.root_asset_directory) / "source" / "music.wav")
    music = Asset(
        project_id=project.id,
        type="upload",
        file_path=str(music_path),
        mime_type="audio/wav",
        checksum=checksum(music_path),
    )
    db.add(music)
    db.flush()
    request = RenderFinalizationRequest.model_validate(
        {"audio": {"normalize": True}, "music": {"asset_id": music.id}}
    )
    execution, _ = capture_render_finalization(db, project, request)
    malformed = Job(
        job_type="mock_project_render",
        project_id=project.id,
        payload={RENDER_FINALIZATION_EXECUTION_KEY: {}},
    )
    tampered = Job(
        job_type="mock_project_render",
        project_id=project.id,
        input_asset_ids=[music.id],
        payload={RENDER_FINALIZATION_EXECUTION_KEY: execution.model_dump(mode="json")},
    )
    db.add_all([malformed, tampered])
    db.commit()
    music_path.write_bytes(_wav_bytes(frames=4_000))

    class MustNotRunPipeline:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("Pipeline must not run with an invalid finalization input")

    monkeypatch.setattr(worker_main, "MockPipeline", MustNotRunPipeline)
    assert await worker_main.process_next(db, "cpu")
    db.refresh(malformed)
    assert malformed.state == JobState.FAILED.value
    assert malformed.error_info["type"] == "ValidationError"
    malformed_read = JobRead.model_validate(malformed)
    assert malformed_read.render_finalization_execution is None
    assert malformed_read.render_finalization_execution_error == "invalid_snapshot"

    assert await worker_main.process_next(db, "cpu")
    db.refresh(tampered)
    assert tampered.state == JobState.FAILED.value
    assert tampered.error_info["type"] == "RenderFinalizationInputError"
    original_snapshot = tampered.payload[RENDER_FINALIZATION_EXECUTION_KEY]
    retry(db, tampered, max_retries=2)
    assert tampered.payload[RENDER_FINALIZATION_EXECUTION_KEY] == original_snapshot
