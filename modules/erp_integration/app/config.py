"""erp_integration 設定。環境変数 or .env から読み込む。"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── 認証 ──────────────────────────────────────────────────────────────────
    # ERP → このアダプターへの Bearer トークン
    API_KEY: str = "dev-erp-integration-key"

    # ── 上流 AI_TM モジュール URL ─────────────────────────────────────────────
    HS_CLASSIFIER_URL: str   = "http://localhost:8006"
    PLATFORM_CORE_URL: str   = "http://localhost:8000"
    SCREENING_URL: str       = "http://localhost:8005"
    AI_VALIDATION_URL: str   = "http://localhost:8001"

    # ── ERP コールバック URL ──────────────────────────────────────────────────
    # AI_TM → ERP への判定結果 Webhook 送信先
    ERP_WEBHOOK_URL: str = "http://localhost:8888"

    # ── HTTP クライアント設定 ─────────────────────────────────────────────────
    HTTP_TIMEOUT: float = 30.0


settings = Settings()
