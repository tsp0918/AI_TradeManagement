from __future__ import annotations

from pathlib import Path

from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DB (SQLite 専用 — DDL が DATETIME 型を使用しているため PostgreSQL 非対応)
    database_url: str = "sqlite:///./rd_risk_app.db"

    # External services
    item_service_base_url: str = Field(
        default="http://localhost:8011",
        validation_alias=AliasChoices("item_service_base_url", "MODULE_AI_VALIDATION_URL"),
    )
    screening_service_base_url: str = Field(
        default="http://localhost:8005",
        validation_alias=AliasChoices("screening_service_base_url", "MODULE_SCREENING_URL"),
    )

    # Versions (evidence / audit)
    ruleset_version: str = "2026-02-02-jp-v1"
    model_version: str = "screening-v0.1"
    scoring_config_version: str = "scoring-v0.1"


settings = Settings()
