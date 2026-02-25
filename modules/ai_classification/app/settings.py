# app/settings.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str = "sqlite:///./products.db"

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2:3b"  # 3B想定。手元のモデル名に合わせて変更

    # 外部判定アプリ連携
    EXTERNAL_APP_BASE_URL: str = "http://localhost:8001"  # 例
    EXTERNAL_APP_TIMEOUT_SEC: float = 30.0

    # 外部アプリが結果を返すWebhook URL（あなたのUI側）
    # 外部アプリに「ここにPOSTしてね」と渡す用途
    PUBLIC_WEBHOOK_URL: str = "http://localhost:8002/integrations/export-control/webhook"

    # HSコード判定モジュール連携
    HS_CLASSIFIER_BASE_URL: str = "http://localhost:8006"
    HS_WEBHOOK_URL: str = "http://localhost:8002/integrations/hs-classifier/webhook"


settings = Settings()
