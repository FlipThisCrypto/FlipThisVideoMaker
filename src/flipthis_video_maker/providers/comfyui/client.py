from typing import Any

import httpx
from pydantic import BaseModel


class PromptSubmission(BaseModel):
    prompt_id: str
    number: int | None = None


class ComfyUIProvider:
    def __init__(self, endpoint: str, timeout: float = 30) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    async def health(self) -> dict[str, object]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.endpoint}/system_stats")
            response.raise_for_status()
            return {"ok": True, "system_stats": response.json()}

    async def submit_workflow(self, workflow: dict[str, Any], client_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.endpoint}/prompt", json={"prompt": workflow, "client_id": client_id}
            )
            response.raise_for_status()
            return PromptSubmission.model_validate(response.json()).model_dump(exclude_none=True)

    async def history(self, prompt_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.endpoint}/history/{prompt_id}")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("ComfyUI history response must be a JSON object")
            return data

    async def interrupt(self) -> None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.endpoint}/interrupt")
            response.raise_for_status()
