from flipthis_video_maker.providers.base.models import (
    Capability,
    PlannerShot,
    ProviderInfo,
    StoryPlan,
)


class DeterministicStoryPlanner:
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="deterministic-planner",
            name="Deterministic Story Planner",
            model_identity="template-planner-v1",
            capabilities={Capability.STORY_PLANNING},
            available=True,
            supported_inputs={"text"},
            notes="Predictable local planner for tests and manual project bootstrapping",
        )

    async def health(self) -> dict[str, object]:
        return {"ok": True}

    async def plan(self, story: str) -> StoryPlan:
        normalized = " ".join(story.split())
        if not normalized:
            raise ValueError("Story text is required")
        summary = normalized[:500]
        return StoryPlan(
            project_title=normalized.split(".", 1)[0][:120] or "Untitled story",
            story_summary=summary,
            characters=[
                {"name": "Alex", "description": "The determined protagonist"},
                {"name": "Riley", "description": "A perceptive companion"},
            ],
            locations=["story entrance", "story destination"],
            props=["continuity token"],
            continuity_rules=["Keep wardrobe and screen direction stable across connected shots"],
            shots=[
                self._shot(1, 1, "establishing", summary, "hard_cut"),
                self._shot(1, 2, "medium", summary, "shared_frame", dialogue="We made it."),
                self._shot(2, 3, "close_up", summary, "crossfade", dialogue="Look at this."),
                self._shot(2, 4, "reaction", summary, "hard_cut"),
            ],
        )

    @staticmethod
    def _shot(
        scene_number: int,
        sequence_number: int,
        shot_type: str,
        summary: str,
        transition: str,
        *,
        dialogue: str = "",
    ) -> PlannerShot:
        prompt = f"Shot {sequence_number}: {summary}"
        return PlannerShot(
            scene_number=scene_number,
            sequence_number=sequence_number,
            shot_type=shot_type,
            duration=8,
            prompt=prompt,
            dialogue=dialogue,
            speaker="Alex" if dialogue else None,
            camera_framing=shot_type,
            camera_movement="slow_dolly_in" if sequence_number == 1 else "static",
            character_positions={"Alex": "left", "Riley": "right"},
            character_actions={"Alex": "advances the story", "Riley": "observes"},
            lighting="cinematic practical light",
            mood="hopeful tension",
            start_frame_description=f"Opening composition for {prompt}",
            end_frame_description=f"Ending composition for {prompt}",
            video_generation_prompt=prompt,
            transition_intent=transition,
        )
