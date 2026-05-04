"""HS コード判定モジュール設定。"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_MODULE_DIR = Path(__file__).resolve().parents[1]   # modules/hs_classifier/
_DATA_DIR   = _MODULE_DIR / "data"
_ENV_FILE   = Path(__file__).resolve().parents[3] / ".env"   # AI_TradeManagement/.env


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HS_CLASSIFIER_",
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "HSコード判定"
    app_version: str = "0.1.0"
    port: int = 8006

    # ── データセットパス ────────────────────────────────────────────────
    hs_data_path: str = str(_DATA_DIR / "hs2022_6digit.json")
    hs_hierarchy_path: str = str(_DATA_DIR / "hs2022_hierarchy.json")
    embedding_cache_path: str = str(_DATA_DIR / "embeddings_cache.npy")

    # ── Embedding モデル ─────────────────────────────────────────────────
    # E5 系は query:/passage: プレフィックスで日本語↔英語の意味的距離を縮める
    # embedding.py が _USE_E5_PREFIX フラグでプレフィックスを自動付与する
    embedding_model_name: str = "intfloat/multilingual-e5-small"
    embedding_dimension: int = 384

    # ── FAISS ───────────────────────────────────────────────────────────
    faiss_use_gpu: bool = False

    # ── 判定パラメータ ───────────────────────────────────────────────────
    default_top_k: int = 5
    stage1_top_chapters: int = 8
    min_similarity_threshold: float = 0.3

    # ── webhook ─────────────────────────────────────────────────────────
    webhook_timeout: float = 30.0
    webhook_max_retries: int = 3
    webhook_retry_delay: float = 2.0


settings = Settings()
