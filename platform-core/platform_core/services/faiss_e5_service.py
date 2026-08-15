"""
faiss_e5_service.py  (platform_core 共有版)
─────────────────────────────────────────────────────────────────────────────
intfloat/multilingual-e5-large による Layer A / B / C / D / E 静的 FAISS インデックス。

設計方針:
- モデルは 1 インスタンスのみ（メモリ ~2GB）、全レイヤー共有
- Layer A: 外為法 + ECCN 規制テキスト (2,343 vec, dim=1024)
- Layer B: US 特許チャンク (10,783 vec, dim=1024)
- Layer C: HS コード (5,476 vec, dim=1024)
- Layer D: 学術論文アブストラクト (academic_intel.db から構築, dim=1024)
- Layer E: 政策・戦略文書 / 技術哲学 (61 vec, dim=1024) — 「なぜ？」に答える知識層
- インデックスは data/staging/ にある事前構築済みファイルをロード（再構築しない）
- クエリは必ず "query: {text}" プレフィックスを付与（e5-large 仕様）
- Layer C / D / E が存在しない場合は警告のみ（A/B は通常通り使用可）

公開インターフェース:
  preload(layers=None)            → 起動時に呼ぶ。layers=frozenset({"c"}) で Layer C のみも可
  is_ready() -> bool              → ロード完了フラグ
  search_layer_a(query, top_k)    → List[LayerAHit]
  search_layer_b(query, top_k)    → List[LayerBHit]
  search_layer_c(query, top_k, fefta_filter)  → List[LayerCHit]
  lookup_layer_c(hs_code)         → dict | None  (hs_code → メタデータ)
  search_layer_d(query, top_k, eccn_filter)   → List[LayerDHit]
  search_layer_e(query, top_k, axis_filter)   → List[LayerEHit]  (新設)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# macOS でのスレッド競合による SIGSEGV を防ぐ
# HuggingFace tokenizer のマルチプロセス fork を無効化し、
# PyTorch/OpenMP のスレッド数を 1 に固定する
if sys.platform == "darwin":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("OMP_NUM_THREADS", "1")

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
_LAYER_D_INDEX = _staging_dir() / "layer_d.index"
_LAYER_D_META  = _staging_dir() / "layer_d_meta.json"
_LAYER_E_INDEX = _staging_dir() / "layer_e.index"
_LAYER_E_META  = _staging_dir() / "layer_e_meta.json"
_LAYER_F_INDEX = _staging_dir() / "layer_f.index"
_LAYER_F_META  = _staging_dir() / "layer_f_meta.json"

# ── グローバルキャッシュ ──────────────────────────────────────────────────────
_model: SentenceTransformer | None = None
_layer_a_index: faiss.Index | None = None
_layer_a_records: list[dict[str, Any]] = []
_layer_b_index: faiss.Index | None = None
_layer_b_records: list[dict[str, Any]] = []
_layer_c_index: faiss.Index | None = None
_layer_c_records: list[dict[str, Any]] = []
_layer_d_index: faiss.Index | None = None
_layer_d_records: list[dict[str, Any]] = []
_layer_e_index: faiss.Index | None = None
_layer_e_records: list[dict[str, Any]] = []
_layer_f_index: faiss.Index | None = None
_layer_f_records: list[dict[str, Any]] = []
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


@dataclass
class LayerDHit:
    score: float
    faiss_id: int
    paper_id: str
    title: str
    abstract_snippet: str
    year: int | None
    citation_count: int
    influential_citation_count: int
    source: str             # "s2" or "lens"
    eccn_tags: list[str]    # ["3A001", "5A002"]
    keywords_matched: str


@dataclass
class LayerEHit:
    score: float
    faiss_id: int
    doc_id: str
    source_type: str        # policy_doc / emerging_tech / keizai_anpo / geopolitical_sc
    category: str
    title: str
    subtitle: str
    keywords: list[str]
    eccn_relevance: list[str]
    fefta_relevance: list[str]
    strategic_axis: list[str]
    full_text: str


@dataclass
class LayerFHit:
    score: float
    faiss_id: int
    case_id: str
    source_type: str        # ofac_sdn / bis_denial / bis_civil_penalty / itar_consent / meti_action
    entity: str
    action_date: str
    authority: str
    risk_level: str
    penalty_usd: int | None
    keywords: list[str]
    related_eccn: list[str]
    full_text: str


# ── ロード ────────────────────────────────────────────────────────────────────
def _load_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info("Loading model: %s", _MODEL_NAME)
        _model = SentenceTransformer(_MODEL_NAME)
        if sys.platform == "darwin":
            try:
                import torch
                torch.set_num_threads(1)
            except Exception:
                pass
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


def _load_layer_d() -> tuple[faiss.Index, list[dict]] | None:
    """Layer D は optional。ファイルがなければ None を返す（警告のみ）。"""
    if not _LAYER_D_INDEX.exists() or not _LAYER_D_META.exists():
        logger.warning("Layer D files not found — academic search unavailable: %s", _LAYER_D_INDEX)
        return None
    index = faiss.read_index(str(_LAYER_D_INDEX))
    meta = json.loads(_LAYER_D_META.read_text(encoding="utf-8"))
    records = meta["records"] if isinstance(meta, dict) else meta
    logger.info("Layer D loaded: ntotal=%d", index.ntotal)
    return index, records


def _load_layer_e() -> tuple[faiss.Index, list[dict]] | None:
    """Layer E（政策・戦略文書）は optional。"""
    if not _LAYER_E_INDEX.exists() or not _LAYER_E_META.exists():
        logger.warning("Layer E files not found — strategic search unavailable: %s", _LAYER_E_INDEX)
        return None
    index = faiss.read_index(str(_LAYER_E_INDEX))
    meta = json.loads(_LAYER_E_META.read_text(encoding="utf-8"))
    records = meta["records"] if isinstance(meta, dict) else meta
    logger.info("Layer E loaded: ntotal=%d", index.ntotal)
    return index, records


def _load_layer_f() -> tuple[faiss.Index, list[dict]] | None:
    """Layer F（制裁執行事例）は optional。Phase 29 新設。"""
    if not _LAYER_F_INDEX.exists() or not _LAYER_F_META.exists():
        logger.warning("Layer F files not found — sanctions search unavailable: %s", _LAYER_F_INDEX)
        return None
    index = faiss.read_index(str(_LAYER_F_INDEX))
    meta = json.loads(_LAYER_F_META.read_text(encoding="utf-8"))
    records = meta["records"] if isinstance(meta, dict) else meta
    logger.info("Layer F loaded: ntotal=%d", index.ntotal)
    return index, records


def preload(layers: frozenset[str] | None = None) -> None:
    """起動時に呼ぶ。モデルと指定レイヤーをメモリにロードする。

    Args:
        layers: ロードするレイヤーのセット。None（デフォルト）は全レイヤー。
                例: frozenset({"c"}) → Layer C のみ（hs_classifier 用）
                    frozenset({"a", "b", "c", "d", "e", "f"}) → 全レイヤー（Layer F 含む）
    """
    global _layer_a_index, _layer_a_records
    global _layer_b_index, _layer_b_records
    global _layer_c_index, _layer_c_records
    global _layer_d_index, _layer_d_records
    global _layer_e_index, _layer_e_records
    global _layer_f_index, _layer_f_records
    global _ready

    if layers is None:
        layers = frozenset({"a", "b", "c", "d", "e", "f"})

    _load_model()

    if "a" in layers:
        _layer_a_index, _layer_a_records = _load_layer_a()
    if "b" in layers:
        _layer_b_index, _layer_b_records = _load_layer_b()
    if "c" in layers:
        result_c = _load_layer_c()
        if result_c is not None:
            _layer_c_index, _layer_c_records = result_c
    if "d" in layers:
        result_d = _load_layer_d()
        if result_d is not None:
            _layer_d_index, _layer_d_records = result_d
    if "e" in layers:
        result_e = _load_layer_e()
        if result_e is not None:
            _layer_e_index, _layer_e_records = result_e
    if "f" in layers:
        result_f = _load_layer_f()
        if result_f is not None:
            _layer_f_index, _layer_f_records = result_f

    _ready = True


def is_ready() -> bool:
    return _ready


def layer_b_available() -> bool:
    return _layer_b_index is not None and _layer_b_index.ntotal > 0


def layer_c_available() -> bool:
    return _layer_c_index is not None and _layer_c_index.ntotal > 0


def layer_d_available() -> bool:
    return _layer_d_index is not None and _layer_d_index.ntotal > 0


def layer_e_available() -> bool:
    return _layer_e_index is not None and _layer_e_index.ntotal > 0


def layer_f_available() -> bool:
    return _layer_f_index is not None and _layer_f_index.ntotal > 0


# ── エンコード ────────────────────────────────────────────────────────────────
def _encode_query(text: str) -> np.ndarray:
    model = _load_model()
    prefixed = _QUERY_PREFIX + text.strip()
    vec = model.encode([prefixed], normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vec, dtype="float32")


# ── 検索 ──────────────────────────────────────────────────────────────────────
def search_layer_a(query: str, top_k: int = 10, source_type: str | None = None) -> list[LayerAHit]:
    """Layer A（外為法 + ECCN）をクエリで検索する。
    source_type を指定した場合はその種別のみを返す（FAISS 検索後にフィルタリング）。
    """
    if _layer_a_index is None or _layer_a_index.ntotal == 0:
        logger.warning("Layer A index not ready")
        return []
    query = (query or "").strip()
    if not query:
        return []
    qv = _encode_query(query)
    # source_type フィルターがある場合は多めに取得してフィルタリング
    fetch_k = min(top_k * 5 if source_type else top_k, _layer_a_index.ntotal)
    D, I = _layer_a_index.search(qv, fetch_k)

    hits: list[LayerAHit] = []
    for score, idx in zip(D[0].tolist(), I[0].tolist()):
        if idx < 0 or idx >= len(_layer_a_records):
            continue
        r = _layer_a_records[idx]
        if source_type and r.get("source_type", "") != source_type:
            continue
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
        if len(hits) >= top_k:
            break
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


def ntotal_layer_d() -> int:
    return _layer_d_index.ntotal if _layer_d_index else 0


def ntotal_layer_e() -> int:
    return _layer_e_index.ntotal if _layer_e_index else 0


def search_layer_d(
    query: str,
    top_k: int = 10,
    eccn_filter: list[str] | None = None,
) -> list[LayerDHit]:
    """Layer D（学術論文）をクエリで検索する。

    Args:
        query:        製品・技術説明テキスト（日英どちらも可）
        top_k:        返す件数
        eccn_filter:  ECCN リスト。指定すると該当 ECCN タグを持つ結果のみ返す。
                      例: ["3A001", "5A002"]
    """
    if _layer_d_index is None or _layer_d_index.ntotal == 0:
        logger.warning("Layer D index not ready (run scripts/build_layer_d.py)")
        return []
    query = (query or "").strip()
    if not query:
        return []
    qv = _encode_query(query)
    fetch_k = min(top_k * 5 if eccn_filter else top_k, _layer_d_index.ntotal)
    D, I = _layer_d_index.search(qv, fetch_k)

    hits: list[LayerDHit] = []
    for score, idx in zip(D[0].tolist(), I[0].tolist()):
        if idx < 0 or idx >= len(_layer_d_records):
            continue
        r = _layer_d_records[idx]
        eccn_tags = r.get("eccn_tags") or []
        if eccn_filter:
            if not any(e in eccn_tags for e in eccn_filter):
                continue
        hits.append(LayerDHit(
            score=float(score),
            faiss_id=int(r.get("faiss_id", idx)),
            paper_id=str(r.get("paper_id", "")),
            title=str(r.get("title", "")),
            abstract_snippet=str(r.get("abstract_snippet", "")),
            year=r.get("year"),
            citation_count=int(r.get("citation_count", 0)),
            influential_citation_count=int(r.get("influential_citation_count", 0)),
            source=str(r.get("source", "s2")),
            eccn_tags=eccn_tags,
            keywords_matched=str(r.get("keywords_matched", "")),
        ))
        if len(hits) >= top_k:
            break
    return hits


def search_layer_e(
    query: str,
    top_k: int = 5,
    axis_filter: list[str] | None = None,
) -> list[LayerEHit]:
    """Layer E（政策・戦略文書 / 技術哲学）をクエリで検索する。

    DAP の「なぜ？」「背景を教えて」「哲学的に説明して」等の問いに応答するための
    戦略知識検索レイヤー。通常の業務検索では使用しない（on-demand）。

    Args:
        query:        「なぜ輸出管理が重要か」等の文脈・哲学・戦略的問い
        top_k:        返す件数（デフォルト5）
        axis_filter:  strategic_axis によるフィルタ。
                      例: ["経済安保"] → 経済安保軸の文書のみ返す
    """
    if _layer_e_index is None or _layer_e_index.ntotal == 0:
        logger.warning("Layer E index not ready (run scripts/build_layer_e.py)")
        return []
    query = (query or "").strip()
    if not query:
        return []
    qv = _encode_query(query)
    fetch_k = min(top_k * 5 if axis_filter else top_k, _layer_e_index.ntotal)
    D, I = _layer_e_index.search(qv, fetch_k)

    hits: list[LayerEHit] = []
    for score, idx in zip(D[0].tolist(), I[0].tolist()):
        if idx < 0 or idx >= len(_layer_e_records):
            continue
        r = _layer_e_records[idx]
        axes = r.get("strategic_axis") or []
        if axis_filter:
            if not any(ax in axes for ax in axis_filter):
                continue
        hits.append(LayerEHit(
            score=float(score),
            faiss_id=int(idx),
            doc_id=str(r.get("doc_id", "")),
            source_type=str(r.get("source_type", "")),
            category=str(r.get("category", "")),
            title=str(r.get("title", "")),
            subtitle=str(r.get("subtitle", "")),
            keywords=list(r.get("keywords") or []),
            eccn_relevance=list(r.get("eccn_relevance") or []),
            fefta_relevance=list(r.get("fefta_relevance") or []),
            strategic_axis=list(axes),
            full_text=str(r.get("full_text", ""))[:500],
        ))
        if len(hits) >= top_k:
            break
    return hits


def search_layer_f(
    query: str,
    top_k: int = 5,
    source_type_filter: str | None = None,
) -> list[LayerFHit]:
    """Layer F（制裁執行事例・BIS Denial・ITAR同意令）をクエリで検索する。Phase 29。

    Args:
        query:              制裁事例の検索クエリ（企業名・規制品目・違反パターン等）
        top_k:              返す件数（デフォルト5）
        source_type_filter: ofac_sdn / bis_denial / bis_civil_penalty /
                            itar_consent / meti_action でフィルタ
    """
    if _layer_f_index is None or _layer_f_index.ntotal == 0:
        logger.warning("Layer F index not ready (run scripts/build_layer_f.py)")
        return []
    query = (query or "").strip()
    if not query:
        return []
    qv = _encode_query(query)
    fetch_k = min(top_k * 4 if source_type_filter else top_k, _layer_f_index.ntotal)
    D, I = _layer_f_index.search(qv, fetch_k)

    hits: list[LayerFHit] = []
    for score, idx in zip(D[0].tolist(), I[0].tolist()):
        if idx < 0 or idx >= len(_layer_f_records):
            continue
        r = _layer_f_records[idx]
        if source_type_filter and r.get("source_type") != source_type_filter:
            continue
        hits.append(LayerFHit(
            score=float(score),
            faiss_id=int(idx),
            case_id=str(r.get("case_id", "")),
            source_type=str(r.get("source_type", "")),
            entity=str(r.get("entity", "")),
            action_date=str(r.get("action_date", "")),
            authority=str(r.get("authority", "")),
            risk_level=str(r.get("risk_level", "medium")),
            penalty_usd=r.get("penalty_usd"),
            keywords=list(r.get("keywords") or []),
            related_eccn=list(r.get("related_eccn") or []),
            full_text=str(r.get("full_text", ""))[:600],
        ))
        if len(hits) >= top_k:
            break
    return hits
