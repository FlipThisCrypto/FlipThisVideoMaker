from collections import defaultdict

from sqlalchemy.orm import Session

from flipthis_video_maker.domain.enums import ProjectStatus, ShotStatus
from flipthis_video_maker.domain.models import Character, Project, Scene, Shot
from flipthis_video_maker.providers.base.models import StoryPlan


def apply_story_plan(
    db: Session, project: Project, plan: StoryPlan, *, auto_approve: bool = False
) -> list[Shot]:
    if project.scenes:
        raise ValueError("Project already has scenes; explicit replacement is required")
    if not project.characters:
        project.characters = [
            Character(
                name=str(item.get("name", "Character")),
                description=str(item.get("description", "")),
                consent_provenance={"kind": "fictional", "source": "deterministic_planner"},
            )
            for item in plan.characters
        ]

    by_scene: dict[int, list[Shot]] = defaultdict(list)
    for item in plan.shots:
        status = ShotStatus.APPROVED if auto_approve else ShotStatus.AWAITING_APPROVAL
        by_scene[item.scene_number].append(
            Shot(
                sequence_number=item.sequence_number,
                shot_type=item.shot_type,
                duration=item.duration,
                prompt=item.video_generation_prompt,
                negative_prompt=item.negative_video_prompt,
                dialogue=item.dialogue,
                narration=item.narration,
                speaker=item.speaker,
                camera={
                    "framing": item.camera_framing,
                    "movement": item.camera_movement,
                },
                character_positions=item.character_positions,
                character_actions=item.character_actions,
                transition_type=item.transition_intent,
                overlap_frame_count=12
                if item.transition_intent == "crossfade"
                else 6
                if item.transition_intent == "shared_frame"
                else 0,
                seed=100 + item.sequence_number,
                status=status.value,
                approval_state="approved" if auto_approve else "pending",
            )
        )

    scenes: list[Scene] = []
    for scene_number, shots in sorted(by_scene.items()):
        location = (
            plan.locations[min(scene_number - 1, len(plan.locations) - 1)]
            if plan.locations
            else "unspecified location"
        )
        scene = Scene(
            number=scene_number,
            title=f"Scene {scene_number}",
            location=location,
            time_of_day="unspecified",
            lighting="cinematic practical light",
            characters=[character.name for character in project.characters],
            props=plan.props,
            environment=location,
            continuity_state={"rules": plan.continuity_rules},
            shots=shots,
        )
        scenes.append(scene)
    project.scenes = scenes
    project.status = ProjectStatus.ACTIVE.value
    db.flush()
    ordered_shots = [shot for scene in scenes for shot in scene.shots]
    if len(ordered_shots) >= 3:
        ordered_shots[2].continuity_source_shot_id = ordered_shots[1].id
    db.commit()
    return ordered_shots
