import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlalchemy.orm import Session

from flipthis_video_maker.config.render_profiles import (
    RenderProfileConfigurationFile,
    RenderProfileExecution,
    load_render_profile_configuration,
)
from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.domain.enums import ShotStatus
from flipthis_video_maker.domain.models import Asset, Candidate, Project, Scene, Shot
from flipthis_video_maker.domain.state_machine import validate_transition
from flipthis_video_maker.media.ffmpeg import extract_frame
from flipthis_video_maker.pipeline.mock_pipeline import PipelineCancelled
from flipthis_video_maker.pipeline.postprocessing import apply_mock_postprocessing
from flipthis_video_maker.pipeline.provider_execution import (
    CleanupCallback,
    FallbackCallback,
    execute_with_oom_fallback,
)
from flipthis_video_maker.providers.base.errors import (
    ProviderCleanupResult,
    ProviderOutOfMemoryError,
)
from flipthis_video_maker.providers.base.models import ImageRequest, TTSRequest, VideoRequest
from flipthis_video_maker.providers.mock.providers import (
    MockImageProvider,
    MockInterpolationProvider,
    MockLipSyncProvider,
    MockTTSProvider,
    MockVideoProvider,
)
from flipthis_video_maker.quality.analyzer import analyze_video
from flipthis_video_maker.storage.assets import register_asset


class MockShotRegenerator:
    def __init__(
        self,
        db: Session,
        cancel_requested: Callable[[], bool] | None = None,
        progress: Callable[[float, str], None] | None = None,
        render_profiles: RenderProfileConfigurationFile | None = None,
        render_profile_execution: RenderProfileExecution | None = None,
        job_attempt: int = 1,
        gpu_assignment: str = "cpu",
        profile_fallback: FallbackCallback | None = None,
        provider_cleanup: CleanupCallback | None = None,
    ) -> None:
        self.db = db
        self.cancel_requested = cancel_requested
        self.progress = progress
        self.render_profiles = render_profiles
        self.render_profile_execution = render_profile_execution
        self.job_attempt = job_attempt
        self.gpu_assignment = gpu_assignment
        self.profile_fallback = profile_fallback
        self.provider_cleanup = provider_cleanup
        self.images = MockImageProvider()
        self.tts = MockTTSProvider()
        self.video = MockVideoProvider(cancel_requested)
        self.lip_sync = MockLipSyncProvider(cancel_requested)
        self.interpolation = MockInterpolationProvider(cancel_requested)

    async def run(
        self,
        shot_id: str,
        *,
        same_seed: bool = True,
        prompt: str | None = None,
        negative_prompt: str | None = None,
        generation_settings: dict[str, object] | None = None,
    ) -> Candidate:
        shot = self.db.get(Shot, shot_id)
        if shot is None:
            raise ValueError(f"Unknown shot {shot_id}")
        scene = self.db.get(Scene, shot.scene_id)
        if scene is None:
            raise RuntimeError("Shot scene is missing")
        project = self.db.get(Project, scene.project_id)
        if project is None:
            raise RuntimeError("Shot project is missing")
        if self.render_profile_execution is not None:
            execution = self.render_profile_execution
        else:
            profiles = self.render_profiles or load_render_profile_configuration(
                get_settings().render_profile_config
            )
            execution = RenderProfileExecution.resolve(profiles, project.resolution_profile)
        profile = execution.profile
        profile_parameters = execution.generation_parameters()
        execution_payload = execution.model_dump(mode="json")
        profile_provenance = {
            **profile_parameters,
            "requested_render_profile": execution.requested_profile,
            "render_profile_execution": execution_payload,
        }
        self._check_cancelled()
        self._report_progress(0.1, "preparing shot inputs")

        run_id = uuid.uuid4().hex
        root = (
            Path(project.root_asset_directory)
            / "shots"
            / f"shot-{shot.sequence_number:03d}"
            / "runs"
            / run_id
        )
        root.mkdir(parents=True, exist_ok=False)
        candidate_seed = shot.seed if same_seed else shot.seed + shot.retry_count + 1
        candidate_prompt = prompt if prompt is not None else shot.prompt
        candidate_negative = (
            negative_prompt if negative_prompt is not None else shot.negative_prompt
        )
        candidate_settings = {
            **shot.generation_settings,
            **(generation_settings or {}),
        }
        original_status = ShotStatus(shot.status)

        start_asset = self._conditioning_start(shot)
        if start_asset is None:
            start_path = root / "frames" / f"planned-start-{candidate_seed}.png"

            async def generate_start(active: RenderProfileExecution) -> Path:
                active_profile = active.profile
                return await self.images.generate(
                    ImageRequest(
                        prompt=f"{candidate_prompt} opening",
                        output_path=start_path,
                        width=active_profile.width,
                        height=active_profile.height,
                        seed=candidate_seed,
                        label=f"Shot {shot.sequence_number} START",
                        characters=scene.characters,
                    )
                )

            _, execution = await self._execute_provider(
                self.images,
                generate_start,
                execution,
            )
            profile = execution.profile
            profile_parameters = execution.generation_parameters()
            execution_payload = execution.model_dump(mode="json")
            profile_provenance = {
                **profile_parameters,
                "requested_render_profile": execution.requested_profile,
                "render_profile_execution": execution_payload,
            }
            start_asset = register_asset(
                self.db,
                project_id=project.id,
                shot_id=shot.id,
                kind="planned_start_frame",
                path=start_path,
                prompt=candidate_prompt,
                seed=candidate_seed,
                generation_parameters=profile_provenance,
            )
        else:
            start_path = Path(start_asset.file_path)

        end_path = root / "frames" / f"planned-end-{candidate_seed}.png"

        async def generate_end(active: RenderProfileExecution) -> Path:
            active_profile = active.profile
            return await self.images.generate(
                ImageRequest(
                    prompt=f"{candidate_prompt} ending",
                    output_path=end_path,
                    width=active_profile.width,
                    height=active_profile.height,
                    seed=candidate_seed + 1,
                    label=f"Shot {shot.sequence_number} END",
                    characters=scene.characters,
                )
            )

        _, execution = await self._execute_provider(
            self.images,
            generate_end,
            execution,
        )
        profile = execution.profile
        profile_parameters = execution.generation_parameters()
        execution_payload = execution.model_dump(mode="json")
        profile_provenance = {
            **profile_parameters,
            "requested_render_profile": execution.requested_profile,
            "render_profile_execution": execution_payload,
        }
        end_asset = register_asset(
            self.db,
            project_id=project.id,
            shot_id=shot.id,
            kind="planned_end_frame",
            path=end_path,
            prompt=candidate_prompt,
            seed=candidate_seed + 1,
            generation_parameters=profile_provenance,
        )

        audio_path: Path | None = None
        audio_asset: Asset | None = None
        input_assets = [start_asset.id, end_asset.id]
        if shot.dialogue:
            audio_path = root / "audio" / f"dialogue-{candidate_seed}.wav"
            await self.tts.synthesize(
                TTSRequest(
                    text=shot.dialogue,
                    output_path=audio_path,
                    voice=shot.speaker or "narrator",
                )
            )
            audio_asset = register_asset(
                self.db,
                project_id=project.id,
                shot_id=shot.id,
                kind="dialogue_audio",
                path=audio_path,
                provider="mock-tts",
                model="mock-tone-v1",
                generation_parameters=profile_provenance,
                cancel_requested=self.cancel_requested,
            )
            input_assets.append(audio_asset.id)

        self._check_cancelled()
        self._report_progress(0.45, "generating shot candidate")
        track_state = self._begin_regeneration_state(shot)
        # Persist prepared inputs and release SQLite's writer lock before provider execution.
        self.db.commit()
        output = root / "candidates" / f"candidate-{candidate_seed}.mp4"
        started = time.monotonic()

        async def generate_video(active: RenderProfileExecution) -> Path:
            active_profile = active.profile
            return await self.video.generate(
                VideoRequest(
                    prompt=candidate_prompt,
                    output_path=output,
                    start_frame=start_path,
                    end_frame=end_path,
                    duration=shot.duration,
                    fps=active_profile.fps,
                    audio_path=audio_path,
                    seed=candidate_seed,
                    width=active_profile.width,
                    height=active_profile.height,
                    settings={**candidate_settings, **active.generation_parameters()},
                )
            )

        try:
            output, execution = await self._execute_provider(
                self.video,
                generate_video,
                execution,
            )
        except Exception:
            self._mark_regeneration_failed(shot, track_state)
            raise
        profile = execution.profile
        profile_parameters = execution.generation_parameters()
        execution_payload = execution.model_dump(mode="json")
        profile_provenance = {
            **profile_parameters,
            "requested_render_profile": execution.requested_profile,
            "render_profile_execution": execution_payload,
        }
        self._check_cancelled()
        try:
            base_quality = analyze_video(
                output,
                shot.duration,
                profile.width,
                profile.height,
                profile.fps,
                audio_expected=True,
                cancel_requested=self.cancel_requested,
                audible_audio_expected=bool(shot.dialogue),
            )
            video_asset = register_asset(
                self.db,
                project_id=project.id,
                shot_id=shot.id,
                kind="video_candidate",
                path=output,
                provider="mock-video",
                model="ffmpeg-xfade-v1",
                prompt=candidate_prompt,
                seed=candidate_seed,
                parents=input_assets,
                generation_parameters=profile_provenance,
                cancel_requested=self.cancel_requested,
            )
            if track_state:
                self._transition(shot, ShotStatus.VIDEO_READY)
            self.db.commit()
            postprocess = await apply_mock_postprocessing(
                self.db,
                project_id=project.id,
                shot=shot,
                video_path=output,
                video_asset=video_asset,
                audio_path=audio_path,
                audio_asset=audio_asset,
                output_directory=root / "postprocessing",
                settings=candidate_settings,
                expected_duration=shot.duration,
                width=profile.width,
                height=profile.height,
                fps=profile.fps,
                prompt=candidate_prompt,
                seed=candidate_seed,
                profile_provenance=profile_provenance,
                base_quality=base_quality,
                audible_audio_expected=bool(shot.dialogue),
                lip_sync=self.lip_sync,
                interpolation=self.interpolation,
                transition=(lambda target: self._transition(shot, target)) if track_state else None,
                cancel_requested=self.cancel_requested,
            )
        except Exception:
            self._mark_regeneration_failed(shot, track_state)
            raise
        output = postprocess.output_path
        selected_video_asset = postprocess.output_asset
        qa = postprocess.quality
        postprocessing = postprocess.metadata
        if track_state:
            self._transition(shot, ShotStatus.QA_PENDING)
            self.db.commit()
        self._check_cancelled()
        self._report_progress(0.75, "extracting candidate frames")
        actual_start_path = root / "frames" / f"actual-start-{candidate_seed}.png"
        actual_end_path = root / "frames" / f"actual-end-{candidate_seed}.png"
        extract_frame(output, actual_start_path, cancel_requested=self.cancel_requested)
        extract_frame(
            output,
            actual_end_path,
            last=True,
            cancel_requested=self.cancel_requested,
        )
        actual_start = register_asset(
            self.db,
            project_id=project.id,
            shot_id=shot.id,
            kind="actual_start_frame",
            path=actual_start_path,
            parents=[selected_video_asset.id],
            generation_parameters=profile_provenance,
        )
        actual_end = register_asset(
            self.db,
            project_id=project.id,
            shot_id=shot.id,
            kind="actual_end_frame",
            path=actual_end_path,
            parents=[selected_video_asset.id],
            generation_parameters=profile_provenance,
        )
        candidate = Candidate(
            shot_id=shot.id,
            provider="mock-video",
            model="ffmpeg-xfade-v1",
            prompt=candidate_prompt,
            negative_prompt=candidate_negative,
            seed=candidate_seed,
            settings={
                **candidate_settings,
                "render_profile_execution": execution_payload,
                "postprocessing": postprocessing,
            },
            generation_seconds=time.monotonic() - started,
            gpu=self.gpu_assignment,
            input_asset_ids=input_assets,
            output_asset_id=selected_video_asset.id,
            first_frame_asset_id=actual_start.id,
            last_frame_asset_id=actual_end.id,
            qa_results=qa,
            disposition="pending",
        )
        shot.retry_count += 1
        self.db.add(candidate)
        if track_state:
            terminal = (
                original_status
                if original_status in {ShotStatus.COMPLETE, ShotStatus.QA_FAILED}
                else ShotStatus.COMPLETE
                if qa["passed"]
                else ShotStatus.QA_FAILED
            )
            self._transition(shot, terminal)
        self._check_cancelled()
        self.db.commit()
        self._report_progress(0.98, "validated shot candidate")
        return candidate

    def _conditioning_start(self, shot: Shot) -> Asset | None:
        for asset_id in (
            shot.continuity_source_frame_id,
            shot.actual_start_frame_id,
            shot.planned_start_frame_id,
        ):
            if asset_id:
                asset = self.db.get(Asset, asset_id)
                if asset is not None and Path(asset.file_path).is_file():
                    return asset
        return None

    def _begin_regeneration_state(self, shot: Shot) -> bool:
        status = ShotStatus(shot.status)
        if status in {ShotStatus.COMPLETE, ShotStatus.QA_FAILED, ShotStatus.FAILED}:
            self._transition(shot, ShotStatus.VIDEO_PENDING)
        elif status is ShotStatus.KEYFRAMES_READY:
            self._transition(shot, ShotStatus.VIDEO_PENDING)
        elif status is not ShotStatus.VIDEO_PENDING:
            return False
        self._transition(shot, ShotStatus.VIDEO_GENERATING)
        return True

    def _mark_regeneration_failed(self, shot: Shot, track_state: bool) -> None:
        self.db.rollback()
        if not track_state:
            return
        failed_shot = self.db.get(Shot, shot.id)
        if failed_shot is None:
            return
        if failed_shot.status in {
            ShotStatus.VIDEO_GENERATING.value,
            ShotStatus.LIPSYNC_PENDING.value,
            ShotStatus.CONTINUITY_PENDING.value,
            ShotStatus.QA_PENDING.value,
        }:
            self._transition(failed_shot, ShotStatus.FAILED)
            self.db.commit()

    def _check_cancelled(self) -> None:
        if self.cancel_requested and self.cancel_requested():
            raise PipelineCancelled("Shot regeneration cancellation requested")

    def _report_progress(self, value: float, stage: str) -> None:
        if self.progress:
            self.progress(value, stage)

    @staticmethod
    def _transition(shot: Shot, target: ShotStatus) -> None:
        validate_transition(ShotStatus(shot.status), target)
        shot.status = target.value

    async def _execute_provider[ResultT](
        self,
        provider: object,
        operation: Callable[[RenderProfileExecution], Awaitable[ResultT]],
        execution: RenderProfileExecution,
    ) -> tuple[ResultT, RenderProfileExecution]:
        def record_fallback(
            advanced: RenderProfileExecution,
            error: ProviderOutOfMemoryError,
            cleanup: ProviderCleanupResult,
        ) -> None:
            self.render_profile_execution = advanced
            if self.profile_fallback is not None:
                self.profile_fallback(advanced, error, cleanup)

        result, effective = await execute_with_oom_fallback(
            provider,
            operation,
            execution,
            job_attempt=self.job_attempt,
            gpu_assignment=self.gpu_assignment,
            on_fallback=record_fallback,
            on_cleanup=self.provider_cleanup,
            check_cancelled=self._check_cancelled,
        )
        self.render_profile_execution = effective
        return result, effective
