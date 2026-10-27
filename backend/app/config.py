"""Application configuration, loaded from the backend/.env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

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
    openai_model: str = "gpt-6-astra"
    reasoning_effort: str = "high"
    image_detail: str = "original"
    openai_timeout_seconds: float = Field(default=900, gt=0)

    # Chart gate: a cheap vision model screens each PDF page before the expensive
    # extraction model sees it. A page is skipped only when the gate is confident there
    # are no charts (P(charts) < gate_threshold). Fails open on any error.
    gate_enabled: bool = True
    gate_model: str = "gpt-5.4-mini"
    gate_reasoning: str = "low"
    gate_image_detail: str = "low"
    gate_threshold: float = 0.3
    gate_images: bool = False  # uploaded images are explicit user intent: no gate by default
    gate_timeout_seconds: float = Field(default=60, gt=0)

    # Rendering
    render_dpi: int = 180
    max_pages: int = 40
    page_batch_size: int = Field(default=5, ge=1, le=20)
    llm_concurrency: int = Field(default=5, ge=1, le=20)
    gate_concurrency: int = Field(default=5, ge=1, le=20)
    extraction_concurrency: int = Field(default=5, ge=1, le=20)

    # Server / storage
    storage_dir: str = "storage"
    storage_backend: Literal["local", "supabase"] = "local"
    supabase_url: str = ""
    supabase_secret_key: str = ""
    supabase_bucket: str = "pdf-chart-studio"
    supabase_table: str = "chart_studio_documents"
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
