import json
from typing import Any

import httpx
from pydantic import BaseModel

from flipthis_video_maker.providers.base.models import Capability, ProviderInfo, StoryPlan
from flipthis_video_maker.providers.planning.prompt import (
    SYSTEM_PROMPT,
    story_plan_schema,
    story_prompt,
)


class _Message(BaseModel):
    content: str


class _ChatResponse(BaseModel):
    message: _Message


class OllamaStoryPlanner:
    """Ollama `/api/chat` adapter using its documented structured-output format."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        *,
        timeout: float = 300,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.transport = transport

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="ollama-planner",
            name="Ollama structured story planner",
            model_identity=self.model,
            capabilities={Capability.STORY_PLANNING},
            available=True,
            supported_inputs={"text"},
            notes="Validated structured output through Ollama /api/chat",
        )

    async def health(self) -> dict[str, object]:
        async with self._client() as client:
            response = await client.get(f"{self.endpoint}/api/tags")
            response.raise_for_status()
            data = response.json()
            return {"ok": isinstance(data, dict), "model": self.model}

    async def plan(self, story: str) -> StoryPlan:
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "format": story_plan_schema(),
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": story_prompt(story)},
            ],
        }
        async with self._client() as client:
            response = await client.post(f"{self.endpoint}/api/chat", json=payload)
            response.raise_for_status()
        result = _ChatResponse.model_validate(response.json())
        return StoryPlan.model_validate(json.loads(result.message.content))

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self.transport)
