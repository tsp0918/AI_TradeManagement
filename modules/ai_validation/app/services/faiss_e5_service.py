"""
faiss_e5_service.py
─────────────────────────────────────────────────────────────────────────────
intfloat/multilingual-e5-large による Layer A / Layer B 静的 FAISS インデックス。

設計方針:
- モデルは 1 インスタンスのみ（メモリ ~2GB）、両レイヤー共有
- Layer A: 外為法 + ECCN 規制テキスト (2,040 vec, dim=1024)
- Layer B: US 特許チャンク (1,595 vec, dim=1024)
- インデックスは data/staging/ にある事前構築済みファイルをロード（再構築しない）
- クエリは必ず "query: {text}" プレフィックスを付与（e5-large 仕様）

公開インターフェース:
  preload()                       → 起動時に呼ぶ（main.py の on_startup）
  is_ready() -> bool              → ロード完了フラグ
  search_layer_a(query, top_k)    → List[LayerAHit]
  search_layer_b(query, top_k)    → List[LayerBHit]
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# ── 定数 ─────────────────────────────────────────────────────────────────────
_MODEL_NAME = "intfloat/multilingual-e5-large"
_QUERY_PREFIX = "query: "

def _staging_dir() -> Path:
    """data/staging/ の絶対パスを返す（modules/ai_validation 起点）。"""
    here = Path(__file__).resolve()
    # modules/ai_validation/app/services/faiss_e5_service.py
    # parents[0]=services/ [1]=app/ [2]=ai_validation/ [3]=modules/ [4]=project_root
    project_root = here.parents[4]
    return project_root / "data" / "staging"


_LAYER_A_INDEX = _staging_dir() / "layer_a.index"
_LAYER_A_META  = _staging_dir() / "layer_a_meta.json"
_LAYER_B_INDEX = _staging_dir() / "layer_b.index"
_LAYER_B_META  = _staging_dir() / "layer_b_meta.json"

# ── グローバルキャッシュ ──────────────────────────────────────────────────────
_model: SentenceTransformer | None = None
_layer_a_index: faiss.Index | None = None
_layer_a_records: list[dict[str, Any]] = []
_layer_b_index: faiss.Index | None = None
_layer_b_records: list[dict[str, Any]] = []
_ready: bool = False


# ── データクラス ──────────────────────────────────────────────────────────────
@dataclass
class LayerAHit:
    score: float
    faiss_id: int
    source_type: str        # law / parameter / tsutatsu / eccn
    source_name: str
    item_no: str
    item_label: str
    full_text: str
    embed_text: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LayerBHit:
    score: float
    faiss_id: int
    publication_number: str
    country_code: str
    ipc_codes: str
    title: str
    abstract: str
    fefta_items: list[str]
    has_fefta_mapping: bool
    embed_text: str


# ── ロード ────────────────────────────────────────────────────────────────────
def _load_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info("Loading model: %s", _MODEL_NAME)
        _model = SentenceTransformer(_MODEL_NAME)
        logger.info("Model loaded (dim=%d)", _model.get_sentence_embedding_dimension())
    return _model


def _load_layer_a() -> tuple[faiss.Index, list[dict]]:
    if not _LAYER_A_INDEX.exists() or not _LAYER_A_META.exists():
        raise FileNotFoundError(f"Layer A files not found: {_LAYER_A_INDEX}")
    index = faiss.read_index(str(_LAYER_A_INDEX))
    meta = json.loads(_LAYER_A_META.read_text(encoding="utf-8"))
    records = meta["records"] if isinstance(meta, dict) else meta
    logger.info("Layer A loaded: ntotal=%d", index.ntotal)
    return index, records


def _load_layer_b() -> tuple[faiss.Index, list[dict]]:
    if not _LAYER_B_INDEX.exists() or not _LAYER_B_META.exists():
        raise FileNotFoundError(f"Layer B files not found: {_LAYER_B_INDEX}")
    index = faiss.read_index(str(_LAYER_B_INDEX))
    meta = json.loads(_LAYER_B_META.read_text(encoding="utf-8"))
    records = meta["records"] if isinstance(meta, dict) else meta
    logger.info("Layer B loaded: ntotal=%d", index.ntotal)
    return index, records


def preload() -> None:
    """起動時に呼ぶ。モデルと両インデックスをメモリにロードする。"""
    global _layer_a_index, _layer_a_records, _layer_b_index, _layer_b_records, _ready
    _load_model()
    _layer_a_index, _layer_a_records = _load_layer_a()
    _layer_b_index, _layer_b_records = _load_layer_b()
    _ready = True


def is_ready() -> bool:
    return _ready


# ── エンコード ────────────────────────────────────────────────────────────────
def _encode_query(text: str) -> np.ndarray:
    model = _load_model()
    prefixed = _QUERY_PREFIX + text.strip()
    vec = model.encode([prefixed], normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vec, dtype="float32")


# ── 検索 ──────────────────────────────────────────────────────────────────────
def search_layer_a(query: str, top_k: int = 10) -> list[LayerAHit]:
    """
    Layer A（外為法 + ECCN）をクエリで検索する。

    Returns:
        スコア降順の LayerAHit リスト。インデックス未ロード時は空リスト。
    """
    if _layer_a_index is None or _layer_a_index.ntotal == 0:
        logger.warning("Layer A index not ready")
        return []

    query = (query or "").strip()
    if not query:
        return []

    qv = _encode_query(query)
    actual_k = min(top_k, _layer_a_index.ntotal)
    D, I = _layer_a_index.search(qv, actual_k)

    hits: list[LayerAHit] = []
    for score, idx in zip(D[0].tolist(), I[0].tolist()):
        if idx < 0 or idx >= len(_layer_a_records):
            continue
        r = _layer_a_records[idx]
        hits.append(LayerAHit(
            score=float(score),
            faiss_id=int(r.get("faiss_id", idx)),
            source_type=str(r.get("source_type", "")),
            source_name=str(r.get("source_name", "")),
            item_no=str(r.get("item_no", "")),
            item_label=str(r.get("item_label", "")),
            full_text=str(r.get("full_text", "")),
            embed_text=str(r.get("embed_text", "")),
            extra={k: v for k, v in r.items()
                   if k not in {"faiss_id", "source_type", "source_name",
                                "item_no", "item_label", "full_text", "embed_text"}},
        ))
    return hits


def search_layer_b(query: str, top_k: int = 10) -> list[LayerBHit]:
    """
    Layer B（US 特許）をクエリで検索する。

    Returns:
        スコア降順の LayerBHit リスト。インデックス未ロード時は空リスト。
    """
    if _layer_b_index is None or _layer_b_index.ntotal == 0:
        logger.warning("Layer B index not ready")
        return []

    query = (query or "").strip()
    if not query:
        return []

    qv = _encode_query(query)
    actual_k = min(top_k, _layer_b_index.ntotal)
    D, I = _layer_b_index.search(qv, actual_k)

    hits: list[LayerBHit] = []
    for score, idx in zip(D[0].tolist(), I[0].tolist()):
        if idx < 0 or idx >= len(_layer_b_records):
            continue
        r = _layer_b_records[idx]
        fefta_raw = r.get("fefta_items", [])
        hits.append(LayerBHit(
            score=float(score),
            faiss_id=int(r.get("faiss_id", idx)),
            publication_number=str(r.get("publication_number", "")),
            country_code=str(r.get("country_code", "")),
            ipc_codes=str(r.get("ipc_codes", "")),
            title=str(r.get("title", "")),
            abstract=str(r.get("abstract", "")),
            fefta_items=list(fefta_raw) if isinstance(fefta_raw, list) else [],
            has_fefta_mapping=bool(r.get("has_fefta_mapping", False)),
            embed_text=str(r.get("embed_text", "")),
        ))
    return hits


def ntotal_layer_a() -> int:
    return _layer_a_index.ntotal if _layer_a_index else 0


def ntotal_layer_b() -> int:
    return _layer_b_index.ntotal if _layer_b_index else 0
