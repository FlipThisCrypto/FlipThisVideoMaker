from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from flipthis_video_maker.api.schemas import JobRead
from flipthis_video_maker.config.render_profiles import (
    RENDER_PROFILE_EXECUTION_KEY,
    RenderProfileConfigurationFile,
    RenderProfileExecution,
    render_profile_execution_from_payload,
)
from flipthis_video_maker.domain.enums import JobState, ShotStatus
from flipthis_video_maker.domain.models import Asset, Candidate, Job, Project, Render, Scene, Shot
from flipthis_video_maker.pipeline.provider_execution import execute_with_oom_fallback
from flipthis_video_maker.providers.base.errors import (
    ProviderCleanupResult,
    ProviderOutOfMemoryError,
)
from flipthis_video_maker.providers.base.models import ImageRequest, VideoRequest
from flipthis_video_maker.providers.mock.providers import MockImageProvider, MockVideoProvider
from flipthis_video_maker.scheduler.gpu import GPUMetrics, GPUProbeResult
from flipthis_video_maker.workers import main as worker_main


def _profiles() -> RenderProfileConfigurationFile:
    return RenderProfileConfigurationFile.model_validate(
        {
            "version": 1,
            "profiles": {
                "high": {
                    "width": 320,
                    "height": 180,
                    "fps": 12,
                    "video_codec": "libx264",
                    "audio_codec": "aac",
                    "fallback_profile": "low",
                },
                "low": {
                    "width": 160,
                    "height": 90,
                    "fps": 12,
                    "video_codec": "libx264",
                    "audio_codec": "aac",
                    "fallback_profile": None,
                },
            },
        }
    )


def _worker_profiles() -> RenderProfileConfigurationFile:
    return RenderProfileConfigurationFile.model_validate(
        {
            "version": 1,
            "profiles": {
                "high": {
                    "width": 320,
                    "height": 180,
                    "fps": 24,
                    "video_codec": "libx264",
                    "audio_codec": "aac",
                    "fallback_profile": "medium",
                },
                "medium": {
                    "width": 240,
                    "height": 136,
                    "fps": 18,
                    "video_codec": "libx264",
                    "audio_codec": "aac",
                    "fallback_profile": "low",
                },
                "low": {
                    "width": 160,
                    "height": 90,
                    "fps": 12,
                    "video_codec": "libx264",
                    "audio_codec": "aac",
                    "fallback_profile": None,
                },
            },
        }
    )


class RecoverableFixtureProvider:
    def __init__(self, *, fail_profiles: set[str], retry_safe: bool = True) -> None:
        self.fail_profiles = fail_profiles
        self.retry_safe = retry_safe
        self.calls: list[str] = []
        self.cleanups: list[str] = []

    async def run(self, execution: RenderProfileExecution) -> str:
        self.calls.append(execution.effective_profile)
        if execution.effective_profile in self.fail_profiles:
            raise ProviderOutOfMemoryError(
                provider_id="fixture-video",
                operation="video_generation",
                backend_code="fixture_oom",
            )
        return execution.effective_profile

    async def cleanup_after_oom(self, error: ProviderOutOfMemoryError) -> ProviderCleanupResult:
        self.cleanups.append(error.backend_code or "")
        return ProviderCleanupResult(
            provider_id=error.provider_id,
            completed=True,
            retry_safe=self.retry_safe,
            action_code="fixture_process_reaped",
        )


@pytest.mark.asyncio
async def test_typed_oom_falls_back_once_after_provider_cleanup() -> None:
    execution = RenderProfileExecution.resolve(_profiles(), "high")
    provider = RecoverableFixtureProvider(fail_profiles={"high"})
    persisted: list[RenderProfileExecution] = []

    result, effective = await execute_with_oom_fallback(
        provider,
        provider.run,
        execution,
        job_attempt=1,
        gpu_assignment="gpu0",
        on_fallback=lambda advanced, _error, _cleanup: persisted.append(advanced),
    )

    assert result == "low"
    assert effective.effective_profile == "low"
    assert provider.calls == ["high", "low"]
    assert provider.cleanups == ["fixture_oom"]
    assert persisted == [effective]
    assert effective.fallback_history[0].gpu_assignment == "gpu0"


@pytest.mark.asyncio
async def test_generic_error_text_never_triggers_oom_fallback() -> None:
    execution = RenderProfileExecution.resolve(_profiles(), "high")
    provider = RecoverableFixtureProvider(fail_profiles=set())

    async def generic_failure(_execution: RenderProfileExecution) -> str:
        raise RuntimeError("CUDA out of memory")

    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        await execute_with_oom_fallback(
            provider,
            generic_failure,
            execution,
            job_attempt=1,
            gpu_assignment="gpu0",
        )
    assert provider.cleanups == []


@pytest.mark.asyncio
async def test_unsafe_cleanup_and_exhausted_chain_are_bounded() -> None:
    execution = RenderProfileExecution.resolve(_profiles(), "high")
    unsafe = RecoverableFixtureProvider(fail_profiles={"high"}, retry_safe=False)
    with pytest.raises(ProviderOutOfMemoryError):
        await execute_with_oom_fallback(
            unsafe,
            unsafe.run,
            execution,
            job_attempt=1,
            gpu_assignment="gpu0",
        )
    assert unsafe.calls == ["high"]
    assert len(unsafe.cleanups) == 1

    exhausted = RecoverableFixtureProvider(fail_profiles={"high", "low"})
    with pytest.raises(ProviderOutOfMemoryError):
        await execute_with_oom_fallback(
            exhausted,
            exhausted.run,
            execution,
            job_attempt=1,
            gpu_assignment="gpu0",
        )
    assert exhausted.calls == ["high", "low"]
    assert len(exhausted.cleanups) == 2


@pytest.mark.asyncio
async def test_cancellation_after_cleanup_prevents_the_lower_profile_retry() -> None:
    class Cancelled(RuntimeError):
        pass

    execution = RenderProfileExecution.resolve(_profiles(), "high")
    provider = RecoverableFixtureProvider(fail_profiles={"high"})
    cancelled = False

    async def cleanup(error: ProviderOutOfMemoryError) -> ProviderCleanupResult:
        nonlocal cancelled
        result = await RecoverableFixtureProvider.cleanup_after_oom(provider, error)
        cancelled = True
        return result

    provider.cleanup_after_oom = cleanup  # type: ignore[method-assign]

    def check_cancelled() -> None:
        if cancelled:
            raise Cancelled("cancelled after provider cleanup")

    with pytest.raises(Cancelled, match="after provider cleanup"):
        await execute_with_oom_fallback(
            provider,
            provider.run,
            execution,
            job_attempt=1,
            gpu_assignment="gpu0",
            check_cancelled=check_cancelled,
        )
    assert provider.calls == ["high"]
    assert len(provider.cleanups) == 1


@pytest.mark.asyncio
async def test_worker_fallback_stays_under_one_gpu_lock_and_persists_provenance(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = _worker_profiles()
    execution = RenderProfileExecution.resolve(profiles, "high")
    project = Project(
        name="OOM fallback fixture",
        root_asset_directory=str(tmp_path / "project"),
        resolution_profile="high",
    )
    scene = Scene(number=1, title="Fallback scene")
    scene.shots.append(
        Shot(
            sequence_number=1,
            duration=0.5,
            prompt="A deterministic fallback frame",
            status=ShotStatus.APPROVED.value,
            approval_state="approved",
        )
    )
    project.scenes.append(scene)
    db.add(project)
    db.flush()
    job = Job(
        job_type="mock_project_render",
        project_id=project.id,
        gpu_assignment="gpu0",
        payload={RENDER_PROFILE_EXECUTION_KEY: execution.model_dump(mode="json")},
    )
    db.add(job)
    db.commit()

    inside_lock = False
    image_calls: list[tuple[int, int]] = []
    video_calls: list[tuple[int, int]] = []
    cleanups = 0

    class TrackingLock:
        def __init__(self, physical_gpu: str) -> None:
            assert physical_gpu == "0"

        def __enter__(self) -> None:
            nonlocal inside_lock
            inside_lock = True

        def __exit__(self, *_args: object) -> None:
            nonlocal inside_lock
            inside_lock = False

    def probe() -> GPUProbeResult:
        assert inside_lock
        return GPUProbeResult(
            metrics=(
                GPUMetrics(
                    index=0,
                    name="Fixture GPU",
                    temperature_c=40,
                    utilization_percent=0,
                    memory_total_mb=12000,
                    memory_used_mb=1000,
                    memory_free_mb=11000,
                ),
            )
        )

    real_image_generate = MockImageProvider.generate
    real_video_generate = MockVideoProvider.generate

    async def image_oom_once(self: MockImageProvider, request: ImageRequest) -> Path:
        assert inside_lock
        image_calls.append((request.width, request.height))
        if len(image_calls) == 1:
            raise ProviderOutOfMemoryError(
                provider_id="mock-image",
                operation="image_generation",
                backend_code="fixture_image_oom",
            )
        return await real_image_generate(self, request)

    async def oom_once(self: MockVideoProvider, request: VideoRequest) -> Path:
        assert inside_lock
        video_calls.append((request.width, request.height))
        if len(video_calls) == 1:
            raise ProviderOutOfMemoryError(
                provider_id="mock-video",
                operation="video_generation",
                backend_code="fixture_oom",
            )
        return await real_video_generate(self, request)

    async def cleanup(_self: object, error: ProviderOutOfMemoryError) -> ProviderCleanupResult:
        nonlocal cleanups
        assert inside_lock
        cleanups += 1
        return ProviderCleanupResult(
            provider_id=error.provider_id,
            completed=True,
            retry_safe=True,
            action_code="fixture_process_reaped",
        )

    monkeypatch.setattr(worker_main, "GPULock", TrackingLock)
    monkeypatch.setattr(worker_main, "probe_gpus", probe)
    monkeypatch.setattr(MockImageProvider, "generate", image_oom_once)
    monkeypatch.setattr(MockImageProvider, "cleanup_after_oom", cleanup, raising=False)
    monkeypatch.setattr(MockVideoProvider, "generate", oom_once)
    monkeypatch.setattr(MockVideoProvider, "cleanup_after_oom", cleanup, raising=False)

    assert await worker_main.process_next(
        db,
        "gpu0",
        physical_gpu=0,
        minimum_free_vram_mb=2000,
        render_profiles=profiles,
    )
    assert not inside_lock
    db.refresh(job)
    effective = render_profile_execution_from_payload(job.payload)
    assert job.state == JobState.SUCCEEDED.value
    assert job.attempt_number == 1
    assert job.gpu_assignment == "gpu0"
    assert image_calls == [(320, 180), (240, 136), (240, 136)]
    assert video_calls == [(240, 136), (160, 90)]
    assert cleanups == 2
    assert effective.requested_profile == "high"
    assert effective.effective_profile == "low"
    assert len(effective.fallback_history) == 2
    assert JobRead.model_validate(job).render_profile_execution == effective
    assert job.log_path is not None
    log_text = Path(job.log_path).read_text(encoding="utf-8")
    assert "provider_oom" in log_text
    assert "provider_cleanup" in log_text
    assert "render_profile_fallback" in log_text

    render = db.scalar(select(Render).where(Render.project_id == project.id))
    candidate = db.scalar(
        select(Candidate).join(Shot).join(Scene).where(Scene.project_id == project.id)
    )
    output_asset = db.get(Asset, job.output_asset_ids[0])
    assert render is not None
    assert candidate is not None
    assert output_asset is not None
    assert render.render_profile == "low"
    assert render.resolution == "160x90"
    assert render.creation_metadata["requested_render_profile"] == "high"
    assert candidate.gpu == "gpu0"
    assert candidate.settings["render_profile_execution"]["effective_profile"] == "low"
    assert (
        output_asset.generation_parameters["render_profile_execution"]["effective_profile"] == "low"
    )
