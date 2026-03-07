from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    gcp_project_id: str = "brainrot-453319"
    bq_dash_dataset: str = "kalshi_dash"
    bq_ops_dataset: str = "kalshi_ops"
    bq_core_dataset: str = "kalshi_core"

    google_application_credentials: Optional[str] = None

    default_dashboard_id: str = "kalshi_autonomous_v1"
    max_tile_rows: int = 500


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if settings.google_application_credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.google_application_credentials
    return settings
