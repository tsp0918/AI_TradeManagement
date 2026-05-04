# app/settings.py
import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "sqlite:///./products.db"

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2:3b"

    # 外部判定アプリ連携 (ai_validation)
    EXTERNAL_APP_BASE_URL: str = os.getenv("MODULE_AI_VALIDATION_URL", "http://localhost:8001")
    EXTERNAL_APP_TIMEOUT_SEC: float = 30.0

    # 外部アプリが結果を返すWebhook URL（ai_classificationのUI側）
    PUBLIC_WEBHOOK_URL: str = os.getenv(
        "AI_CLASSIFICATION_WEBHOOK_URL",
        "http://localhost:8002/integrations/export-control/webhook",
    )

    # HSコード判定モジュール連携
    HS_CLASSIFIER_BASE_URL: str = os.getenv("MODULE_HS_CLASSIFIER_URL", "http://localhost:8006")
    HS_WEBHOOK_URL: str = os.getenv(
        "HS_CLASSIFIER_WEBHOOK_URL",
        "http://localhost:8002/integrations/hs-classifier/webhook",
    )


settings = Settings()
