import os
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from sqlalchemy.orm import Session

from flipthis_video_maker.config.render_finalization import RenderFinalizationExecution
from flipthis_video_maker.config.render_profiles import (
    RenderProfileConfigurationFile,
    RenderProfileExecution,
    load_render_profile_configuration,
)
from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.continuity.packet import build_packet, write_packet
from flipthis_video_maker.domain.enums import ProjectStatus, ShotStatus
from flipthis_video_maker.domain.models import (
    Asset,
    Candidate,
    Character,
    Project,
    Render,
    Scene,
    Shot,
)
from flipthis_video_maker.domain.state_machine import validate_transition
from flipthis_video_maker.media.ffmpeg import checksum, extract_frame, run
from flipthis_video_maker.media.finalization import (
    AudioFinalizationOptions,
    AudioFinalizationResult,
    BackgroundMusicOptions,
    BurnSubtitleOptions,
    LoudnessTarget,
    SoftSubtitleOptions,
    SubtitleFinalizationResult,
    burn_subtitles,
    mux_soft_subtitles,
    normalize_audio,
)
from flipthis_video_maker.media.render import (
    assemble_with_transitions,
    contact_sheet,
    thumbnail,
    write_manifest,
    write_subtitles,
)
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


class PipelineCancelled(RuntimeError):
    pass


class MockPipeline:
    def __init__(
        self,
        db: Session,
        cancel_requested: Callable[[], bool] | None = None,
        progress: Callable[[float, str], None] | None = None,
        render_profiles: RenderProfileConfigurationFile | None = None,
        render_profile_execution: RenderProfileExecution | None = None,
        render_finalization_execution: RenderFinalizationExecution | None = None,
        music_asset: Asset | None = None,
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
        self.render_finalization_execution = (
            render_finalization_execution or RenderFinalizationExecution.compatibility_default()
        )
        self.music_asset = music_asset
        self.job_attempt = job_attempt
        self.gpu_assignment = gpu_assignment
        self.profile_fallback = profile_fallback
        self.provider_cleanup = provider_cleanup
        self.images = MockImageProvider()
        self.tts = MockTTSProvider()
        self.video = MockVideoProvider(cancel_requested)
        self.lip_sync = MockLipSyncProvider(cancel_requested)
        self.interpolation = MockInterpolationProvider(cancel_requested)

    async def run(self, project_id: str, *, render_profile_name: str | None = None) -> Render:
        project = self.db.get(Project, project_id)
        if project is None:
            raise ValueError(f"Unknown project {project_id}")
        self._validate_finalization_inputs(project)
        finalization = self.render_finalization_execution
        finalization_payload = finalization.model_dump(mode="json")
        if finalization.subtitle.mode != "sidecar" and not any(
            shot.dialogue.strip() for scene in project.scenes for shot in scene.shots
        ):
            raise ValueError(
                f"{finalization.subtitle.mode} subtitles require at least one subtitle cue"
            )
        if self.render_profile_execution is not None:
            execution = self.render_profile_execution
            if (
                render_profile_name is not None
                and render_profile_name != execution.effective_profile
            ):
                raise ValueError(
                    "A queued render-profile snapshot cannot be overridden at execution"
                )
        else:
            profiles = self.render_profiles or load_render_profile_configuration(
                get_settings().render_profile_config
            )
            execution = RenderProfileExecution.resolve(
                profiles, render_profile_name or project.resolution_profile
            )
        profile_name = execution.effective_profile
        profile = execution.profile
        profile_parameters = execution.generation_parameters()
        execution_payload = execution.model_dump(mode="json")
        profile_provenance = {
            **profile_parameters,
            "requested_render_profile": execution.requested_profile,
            "render_profile_execution": execution_payload,
        }

        root = Path(project.root_asset_directory)
        run_id = uuid.uuid4().hex
        clips: list[Path] = []
        selected_clip_asset_ids: list[str] = []
        transitions: list[tuple[str, int]] = []
        actual_end_count = 0
        previous_shot: Shot | None = None
        shot_index = 0
        total_shots = sum(len(scene.shots) for scene in project.scenes)
        self._report_progress(0.02, "starting render")

        for scene in project.scenes:
            for shot in scene.shots:
                profile_name = execution.effective_profile
                profile = execution.profile
                profile_parameters = execution.generation_parameters()
                execution_payload = execution.model_dump(mode="json")
                profile_provenance = {
                    **profile_parameters,
                    "requested_render_profile": execution.requested_profile,
                    "render_profile_execution": execution_payload,
                }
                self._check_cancelled()
                shot_root = root / "shots" / f"shot-{shot.sequence_number:03d}" / "runs" / run_id
                shot_root.mkdir(parents=True, exist_ok=False)
                rerun = shot.status in {ShotStatus.COMPLETE.value, ShotStatus.QA_FAILED.value}
                if rerun:
                    self._transition(shot, ShotStatus.VIDEO_PENDING)
                elif shot.dialogue:
                    self._transition(shot, ShotStatus.AUDIO_PENDING)
                else:
                    self._transition(shot, ShotStatus.KEYFRAMES_PENDING)

                planned_start_path = shot_root / "frames" / f"planned-start-{shot.seed}.png"
                planned_end_path = shot_root / "frames" / f"planned-end-{shot.seed}.png"
                if previous_shot and shot.continuity_source_shot_id == previous_shot.id:
                    source = self.db.get(Asset, previous_shot.actual_end_frame_id)
                    if source is None:
                        raise RuntimeError("Connected shot has no actual ending frame")
                    planned_start_path = Path(source.file_path)
                    shot.continuity_source_frame_id = source.id
                else:

                    async def generate_start(
                        active: RenderProfileExecution,
                        *,
                        current_shot: Shot = shot,
                        current_scene: Scene = scene,
                        current_output: Path = planned_start_path,
                    ) -> Path:
                        active_profile = active.profile
                        return await self.images.generate(
                            ImageRequest(
                                prompt=f"{current_shot.prompt} opening",
                                output_path=current_output,
                                width=active_profile.width,
                                height=active_profile.height,
                                seed=current_shot.seed,
                                label=f"Shot {current_shot.sequence_number} START",
                                characters=current_scene.characters,
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
                        path=planned_start_path,
                        prompt=shot.prompt,
                        seed=shot.seed,
                        generation_parameters=profile_provenance,
                    )
                    shot.planned_start_frame_id = start_asset.id

                async def generate_end(
                    active: RenderProfileExecution,
                    *,
                    current_shot: Shot = shot,
                    current_scene: Scene = scene,
                    current_output: Path = planned_end_path,
                ) -> Path:
                    active_profile = active.profile
                    return await self.images.generate(
                        ImageRequest(
                            prompt=f"{current_shot.prompt} ending",
                            output_path=current_output,
                            width=active_profile.width,
                            height=active_profile.height,
                            seed=current_shot.seed + 1,
                            label=f"Shot {current_shot.sequence_number} END",
                            characters=current_scene.characters,
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
                    path=planned_end_path,
                    prompt=shot.prompt,
                    seed=shot.seed + 1,
                    generation_parameters=profile_provenance,
                )
                shot.planned_end_frame_id = end_asset.id
                shot.continuity_target_frame_id = end_asset.id

                audio_path: Path | None = None
                audio_asset: Asset | None = None
                if shot.dialogue:
                    audio_path = shot_root / "audio" / f"dialogue-{shot.seed}.wav"
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
                    if not rerun:
                        self._transition(shot, ShotStatus.AUDIO_READY)
                        self._transition(shot, ShotStatus.KEYFRAMES_PENDING)

                video_input_asset_ids = [
                    item
                    for item in (
                        shot.planned_start_frame_id,
                        shot.continuity_source_frame_id,
                        end_asset.id,
                        audio_asset.id if audio_asset is not None else None,
                    )
                    if item
                ]

                if not rerun:
                    self._transition(shot, ShotStatus.KEYFRAMES_READY)
                    self._transition(shot, ShotStatus.VIDEO_PENDING)
                self._transition(shot, ShotStatus.VIDEO_GENERATING)
                # Release SQLite's writer lock before the long-running provider process so
                # the API can persist a cancellation request from another connection.
                self.db.commit()

                output = shot_root / "candidates" / f"candidate-{shot.seed}.mp4"
                started = time.monotonic()
                try:

                    async def generate_video(
                        active: RenderProfileExecution,
                        *,
                        current_shot: Shot = shot,
                        current_output: Path = output,
                        current_start: Path = planned_start_path,
                        current_end: Path = planned_end_path,
                        current_audio: Path | None = audio_path,
                    ) -> Path:
                        active_profile = active.profile
                        active_parameters = active.generation_parameters()
                        return await self.video.generate(
                            VideoRequest(
                                prompt=current_shot.prompt,
                                output_path=current_output,
                                start_frame=current_start,
                                end_frame=current_end,
                                duration=current_shot.duration,
                                fps=active_profile.fps,
                                audio_path=current_audio,
                                seed=current_shot.seed,
                                width=active_profile.width,
                                height=active_profile.height,
                                settings={
                                    **current_shot.generation_settings,
                                    **active_parameters,
                                },
                            )
                        )

                    output, execution = await self._execute_provider(
                        self.video,
                        generate_video,
                        execution,
                    )
                    profile_name = execution.effective_profile
                    profile = execution.profile
                    profile_parameters = execution.generation_parameters()
                    execution_payload = execution.model_dump(mode="json")
                    profile_provenance = {
                        **profile_parameters,
                        "requested_render_profile": execution.requested_profile,
                        "render_profile_execution": execution_payload,
                    }
                    self._check_cancelled()
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
                        prompt=shot.prompt,
                        seed=shot.seed,
                        parents=video_input_asset_ids,
                        generation_parameters=profile_provenance,
                        cancel_requested=self.cancel_requested,
                    )
                    self._transition(shot, ShotStatus.VIDEO_READY)
                    self.db.commit()

                    def transition_postprocessing(
                        target: ShotStatus,
                        current_shot: Shot = shot,
                    ) -> None:
                        self._transition(current_shot, target)

                    postprocess = await apply_mock_postprocessing(
                        self.db,
                        project_id=project.id,
                        shot=shot,
                        video_path=output,
                        video_asset=video_asset,
                        audio_path=audio_path,
                        audio_asset=audio_asset,
                        output_directory=shot_root / "postprocessing",
                        settings=shot.generation_settings,
                        expected_duration=shot.duration,
                        width=profile.width,
                        height=profile.height,
                        fps=profile.fps,
                        prompt=shot.prompt,
                        seed=shot.seed,
                        profile_provenance=profile_provenance,
                        base_quality=base_quality,
                        audible_audio_expected=bool(shot.dialogue),
                        lip_sync=self.lip_sync,
                        interpolation=self.interpolation,
                        transition=transition_postprocessing,
                        cancel_requested=self.cancel_requested,
                    )
                    output = postprocess.output_path
                    selected_video_asset = postprocess.output_asset
                    qa = postprocess.quality
                    postprocessing = postprocess.metadata
                    self._transition(shot, ShotStatus.QA_PENDING)
                    self.db.commit()
                    self._check_cancelled()
                    actual_start_path = shot_root / "frames" / f"actual-start-{shot.seed}.png"
                    actual_end_path = shot_root / "frames" / f"actual-end-{shot.seed}.png"
                    extract_frame(
                        output,
                        actual_start_path,
                        cancel_requested=self.cancel_requested,
                    )
                    extract_frame(
                        output,
                        actual_end_path,
                        last=True,
                        cancel_requested=self.cancel_requested,
                    )
                except Exception:
                    self.db.rollback()
                    failed_shot = self.db.get(Shot, shot.id)
                    if failed_shot is not None and failed_shot.status in {
                        ShotStatus.VIDEO_GENERATING.value,
                        ShotStatus.LIPSYNC_PENDING.value,
                        ShotStatus.CONTINUITY_PENDING.value,
                        ShotStatus.QA_PENDING.value,
                    }:
                        self._transition(failed_shot, ShotStatus.FAILED)
                        self.db.commit()
                    raise
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
                for old_candidate in shot.candidates:
                    if old_candidate.disposition == "selected":
                        old_candidate.disposition = "superseded"
                candidate = Candidate(
                    shot_id=shot.id,
                    provider="mock-video",
                    model="ffmpeg-xfade-v1",
                    prompt=shot.prompt,
                    negative_prompt=shot.negative_prompt,
                    seed=shot.seed,
                    settings={
                        **shot.generation_settings,
                        "render_profile_execution": execution_payload,
                        "postprocessing": postprocessing,
                    },
                    generation_seconds=time.monotonic() - started,
                    gpu=self.gpu_assignment,
                    input_asset_ids=video_input_asset_ids,
                    output_asset_id=selected_video_asset.id,
                    first_frame_asset_id=actual_start.id,
                    last_frame_asset_id=actual_end.id,
                    qa_results=qa,
                    disposition="selected",
                )
                self.db.add(candidate)
                self.db.flush()
                shot.actual_start_frame_id = actual_start.id
                shot.actual_end_frame_id = actual_end.id
                shot.selected_candidate_id = candidate.id
                packet = build_packet(project, scene, shot)
                packet["generation"]["render_profile_execution"] = execution_payload
                packet["generation"]["postprocessing"] = postprocessing
                shot.continuity_packet = packet
                write_packet(shot_root / "continuity.yaml", packet)
                self._transition(
                    shot, ShotStatus.COMPLETE if qa["passed"] else ShotStatus.QA_FAILED
                )

                clips.append(output)
                selected_clip_asset_ids.append(selected_video_asset.id)
                transitions.append((shot.transition_type, shot.overlap_frame_count))
                actual_end_count += 1
                previous_shot = shot
                shot_index += 1
                self.db.commit()
                self._report_progress(
                    0.1 + 0.65 * shot_index / total_shots,
                    f"completed shot {shot.sequence_number} of {total_shots}",
                )

        self._check_cancelled()
        self._report_progress(0.8, "assembling final media")
        render_dir = root / "renders" / run_id
        finalization_provenance = {
            **profile_provenance,
            "render_finalization_execution": finalization_payload,
        }
        media_stages: list[dict[str, object]] = []
        has_media_finalization = (
            finalization.audio.normalize or finalization.subtitle.mode != "sidecar"
        )
        assembly_path = render_dir / ("assembled.mp4" if has_media_finalization else "final.mp4")
        try:
            assembly_path, applied_transitions = assemble_with_transitions(
                clips,
                transitions,
                assembly_path,
                fps=profile.fps,
                width=profile.width,
                height=profile.height,
                video_codec=profile.video_codec,
                audio_codec=profile.audio_codec,
                cancel_requested=self.cancel_requested,
            )
        except BaseException:
            self._remove_stage_partials(assembly_path)
            raise
        assembly_asset = register_asset(
            self.db,
            project_id=project.id,
            shot_id=None,
            kind="assembled_render" if has_media_finalization else "final_render",
            path=assembly_path,
            provider="ffmpeg",
            model="transition-assembler-v1",
            generation_parameters={
                **finalization_provenance,
                "finalization_stage": "assembly",
                "transitions": applied_transitions,
            },
            parents=selected_clip_asset_ids,
            cancel_requested=self.cancel_requested,
        )
        media_stages.append(self._stage_record("assembly", assembly_asset, root, applied=True))
        self.db.commit()
        self._report_progress(0.84, "assembled final timeline")

        subtitles = self._subtitle_entries(project, fps=profile.fps)
        subtitle_path = self._write_subtitles_immutable(
            subtitles,
            render_dir / "final.srt",
        )
        subtitle_asset = register_asset(
            self.db,
            project_id=project.id,
            shot_id=None,
            kind="subtitle_sidecar",
            path=subtitle_path,
            provider="flipthis-core",
            model="srt-timeline-v1",
            generation_parameters={
                **finalization_provenance,
                "finalization_stage": "subtitle_sidecar",
                "cue_count": len(subtitles),
            },
            parents=selected_clip_asset_ids,
            cancel_requested=self.cancel_requested,
        )
        media_stages.append(
            self._stage_record("subtitle_sidecar", subtitle_asset, root, applied=True)
        )
        self.db.commit()
        self._report_progress(0.87, "created subtitle sidecar")

        current_path = assembly_path
        current_asset = assembly_asset
        audio_result_metadata: dict[str, object] | None = None
        if finalization.audio.normalize:
            self._report_progress(0.88, "normalizing and mixing audio")
            audio_output = render_dir / (
                "audio-finalized.mp4" if finalization.subtitle.mode != "sidecar" else "final.mp4"
            )
            loudness = LoudnessTarget(
                integrated_lufs=finalization.audio.integrated_lufs,
                loudness_range_lu=finalization.audio.loudness_range_lu,
                true_peak_dbfs=finalization.audio.true_peak_dbfs,
            )
            music_options = self._music_options()
            audio_result = normalize_audio(
                current_path,
                audio_output,
                options=AudioFinalizationOptions(
                    loudness=loudness,
                    audio_codec=profile.audio_codec,
                ),
                music=music_options,
                cancel_requested=self.cancel_requested,
            )
            audio_result_metadata = self._audio_result_record(audio_result)
            audio_parents = [current_asset.id]
            if self.music_asset is not None:
                audio_parents.append(self.music_asset.id)
            current_asset = register_asset(
                self.db,
                project_id=project.id,
                shot_id=None,
                kind=(
                    "audio_finalized_render"
                    if finalization.subtitle.mode != "sidecar"
                    else "final_render"
                ),
                path=audio_output,
                provider="ffmpeg",
                model=("loudnorm-ducking-v1" if self.music_asset is not None else "loudnorm-v1"),
                generation_parameters={
                    **finalization_provenance,
                    "finalization_stage": "audio",
                    "result": audio_result_metadata,
                },
                parents=audio_parents,
                cancel_requested=self.cancel_requested,
            )
            current_path = audio_output
            media_stages.append(
                self._stage_record(
                    "audio",
                    current_asset,
                    root,
                    applied=True,
                    result=audio_result_metadata,
                )
            )
            self.db.commit()
            self._report_progress(0.91, "finalized audio")
        else:
            media_stages.append(
                {
                    "stage": "audio",
                    "applied": False,
                    "reason": "not_requested",
                    "asset_id": None,
                }
            )

        subtitle_result_metadata: dict[str, object] | None = None
        if finalization.subtitle.mode != "sidecar":
            self._report_progress(0.92, f"applying {finalization.subtitle.mode} subtitles")
            if finalization.subtitle.mode == "soft":
                subtitle_result = mux_soft_subtitles(
                    current_path,
                    subtitle_path,
                    render_dir / "final.mp4",
                    options=SoftSubtitleOptions(
                        language=finalization.subtitle.language,
                        title=finalization.subtitle.title,
                        default=finalization.subtitle.default,
                        forced=finalization.subtitle.forced,
                    ),
                    cancel_requested=self.cancel_requested,
                )
                subtitle_model = "subtitle-mux-v1"
            else:
                subtitle_result = burn_subtitles(
                    current_path,
                    subtitle_path,
                    render_dir / "final.mp4",
                    options=BurnSubtitleOptions(video_codec=profile.video_codec),
                    cancel_requested=self.cancel_requested,
                )
                subtitle_model = "subtitle-burn-v1"
            subtitle_result_metadata = self._subtitle_result_record(subtitle_result)
            current_asset = register_asset(
                self.db,
                project_id=project.id,
                shot_id=None,
                kind="final_render",
                path=subtitle_result.output_path,
                provider="ffmpeg",
                model=subtitle_model,
                generation_parameters={
                    **finalization_provenance,
                    "finalization_stage": "subtitles",
                    "result": subtitle_result_metadata,
                },
                parents=[current_asset.id, subtitle_asset.id],
                cancel_requested=self.cancel_requested,
            )
            current_path = subtitle_result.output_path
            media_stages.append(
                self._stage_record(
                    "subtitles",
                    current_asset,
                    root,
                    applied=True,
                    result=subtitle_result_metadata,
                )
            )
            self.db.commit()
            self._report_progress(0.95, "finalized subtitles")
        else:
            media_stages.append(
                {
                    "stage": "subtitles",
                    "applied": False,
                    "reason": "sidecar_only",
                    "asset_id": None,
                }
            )

        final_path = current_path
        final_asset = current_asset
        expected_final_duration = sum(
            shot.duration for scene in project.scenes for shot in scene.shots
        ) - sum(float(item["duration"]) for item in applied_transitions)
        self._report_progress(0.96, "validating final media")
        final_quality = analyze_video(
            final_path,
            expected_final_duration,
            profile.width,
            profile.height,
            profile.fps,
            audio_expected=True,
            audible_audio_expected=bool(self.music_asset)
            or any(shot.dialogue.strip() for scene in project.scenes for shot in scene.shots),
            cancel_requested=self.cancel_requested,
        )
        required_final_checks = {
            "decodable_video",
            "duration_in_tolerance",
            "dimensions_correct",
            "frame_rate_valid",
            "frame_rate_correct",
            "audio_present_when_expected",
        }
        failed_required_checks = sorted(
            key for key in required_final_checks if not bool(final_quality["checks"].get(key))
        )
        if failed_required_checks:
            raise RuntimeError(
                "Final render failed required media QA: " + ", ".join(failed_required_checks)
            )

        thumb_path = self._thumbnail_immutable(
            final_path,
            render_dir / "thumbnail.jpg",
        )
        thumbnail_asset = register_asset(
            self.db,
            project_id=project.id,
            shot_id=None,
            kind="render_thumbnail",
            path=thumb_path,
            provider="ffmpeg",
            model="thumbnail-v1",
            generation_parameters=finalization_provenance,
            parents=[final_asset.id],
            cancel_requested=self.cancel_requested,
        )
        self.db.commit()

        contact_path = self._contact_sheet_from_final(
            final_path,
            render_dir / "contact-sheet.jpg",
            frame_count=max(1, actual_end_count),
            duration_seconds=float(final_quality["duration"]),
        )
        contact_asset = register_asset(
            self.db,
            project_id=project.id,
            shot_id=None,
            kind="render_contact_sheet",
            path=contact_path,
            provider="ffmpeg",
            model="timeline-contact-sheet-v1",
            generation_parameters=finalization_provenance,
            parents=[final_asset.id],
            cancel_requested=self.cancel_requested,
        )
        self.db.commit()
        self._check_cancelled()
        manifest_path = root / "manifests" / f"{run_id}.json"
        manifest = {
            "version": 2,
            "run_id": run_id,
            "project_id": project.id,
            "requested_render_profile": execution.requested_profile,
            "effective_render_profile": profile_name,
            "render_profile_execution": execution_payload,
            "render_finalization_execution": finalization_payload,
            "render": str(final_path.relative_to(root)),
            "subtitles": str(subtitle_path.relative_to(root)),
            "thumbnail": str(thumb_path.relative_to(root)),
            "contact_sheet": str(contact_path.relative_to(root)),
            "final_quality": final_quality,
            "finalization_stages": media_stages,
            "asset_ids": {
                "render": final_asset.id,
                "subtitles": subtitle_asset.id,
                "thumbnail": thumbnail_asset.id,
                "contact_sheet": contact_asset.id,
            },
            "transitions": applied_transitions,
            "shots": [
                {
                    "id": shot.id,
                    "selected_candidate_id": shot.selected_candidate_id,
                    "planned_start_frame": shot.planned_start_frame_id,
                    "planned_end_frame": shot.planned_end_frame_id,
                    "actual_start_frame": shot.actual_start_frame_id,
                    "actual_end_frame": shot.actual_end_frame_id,
                    "continuity_source_frame": shot.continuity_source_frame_id,
                    "continuity_target_frame": shot.continuity_target_frame_id,
                }
                for scene in project.scenes
                for shot in scene.shots
            ],
        }
        write_manifest(manifest_path, manifest)
        manifest_asset = register_asset(
            self.db,
            project_id=project.id,
            shot_id=None,
            kind="project_manifest",
            path=manifest_path,
            provider="flipthis-core",
            model="manifest-v2",
            generation_parameters=finalization_provenance,
            parents=[
                final_asset.id,
                subtitle_asset.id,
                thumbnail_asset.id,
                contact_asset.id,
            ],
            cancel_requested=self.cancel_requested,
        )
        self.db.commit()
        render = Render(
            project_id=project.id,
            render_profile=profile_name,
            included_scene_ids=[scene.id for scene in project.scenes],
            included_shot_ids=[shot.id for scene in project.scenes for shot in scene.shots],
            output_path=str(final_path),
            codec=profile.video_codec,
            resolution=f"{profile.width}x{profile.height}",
            frame_rate=profile.fps,
            audio_configuration={
                "codec": profile.audio_codec,
                "sample_rate": 48000,
                "channels": 2,
                "normalize": finalization.audio.normalize,
                "target": finalization.audio.model_dump(mode="json"),
                "music_asset_id": (
                    finalization.music.asset_id if finalization.music is not None else None
                ),
                "result": audio_result_metadata,
            },
            subtitle_configuration={
                **finalization.subtitle.model_dump(mode="json"),
                "path": str(subtitle_path),
                "asset_id": subtitle_asset.id,
                "result": subtitle_result_metadata,
            },
            creation_metadata={
                "run_id": run_id,
                "manifest": str(manifest_path),
                "thumbnail": str(thumb_path),
                "contact_sheet": str(contact_path),
                "transitions": applied_transitions,
                "output_asset_id": final_asset.id,
                "manifest_asset_id": manifest_asset.id,
                "subtitle_asset_id": subtitle_asset.id,
                "thumbnail_asset_id": thumbnail_asset.id,
                "contact_sheet_asset_id": contact_asset.id,
                "requested_render_profile": execution.requested_profile,
                "effective_render_profile": profile_name,
                "render_profile_execution": execution_payload,
                "render_finalization_execution": finalization_payload,
                "finalization_stages": media_stages,
                "final_quality": final_quality,
            },
        )
        project.status = ProjectStatus.COMPLETE.value
        self.db.add(render)
        self._check_cancelled()
        self.db.commit()
        self._report_progress(0.98, "validated final media")
        return render

    def _check_cancelled(self) -> None:
        if self.cancel_requested and self.cancel_requested():
            raise PipelineCancelled("Render cancellation requested")

    def _report_progress(self, value: float, stage: str) -> None:
        if self.progress:
            self.progress(value, stage)

    def _validate_finalization_inputs(self, project: Project) -> None:
        captured = self.render_finalization_execution.music
        if captured is None:
            if self.music_asset is not None:
                raise ValueError("A music Asset was supplied without captured music settings")
            return
        if self.music_asset is None:
            raise ValueError("Captured background music requires its immutable Asset")
        asset = self.music_asset
        if asset.id != captured.asset_id:
            raise ValueError("Background-music Asset does not match the captured Asset ID")
        if asset.project_id != project.id:
            raise ValueError("Background-music Asset belongs to another project")
        if asset.mime_type != captured.mime_type:
            raise ValueError("Background-music MIME type changed after enqueue")
        if asset.checksum != captured.checksum:
            raise ValueError("Background-music database checksum changed after enqueue")
        path = Path(asset.file_path)
        if not path.is_file():
            raise ValueError("Background-music file is missing")
        try:
            path.resolve().relative_to(Path(project.root_asset_directory).resolve())
        except ValueError as error:
            raise ValueError("Background-music file escapes the project asset root") from error
        if checksum(path) != captured.checksum:
            raise ValueError("Background-music file checksum changed after enqueue")

    def _music_options(self) -> BackgroundMusicOptions | None:
        captured = self.render_finalization_execution.music
        if captured is None:
            return None
        if self.music_asset is None:
            raise RuntimeError("Validated background-music Asset is unavailable")
        return BackgroundMusicOptions(
            path=Path(self.music_asset.file_path),
            gain_db=captured.gain_db,
            threshold=captured.threshold,
            ratio=captured.ratio,
            attack_ms=captured.attack_ms,
            release_ms=captured.release_ms,
            loop=captured.loop,
        )

    def _write_subtitles_immutable(
        self,
        entries: list[tuple[float, float, str]],
        output: Path,
    ) -> Path:
        self._check_cancelled()
        if os.path.lexists(output):
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}")
        try:
            write_subtitles(entries, temporary)
            self._check_cancelled()
            if os.path.lexists(output):
                raise FileExistsError(output)
            temporary.replace(output)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return output

    def _thumbnail_immutable(self, video: Path, output: Path) -> Path:
        self._check_cancelled()
        if os.path.lexists(output):
            raise FileExistsError(output)
        temporary = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}")
        try:
            thumbnail(
                video,
                temporary,
                cancel_requested=self.cancel_requested,
            )
            self._check_cancelled()
            if os.path.lexists(output):
                raise FileExistsError(output)
            temporary.replace(output)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return output

    def _contact_sheet_from_final(
        self,
        video: Path,
        output: Path,
        *,
        frame_count: int,
        duration_seconds: float,
    ) -> Path:
        self._check_cancelled()
        if os.path.lexists(output):
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = output.with_name(
            f".{output.stem}-{uuid.uuid4().hex}.partial{output.suffix}"
        )
        try:
            with tempfile.TemporaryDirectory(
                prefix=".contact-frames-",
                dir=output.parent,
            ) as temporary_directory:
                frames: list[Path] = []
                for index in range(frame_count):
                    self._check_cancelled()
                    timestamp = duration_seconds * (index + 0.5) / frame_count
                    frame = Path(temporary_directory) / f"frame-{index:03d}.png"
                    run(
                        [
                            get_settings().ffmpeg_path,
                            "-nostdin",
                            "-y",
                            "-v",
                            "error",
                            "-ss",
                            f"{timestamp:.6f}",
                            "-i",
                            str(video),
                            "-frames:v",
                            "1",
                            str(frame),
                        ],
                        cancel_requested=self.cancel_requested,
                    )
                    frames.append(frame)
                contact_sheet(frames, temporary_output)
            self._check_cancelled()
            if os.path.lexists(output):
                raise FileExistsError(output)
            temporary_output.replace(output)
        except BaseException:
            temporary_output.unlink(missing_ok=True)
            raise
        return output

    @staticmethod
    def _remove_stage_partials(output: Path) -> None:
        for partial in output.parent.glob(f".{output.stem}-*.partial{output.suffix}"):
            partial.unlink(missing_ok=True)

    @staticmethod
    def _stage_record(
        stage: str,
        asset: Asset,
        root: Path,
        *,
        applied: bool,
        result: dict[str, object] | None = None,
    ) -> dict[str, object]:
        record: dict[str, object] = {
            "stage": stage,
            "applied": applied,
            "asset_id": asset.id,
            "asset_type": asset.type,
            "path": str(Path(asset.file_path).relative_to(root)),
            "checksum": asset.checksum,
            "parent_asset_ids": asset.parent_asset_ids,
        }
        if result is not None:
            record["result"] = result
        return record

    @staticmethod
    def _audio_result_record(result: AudioFinalizationResult) -> dict[str, object]:
        return {
            "source_video_duration_seconds": result.source_video_duration_seconds,
            "output_video_duration_seconds": result.output_video_duration_seconds,
            "width": result.width,
            "height": result.height,
            "frame_rate": result.frame_rate,
            "video_codec": result.video_codec,
            "audio_codec": result.audio_codec,
            "sample_rate": result.sample_rate,
            "channels": result.channels,
            "target": asdict(result.target),
            "pre_normalization": asdict(result.pre_normalization),
            "music_ducking_applied": result.music_ducking_applied,
        }

    @staticmethod
    def _subtitle_result_record(result: SubtitleFinalizationResult) -> dict[str, object]:
        return {
            "mode": result.mode.value,
            "source_video_duration_seconds": result.source_video_duration_seconds,
            "output_video_duration_seconds": result.output_video_duration_seconds,
            "width": result.width,
            "height": result.height,
            "frame_rate": result.frame_rate,
            "video_codec": result.video_codec,
            "audio_codec": result.audio_codec,
            "subtitle_codec": result.subtitle_codec,
            "subtitle_stream_count": result.subtitle_stream_count,
        }

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

    @staticmethod
    def _transition(shot: Shot, target: ShotStatus) -> None:
        validate_transition(ShotStatus(shot.status), target)
        shot.status = target.value

    @staticmethod
    def _timeline_overlap(shot: Shot, shot_index: int, *, fps: int) -> float:
        if shot_index == 0:
            return 0.0
        if shot.transition_type == "crossfade":
            return max(1 / fps, shot.overlap_frame_count / fps)
        if shot.transition_type == "shared_frame":
            return shot.overlap_frame_count / fps
        return 0.0

    @classmethod
    def _subtitle_entries(
        cls,
        project: Project,
        *,
        fps: int,
    ) -> list[tuple[float, float, str]]:
        entries: list[tuple[float, float, str]] = []
        timeline = 0.0
        shot_index = 0
        for scene in project.scenes:
            for shot in scene.shots:
                overlap = cls._timeline_overlap(shot, shot_index, fps=fps)
                shot_start = timeline - overlap if shot.transition_type == "crossfade" else timeline
                if shot.dialogue:
                    entries.append(
                        (
                            shot_start + 0.2,
                            shot_start + shot.duration - overlap - 0.2,
                            f"{shot.speaker}: {shot.dialogue}",
                        )
                    )
                timeline += shot.duration - overlap
                shot_index += 1
        return entries


def create_sample(db: Session, root: Path) -> Project:
    project = Project(
        name="The Last Token - Smoke Test",
        description="Two friends recover a glowing token in a rain-soaked arcade.",
        target_duration=32,
        root_asset_directory=str(root),
        original_story=(
            "Ash and Mira enter an abandoned arcade. They find a glowing token and decide "
            "to restore the last machine."
        ),
        global_visual_style="stylized cinematic animation, blue and magenta neon",
    )
    project.characters = [
        Character(
            name="Ash",
            canonical_appearance="young adult, black hoodie, flashlight",
            wardrobe_rules="black hoodie",
            consent_provenance={"kind": "fictional"},
        ),
        Character(
            name="Mira",
            canonical_appearance="young adult, yellow raincoat, tool bag",
            wardrobe_rules="yellow raincoat",
            consent_provenance={"kind": "fictional"},
        ),
    ]
    scene1 = Scene(
        number=1,
        title="Arrival",
        location="abandoned arcade entrance",
        time_of_day="night",
        lighting="blue emergency light",
        characters=["Ash", "Mira"],
        props=["flashlight", "token"],
        environment="Rain streaks the windows",
    )
    scene2 = Scene(
        number=2,
        title="The Machine",
        location="arcade main floor",
        time_of_day="night",
        lighting="magenta machine glow",
        characters=["Ash", "Mira"],
        props=["token", "arcade cabinet"],
        environment="Dusty cabinets wake one by one",
    )
    shots = [
        Shot(
            sequence_number=1,
            shot_type="establishing",
            duration=8,
            prompt="Wide view as Ash and Mira enter the abandoned arcade",
            camera={"framing": "wide", "movement": "slow_dolly_in"},
            character_positions={"Ash": "left", "Mira": "right"},
            transition_type="hard_cut",
            seed=101,
            status=ShotStatus.APPROVED.value,
        ),
        Shot(
            sequence_number=2,
            shot_type="medium",
            duration=8,
            prompt="Ash raises a flashlight toward a glowing token",
            dialogue="Did you see that light?",
            speaker="Ash",
            camera={"framing": "medium", "movement": "slow_pan_right"},
            character_positions={"Ash": "left", "Mira": "right"},
            transition_type="shared_frame",
            overlap_frame_count=6,
            seed=102,
            status=ShotStatus.APPROVED.value,
        ),
        Shot(
            sequence_number=3,
            shot_type="close_up",
            duration=8,
            prompt="Mira picks up the glowing token and smiles",
            dialogue="It still has power.",
            speaker="Mira",
            camera={"framing": "close_up", "movement": "static"},
            character_positions={"Mira": "center"},
            transition_type="crossfade",
            overlap_frame_count=12,
            seed=103,
            status=ShotStatus.APPROVED.value,
        ),
        Shot(
            sequence_number=4,
            shot_type="reaction",
            duration=8,
            prompt="The arcade cabinet turns on and fills the room with warm light",
            camera={"framing": "medium_two_shot", "movement": "pull_back"},
            character_positions={"Ash": "left", "Mira": "right"},
            transition_type="hard_cut",
            seed=104,
            status=ShotStatus.APPROVED.value,
        ),
    ]
    scene1.shots = shots[:2]
    scene2.shots = shots[2:]
    project.scenes = [scene1, scene2]
    db.add(project)
    db.flush()
    shots[2].continuity_source_shot_id = shots[1].id
    db.commit()
    return project
