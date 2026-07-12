import time
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from flipthis_video_maker.domain.models import Asset, Candidate, Project, Scene, Shot
from flipthis_video_maker.media.ffmpeg import extract_frame
from flipthis_video_maker.pipeline.mock_pipeline import PipelineCancelled
from flipthis_video_maker.providers.base.models import ImageRequest, TTSRequest, VideoRequest
from flipthis_video_maker.providers.mock.providers import (
    MockImageProvider,
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
    ) -> None:
        self.db = db
        self.cancel_requested = cancel_requested
        self.progress = progress
        self.images = MockImageProvider()
        self.tts = MockTTSProvider()
        self.video = MockVideoProvider(cancel_requested)

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
        candidate_settings = (
            generation_settings if generation_settings is not None else shot.generation_settings
        )

        start_asset = self._conditioning_start(shot)
        if start_asset is None:
            start_path = root / "frames" / f"planned-start-{candidate_seed}.png"
            await self.images.generate(
                ImageRequest(
                    prompt=f"{candidate_prompt} opening",
                    output_path=start_path,
                    seed=candidate_seed,
                    label=f"Shot {shot.sequence_number} START",
                    characters=scene.characters,
                )
            )
            start_asset = register_asset(
                self.db,
                project_id=project.id,
                shot_id=shot.id,
                kind="planned_start_frame",
                path=start_path,
                prompt=candidate_prompt,
                seed=candidate_seed,
            )
        else:
            start_path = Path(start_asset.file_path)

        end_path = root / "frames" / f"planned-end-{candidate_seed}.png"
        await self.images.generate(
            ImageRequest(
                prompt=f"{candidate_prompt} ending",
                output_path=end_path,
                seed=candidate_seed + 1,
                label=f"Shot {shot.sequence_number} END",
                characters=scene.characters,
            )
        )
        end_asset = register_asset(
            self.db,
            project_id=project.id,
            shot_id=shot.id,
            kind="planned_end_frame",
            path=end_path,
            prompt=candidate_prompt,
            seed=candidate_seed + 1,
        )

        audio_path: Path | None = None
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
                cancel_requested=self.cancel_requested,
            )
            input_assets.append(audio_asset.id)

        self._check_cancelled()
        self._report_progress(0.45, "generating shot candidate")
        # Persist prepared inputs and release SQLite's writer lock before provider execution.
        self.db.commit()
        output = root / "candidates" / f"candidate-{candidate_seed}.mp4"
        started = time.monotonic()
        await self.video.generate(
            VideoRequest(
                prompt=candidate_prompt,
                output_path=output,
                start_frame=start_path,
                end_frame=end_path,
                duration=shot.duration,
                fps=24,
                audio_path=audio_path,
                seed=candidate_seed,
                settings=dict(candidate_settings),
            )
        )
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
        qa = analyze_video(
            output,
            shot.duration,
            854,
            480,
            audio_expected=True,
            cancel_requested=self.cancel_requested,
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
            cancel_requested=self.cancel_requested,
        )
        actual_start = register_asset(
            self.db,
            project_id=project.id,
            shot_id=shot.id,
            kind="actual_start_frame",
            path=actual_start_path,
            parents=[video_asset.id],
        )
        actual_end = register_asset(
            self.db,
            project_id=project.id,
            shot_id=shot.id,
            kind="actual_end_frame",
            path=actual_end_path,
            parents=[video_asset.id],
        )
        candidate = Candidate(
            shot_id=shot.id,
            provider="mock-video",
            model="ffmpeg-xfade-v1",
            prompt=candidate_prompt,
            negative_prompt=candidate_negative,
            seed=candidate_seed,
            settings=dict(candidate_settings),
            generation_seconds=time.monotonic() - started,
            gpu="cpu",
            input_asset_ids=input_assets,
            output_asset_id=video_asset.id,
            first_frame_asset_id=actual_start.id,
            last_frame_asset_id=actual_end.id,
            qa_results=qa,
            disposition="pending",
        )
        shot.retry_count += 1
        self.db.add(candidate)
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

    def _check_cancelled(self) -> None:
        if self.cancel_requested and self.cancel_requested():
            raise PipelineCancelled("Shot regeneration cancellation requested")

    def _report_progress(self, value: float, stage: str) -> None:
        if self.progress:
            self.progress(value, stage)
