"""
faiss_e5_service.py  (platform_core 共有版)
─────────────────────────────────────────────────────────────────────────────
intfloat/multilingual-e5-large による Layer A / B / C 静的 FAISS インデックス。

設計方針:
- モデルは 1 インスタンスのみ（メモリ ~2GB）、全レイヤー共有
- Layer A: 外為法 + ECCN 規制テキスト (2,040 vec, dim=1024)
- Layer B: US 特許チャンク (1,595 vec, dim=1024)
- Layer C: HS コード (5,476 vec, dim=1024)
- インデックスは data/staging/ にある事前構築済みファイルをロード（再構築しない）
- クエリは必ず "query: {text}" プレフィックスを付与（e5-large 仕様）
- Layer C が存在しない場合は警告のみ（A/B は通常通り使用可）

公開インターフェース:
  preload(layers=None)            → 起動時に呼ぶ。layers=frozenset({"c"}) で Layer C のみも可
  is_ready() -> bool              → ロード完了フラグ
  search_layer_a(query, top_k)    → List[LayerAHit]
  search_layer_b(query, top_k)    → List[LayerBHit]
  search_layer_c(query, top_k, fefta_filter)  → List[LayerCHit]
  lookup_layer_c(hs_code)         → dict | None  (hs_code → メタデータ)
"""
from __future__ import annotations

import json
import logging
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
    """data/staging/ の絶対パスを返す（platform-core 起点）。"""
    here = Path(__file__).resolve()
    # platform-core/platform_core/services/faiss_e5_service.py
    # parents[0]=services/ [1]=platform_core/ [2]=platform-core/ [3]=project_root
    project_root = here.parents[3]
    return project_root / "data" / "staging"


def get_staging_dir() -> Path:
    """data/staging/ の絶対パスを返す（公開 API）。"""
    return _staging_dir()


_LAYER_A_INDEX = _staging_dir() / "layer_a.index"
_LAYER_A_META  = _staging_dir() / "layer_a_meta.json"
_LAYER_B_INDEX = _staging_dir() / "layer_b.index"
_LAYER_B_META  = _staging_dir() / "layer_b_meta.json"
_LAYER_C_INDEX = _staging_dir() / "layer_c.index"
_LAYER_C_META  = _staging_dir() / "layer_c_meta.json"

# ── グローバルキャッシュ ──────────────────────────────────────────────────────
_model: SentenceTransformer | None = None
_layer_a_index: faiss.Index | None = None
_layer_a_records: list[dict[str, Any]] = []
_layer_b_index: faiss.Index | None = None
_layer_b_records: list[dict[str, Any]] = []
_layer_c_index: faiss.Index | None = None
_layer_c_records: list[dict[str, Any]] = []
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


@dataclass
class LayerCHit:
    score: float
    faiss_id: int
    hs_code: str
    hs_version: str
    description: str
    chapter: str
    heading: str
    subheading: str
    level: str              # heading / subheading
    fefta_items: list[dict[str, Any]]   # [{"item_no": "7", "item_label": "..."}]
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


def _load_layer_c() -> tuple[faiss.Index, list[dict]] | None:
    """Layer C は optional。ファイルがなければ None を返す（警告のみ）。"""
    if not _LAYER_C_INDEX.exists() or not _LAYER_C_META.exists():
        logger.warning("Layer C files not found — HS search unavailable: %s", _LAYER_C_INDEX)
        return None
    index = faiss.read_index(str(_LAYER_C_INDEX))
    meta = json.loads(_LAYER_C_META.read_text(encoding="utf-8"))
    records = meta["records"] if isinstance(meta, dict) else meta
    logger.info("Layer C loaded: ntotal=%d", index.ntotal)
    return index, records


def preload(layers: frozenset[str] | None = None) -> None:
    """起動時に呼ぶ。モデルと指定レイヤーをメモリにロードする。

    Args:
        layers: ロードするレイヤーのセット。None（デフォルト）は全レイヤー。
                例: frozenset({"c"}) → Layer C のみ（hs_classifier 用）
                    frozenset({"a", "b", "c"}) → 全レイヤー（ai_validation 用）
    """
    global _layer_a_index, _layer_a_records
    global _layer_b_index, _layer_b_records
    global _layer_c_index, _layer_c_records
    global _ready

    if layers is None:
        layers = frozenset({"a", "b", "c"})

    _load_model()

    if "a" in layers:
        _layer_a_index, _layer_a_records = _load_layer_a()
    if "b" in layers:
        _layer_b_index, _layer_b_records = _load_layer_b()
    if "c" in layers:
        result_c = _load_layer_c()
        if result_c is not None:
            _layer_c_index, _layer_c_records = result_c

    _ready = True


def is_ready() -> bool:
    return _ready


def layer_c_available() -> bool:
    return _layer_c_index is not None and _layer_c_index.ntotal > 0


# ── エンコード ────────────────────────────────────────────────────────────────
def _encode_query(text: str) -> np.ndarray:
    model = _load_model()
    prefixed = _QUERY_PREFIX + text.strip()
    vec = model.encode([prefixed], normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vec, dtype="float32")


# ── 検索 ──────────────────────────────────────────────────────────────────────
def search_layer_a(query: str, top_k: int = 10) -> list[LayerAHit]:
    """Layer A（外為法 + ECCN）をクエリで検索する。"""
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
    """Layer B（US 特許）をクエリで検索する。"""
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


def search_layer_c(
    query: str,
    top_k: int = 10,
    fefta_filter: list[str] | None = None,
) -> list[LayerCHit]:
    """Layer C（HS コード）をクエリで検索する。

    Args:
        query:         製品説明テキスト（日英どちらも可）
        top_k:         返す件数
        fefta_filter:  FEFTA 項番リスト。指定すると該当 item_no を持つ結果のみ返す。
                       例: ["7", "10"] → 外為法7号・10号に対応する HS コードに絞り込む
    """
    if _layer_c_index is None or _layer_c_index.ntotal == 0:
        logger.warning("Layer C index not ready (run scripts/build_layer_c.py)")
        return []
    query = (query or "").strip()
    if not query:
        return []
    qv = _encode_query(query)
    fetch_k = min(top_k * 5 if fefta_filter else top_k, _layer_c_index.ntotal)
    D, I = _layer_c_index.search(qv, fetch_k)

    hits: list[LayerCHit] = []
    for score, idx in zip(D[0].tolist(), I[0].tolist()):
        if idx < 0 or idx >= len(_layer_c_records):
            continue
        r = _layer_c_records[idx]
        fefta_items = r.get("fefta_items") or []
        if fefta_filter:
            item_nos = {str(f.get("item_no", "")) for f in fefta_items}
            if not item_nos.intersection(set(fefta_filter)):
                continue
        hits.append(LayerCHit(
            score=float(score),
            faiss_id=int(r.get("faiss_id", idx)),
            hs_code=str(r.get("hs_code", "")),
            hs_version=str(r.get("hs_version", "")),
            description=str(r.get("description", "")),
            chapter=str(r.get("chapter", "")),
            heading=str(r.get("heading", "")),
            subheading=str(r.get("subheading", "")),
            level=str(r.get("level", "")),
            fefta_items=list(fefta_items),
            has_fefta_mapping=bool(r.get("has_fefta_mapping", False)),
            embed_text=str(r.get("embed_text", "")),
        ))
        if len(hits) >= top_k:
            break
    return hits


def lookup_layer_c(hs_code: str) -> dict[str, Any] | None:
    """hs_code からメタデータを直接引く（O(n) 線形探索）。"""
    for r in _layer_c_records:
        if r.get("hs_code") == hs_code:
            return r
    return None


def ntotal_layer_a() -> int:
    return _layer_a_index.ntotal if _layer_a_index else 0


def ntotal_layer_b() -> int:
    return _layer_b_index.ntotal if _layer_b_index else 0


def ntotal_layer_c() -> int:
    return _layer_c_index.ntotal if _layer_c_index else 0
