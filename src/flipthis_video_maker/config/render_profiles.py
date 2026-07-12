from datetime import datetime
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RenderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    width: int = Field(ge=64, le=8192)
    height: int = Field(ge=64, le=8192)
    fps: int = Field(ge=1, le=120)
    video_codec: str = Field(min_length=1, max_length=80)
    audio_codec: str = Field(min_length=1, max_length=80)
    fallback_profile: str | None = Field(default=None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def dimensions_support_yuv420(self) -> Self:
        if self.width % 2 or self.height % 2:
            raise ValueError("Render profile dimensions must be even for yuv420p output")
        return self

    def generation_parameters(self, name: str) -> dict[str, int | str]:
        return {
            "render_profile": name,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
        }


class RenderProfileConfigurationFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    profiles: dict[str, RenderProfile] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_profile_graph(self) -> Self:
        if any(not name.strip() or len(name) > 40 for name in self.profiles):
            raise ValueError("Render profile names must contain 1-40 non-whitespace characters")
        for name, profile in self.profiles.items():
            fallback = profile.fallback_profile
            if fallback is not None and fallback not in self.profiles:
                raise ValueError(f"Render profile {name!r} has unknown fallback {fallback!r}")
        for name, profile in self.profiles.items():
            fallback = profile.fallback_profile
            if fallback is not None:
                fallback_values = self.profiles[fallback]
                if (
                    fallback_values.width > profile.width
                    or fallback_values.height > profile.height
                    or fallback_values.fps > profile.fps
                ):
                    raise ValueError(
                        f"Render profile {name!r} fallback {fallback!r} is more demanding"
                    )
            visited = {name}
            while fallback is not None:
                if fallback in visited:
                    raise ValueError(f"Render profile fallback cycle includes {fallback!r}")
                visited.add(fallback)
                fallback = self.profiles[fallback].fallback_profile
        return self

    def require(self, name: str) -> RenderProfile:
        try:
            return self.profiles[name]
        except KeyError as error:
            raise KeyError(f"Unknown render profile: {name}") from error

    def fallback_chain(self, name: str) -> list[str]:
        chain = [name]
        profile = self.require(name)
        while profile.fallback_profile is not None:
            chain.append(profile.fallback_profile)
            profile = self.profiles[profile.fallback_profile]
        return chain


class NamedRenderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=40)
    profile: RenderProfile


class RenderProfileFallbackRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    occurred_at: datetime
    reason: Literal["provider_out_of_memory"] = "provider_out_of_memory"
    provider_id: str = Field(min_length=1, max_length=200)
    operation: str = Field(min_length=1, max_length=200)
    from_profile: str = Field(min_length=1, max_length=40)
    to_profile: str = Field(min_length=1, max_length=40)
    job_attempt: int = Field(ge=1)
    gpu_assignment: str = Field(min_length=1, max_length=40)
    backend_code: str | None = Field(default=None, max_length=200)
    cleanup_action: str = Field(min_length=1, max_length=100)
    cleanup_completed: bool
    cleanup_retry_safe: bool


class RenderProfileExecution(BaseModel):
    """Immutable render settings captured before a persistent job is queued."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    version: Literal[1] = 1
    requested_profile: str = Field(min_length=1, max_length=40)
    effective_profile: str = Field(min_length=1, max_length=40)
    profile: RenderProfile
    fallback_chain: tuple[NamedRenderProfile, ...] = Field(min_length=1)
    fallback_history: tuple[RenderProfileFallbackRecord, ...] = ()

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        names = [item.name for item in self.fallback_chain]
        if len(names) != len(set(names)):
            raise ValueError("Render-profile execution fallback names must be unique")
        if names[0] != self.requested_profile:
            raise ValueError("Render-profile execution chain must begin with requested profile")
        if self.effective_profile not in names:
            raise ValueError("Effective render profile is missing from the fallback chain")

        effective_index = names.index(self.effective_profile)
        if self.profile != self.fallback_chain[effective_index].profile:
            raise ValueError("Effective render-profile values do not match the captured chain")
        if len(self.fallback_history) != effective_index:
            raise ValueError("Fallback history must lead exactly to the effective profile")

        for index, item in enumerate(self.fallback_chain):
            expected_fallback = names[index + 1] if index + 1 < len(names) else None
            if item.profile.fallback_profile != expected_fallback:
                raise ValueError("Captured render-profile fallback chain is inconsistent")
        for index, record in enumerate(self.fallback_history):
            if record.from_profile != names[index] or record.to_profile != names[index + 1]:
                raise ValueError("Render-profile fallback history is not monotonic")
        return self

    @classmethod
    def resolve(
        cls,
        configuration: RenderProfileConfigurationFile,
        requested_profile: str,
    ) -> Self:
        chain = tuple(
            NamedRenderProfile(name=name, profile=configuration.require(name))
            for name in configuration.fallback_chain(requested_profile)
        )
        return cls(
            requested_profile=requested_profile,
            effective_profile=requested_profile,
            profile=chain[0].profile,
            fallback_chain=chain,
        )

    def advance(self, record: RenderProfileFallbackRecord) -> Self:
        current_index = next(
            index
            for index, item in enumerate(self.fallback_chain)
            if item.name == self.effective_profile
        )
        if current_index + 1 >= len(self.fallback_chain):
            raise ValueError(f"Render profile {self.effective_profile!r} has no fallback")
        target = self.fallback_chain[current_index + 1]
        if record.from_profile != self.effective_profile or record.to_profile != target.name:
            raise ValueError("Fallback record does not describe the next configured profile")
        return type(self)(
            requested_profile=self.requested_profile,
            effective_profile=target.name,
            profile=target.profile,
            fallback_chain=self.fallback_chain,
            fallback_history=(*self.fallback_history, record),
        )

    def generation_parameters(self) -> dict[str, int | str]:
        return self.profile.generation_parameters(self.effective_profile)


RENDER_PROFILE_EXECUTION_KEY = "render_profile_execution"


def render_profile_execution_from_payload(payload: dict[str, object]) -> RenderProfileExecution:
    raw = payload.get(RENDER_PROFILE_EXECUTION_KEY)
    if not isinstance(raw, dict):
        raise ValueError("Job payload has no render-profile execution snapshot")
    return RenderProfileExecution.model_validate(raw)


def load_render_profile_configuration(path: Path) -> RenderProfileConfigurationFile:
    if not path.is_file():
        raise FileNotFoundError(f"Render-profile configuration not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return RenderProfileConfigurationFile.model_validate(data)
