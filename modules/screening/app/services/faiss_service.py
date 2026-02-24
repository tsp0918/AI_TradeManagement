"""FAISS による企業名セマンティック類似度検索サービス。

ai_validation の patent_retrieve.py と同パターン。
モデル: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384 次元)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_INDEX_DIR  = Path(__file__).resolve().parents[3] / "data" / "faiss"
_INDEX_PATH = _INDEX_DIR / "entities.index"
_META_PATH  = _INDEX_DIR / "entities_meta.json"

# ── グローバルキャッシュ ────────────────────────────────────────────────────
_faiss_index: faiss.Index | None = None
_faiss_meta:  list[dict]   | None = None


# ── I/O ────────────────────────────────────────────────────────────────────

def _load_from_disk() -> tuple[faiss.Index, list[dict]] | None:
    if not _INDEX_PATH.exists() or not _META_PATH.exists():
        return None
    try:
        index = faiss.read_index(str(_INDEX_PATH))
        meta  = json.loads(_META_PATH.read_text(encoding="utf-8"))
        logger.info("FAISS index loaded: ntotal=%d", index.ntotal)
        return index, meta
    except Exception as exc:
        logger.warning("Failed to load FAISS index: %s", exc)
        return None


def _save_to_disk(index: faiss.Index, meta: list[dict]) -> None:
    _INDEX_DIR.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(_INDEX_PATH))
    _META_PATH.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    logger.info("FAISS index saved: ntotal=%d", index.ntotal)


# ── インデックス構築 ────────────────────────────────────────────────────────

def _entity_to_text(entity: Any) -> str:
    """Watchlist エンティティをインデックス用テキストに変換する。"""
    parts = [entity.entity_name]
    if entity.aliases:
        if isinstance(entity.aliases, list):
            parts.extend(entity.aliases)
        elif isinstance(entity.aliases, str):
            parts.append(entity.aliases)
    if entity.address:
        parts.append(entity.address)
    return "\n".join(p for p in parts if p)


def _build(entities: list[Any]) -> tuple[faiss.Index, list[dict]]:
    model = SentenceTransformer(MODEL_NAME)
    dim   = model.get_sentence_embedding_dimension()
    index = faiss.IndexFlatIP(dim)

    if not entities:
        logger.warning("No entities to index — FAISS index is empty (ntotal=0).")
        return index, []

    texts = [_entity_to_text(e) for e in entities]
    keep  = [(e, t) for e, t in zip(entities, texts) if t]
    if not keep:
        return index, []

    entities_k, texts_k = zip(*keep)
    emb = model.encode(list(texts_k), normalize_embeddings=True, show_progress_bar=False)
    emb = np.asarray(emb, dtype="float32")
    index.add(emb)

    meta = [{"id": str(e.id)} for e in entities_k]
    logger.info("FAISS index built: ntotal=%d", index.ntotal)
    return index, meta


# ── 公開インターフェース ─────────────────────────────────────────────────────

def get_or_build(entities: list[Any]) -> None:
    """起動時に呼ぶ。ディスクに既存インデックスがあればロード、なければ構築。"""
    global _faiss_index, _faiss_meta
    loaded = _load_from_disk()
    if loaded:
        _faiss_index, _faiss_meta = loaded
    else:
        _faiss_index, _faiss_meta = _build(entities)
        if _faiss_index.ntotal > 0:
            _save_to_disk(_faiss_index, _faiss_meta)


def rebuild(entities: list[Any]) -> None:
    """ウォッチリスト更新後にインデックスを強制再構築する。"""
    global _faiss_index, _faiss_meta
    _faiss_index, _faiss_meta = _build(entities)
    _save_to_disk(_faiss_index, _faiss_meta)


def search(query: str, top_k: int = 20) -> list[tuple[str, float]]:
    """
    クエリ企業名を FAISS で検索する。

    Returns:
        List of (watchlist_entity_id: str, score: float) sorted by score desc.
        FAISS が空（ntotal==0）の場合は空リストを返す。
    """
    if _faiss_index is None or _faiss_index.ntotal == 0:
        return []

    model = SentenceTransformer(MODEL_NAME)
    qv = model.encode([query], normalize_embeddings=True, show_progress_bar=False)
    qv = np.asarray(qv, dtype="float32")

    actual_k = min(top_k, _faiss_index.ntotal)
    D, I = _faiss_index.search(qv, actual_k)

    results = []
    for score, idx in zip(D[0].tolist(), I[0].tolist()):
        if idx < 0 or idx >= len(_faiss_meta):
            continue
        entity_id = _faiss_meta[idx]["id"]
        results.append((entity_id, float(score)))

    return sorted(results, key=lambda x: x[1], reverse=True)


def ntotal() -> int:
    """現在インデックスに登録されているエンティティ数。"""
    return _faiss_index.ntotal if _faiss_index else 0
