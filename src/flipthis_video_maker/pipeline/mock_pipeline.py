import time
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

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
from flipthis_video_maker.media.ffmpeg import extract_frame
from flipthis_video_maker.media.render import (
    assemble_with_transitions,
    contact_sheet,
    thumbnail,
    write_manifest,
    write_subtitles,
)
from flipthis_video_maker.providers.base.models import ImageRequest, TTSRequest, VideoRequest
from flipthis_video_maker.providers.mock.providers import (
    MockImageProvider,
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
    ) -> None:
        self.db = db
        self.cancel_requested = cancel_requested
        self.progress = progress
        self.images = MockImageProvider()
        self.tts = MockTTSProvider()
        self.video = MockVideoProvider()

    async def run(self, project_id: str) -> Render:
        project = self.db.get(Project, project_id)
        if project is None:
            raise ValueError(f"Unknown project {project_id}")

        root = Path(project.root_asset_directory)
        run_id = uuid.uuid4().hex
        clips: list[Path] = []
        transitions: list[tuple[str, int]] = []
        actual_ends: list[Path] = []
        subtitles: list[tuple[float, float, str]] = []
        timeline = 0.0
        previous_shot: Shot | None = None
        shot_index = 0
        total_shots = sum(len(scene.shots) for scene in project.scenes)
        self._report_progress(0.02, "starting render")

        for scene in project.scenes:
            for shot in scene.shots:
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
                    await self.images.generate(
                        ImageRequest(
                            prompt=f"{shot.prompt} opening",
                            output_path=planned_start_path,
                            seed=shot.seed,
                            label=f"Shot {shot.sequence_number} START",
                            characters=scene.characters,
                        )
                    )
                    start_asset = register_asset(
                        self.db,
                        project_id=project.id,
                        shot_id=shot.id,
                        kind="planned_start_frame",
                        path=planned_start_path,
                        prompt=shot.prompt,
                        seed=shot.seed,
                    )
                    shot.planned_start_frame_id = start_asset.id

                await self.images.generate(
                    ImageRequest(
                        prompt=f"{shot.prompt} ending",
                        output_path=planned_end_path,
                        seed=shot.seed + 1,
                        label=f"Shot {shot.sequence_number} END",
                        characters=scene.characters,
                    )
                )
                end_asset = register_asset(
                    self.db,
                    project_id=project.id,
                    shot_id=shot.id,
                    kind="planned_end_frame",
                    path=planned_end_path,
                    prompt=shot.prompt,
                    seed=shot.seed + 1,
                )
                shot.planned_end_frame_id = end_asset.id
                shot.continuity_target_frame_id = end_asset.id

                audio_path: Path | None = None
                if shot.dialogue:
                    audio_path = shot_root / "audio" / f"dialogue-{shot.seed}.wav"
                    await self.tts.synthesize(
                        TTSRequest(
                            text=shot.dialogue,
                            output_path=audio_path,
                            voice=shot.speaker or "narrator",
                        )
                    )
                    register_asset(
                        self.db,
                        project_id=project.id,
                        shot_id=shot.id,
                        kind="dialogue_audio",
                        path=audio_path,
                        provider="mock-tts",
                        model="mock-tone-v1",
                    )
                    if not rerun:
                        self._transition(shot, ShotStatus.AUDIO_READY)
                        self._transition(shot, ShotStatus.KEYFRAMES_PENDING)

                if not rerun:
                    self._transition(shot, ShotStatus.KEYFRAMES_READY)
                    self._transition(shot, ShotStatus.VIDEO_PENDING)
                self._transition(shot, ShotStatus.VIDEO_GENERATING)

                output = shot_root / "candidates" / f"candidate-{shot.seed}.mp4"
                started = time.monotonic()
                await self.video.generate(
                    VideoRequest(
                        prompt=shot.prompt,
                        output_path=output,
                        start_frame=planned_start_path,
                        end_frame=planned_end_path,
                        duration=shot.duration,
                        fps=24,
                        audio_path=audio_path,
                        seed=shot.seed,
                    )
                )
                self._check_cancelled()
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
                )
                actual_start_path = shot_root / "frames" / f"actual-start-{shot.seed}.png"
                actual_end_path = shot_root / "frames" / f"actual-end-{shot.seed}.png"
                extract_frame(output, actual_start_path)
                extract_frame(output, actual_end_path, last=True)
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
                qa = analyze_video(output, shot.duration, 854, 480, audio_expected=True)
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
                    settings=shot.generation_settings,
                    generation_seconds=time.monotonic() - started,
                    gpu="cpu",
                    input_asset_ids=[
                        item
                        for item in (
                            shot.planned_start_frame_id,
                            shot.continuity_source_frame_id,
                            end_asset.id,
                        )
                        if item
                    ],
                    output_asset_id=video_asset.id,
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
                self._transition(shot, ShotStatus.VIDEO_READY)
                self._transition(shot, ShotStatus.CONTINUITY_PENDING)
                packet = build_packet(project, scene, shot)
                shot.continuity_packet = packet
                write_packet(shot_root / "continuity.yaml", packet)
                self._transition(shot, ShotStatus.CONTINUITY_READY)
                self._transition(shot, ShotStatus.QA_PENDING)
                self._transition(
                    shot, ShotStatus.COMPLETE if qa["passed"] else ShotStatus.QA_FAILED
                )

                clips.append(output)
                transitions.append((shot.transition_type, shot.overlap_frame_count))
                actual_ends.append(actual_end_path)
                overlap = self._timeline_overlap(shot, shot_index, fps=24)
                shot_start = timeline - overlap if shot.transition_type == "crossfade" else timeline
                if shot.dialogue:
                    subtitles.append(
                        (
                            shot_start + 0.2,
                            shot_start + shot.duration - overlap - 0.2,
                            f"{shot.speaker}: {shot.dialogue}",
                        )
                    )
                timeline += shot.duration - overlap
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
        final_path, applied_transitions = assemble_with_transitions(
            clips, transitions, render_dir / "final.mp4", fps=24
        )
        subtitle_path = write_subtitles(subtitles, render_dir / "final.srt")
        thumb_path = thumbnail(final_path, render_dir / "thumbnail.jpg")
        contact_path = contact_sheet(actual_ends, render_dir / "contact-sheet.jpg")
        manifest_path = root / "manifests" / f"{run_id}.json"
        manifest = {
            "version": 1,
            "run_id": run_id,
            "project_id": project.id,
            "render": str(final_path.relative_to(root)),
            "subtitles": str(subtitle_path.relative_to(root)),
            "thumbnail": str(thumb_path.relative_to(root)),
            "contact_sheet": str(contact_path.relative_to(root)),
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
        final_asset = register_asset(
            self.db,
            project_id=project.id,
            shot_id=None,
            kind="final_render",
            path=final_path,
            provider="ffmpeg",
            model="transition-assembler-v1",
            parents=[
                candidate.output_asset_id
                for scene in project.scenes
                for shot in scene.shots
                for candidate in shot.candidates
                if candidate.id == shot.selected_candidate_id and candidate.output_asset_id
            ],
        )
        render = Render(
            project_id=project.id,
            render_profile="draft",
            included_scene_ids=[scene.id for scene in project.scenes],
            included_shot_ids=[shot.id for scene in project.scenes for shot in scene.shots],
            output_path=str(final_path),
            codec="libx264",
            resolution="854x480",
            frame_rate=24,
            audio_configuration={"codec": "aac", "sample_rate": 48000, "channels": 2},
            subtitle_configuration={"path": str(subtitle_path)},
            creation_metadata={
                "run_id": run_id,
                "manifest": str(manifest_path),
                "thumbnail": str(thumb_path),
                "contact_sheet": str(contact_path),
                "transitions": applied_transitions,
                "output_asset_id": final_asset.id,
            },
        )
        project.status = ProjectStatus.COMPLETE.value
        self.db.add(render)
        self.db.commit()
        self._report_progress(0.98, "validated final media")
        return render

    def _check_cancelled(self) -> None:
        if self.cancel_requested and self.cancel_requested():
            raise PipelineCancelled("Render cancellation requested")

    def _report_progress(self, value: float, stage: str) -> None:
        if self.progress:
            self.progress(value, stage)

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
