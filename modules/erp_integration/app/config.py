"""erp_integration 設定。環境変数 or .env から読み込む。"""
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

    # ── 認証 ──────────────────────────────────────────────────────────────────
    # ERP → このアダプターへの Bearer トークン
    API_KEY: str = "dev-erp-integration-key"

    # ── 上流 AI_TM モジュール URL ─────────────────────────────────────────────
    HS_CLASSIFIER_URL: str = Field(
        default="http://localhost:8006",
        validation_alias=AliasChoices("HS_CLASSIFIER_URL", "MODULE_HS_CLASSIFIER_URL"),
    )
    PLATFORM_CORE_URL: str = Field(
        default="http://localhost:8000",
        validation_alias=AliasChoices("PLATFORM_CORE_URL", "MODULE_PLATFORM_URL"),
    )
    SCREENING_URL: str = Field(
        default="http://localhost:8005",
        validation_alias=AliasChoices("SCREENING_URL", "MODULE_SCREENING_URL"),
    )
    AI_VALIDATION_URL: str = Field(
        default="http://localhost:8011",
        validation_alias=AliasChoices("AI_VALIDATION_URL", "MODULE_AI_VALIDATION_URL"),
    )

    # ── ERP コールバック URL / 認証 ───────────────────────────────────────────
    # AI_TM → ERP への判定結果 Webhook 送信先
    ERP_WEBHOOK_URL: str = "http://localhost:8888"
    # ERP 側の webhook が要求する Bearer トークン（ERP .env の AI_TM_API_KEY と一致させる）
    ERP_WEBHOOK_API_KEY: str = Field(
        default="dev-erp-integration-key",
        validation_alias=AliasChoices("ERP_WEBHOOK_API_KEY", "AI_TM_API_KEY"),
    )

    # ── HTTP クライアント設定 ─────────────────────────────────────────────────
    HTTP_TIMEOUT: float = 30.0


settings = Settings()
