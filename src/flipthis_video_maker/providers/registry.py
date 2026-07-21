import os
from collections.abc import Callable
from pathlib import Path
from string import Formatter
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from flipthis_video_maker.providers.base.models import Capability, ProviderInfo
from flipthis_video_maker.providers.base.protocols import Provider, StoryPlanner
from flipthis_video_maker.providers.cli.providers import GenericCLIImageProvider
from flipthis_video_maker.providers.comfyui.client import ComfyUIProvider
from flipthis_video_maker.providers.comfyui.wan_flf import (
    MODEL_IDENTITY as WAN_FLF_MODEL_IDENTITY,
)
from flipthis_video_maker.providers.comfyui.wan_flf import (
    ComfyUIWanFirstLastFrameProvider,
)
from flipthis_video_maker.providers.latentsync.cli import LatentSyncCliProvider
from flipthis_video_maker.providers.ltx.client import LtxVideoProvider
from flipthis_video_maker.providers.luma.client import LumaRayVideoProvider
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
from flipthis_video_maker.providers.rife.cli import RifeCliInterpolationProvider
from flipthis_video_maker.providers.wangp.client import WanGPHeadlessProvider


class ProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str
    kind: Literal[
        "mock",
        "comfyui",
        "comfyui_wan_flf",
        "wangp",
        "cli",
        "ollama",
        "openai_compatible",
        "luma",
        "rife",
        "latentsync",
        "ltx",
    ]
    enabled: bool = False
    endpoint: str | None = None
    health_path: str = "/"
    health_command: list[str] = Field(default_factory=list)
    submit_path: str | None = None
    workflow_template_directory: Path | None = None
    workflow_template: Path | None = None
    gpu_assignment: Literal["gpu0", "gpu1"] | None = None
    command: list[str] = Field(default_factory=list)
    oom_exit_codes: set[int] = Field(default_factory=set)
    python: Path | None = None
    script: Path | None = None
    model_directory: Path | None = None
    repository_directory: Path | None = None
    unet_config_path: Path | None = None
    checkpoint_path: Path | None = None
    syncnet_checkpoint_path: Path | None = None
    model: str | None = None
    api_key_env: str | None = None
    timeout_seconds: float = Field(default=900, gt=0, le=7200)
    poll_interval_seconds: float = Field(default=2, gt=0, le=60)
    max_download_mb: int = Field(default=2048, ge=1, le=10240)
    max_inline_image_mb: int = Field(default=20, ge=1, le=100)
    inference_steps: int = Field(default=20, ge=20, le=50)
    guidance_scale: float = Field(default=1.5, ge=1, le=3)
    enable_deepcache: bool = True

    @model_validator(mode="after")
    def validate_oom_exit_codes(self) -> Self:
        if 0 in self.oom_exit_codes:
            raise ValueError("OOM exit codes cannot include successful exit code 0")
        if self.oom_exit_codes and self.kind not in {"cli", "rife", "latentsync"}:
            raise ValueError("OOM exit codes are supported only by child-process providers")
        if self.kind == "luma":
            if not self.endpoint or not self.endpoint.startswith("https://"):
                raise ValueError("Luma requires an HTTPS endpoint")
            if self.model != "ray-3.2":
                raise ValueError("The implemented Luma adapter supports model ray-3.2")
            if not self.api_key_env:
                raise ValueError("Luma requires an API-key environment-variable name")
        if self.kind == "ltx":
            if not self.endpoint or not self.endpoint.startswith("https://"):
                raise ValueError("LTX requires an HTTPS endpoint")
            if self.model not in {"ltx-2-3-fast", "ltx-2-3-pro"}:
                raise ValueError("The implemented LTX adapter supports LTX-2.3 Fast or Pro")
            if not self.api_key_env:
                raise ValueError("LTX requires an API-key environment-variable name")
        if self.kind == "comfyui_wan_flf":
            if not self.endpoint or self.workflow_template is None:
                raise ValueError("ComfyUI Wan FLF requires endpoint and workflow_template")
            if self.model != WAN_FLF_MODEL_IDENTITY:
                raise ValueError(f"ComfyUI Wan FLF supports only {WAN_FLF_MODEL_IDENTITY}")
            if self.gpu_assignment not in {"gpu0", "gpu1"}:
                raise ValueError("ComfyUI Wan FLF requires an exact GPU assignment")
        if self.kind == "rife" and (
            self.python is None or self.script is None or self.model_directory is None
        ):
            raise ValueError("RIFE requires python, script, and model_directory paths")
        if self.kind == "latentsync" and (
            self.python is None
            or self.repository_directory is None
            or self.unet_config_path is None
            or self.checkpoint_path is None
            or self.syncnet_checkpoint_path is None
        ):
            raise ValueError(
                "LatentSync requires python, repository_directory, unet_config_path, "
                "checkpoint_path, and syncnet_checkpoint_path"
            )
        if self.kind == "latentsync" and self.model != "LatentSync-1.5":
            raise ValueError("The implemented local adapter supports LatentSync-1.5")
        if self.kind == "cli":
            if self.enabled and not self.command:
                raise ValueError("An enabled CLI provider requires an argv template")
            if self.enabled and not self.model:
                raise ValueError("An enabled CLI provider requires an exact model identity")
            if self.enabled and not self.health_command:
                raise ValueError("An enabled CLI provider requires a health command")
            if any(
                field is not None
                for part in self.health_command
                for _, field, _, _ in Formatter().parse(part)
            ):
                raise ValueError("CLI health command cannot contain runtime placeholders")
            allowed = {
                "prompt",
                "negative_prompt",
                "output",
                "reference_image",
                "width",
                "height",
                "seed",
                "text",
                "voice",
                "speed",
                "start_frame",
                "end_frame",
                "audio",
                "duration",
                "fps",
            }
            fields = {
                field_name
                for part in self.command
                for _literal, field_name, _format, _conversion in Formatter().parse(part)
                if field_name is not None
            }
            unknown = fields - allowed
            if unknown:
                raise ValueError(
                    "CLI argv template contains unsupported placeholders: "
                    + ", ".join(sorted(unknown))
                )
            if self.enabled and not {"prompt", "reference_image", "output"}.issubset(fields):
                raise ValueError(
                    "An enabled target-image CLI must use prompt, reference_image, and output"
                )
        return self


class ProviderConfigurationFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


def configured_image_provider(
    path: Path,
    provider_id: str,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> MockImageProvider | GenericCLIImageProvider:
    if provider_id == "mock-image":
        return MockImageProvider()
    configured = load_provider_configuration(path)
    item = next((provider for provider in configured.providers if provider.id == provider_id), None)
    if item is None:
        raise KeyError(f"Unknown provider: {provider_id}")
    if not item.enabled:
        raise ValueError(f"Provider is disabled: {provider_id}")
    if item.kind != "cli" or not item.command:
        raise ValueError(f"Provider is not an implemented image generator: {provider_id}")
    return GenericCLIImageProvider(
        item.id,
        item.command,
        {Capability.IMAGE_GENERATION, Capability.IMAGE_EDITING},
        timeout=int(item.timeout_seconds),
        oom_exit_codes=item.oom_exit_codes,
        cancel_requested=cancel_requested,
        model_identity=item.model or "administrator-configured",
        health_command=item.health_command,
        max_output_mb=item.max_download_mb,
    )


def configured_first_last_frame_provider(
    path: Path,
    provider_id: str,
) -> LumaRayVideoProvider | LtxVideoProvider | ComfyUIWanFirstLastFrameProvider:
    configured = load_provider_configuration(path)
    item = next((provider for provider in configured.providers if provider.id == provider_id), None)
    if item is None:
        raise KeyError(f"Unknown provider: {provider_id}")
    if not item.enabled:
        raise ValueError(f"Provider is disabled: {provider_id}")
    if item.kind == "comfyui_wan_flf":
        if (
            item.endpoint is None
            or item.model is None
            or item.workflow_template is None
            or item.gpu_assignment is None
        ):
            raise ValueError(f"Provider has incomplete Wan FLF configuration: {provider_id}")
        workflow_template = item.workflow_template
        if not workflow_template.is_absolute():
            workflow_template = path.parent / workflow_template
        return ComfyUIWanFirstLastFrameProvider(
            item.id,
            endpoint=item.endpoint,
            workflow_template=workflow_template,
            gpu_assignment=item.gpu_assignment,
            model=item.model,
            timeout_seconds=item.timeout_seconds,
            poll_interval_seconds=item.poll_interval_seconds,
            max_download_mb=item.max_download_mb,
        )
    if item.kind not in {"luma", "ltx"} or item.endpoint is None or item.model is None:
        raise ValueError(f"Provider is not an implemented FLF provider: {provider_id}")
    api_key = os.environ.get(item.api_key_env) if item.api_key_env else None
    if not api_key:
        raise ValueError(f"Provider credential is missing: {provider_id}")
    if item.kind == "luma":
        return LumaRayVideoProvider(
            api_key=api_key,
            endpoint=item.endpoint,
            model=item.model,
            timeout_seconds=item.timeout_seconds,
            poll_interval_seconds=item.poll_interval_seconds,
            max_download_mb=item.max_download_mb,
            max_inline_image_mb=item.max_inline_image_mb,
        )
    return LtxVideoProvider(
        item.id,
        api_key=api_key,
        endpoint=item.endpoint,
        model=item.model,
        timeout_seconds=item.timeout_seconds,
        poll_interval_seconds=item.poll_interval_seconds,
        max_download_mb=item.max_download_mb,
        max_inline_image_mb=item.max_inline_image_mb,
    )


def configured_interpolation_provider(
    path: Path,
    provider_id: str,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> RifeCliInterpolationProvider:
    configured = load_provider_configuration(path)
    item = next((provider for provider in configured.providers if provider.id == provider_id), None)
    if item is None:
        raise KeyError(f"Unknown provider: {provider_id}")
    if not item.enabled:
        raise ValueError(f"Provider is disabled: {provider_id}")
    if (
        item.kind != "rife"
        or item.python is None
        or item.script is None
        or item.model_directory is None
    ):
        raise ValueError(f"Provider is not an implemented interpolator: {provider_id}")
    return RifeCliInterpolationProvider(
        item.python,
        item.script,
        item.model_directory,
        provider_id=item.id,
        timeout_seconds=item.timeout_seconds,
        oom_exit_codes=item.oom_exit_codes,
        cancel_requested=cancel_requested,
    )


def configured_lip_sync_provider(
    path: Path,
    provider_id: str,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> LatentSyncCliProvider:
    configured = load_provider_configuration(path)
    item = next((provider for provider in configured.providers if provider.id == provider_id), None)
    if item is None:
        raise KeyError(f"Unknown provider: {provider_id}")
    if not item.enabled:
        raise ValueError(f"Provider is disabled: {provider_id}")
    if (
        item.kind != "latentsync"
        or item.python is None
        or item.repository_directory is None
        or item.unet_config_path is None
        or item.checkpoint_path is None
        or item.syncnet_checkpoint_path is None
    ):
        raise ValueError(f"Provider is not an implemented lip-sync provider: {provider_id}")
    return LatentSyncCliProvider(
        item.id,
        item.python,
        item.repository_directory,
        item.unet_config_path,
        item.checkpoint_path,
        item.syncnet_checkpoint_path,
        timeout_seconds=item.timeout_seconds,
        inference_steps=item.inference_steps,
        guidance_scale=item.guidance_scale,
        enable_deepcache=item.enable_deepcache,
        oom_exit_codes=item.oom_exit_codes,
        cancel_requested=cancel_requested,
    )


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
        if item.kind == "luma" and item.endpoint and item.model:
            key = os.environ.get(item.api_key_env) if item.enabled and item.api_key_env else None
            luma = LumaRayVideoProvider(
                api_key=key,
                endpoint=item.endpoint,
                model=item.model,
                timeout_seconds=item.timeout_seconds,
                poll_interval_seconds=item.poll_interval_seconds,
                max_download_mb=item.max_download_mb,
                max_inline_image_mb=item.max_inline_image_mb,
            ).info()
            records.append(
                luma.model_copy(
                    update={
                        "available": item.enabled and key is not None,
                        "notes": luma.notes
                        if item.enabled
                        else "Disabled in provider configuration",
                    }
                )
            )
            continue
        if item.kind == "ltx" and item.endpoint and item.model:
            key = os.environ.get(item.api_key_env) if item.enabled and item.api_key_env else None
            ltx = LtxVideoProvider(
                item.id,
                api_key=key,
                endpoint=item.endpoint,
                model=item.model,
                timeout_seconds=item.timeout_seconds,
                poll_interval_seconds=item.poll_interval_seconds,
                max_download_mb=item.max_download_mb,
                max_inline_image_mb=item.max_inline_image_mb,
            ).info()
            records.append(
                ltx.model_copy(
                    update={
                        "available": item.enabled and key is not None,
                        "notes": ltx.notes
                        if item.enabled
                        else "Disabled in provider configuration",
                    }
                )
            )
            continue
        if (
            item.kind == "comfyui_wan_flf"
            and item.endpoint
            and item.model
            and item.workflow_template
            and item.gpu_assignment
        ):
            workflow_template = item.workflow_template
            if not workflow_template.is_absolute():
                workflow_template = path.parent / workflow_template
            record = ComfyUIWanFirstLastFrameProvider(
                item.id,
                endpoint=item.endpoint,
                workflow_template=workflow_template,
                gpu_assignment=item.gpu_assignment,
                model=item.model,
                timeout_seconds=item.timeout_seconds,
                poll_interval_seconds=item.poll_interval_seconds,
                max_download_mb=item.max_download_mb,
            ).info()
            records.append(
                record.model_copy(
                    update={
                        "available": item.enabled and record.available,
                        "notes": record.notes
                        if item.enabled
                        else "Disabled in provider configuration",
                    }
                )
            )
            continue
        if (
            item.kind == "rife"
            and item.python is not None
            and item.script is not None
            and item.model_directory is not None
        ):
            record = RifeCliInterpolationProvider(
                item.python,
                item.script,
                item.model_directory,
                provider_id=item.id,
                timeout_seconds=item.timeout_seconds,
                oom_exit_codes=item.oom_exit_codes,
            ).info()
            records.append(
                record.model_copy(
                    update={
                        "available": item.enabled and record.available,
                        "notes": record.notes
                        if item.enabled
                        else "Disabled in provider configuration",
                    }
                )
            )
            continue
        if (
            item.kind == "latentsync"
            and item.python is not None
            and item.repository_directory is not None
            and item.unet_config_path is not None
            and item.checkpoint_path is not None
            and item.syncnet_checkpoint_path is not None
        ):
            record = LatentSyncCliProvider(
                item.id,
                item.python,
                item.repository_directory,
                item.unet_config_path,
                item.checkpoint_path,
                item.syncnet_checkpoint_path,
                timeout_seconds=item.timeout_seconds,
                inference_steps=item.inference_steps,
                guidance_scale=item.guidance_scale,
                enable_deepcache=item.enable_deepcache,
                oom_exit_codes=item.oom_exit_codes,
            ).info()
            records.append(
                record.model_copy(
                    update={
                        "available": item.enabled and record.available,
                        "notes": record.notes
                        if item.enabled
                        else "Disabled in provider configuration",
                    }
                )
            )
            continue
        if item.kind == "cli" and item.command:
            record = GenericCLIImageProvider(
                item.id,
                item.command,
                {Capability.IMAGE_GENERATION, Capability.IMAGE_EDITING},
                timeout=int(item.timeout_seconds),
                oom_exit_codes=item.oom_exit_codes,
                model_identity=item.model or "administrator-configured",
                health_command=item.health_command,
                max_output_mb=item.max_download_mb,
            ).info()
            records.append(
                record.model_copy(
                    update={
                        "available": item.enabled and record.available,
                        "notes": record.notes
                        if item.enabled
                        else "Disabled in provider configuration",
                    }
                )
            )
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
        if item.kind == "luma":
            if item.endpoint is None or item.model is None:
                results.append({"provider": item.id, "ok": False, "status": "missing_config"})
                continue
            key = os.environ.get(item.api_key_env) if item.api_key_env else None
            health = await LumaRayVideoProvider(
                api_key=key,
                endpoint=item.endpoint,
                model=item.model,
                timeout_seconds=item.timeout_seconds,
                poll_interval_seconds=item.poll_interval_seconds,
                max_download_mb=item.max_download_mb,
                max_inline_image_mb=item.max_inline_image_mb,
            ).health()
            results.append({"provider": item.id, **health})
            continue
        if item.kind == "comfyui_wan_flf":
            if (
                item.endpoint is None
                or item.model is None
                or item.workflow_template is None
                or item.gpu_assignment is None
            ):
                results.append({"provider": item.id, "ok": False, "status": "missing_config"})
                continue
            workflow_template = item.workflow_template
            if not workflow_template.is_absolute():
                workflow_template = path.parent / workflow_template
            health = await ComfyUIWanFirstLastFrameProvider(
                item.id,
                endpoint=item.endpoint,
                workflow_template=workflow_template,
                gpu_assignment=item.gpu_assignment,
                model=item.model,
                timeout_seconds=item.timeout_seconds,
                poll_interval_seconds=item.poll_interval_seconds,
                max_download_mb=item.max_download_mb,
            ).health()
            results.append({"provider": item.id, **health})
            continue
        if item.kind == "ltx":
            if item.endpoint is None or item.model is None:
                results.append({"provider": item.id, "ok": False, "status": "missing_config"})
                continue
            key = os.environ.get(item.api_key_env) if item.api_key_env else None
            health = await LtxVideoProvider(
                item.id,
                api_key=key,
                endpoint=item.endpoint,
                model=item.model,
                timeout_seconds=item.timeout_seconds,
                poll_interval_seconds=item.poll_interval_seconds,
                max_download_mb=item.max_download_mb,
                max_inline_image_mb=item.max_inline_image_mb,
            ).health()
            results.append({"provider": item.id, **health})
            continue
        if item.kind == "rife":
            if item.python is None or item.script is None or item.model_directory is None:
                results.append({"provider": item.id, "ok": False, "status": "missing_paths"})
                continue
            health = await RifeCliInterpolationProvider(
                item.python,
                item.script,
                item.model_directory,
                provider_id=item.id,
                timeout_seconds=item.timeout_seconds,
                oom_exit_codes=item.oom_exit_codes,
            ).health()
            results.append({"provider": item.id, **health})
            continue
        if item.kind == "latentsync":
            if (
                item.python is None
                or item.repository_directory is None
                or item.unet_config_path is None
                or item.checkpoint_path is None
                or item.syncnet_checkpoint_path is None
            ):
                results.append({"provider": item.id, "ok": False, "status": "missing_paths"})
                continue
            health = await LatentSyncCliProvider(
                item.id,
                item.python,
                item.repository_directory,
                item.unet_config_path,
                item.checkpoint_path,
                item.syncnet_checkpoint_path,
                timeout_seconds=item.timeout_seconds,
                inference_steps=item.inference_steps,
                guidance_scale=item.guidance_scale,
                enable_deepcache=item.enable_deepcache,
                oom_exit_codes=item.oom_exit_codes,
            ).health()
            results.append({"provider": item.id, **health})
            continue
        if item.kind == "cli":
            if not item.command:
                results.append({"provider": item.id, "ok": False, "status": "missing_command"})
                continue
            health = await GenericCLIImageProvider(
                item.id,
                item.command,
                {Capability.IMAGE_GENERATION, Capability.IMAGE_EDITING},
                timeout=int(item.timeout_seconds),
                oom_exit_codes=item.oom_exit_codes,
                model_identity=item.model or "administrator-configured",
                health_command=item.health_command,
                max_output_mb=item.max_download_mb,
            ).health()
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
