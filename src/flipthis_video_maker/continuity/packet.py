from pathlib import Path
from typing import Any

import yaml

from flipthis_video_maker.domain.models import Project, Scene, Shot


def build_packet(project: Project, scene: Scene, shot: Shot) -> dict[str, Any]:
    return {
        "version": 1,
        "project_id": project.id,
        "scene_id": scene.id,
        "shot_id": shot.id,
        "characters": [
            {
                "id": name,
                "screen_position": shot.character_positions.get(name),
                "action": shot.character_actions.get(name),
            }
            for name in scene.characters
        ],
        "environment": {
            "location": scene.location,
            "time_of_day": scene.time_of_day,
            "lighting": scene.lighting,
            "description": scene.environment,
        },
        "camera": shot.camera,
        "continuity": {
            "source_shot_id": shot.continuity_source_shot_id,
            "source_asset_id": shot.continuity_source_frame_id,
            "planned_end_asset_id": shot.planned_end_frame_id,
            "target_asset_id": shot.continuity_target_frame_id,
            "overlap_frames": shot.overlap_frame_count,
            "transition": shot.transition_type,
        },
        "audio": {"speaker": shot.speaker},
        "generation": {
            "provider": shot.provider,
            "model": shot.model,
            "candidate_count": shot.generation_settings.get("candidate_count", 1),
            "seed": shot.seed,
        },
    }


def write_packet(path: Path, packet: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")
    temporary.replace(path)
