"""Application configuration, loaded from the backend/.env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ directory (parent of app/)
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-sol"
    reasoning_effort: str = "high"
    image_detail: str = "original"

    # Rendering
    render_dpi: int = 180
    max_pages: int = 40

    # Server / storage
    storage_dir: str = "storage"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Any of these frontends are allowed in addition to the explicit list above.
    # Covers Vercel deploy URLs (which change per deploy) and Render static sites.
    # Override with the CORS_ORIGIN_REGEX env var; set it empty to disable.
    cors_origin_regex: str = r"^https://(pdf-to-graph[a-z0-9-]*\.vercel\.app|[a-z0-9-]+\.onrender\.com)$"

    @property
    def storage_path(self) -> Path:
        p = BASE_DIR / self.storage_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
