from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application Settings
    app_name: str = "Patent Search Application"
    app_version: str = "1.0.0"
    debug: bool = True
    host: str = "127.0.0.1"
    port: int = 8004

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/patents.db"

    # Google Cloud Platform
    gcp_project_id: Optional[str] = None
    google_application_credentials: Optional[str] = "./credentials/gcp-service-account.json"
    bigquery_dataset: str = "patents-public-data"
    bigquery_table: str = "patents.publications"

    # Ollama Configuration
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_timeout: int = 120

    # Search Configuration
    default_results_limit: int = 50
    max_results_limit: int = 1000

    # Export Configuration
    export_max_rows: int = 10000

    # Logging
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


# Global settings instance
settings = Settings()
