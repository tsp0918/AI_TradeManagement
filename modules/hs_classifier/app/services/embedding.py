"""Sentence Transformer シングルトン管理。"""
from __future__ import annotations

import logging
import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import settings

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None

# E5 系モデルは "query:"/"passage:" プレフィックスが必要
_USE_E5_PREFIX = "e5" in settings.embedding_model_name.lower()


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info("Loading embedding model: %s", settings.embedding_model_name)
        _model = SentenceTransformer(settings.embedding_model_name)
        logger.info("Embedding model loaded (dim=%d)", settings.embedding_dimension)
    return _model


def encode_passages(texts: list[str]) -> np.ndarray:
    """HS コード説明文（passage）をエンコードする。"""
    if _USE_E5_PREFIX:
        texts = [f"passage: {t}" for t in texts]
    model = get_model()
    emb = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=64,
    )
    return np.asarray(emb, dtype="float32")


def encode_query(text: str) -> np.ndarray:
    """検索クエリをエンコードする（1 件）。"""
    if _USE_E5_PREFIX:
        text = f"query: {text}"
    model = get_model()
    emb = model.encode([text], normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(emb, dtype="float32")
