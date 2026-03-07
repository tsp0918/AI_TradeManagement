from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

# プロジェクトルートの .env を参照（modules/patent_search/app/ から4階層上）
_ROOT_ENV = Path(__file__).parent.parent.parent.parent / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application Settings
    app_name: str = "Patent Search Application"
    app_version: str = "1.0.0"
    debug: bool = True
    host: str = "127.0.0.1"
    port: int = 8000

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/patents.db"

    # Google Cloud Platform
    gcp_project_id: Optional[str] = None
    google_application_credentials: Optional[str] = "./credentials/gcp-service-account.json"
    bigquery_dataset: str = "patents-public-data"
    bigquery_table: str = "patents.publications"

    # Ollama Configuration
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama2"
    ollama_timeout: int = 120

    # Search Configuration
    default_results_limit: int = 50
    max_results_limit: int = 1000

    # Export Configuration
    export_max_rows: int = 10000

    # J-Platpat API (特許庁 特許情報取得API)
    jplatpat_username: Optional[str] = None
    jplatpat_password: Optional[str] = None
    jplatpat_token_url: str = "https://ip-data.jpo.go.jp/auth/token"
    jplatpat_base_url: str = "https://ip-data.jpo.go.jp"

    # Logging
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=str(_ROOT_ENV),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


# Global settings instance
settings = Settings()
