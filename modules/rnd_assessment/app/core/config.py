from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # DB
    database_url: str = "sqlite:///./rd_risk_app.db"

    # External services
    item_service_base_url: str = "http://127.0.0.1:8001"       # ai_validation
    screening_service_base_url: str = "http://127.0.0.1:8005"  # screening（旧: 8002 は誤り）

    # Versions (evidence / audit)
    ruleset_version: str = "2026-02-02-jp-v1"
    model_version: str = "screening-v0.1"
    scoring_config_version: str = "scoring-v0.1"


settings = Settings()
