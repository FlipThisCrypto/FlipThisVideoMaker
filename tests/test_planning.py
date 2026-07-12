from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from flipthis_video_maker.domain.enums import ShotStatus
from flipthis_video_maker.domain.models import Project
from flipthis_video_maker.providers.planning.deterministic import DeterministicStoryPlanner
from flipthis_video_maker.services.planning import apply_story_plan


@pytest.mark.asyncio
async def test_deterministic_plan_persists_renderable_storyboard(
    db: Session, tmp_path: Path
) -> None:
    project = Project(
        name="Planned project",
        root_asset_directory=str(tmp_path / "project"),
        original_story="Alex and Riley restore a forgotten signal beacon.",
    )
    db.add(project)
    db.commit()

    plan = await DeterministicStoryPlanner().plan(project.original_story)
    shots = apply_story_plan(db, project, plan, auto_approve=True)

    assert len(project.scenes) == 2
    assert len(shots) == 4
    assert sum(shot.duration for shot in shots) == 32
    assert all(shot.status == ShotStatus.APPROVED.value for shot in shots)
    assert shots[2].continuity_source_shot_id == shots[1].id
