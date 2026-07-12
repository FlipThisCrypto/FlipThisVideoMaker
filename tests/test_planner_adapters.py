import json

import httpx
import pytest

from flipthis_video_maker.providers.planning.deterministic import DeterministicStoryPlanner
from flipthis_video_maker.providers.planning.ollama import OllamaStoryPlanner
from flipthis_video_maker.providers.planning.openai_compatible import (
    OpenAICompatibleStoryPlanner,
)


async def _plan_json() -> str:
    plan = await DeterministicStoryPlanner().plan("Alex and Riley find the signal.")
    return plan.model_dump_json()


@pytest.mark.asyncio
async def test_ollama_planner_sends_schema_and_validates_response() -> None:
    expected = await _plan_json()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/api/chat"
        assert payload["stream"] is False
        assert payload["format"]["title"] == "StoryPlan"
        return httpx.Response(200, json={"message": {"content": expected}})

    planner = OllamaStoryPlanner(
        "http://ollama", "test-model", transport=httpx.MockTransport(handler)
    )
    plan = await planner.plan("A test story")
    assert len(plan.shots) == 4


@pytest.mark.asyncio
async def test_openai_compatible_planner_uses_strict_schema_and_auth() -> None:
    expected = await _plan_json()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        assert payload["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(200, json={"choices": [{"message": {"content": expected}}]})

    planner = OpenAICompatibleStoryPlanner(
        "http://local", "test-model", api_key="secret", transport=httpx.MockTransport(handler)
    )
    plan = await planner.plan("A test story")
    assert plan.project_title


@pytest.mark.asyncio
async def test_planner_rejects_invalid_structured_output() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"message": {"content": "{}"}})
    )
    planner = OllamaStoryPlanner("http://ollama", "test", transport=transport)
    with pytest.raises(ValueError):
        await planner.plan("A story")
