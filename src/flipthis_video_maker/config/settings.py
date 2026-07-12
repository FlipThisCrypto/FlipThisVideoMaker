from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FTVM_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/flipthis.db"
    data_dir: Path = Path("./projects")
    model_dir: Path = Path("./models")
    cache_dir: Path = Path("./.cache")
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    provider_config: Path = Path("config/providers.yaml")
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    max_upload_mb: int = 100
    max_job_retries: int = 2
    max_cpu_jobs: int = 2
    default_render_profile: Literal["draft", "standard", "final"] = "draft"
    auth_mode: Literal["local", "token"] = "local"
    log_level: str = "INFO"
    watermark_enabled: bool = False
    gpu_video: int = 0
    gpu_secondary: int = 1
    min_free_vram_mb: int = 2000
    hf_token_file: Path | None = Field(default=None, repr=False)

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.cache_dir, Path("data")):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
