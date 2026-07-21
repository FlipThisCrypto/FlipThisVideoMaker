import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from flipthis_video_maker.config.settings import get_settings
from flipthis_video_maker.contracts.video_generation import (
    ChainClipState,
    FirstLastFrameGenerationRequest,
    FirstLastFrameGenerationResult,
    InterpolationMode,
    LipSyncMode,
    ProviderTiming,
    utc_now,
)
from flipthis_video_maker.domain.models import Asset, Project, VideoChainClip
from flipthis_video_maker.media.ffmpeg import checksum, probe
from flipthis_video_maker.media.video_delivery import (
    PerceptualMetricRunner,
    inspect_delivery_contract,
    inspect_frame_timing,
    mock_motion_interpolate,
    mux_exact_delivery_audio,
    normalize_interpolated_delivery,
    write_qa_report,
)
from flipthis_video_maker.providers.base.video_generation import (
    ProviderRunOutput,
    ResolvedFirstLastFrameRequest,
)
from flipthis_video_maker.providers.latentsync.cli import LipSyncRunOutput
from flipthis_video_maker.providers.registry import (
    configured_interpolation_provider,
    configured_lip_sync_provider,
)
from flipthis_video_maker.scheduler.gpu import GPUTelemetryRecorder
from flipthis_video_maker.services.asset_inputs import validated_asset_input
from flipthis_video_maker.services.video_chains import (
    SUPPORTED_KEYFRAME_MIME_TYPES,
    request_from_clip,
)
from flipthis_video_maker.storage.assets import register_asset

ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


class FirstLastFrameRunner(Protocol):
    async def generate(
        self,
        request: ResolvedFirstLastFrameRequest,
        *,
        cancel_requested: CancelCheck | None = None,
        progress: ProgressCallback | None = None,
    ) -> ProviderRunOutput: ...


class InterpolationRunner(Protocol):
    async def process(self, video: Path, output: Path, *, target_fps: int) -> Path: ...


class LipSyncRunner(Protocol):
    async def process(
        self,
        video: Path,
        audio: Path,
        output: Path,
        *,
        seed: int | None,
    ) -> LipSyncRunOutput: ...


class VideoChainPipeline:
    def __init__(
        self,
        db: Session,
        *,
        provider: FirstLastFrameRunner,
        interpolation_provider: InterpolationRunner | None = None,
        lip_sync_provider: LipSyncRunner | None = None,
        perceptual_metric_provider: PerceptualMetricRunner | None = None,
        gpu_telemetry: GPUTelemetryRecorder | None = None,
        cancel_requested: CancelCheck | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.db = db
        self.provider = provider
        self.interpolation_provider = interpolation_provider
        self.lip_sync_provider = lip_sync_provider
        self.perceptual_metric_provider = perceptual_metric_provider
        self.gpu_telemetry = gpu_telemetry
        self.cancel_requested = cancel_requested
        self.progress = progress

    async def run(self, project: Project, clip: VideoChainClip) -> Asset:
        request = request_from_clip(clip)
        start_asset, start_path = validated_asset_input(
            self.db,
            project,
            request.start_frame_asset_id,
            allowed_mime_types=SUPPORTED_KEYFRAME_MIME_TYPES,
        )
        end_asset, end_path = validated_asset_input(
            self.db,
            project,
            request.target_end_frame_asset_id,
            allowed_mime_types=SUPPORTED_KEYFRAME_MIME_TYPES,
        )
        self._check_cancelled()
        root = (
            Path(project.root_asset_directory)
            / "chains"
            / clip.chain_id
            / f"lineage-{clip.lineage_version}"
            / f"clip-{clip.sequence_number:05d}-r{clip.revision}"
        )
        root.mkdir(parents=True, exist_ok=True)

        provider_run: ProviderRunOutput
        native_asset = self._existing_asset(project, clip.native_video_asset_id, "video/mp4")
        if native_asset is None:
            self._set_telemetry_stage("generating")
            clip.state = ChainClipState.GENERATING.value
            self.db.commit()
            native_path = root / f"native-provider-output-{uuid.uuid4().hex}.mp4"
            provider_run = await self.provider.generate(
                ResolvedFirstLastFrameRequest(
                    snapshot=request,
                    start_frame_path=start_path,
                    start_frame_mime_type=start_asset.mime_type,
                    end_frame_path=end_path,
                    end_frame_mime_type=end_asset.mime_type,
                    output_path=native_path,
                ),
                cancel_requested=self.cancel_requested,
                progress=self.progress,
            )
            native_facts = inspect_frame_timing(
                native_path,
                cancel_requested=self.cancel_requested,
            )
            native_asset = register_asset(
                self.db,
                project_id=project.id,
                shot_id=None,
                kind="native_generative_video",
                path=native_path,
                provider=request.provider_id,
                model=provider_run.actual_model,
                prompt=request.prompt,
                seed=request.seed,
                parents=[start_asset.id, end_asset.id],
                generation_parameters={
                    "generation_category": request.generation_category.value,
                    "request_digest": request.digest(),
                    "provider_job_id": provider_run.provider_job_id,
                    "provider_api_version": provider_run.api_version,
                    "native_requested_fps": request.native_requested_fps,
                    "actual_native_fps": native_facts["average_frame_rate"],
                    "actual_settings": provider_run.actual_settings,
                },
                cancel_requested=self.cancel_requested,
            )
            clip.native_video_asset_id = native_asset.id
            clip.provider_job_id = provider_run.provider_job_id
            clip.result_snapshot = {
                "provider_run": provider_run.model_dump(mode="json"),
            }
            self.db.commit()
        else:
            provider_run = self._provider_run_from_resume(clip, request)

        motion_source_asset = native_asset
        lip_sync_run: LipSyncRunOutput | None = None
        lip_source_asset: Asset | None = None
        lip_source_interpolated_asset: Asset | None = None
        audio_asset: Asset | None = None
        audio_path: Path | None = None
        if request.lip_sync_mode is not LipSyncMode.SKIP:
            self._set_telemetry_stage("lip_syncing")
            if request.lip_sync_mode is not LipSyncMode.LATENTSYNC:
                raise RuntimeError("The selected integrated lip-sync mode is not implemented")
            if request.audio_reference_asset_id is None or not request.lip_sync_provider_id:
                raise RuntimeError("Lip-sync request is missing its persisted audio or provider")
            audio_asset, audio_path = validated_asset_input(
                self.db,
                project,
                request.audio_reference_asset_id,
                allowed_mime_types=frozenset({"audio/wav", "audio/mpeg"}),
            )
            if (
                audio_asset.duration is None
                or abs(audio_asset.duration - request.duration_seconds) > 0.1
            ):
                raise RuntimeError(
                    "Standard lip-sync audio must be within 100 ms of the 10-second clip"
                )
            clip.state = ChainClipState.LIP_SYNCING.value
            self.db.commit()
            lip_source_asset = self._snapshot_asset(
                project,
                clip,
                "lip_sync_source_asset_id",
                "video/mp4",
            )
            lip_source_interpolated_asset = self._snapshot_asset(
                project,
                clip,
                "lip_sync_interpolated_asset_id",
                "video/mp4",
            )
            interpolator = self._interpolator(request)
            if lip_source_asset is None:
                if lip_source_interpolated_asset is None:
                    lip_source_interpolated_path = (
                        root / f"lip-sync-rife-25fps-{uuid.uuid4().hex}.mp4"
                    )
                    await interpolator.process(
                        Path(native_asset.file_path),
                        lip_source_interpolated_path,
                        target_fps=25,
                    )
                    lip_source_interpolated_asset = register_asset(
                        self.db,
                        project_id=project.id,
                        shot_id=None,
                        kind="lip_sync_source_interpolated_raw_video",
                        path=lip_source_interpolated_path,
                        provider=request.interpolation_provider_id or "rife-local",
                        model="Practical-RIFE-4.25",
                        parents=[native_asset.id],
                        generation_parameters={
                            "generation_category": "frame_interpolation",
                            "purpose": "LatentSync 1.5 pre-normalization",
                            "source_native_fps": native_asset.frame_rate,
                            "requested_target_fps": 25,
                        },
                        cancel_requested=self.cancel_requested,
                    )
                    clip.result_snapshot = {
                        **clip.result_snapshot,
                        "lip_sync_interpolated_asset_id": lip_source_interpolated_asset.id,
                    }
                    self.db.commit()
                lip_source_path = root / f"lip-sync-source-25fps-{uuid.uuid4().hex}.mp4"
                normalize_interpolated_delivery(
                    Path(lip_source_interpolated_asset.file_path),
                    lip_source_path,
                    duration_seconds=request.duration_seconds,
                    delivery_fps=25,
                    cancel_requested=self.cancel_requested,
                )
                lip_source_asset = register_asset(
                    self.db,
                    project_id=project.id,
                    shot_id=None,
                    kind="lip_sync_source_interpolated_video",
                    path=lip_source_path,
                    provider=request.interpolation_provider_id or "rife-local",
                    model="Practical-RIFE-4.25",
                    parents=[native_asset.id, lip_source_interpolated_asset.id],
                    generation_parameters={
                        "generation_category": "frame_rate_conversion_and_encoding",
                        "purpose": "LatentSync 1.5 documented 25-fps input",
                        "source_native_fps": native_asset.frame_rate,
                        "target_fps": 25,
                        "expected_frame_count": int(request.duration_seconds * 25),
                    },
                    cancel_requested=self.cancel_requested,
                )
                clip.result_snapshot = {
                    **clip.result_snapshot,
                    "lip_sync_source_asset_id": lip_source_asset.id,
                }
                self.db.commit()
            existing_lip_asset = self._snapshot_asset(
                project,
                clip,
                "lip_sync_asset_id",
                "video/mp4",
            )
            if existing_lip_asset is None:
                lip_sync_provider = self.lip_sync_provider
                if lip_sync_provider is None:
                    lip_sync_provider = configured_lip_sync_provider(
                        get_settings().provider_config,
                        request.lip_sync_provider_id,
                        cancel_requested=self.cancel_requested,
                    )
                lip_output_path = root / f"latentsync-performance-output-{uuid.uuid4().hex}.mp4"
                lip_sync_run = await lip_sync_provider.process(
                    Path(lip_source_asset.file_path),
                    audio_path,
                    lip_output_path,
                    seed=request.seed,
                )
                existing_lip_asset = register_asset(
                    self.db,
                    project_id=project.id,
                    shot_id=None,
                    kind="performance_conditioned_video",
                    path=lip_output_path,
                    provider=request.lip_sync_provider_id,
                    model=lip_sync_run.actual_model,
                    parents=[lip_source_asset.id, audio_asset.id],
                    generation_parameters={
                        "generation_category": "performance_conditioned_video",
                        "request_digest": request.digest(),
                        "sync_confidence": lip_sync_run.sync_confidence,
                        "av_offset_frames": lip_sync_run.av_offset_frames,
                        "sync_qa_passed": lip_sync_run.sync_qa_passed,
                        "actual_settings": lip_sync_run.actual_settings,
                    },
                    cancel_requested=self.cancel_requested,
                )
                clip.result_snapshot = {
                    **clip.result_snapshot,
                    "lip_sync_asset_id": existing_lip_asset.id,
                    "lip_sync_run": lip_sync_run.model_dump(mode="json"),
                }
                self.db.commit()
            else:
                stored_lip_sync = clip.result_snapshot.get("lip_sync_run")
                if not isinstance(stored_lip_sync, dict):
                    raise RuntimeError("Resumable lip-sync output has no provider result")
                lip_sync_run = LipSyncRunOutput.model_validate(stored_lip_sync)
                if lip_sync_run.provider_id != request.lip_sync_provider_id:
                    raise RuntimeError("Resumable lip-sync output has stale provider lineage")
            motion_source_asset = existing_lip_asset

        self._check_cancelled()
        delivery_asset = self._existing_asset(project, clip.delivery_video_asset_id, "video/mp4")
        if delivery_asset is None:
            self._set_telemetry_stage("interpolating_and_encoding")
            clip.state = ChainClipState.INTERPOLATING.value
            self.db.commit()
            stage_version = uuid.uuid4().hex
            delivery_path = root / f"delivery-60fps-{stage_version}.mp4"
            video_only_delivery_path = (
                root / f"delivery-60fps-video-only-{stage_version}.mp4"
                if lip_sync_run is not None
                else delivery_path
            )
            if request.interpolation_mode is InterpolationMode.RIFE:
                interpolator = self._interpolator(request)
                interpolated_asset = self._snapshot_asset(
                    project,
                    clip,
                    "interpolated_asset_id",
                    "video/mp4",
                )
                if interpolated_asset is None:
                    interpolated_path = root / f"rife-interpolated-{uuid.uuid4().hex}.mp4"
                    await interpolator.process(
                        Path(motion_source_asset.file_path),
                        interpolated_path,
                        target_fps=request.delivery_fps,
                    )
                    interpolated_asset = register_asset(
                        self.db,
                        project_id=project.id,
                        shot_id=None,
                        kind="temporally_interpolated_video",
                        path=interpolated_path,
                        provider=request.interpolation_provider_id or "rife-local",
                        model="Practical-RIFE-4.25",
                        parents=[motion_source_asset.id],
                        generation_parameters={
                            "generation_category": "frame_interpolation",
                            "source_native_fps": motion_source_asset.frame_rate,
                            "requested_delivery_fps": request.delivery_fps,
                        },
                        cancel_requested=self.cancel_requested,
                    )
                    clip.result_snapshot = {
                        **clip.result_snapshot,
                        "interpolated_asset_id": interpolated_asset.id,
                    }
                    self.db.commit()
                normalize_interpolated_delivery(
                    Path(interpolated_asset.file_path),
                    video_only_delivery_path,
                    duration_seconds=request.duration_seconds,
                    delivery_fps=request.delivery_fps,
                    cancel_requested=self.cancel_requested,
                )
                delivery_parents = list(
                    dict.fromkeys([native_asset.id, motion_source_asset.id, interpolated_asset.id])
                )
                delivery_provider = request.interpolation_provider_id or "rife-local"
            elif request.interpolation_mode is InterpolationMode.MOCK_FFMPEG_MINTERPOLATE:
                if not request.provider_id.startswith("fixture-"):
                    raise RuntimeError(
                        "Mock FFmpeg interpolation is restricted to deterministic test providers"
                    )
                mock_motion_interpolate(
                    Path(motion_source_asset.file_path),
                    video_only_delivery_path,
                    duration_seconds=request.duration_seconds,
                    delivery_fps=request.delivery_fps,
                    cancel_requested=self.cancel_requested,
                )
                delivery_parents = list(dict.fromkeys([native_asset.id, motion_source_asset.id]))
                delivery_provider = "mock-ffmpeg-minterpolate"
            elif request.interpolation_mode is InterpolationMode.PROVIDER_NATIVE:
                normalize_interpolated_delivery(
                    Path(motion_source_asset.file_path),
                    video_only_delivery_path,
                    duration_seconds=request.duration_seconds,
                    delivery_fps=request.delivery_fps,
                    cancel_requested=self.cancel_requested,
                )
                delivery_parents = list(dict.fromkeys([native_asset.id, motion_source_asset.id]))
                delivery_provider = request.provider_id
            else:
                raise RuntimeError("Delivery conversion is disabled for a non-delivery native FPS")
            if lip_sync_run is not None:
                if audio_path is None or audio_asset is None:
                    raise RuntimeError("Lip-sync delivery lost its persisted audio lineage")
                mux_exact_delivery_audio(
                    video_only_delivery_path,
                    audio_path,
                    delivery_path,
                    duration_seconds=request.duration_seconds,
                    cancel_requested=self.cancel_requested,
                )
                delivery_parents.append(audio_asset.id)
                delivery_provider = (
                    f"{request.lip_sync_provider_id}+{request.interpolation_provider_id}"
                )
            delivery_asset = register_asset(
                self.db,
                project_id=project.id,
                shot_id=None,
                kind="delivery_video",
                path=delivery_path,
                provider=delivery_provider,
                model="libx264-cfr-delivery-v1",
                parents=delivery_parents,
                generation_parameters={
                    "generation_category": "frame_rate_conversion",
                    "native_video_asset_id": native_asset.id,
                    "native_frame_rate": native_asset.frame_rate,
                    "delivery_frame_rate": request.delivery_fps,
                    "expected_delivery_frames": request.expected_delivery_frames,
                    "interpolation_mode": request.interpolation_mode.value,
                    "lip_sync_mode": request.lip_sync_mode.value,
                    "lip_sync_asset_id": (
                        motion_source_asset.id if lip_sync_run is not None else None
                    ),
                    "audio_reference_asset_id": request.audio_reference_asset_id,
                },
                cancel_requested=self.cancel_requested,
            )
            clip.delivery_video_asset_id = delivery_asset.id
            self.db.commit()

        self._check_cancelled()
        self._set_telemetry_stage("validating")
        clip.state = ChainClipState.VALIDATING.value
        self.db.commit()
        # A failed QA attempt may already have registered some evidence before the
        # worker records its failure. Never overwrite those immutable files on retry.
        evidence = root / f"qa-{uuid.uuid4().hex}"
        report = inspect_delivery_contract(
            Path(delivery_asset.file_path),
            request=request,
            required_start=start_path,
            target_end=end_path,
            evidence_directory=evidence,
            cancel_requested=self.cancel_requested,
            perceptual_metric=self.perceptual_metric_provider,
        )
        if lip_sync_run is not None:
            final_media = probe(
                Path(delivery_asset.file_path),
                cancel_requested=self.cancel_requested,
            )
            streams = final_media.get("streams", [])
            audio_stream_present = isinstance(streams, list) and any(
                isinstance(stream, dict) and stream.get("codec_type") == "audio"
                for stream in streams
            )
            report["checks"]["audio_stream_present"] = audio_stream_present
            report["checks"]["lip_sync_qa_passed"] = lip_sync_run.sync_qa_passed
            report["lip_sync"] = {
                "provider_id": lip_sync_run.provider_id,
                "actual_model": lip_sync_run.actual_model,
                "sync_confidence": lip_sync_run.sync_confidence,
                "av_offset_frames": lip_sync_run.av_offset_frames,
                "sync_qa_passed": lip_sync_run.sync_qa_passed,
                "audio_reference_asset_id": request.audio_reference_asset_id,
                "eligibility": request.lip_sync_settings.eligibility.value,
            }
            report["passed"] = all(report["checks"].values())
        actual_start = register_asset(
            self.db,
            project_id=project.id,
            shot_id=None,
            kind="actual_first_frame",
            path=evidence / "frame-0000.png",
            provider="ffmpeg",
            model="decoded-frame-v1",
            parents=[delivery_asset.id],
            generation_parameters={"frame_index": 0, "request_digest": request.digest()},
        )
        actual_last = register_asset(
            self.db,
            project_id=project.id,
            shot_id=None,
            kind="actual_last_frame",
            path=evidence / f"frame-{request.expected_delivery_frames - 1:04d}.png",
            provider="ffmpeg",
            model="decoded-frame-v1",
            parents=[delivery_asset.id],
            generation_parameters={
                "frame_index": request.expected_delivery_frames - 1,
                "request_digest": request.digest(),
            },
        )
        inspection_asset_ids: list[str] = [actual_start.id, actual_last.id]
        for index in (1, 60, 150, 300, 450, 598):
            frame_path = evidence / f"frame-{index:04d}.png"
            if not frame_path.is_file():
                continue
            inspection_asset_ids.append(
                register_asset(
                    self.db,
                    project_id=project.id,
                    shot_id=None,
                    kind="qa_inspection_frame",
                    path=frame_path,
                    provider="ffmpeg",
                    model="decoded-frame-v1",
                    parents=[delivery_asset.id],
                    generation_parameters={"frame_index": index},
                ).id
            )
        contact_sheet_asset = register_asset(
            self.db,
            project_id=project.id,
            shot_id=None,
            kind="qa_contact_sheet",
            path=evidence / "contact-sheet.png",
            provider="flipthis-qa",
            model="contact-sheet-v1",
            parents=inspection_asset_ids,
        )
        report["asset_lineage"] = {
            "native_video_asset_id": native_asset.id,
            "performance_conditioned_video_asset_id": (
                motion_source_asset.id if lip_sync_run is not None else None
            ),
            "delivery_video_asset_id": delivery_asset.id,
            "actual_first_frame_asset_id": actual_start.id,
            "actual_last_frame_asset_id": actual_last.id,
            "inspection_frame_asset_ids": inspection_asset_ids,
            "contact_sheet_asset_id": contact_sheet_asset.id,
        }
        report_path = write_qa_report(report, evidence / "qa-report.json")
        qa_asset = register_asset(
            self.db,
            project_id=project.id,
            shot_id=None,
            kind="video_qa_report",
            path=report_path,
            provider="flipthis-qa",
            model="delivery-contract-v1",
            parents=[delivery_asset.id, start_asset.id, end_asset.id, contact_sheet_asset.id],
            generation_parameters={"passed": report["passed"]},
        )
        native_facts = inspect_frame_timing(
            Path(native_asset.file_path),
            cancel_requested=self.cancel_requested,
        )
        gpu_snapshot = self.gpu_telemetry.snapshot() if self.gpu_telemetry is not None else None
        result = FirstLastFrameGenerationResult(
            request_digest=request.digest(),
            provider_job_id=provider_run.provider_job_id,
            provider_id=request.provider_id,
            actual_model=provider_run.actual_model,
            provider_api_version=provider_run.api_version,
            actual_settings={key: value for key, value in provider_run.actual_settings.items()},
            actual_duration_seconds=native_facts["duration_seconds"],
            actual_native_fps=native_facts["average_frame_rate"],
            actual_width=native_facts["width"],
            actual_height=native_facts["height"],
            native_video_asset_id=native_asset.id,
            delivery_video_asset_id=delivery_asset.id,
            output_checksum=checksum(Path(delivery_asset.file_path)),
            actual_first_frame_asset_id=actual_start.id,
            actual_last_frame_asset_id=actual_last.id,
            qa_report_asset_id=qa_asset.id,
            provenance={
                "generation_category": request.generation_category.value,
                "native_requested_fps": request.native_requested_fps,
                "actual_native_fps": native_facts["average_frame_rate"],
                "delivery_fps": request.delivery_fps,
                "delivery_frames": request.expected_delivery_frames,
                "interpolation_mode": request.interpolation_mode.value,
                "lip_sync_mode": request.lip_sync_mode.value,
                "lip_sync_provider_id": request.lip_sync_provider_id,
                "lip_sync_native_fps": (
                    motion_source_asset.frame_rate if lip_sync_run is not None else None
                ),
                "sync_confidence": (
                    lip_sync_run.sync_confidence if lip_sync_run is not None else None
                ),
                "av_offset_frames": (
                    lip_sync_run.av_offset_frames if lip_sync_run is not None else None
                ),
                "lip_sync_source_asset_id": (
                    lip_source_asset.id if lip_source_asset is not None else None
                ),
                "lip_sync_interpolated_asset_id": (
                    lip_source_interpolated_asset.id
                    if lip_source_interpolated_asset is not None
                    else None
                ),
            },
            timing=ProviderTiming(
                submitted_at=provider_run.submitted_at,
                completed_at=provider_run.completed_at,
                provider_seconds=provider_run.provider_seconds,
                download_seconds=provider_run.download_seconds,
            ),
            resource_usage={
                "pipeline_wall_seconds": max(
                    0.0,
                    (utc_now() - provider_run.submitted_at).total_seconds(),
                ),
                "gpu_measurements_available": gpu_snapshot is not None and gpu_snapshot.available,
                "gpu_telemetry": (
                    gpu_snapshot.model_dump(mode="json")
                    if gpu_snapshot is not None
                    else {
                        "available": False,
                        "error_code": "cpu_or_telemetry_not_configured",
                    }
                ),
            },
            warnings=provider_run.warnings
            + (lip_sync_run.warnings if lip_sync_run is not None else ()),
            continuity_qa={
                "passed": report["passed"],
                "checks": report["checks"],
                "boundaries": report["boundaries"],
            },
        )
        clip.actual_start_frame_asset_id = actual_start.id
        clip.actual_last_frame_asset_id = actual_last.id
        clip.qa_report_asset_id = qa_asset.id
        clip.result_snapshot = result.model_dump(mode="json")
        clip.provider_warnings = list(
            provider_run.warnings + (lip_sync_run.warnings if lip_sync_run is not None else ())
        )
        clip.state = (
            ChainClipState.AWAITING_REVIEW.value
            if report["passed"]
            else ChainClipState.DEGRADED.value
        )
        self.db.commit()
        if self.progress:
            self.progress(1.0, "awaiting_review" if report["passed"] else "degraded")
        return delivery_asset

    def _existing_asset(
        self,
        project: Project,
        asset_id: str | None,
        mime_type: str,
    ) -> Asset | None:
        if asset_id is None:
            return None
        asset, _ = validated_asset_input(
            self.db,
            project,
            asset_id,
            allowed_mime_types=frozenset({mime_type}),
        )
        return asset

    def _snapshot_asset(
        self,
        project: Project,
        clip: VideoChainClip,
        key: str,
        mime_type: str,
    ) -> Asset | None:
        asset_id = clip.result_snapshot.get(key)
        return self._existing_asset(
            project,
            asset_id if isinstance(asset_id, str) else None,
            mime_type,
        )

    def _interpolator(
        self,
        request: FirstLastFrameGenerationRequest,
    ) -> InterpolationRunner:
        if request.interpolation_mode is not InterpolationMode.RIFE:
            raise RuntimeError("This stage requires the production RIFE interpolator")
        if self.interpolation_provider is not None:
            return self.interpolation_provider
        if not request.interpolation_provider_id:
            raise RuntimeError("RIFE request has no configured interpolation provider")
        return configured_interpolation_provider(
            get_settings().provider_config,
            request.interpolation_provider_id,
            cancel_requested=self.cancel_requested,
        )

    @staticmethod
    def _provider_run_from_resume(
        clip: VideoChainClip,
        request: FirstLastFrameGenerationRequest,
    ) -> ProviderRunOutput:
        stored = clip.result_snapshot.get("provider_run")
        if not isinstance(stored, dict):
            raise RuntimeError("Resumable native output has no captured provider result")
        run_output = ProviderRunOutput.model_validate(stored)
        if run_output.provider_job_id != clip.provider_job_id:
            raise RuntimeError("Resumable provider result does not match clip lineage")
        if request.provider_model != run_output.actual_model:
            raise RuntimeError("Resumable provider result model does not match request")
        return run_output

    def _check_cancelled(self) -> None:
        if self.cancel_requested and self.cancel_requested():
            from flipthis_video_maker.pipeline.mock_pipeline import PipelineCancelled

            raise PipelineCancelled("Video chain generation cancelled")

    def _set_telemetry_stage(self, stage: str) -> None:
        if self.gpu_telemetry is not None:
            self.gpu_telemetry.set_stage(stage)


__all__ = ["FirstLastFrameRunner", "InterpolationRunner", "VideoChainPipeline"]
