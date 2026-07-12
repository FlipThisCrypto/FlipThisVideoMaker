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


def load_render_profile_configuration(path: Path) -> RenderProfileConfigurationFile:
    if not path.is_file():
        raise FileNotFoundError(f"Render-profile configuration not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return RenderProfileConfigurationFile.model_validate(data)
