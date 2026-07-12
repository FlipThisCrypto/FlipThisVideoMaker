import os
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, Field, model_validator

from flipthis_video_maker.providers.base.models import ProviderInfo
from flipthis_video_maker.providers.base.protocols import Provider, StoryPlanner
from flipthis_video_maker.providers.comfyui.client import ComfyUIProvider
from flipthis_video_maker.providers.mock.providers import (
    MockImageProvider,
    MockInterpolationProvider,
    MockLipSyncProvider,
    MockTTSProvider,
    MockVideoProvider,
)
from flipthis_video_maker.providers.planning.deterministic import DeterministicStoryPlanner
from flipthis_video_maker.providers.planning.ollama import OllamaStoryPlanner
from flipthis_video_maker.providers.planning.openai_compatible import (
    OpenAICompatibleStoryPlanner,
)
from flipthis_video_maker.providers.wangp.client import WanGPHeadlessProvider


class ProviderConfiguration(BaseModel):
    id: str
    kind: Literal["mock", "comfyui", "wangp", "cli", "ollama", "openai_compatible"]
    enabled: bool = False
    endpoint: str | None = None
    health_path: str = "/"
    submit_path: str | None = None
    workflow_template_directory: Path | None = None
    command: list[str] = Field(default_factory=list)
    oom_exit_codes: set[int] = Field(default_factory=set)
    python: Path | None = None
    script: Path | None = None
    model: str | None = None
    api_key_env: str | None = None

    @model_validator(mode="after")
    def validate_oom_exit_codes(self) -> Self:
        if 0 in self.oom_exit_codes:
            raise ValueError("OOM exit codes cannot include successful exit code 0")
        if self.oom_exit_codes and self.kind != "cli":
            raise ValueError("OOM exit codes are supported only by CLI providers")
        return self


class ProviderConfigurationFile(BaseModel):
    version: int = 1
    providers: list[ProviderConfiguration]


def load_provider_configuration(path: Path) -> ProviderConfigurationFile:
    if not path.is_file():
        raise FileNotFoundError(f"Provider configuration not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ProviderConfigurationFile.model_validate(data)


def configured_story_planner(path: Path, provider_id: str) -> StoryPlanner:
    configured = load_provider_configuration(path)
    item = next((provider for provider in configured.providers if provider.id == provider_id), None)
    if item is None:
        raise KeyError(f"Unknown provider: {provider_id}")
    if not item.enabled:
        raise ValueError(f"Provider is disabled: {provider_id}")
    if not item.endpoint or not item.model:
        raise ValueError(f"Planner endpoint and model are required: {provider_id}")
    if item.kind == "ollama":
        return OllamaStoryPlanner(item.endpoint, item.model)
    if item.kind == "openai_compatible":
        key = os.environ.get(item.api_key_env) if item.api_key_env else None
        return OpenAICompatibleStoryPlanner(item.endpoint, item.model, api_key=key)
    raise ValueError(f"Provider is not a story planner: {provider_id}")


def provider_records(path: Path) -> list[ProviderInfo]:
    mocks: list[Provider] = [
        DeterministicStoryPlanner(),
        MockImageProvider(),
        MockTTSProvider(),
        MockVideoProvider(),
        MockLipSyncProvider(),
        MockInterpolationProvider(),
    ]
    records = [provider.info() for provider in mocks]
    configured = load_provider_configuration(path)
    mock_ids = {record.id for record in records}
    for item in configured.providers:
        if item.id in mock_ids:
            continue
        records.append(
            ProviderInfo(
                id=item.id,
                name=f"Configured {item.kind}: {item.id}",
                model_identity="administrator-configured",
                capabilities=set(),
                available=item.enabled,
                notes=(
                    "Configured adapter; protocol has not been exercised"
                    if item.enabled
                    else "Disabled in provider configuration"
                ),
            )
        )
    return records


async def provider_health_records(path: Path) -> list[dict[str, Any]]:
    mock_providers: list[Provider] = [
        DeterministicStoryPlanner(),
        MockImageProvider(),
        MockTTSProvider(),
        MockVideoProvider(),
        MockLipSyncProvider(),
        MockInterpolationProvider(),
    ]
    results = [
        {"provider": provider.info().id, **await provider.health()} for provider in mock_providers
    ]
    configured = load_provider_configuration(path)
    mock_ids = {provider.info().id for provider in mock_providers}
    for item in configured.providers:
        if item.id in mock_ids:
            continue
        if not item.enabled:
            results.append({"provider": item.id, "ok": False, "status": "disabled"})
            continue
        if item.kind == "wangp":
            if item.python is None or item.script is None:
                results.append({"provider": item.id, "ok": False, "status": "missing_paths"})
                continue
            health = await WanGPHeadlessProvider(item.python, item.script).health()
            results.append({"provider": item.id, **health})
            continue
        if item.endpoint is None:
            results.append({"provider": item.id, "ok": False, "status": "missing_endpoint"})
            continue
        if item.kind in {"ollama", "openai_compatible"} and not item.model:
            results.append({"provider": item.id, "ok": False, "status": "missing_model"})
            continue
        try:
            if item.kind == "comfyui":
                health = await ComfyUIProvider(item.endpoint).health()
            elif item.kind == "ollama" and item.model:
                health = await OllamaStoryPlanner(item.endpoint, item.model).health()
            elif item.kind == "openai_compatible" and item.model:
                key = os.environ.get(item.api_key_env) if item.api_key_env else None
                health = await OpenAICompatibleStoryPlanner(
                    item.endpoint, item.model, api_key=key
                ).health()
            else:
                health = {"ok": False, "status": "health_not_implemented"}
        except Exception as error:
            health = {"ok": False, "status": "unreachable", "error_type": type(error).__name__}
        results.append({"provider": item.id, **health})
    return results
