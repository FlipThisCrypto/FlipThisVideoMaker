from flipthis_video_maker.providers.base.models import StoryPlan

SYSTEM_PROMPT = """You are a film storyboard planner. Convert the supplied story into a practical,
editable multi-shot production plan. Prefer simple film grammar, one visible speaker per dialogue
shot, and explicit continuity. Return only data matching the supplied JSON schema."""


def story_plan_schema() -> dict[str, object]:
    return StoryPlan.model_json_schema()


def story_prompt(story: str) -> str:
    normalized = story.strip()
    if not normalized:
        raise ValueError("Story text is required")
    return f"Plan this story for local video production:\n\n{normalized}"
