"""FAISS による企業名セマンティック類似度検索サービス。

ai_validation の patent_retrieve.py と同パターン。
モデル: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384 次元)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# ── エイリアス正規化 ──────────────────────────────────────────────────────────

_CORP_SUFFIX_RE = re.compile(
    r"\b(?:"
    r"co(?:mpany|rporation|\.?\s*ltd\.?)?\.?|"
    r"corp(?:oration)?\.?|"
    r"ltd\.?|"
    r"inc(?:orporated)?\.?|"
    r"llc\.?|"
    r"gmbh\.?|"
    r"s\.?\s*a\.?|"   # S.A.
    r"b\.?\s*v\.?|"   # B.V.
    r"plc\.?|"
    r"pte\.?\s*ltd\.?|"
    r"株式会社|有限会社|合同会社|合資会社|合名会社"
    r")\b",
    re.IGNORECASE,
)


def _normalize_query(name: str) -> str:
    """企業名からよくある法人格表記を除去して正規化する（クエリ専用）。"""
    normalized = _CORP_SUFFIX_RE.sub(" ", name)
    return " ".join(normalized.split())

_INDEX_DIR  = Path(__file__).resolve().parents[3] / "data" / "faiss"
_INDEX_PATH = _INDEX_DIR / "entities.index"
_META_PATH  = _INDEX_DIR / "entities_meta.json"

# ── グローバルキャッシュ ────────────────────────────────────────────────────
_faiss_index: faiss.Index | None = None
_faiss_meta:  list[dict]   | None = None
_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    """モデルをグローバルキャッシュしてリクエスト毎の再ロードを防ぐ。"""
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("SentenceTransformer model loaded: %s", MODEL_NAME)
    return _model


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
    model = _get_model()
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
    texts_list = list(texts_k)

    # 大量エンティティでのメモリクラッシュを防ぐため、明示的にバッチ処理する
    ENCODE_BATCH = 256
    all_emb: list[np.ndarray] = []
    for start in range(0, len(texts_list), ENCODE_BATCH):
        chunk = texts_list[start: start + ENCODE_BATCH]
        chunk_emb = model.encode(chunk, normalize_embeddings=True, show_progress_bar=False,
                                 batch_size=64)
        all_emb.append(np.asarray(chunk_emb, dtype="float32"))
        logger.debug("Encoded %d / %d", min(start + ENCODE_BATCH, len(texts_list)), len(texts_list))
    emb = np.vstack(all_emb)
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

    # 法人格サフィックスを除去して正規化（精度向上）
    normalized = _normalize_query(query)
    if not normalized:
        normalized = query

    model = _get_model()
    qv = model.encode([normalized], normalize_embeddings=True, show_progress_bar=False)
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
