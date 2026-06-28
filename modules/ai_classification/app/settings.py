from __future__ import annotations

from pathlib import Path

from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = Field(
        default="sqlite:///./products.db",
        validation_alias=AliasChoices("DATABASE_URL", "CLASSIFICATION_DATABASE_URL"),
    )

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2:3b"

    # 外部判定アプリ連携 (ai_validation — port 8011)
    EXTERNAL_APP_BASE_URL: str = Field(
        default="http://localhost:8011",
        validation_alias=AliasChoices("EXTERNAL_APP_BASE_URL", "MODULE_AI_VALIDATION_URL"),
    )
    EXTERNAL_APP_TIMEOUT_SEC: float = 30.0

    # 外部アプリが結果を返す Webhook URL (ai_classification の UI 側)
    PUBLIC_WEBHOOK_URL: str = Field(
        default="http://localhost:8002/integrations/export-control/webhook",
        validation_alias=AliasChoices("PUBLIC_WEBHOOK_URL", "AI_CLASSIFICATION_WEBHOOK_URL"),
    )

    # HS コード判定モジュール連携 (hs_classifier — port 8006)
    HS_CLASSIFIER_BASE_URL: str = Field(
        default="http://localhost:8006",
        validation_alias=AliasChoices("HS_CLASSIFIER_BASE_URL", "MODULE_HS_CLASSIFIER_URL"),
    )
    HS_WEBHOOK_URL: str = Field(
        default="http://localhost:8002/integrations/hs-classifier/webhook",
        validation_alias=AliasChoices("HS_WEBHOOK_URL", "HS_CLASSIFIER_WEBHOOK_URL"),
    )


settings = Settings()
