from pathlib import Path
from typing import Protocol

from flipthis_video_maker.providers.base.models import (
    ImageRequest,
    ProviderInfo,
    StoryPlan,
    TTSRequest,
    VideoRequest,
)


class Provider(Protocol):
    def info(self) -> ProviderInfo: ...
    async def health(self) -> dict[str, object]: ...


class ImageProvider(Provider, Protocol):
    async def generate(self, request: ImageRequest) -> Path: ...


class TTSProvider(Provider, Protocol):
    async def synthesize(self, request: TTSRequest) -> Path: ...


class VideoProvider(Provider, Protocol):
    async def generate(self, request: VideoRequest) -> Path: ...


class StoryPlanner(Provider, Protocol):
    async def plan(self, story: str) -> StoryPlan: ...
