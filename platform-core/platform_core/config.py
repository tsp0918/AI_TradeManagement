from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# monorepo の場合、.env はリポジトリルートに置く
# このファイルの位置: platform-core/platform_core/config.py
# リポジトリルート:  ../../
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent  # AI_TradeManagement/
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    platform_database_url: str = "postgresql+asyncpg://user:password@localhost:5432/platform_db"

    # JWT
    jwt_secret_key: str = "change-me-in-production-min-32-chars"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30

    # Google OAuth2
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # Microsoft OAuth2
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant_id: str = "common"
    microsoft_redirect_uri: str = "http://localhost:8000/auth/microsoft/callback"

    # LLM
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    default_llm_provider: str = "anthropic"
    default_llm_model: str = "claude-sonnet-4-6"

    # Storage
    storage_backend: str = "local"
    storage_local_path: str = "./storage"

    # Platform
    platform_env: str = "development"
    platform_log_level: str = "INFO"

    # Module URLs
    module_ai_validation_url: str = "http://localhost:8001"
    module_ai_classification_url: str = "http://localhost:8002"
    module_rnd_assessment_url: str = "http://localhost:8003"
    module_patent_search_url: str = "http://localhost:8004"
    module_screening_url: str = "http://localhost:8005"

    @field_validator("jwt_secret_key")
    @classmethod
    def secret_key_min_length(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return v


settings = Settings()
