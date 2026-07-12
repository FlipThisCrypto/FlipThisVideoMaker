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


class _Choice(BaseModel):
    message: _Message


class _CompletionResponse(BaseModel):
    choices: list[_Choice]


class OpenAICompatibleStoryPlanner:
    """Local Chat Completions adapter using strict JSON-schema response formatting."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = 300,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="openai-compatible-planner",
            name="OpenAI-compatible local story planner",
            model_identity=self.model,
            capabilities={Capability.STORY_PLANNING},
            available=True,
            supported_inputs={"text"},
            notes="Validated strict JSON-schema Chat Completions response",
        )

    async def health(self) -> dict[str, object]:
        async with self._client() as client:
            response = await client.get(f"{self.endpoint}/v1/models")
            response.raise_for_status()
            return {"ok": isinstance(response.json(), dict), "model": self.model}

    async def plan(self, story: str) -> StoryPlan:
        schema = story_plan_schema()
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": story_prompt(story)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "story_plan", "strict": True, "schema": schema},
            },
        }
        async with self._client() as client:
            response = await client.post(f"{self.endpoint}/v1/chat/completions", json=payload)
            response.raise_for_status()
        result = _CompletionResponse.model_validate(response.json())
        if not result.choices:
            raise ValueError("Planner response contained no choices")
        return StoryPlan.model_validate(json.loads(result.choices[0].message.content))

    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        return httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            headers=headers,
        )
