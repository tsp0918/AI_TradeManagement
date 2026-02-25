"""HS コード判定モジュール設定。"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_MODULE_DIR = Path(__file__).resolve().parents[2]   # modules/hs_classifier/
_DATA_DIR   = _MODULE_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HS_CLASSIFIER_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    app_name: str = "HSコード判定"
    app_version: str = "0.1.0"
    port: int = 8006

    # ── データセットパス ────────────────────────────────────────────────
    hs_data_path: str = str(_DATA_DIR / "hs2022_6digit.json")
    hs_hierarchy_path: str = str(_DATA_DIR / "hs2022_hierarchy.json")
    embedding_cache_path: str = str(_DATA_DIR / "embeddings_cache.npy")

    # ── Embedding モデル ─────────────────────────────────────────────────
    # デフォルト: 既にキャッシュ済みの multilingual モデル
    # E5 モデルを使う場合: intfloat/multilingual-e5-small
    embedding_model_name: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    embedding_dimension: int = 384

    # ── FAISS ───────────────────────────────────────────────────────────
    faiss_use_gpu: bool = False

    # ── 判定パラメータ ───────────────────────────────────────────────────
    default_top_k: int = 5
    stage1_top_chapters: int = 3
    min_similarity_threshold: float = 0.3

    # ── webhook ─────────────────────────────────────────────────────────
    webhook_timeout: float = 30.0
    webhook_max_retries: int = 3
    webhook_retry_delay: float = 2.0


settings = Settings()
