"""
faiss_search.py — FAISS 検索 HTTP エンドポイント

DAP や他モジュールが Layer A / B / C / D の検索を HTTP 経由で実行するための内部 API。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/faiss", tags=["faiss"])


# ── リクエスト/レスポンスモデル ────────────────────────────────────────────

class LayerAResult(BaseModel):
    score: float
    source_type: str
    source_name: str
    title: str
    full_text: str
    embed_text: str
    article_no: str = ""
    item_no: str = ""
    value_mm: float | None = None
    value_unit: str = ""


class LayerASearchResponse(BaseModel):
    query: str
    top_k: int
    hits: list[LayerAResult]
    error: str | None = None


class LayerBResult(BaseModel):
    score: float
    faiss_id: int
    publication_number: str
    country_code: str
    ipc_codes: str
    title: str
    abstract: str
    fefta_items: list[str]
    has_fefta_mapping: bool


class LayerBSearchResponse(BaseModel):
    query: str
    top_k: int
    hits: list[LayerBResult]
    error: str | None = None


class LayerDResult(BaseModel):
    score: float
    faiss_id: int
    paper_id: str
    title: str
    abstract_snippet: str
    year: int | None = None
    citation_count: int
    source: str
    eccn_tags: list[str]
    keywords_matched: str


class LayerDSearchResponse(BaseModel):
    query: str
    top_k: int
    hits: list[LayerDResult]
    error: str | None = None


# ── エンドポイント ─────────────────────────────────────────────────────────

@router.get("/search/layer-a", response_model=LayerASearchResponse)
def search_layer_a_endpoint(
    q: str = Query(..., description="検索クエリ"),
    top_k: int = Query(5, ge=1, le=20),
    source_type: str | None = Query(None, description="絞り込む source_type（例: nsg, ag, mtcr, fdpr）"),
) -> LayerASearchResponse:
    """
    FAISS Layer A（外為法 + ECCN 規制テキスト）を検索する。

    DAP などの他モジュールが Claude の RAG コンテキスト生成に使用する。
    source_type を指定すると、その種別のみに絞り込んだ結果を返す。
    """
    try:
        from platform_core.services.faiss_e5_service import search_layer_a, is_ready
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"FAISS service import error: {e}")

    if not is_ready():
        # Try to load lazily
        try:
            from platform_core.services.faiss_e5_service import preload
            preload(layers=frozenset({"a"}))
        except Exception:
            pass

    try:
        hits = search_layer_a(q, top_k=top_k, source_type=source_type)
    except Exception as e:
        logger.warning("Layer A search failed: %s", e)
        return LayerASearchResponse(query=q, top_k=top_k, hits=[], error=str(e))

    results = []
    for h in hits:
        # LayerAHit fields from faiss_e5_service
        r = h.__dict__ if hasattr(h, '__dict__') else {}
        results.append(LayerAResult(
            score=float(r.get("score", 0.0)),
            source_type=str(r.get("source_type", "")),
            source_name=str(r.get("source_name", "")),
            title=str(r.get("title", "") or r.get("item_label", "")),
            full_text=str(r.get("full_text", "") or r.get("chunk_text", "")),
            embed_text=str(r.get("embed_text", ""))[:300],
            article_no=str(r.get("article_no", "")),
            item_no=str(r.get("item_no", "")),
            value_mm=r.get("value_mm"),
            value_unit=str(r.get("value_unit", "")),
        ))

    return LayerASearchResponse(query=q, top_k=top_k, hits=results)


@router.get("/search/layer-b", response_model=LayerBSearchResponse)
def search_layer_b_endpoint(
    q: str = Query(..., description="検索クエリ"),
    top_k: int = Query(5, ge=1, le=20),
) -> LayerBSearchResponse:
    """FAISS Layer B（US 特許チャンク）を検索する。"""
    try:
        from platform_core.services.faiss_e5_service import search_layer_b, layer_b_available, preload
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"FAISS service import error: {e}")

    if not layer_b_available():
        try:
            preload(layers=frozenset({"b"}))
        except Exception:
            pass

    try:
        hits = search_layer_b(q, top_k=top_k)
    except Exception as e:
        logger.warning("Layer B search failed: %s", e)
        return LayerBSearchResponse(query=q, top_k=top_k, hits=[], error=str(e))

    results = []
    for h in hits:
        r = h.__dict__ if hasattr(h, "__dict__") else {}
        results.append(LayerBResult(
            score=float(r.get("score", 0.0)),
            faiss_id=int(r.get("faiss_id", 0)),
            publication_number=str(r.get("publication_number", "")),
            country_code=str(r.get("country_code", "")),
            ipc_codes=str(r.get("ipc_codes", "")),
            title=str(r.get("title", "")),
            abstract=str(r.get("abstract", ""))[:500],
            fefta_items=list(r.get("fefta_items", [])),
            has_fefta_mapping=bool(r.get("has_fefta_mapping", False)),
        ))
    return LayerBSearchResponse(query=q, top_k=top_k, hits=results)


class LayerCResult(BaseModel):
    score: float
    faiss_id: int
    hs_code: str
    hs_version: str
    description: str
    chapter: str
    heading: str
    subheading: str
    level: str
    category: str
    fefta_items: list[dict[str, Any]]
    has_fefta_mapping: bool


class LayerCSearchResponse(BaseModel):
    query: str
    top_k: int
    hits: list[LayerCResult]
    error: str | None = None


@router.get("/search/layer-c", response_model=LayerCSearchResponse)
def search_layer_c_endpoint(
    q: str = Query(..., description="検索クエリ（製品説明・HSコード等）"),
    top_k: int = Query(5, ge=1, le=20),
    fefta: str = Query(None, description="FEFTA 項番フィルター (カンマ区切り, 例: 7,10)"),
) -> LayerCSearchResponse:
    """FAISS Layer C（HS コード）を検索する。"""
    try:
        from platform_core.services.faiss_e5_service import search_layer_c, layer_c_available, preload
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"FAISS service import error: {e}")

    if not layer_c_available():
        try:
            preload(layers=frozenset({"c"}))
        except Exception:
            pass

    fefta_filter = [f.strip() for f in fefta.split(",") if f.strip()] if fefta else None

    try:
        hits = search_layer_c(q, top_k=top_k, fefta_filter=fefta_filter)
    except Exception as e:
        logger.warning("Layer C search failed: %s", e)
        return LayerCSearchResponse(query=q, top_k=top_k, hits=[], error=str(e))

    results = []
    for h in hits:
        r = h.__dict__ if hasattr(h, "__dict__") else {}
        results.append(LayerCResult(
            score=float(r.get("score", 0.0)),
            faiss_id=int(r.get("faiss_id", 0)),
            hs_code=str(r.get("hs_code", "")),
            hs_version=str(r.get("hs_version", "")),
            description=str(r.get("description", "")),
            chapter=str(r.get("chapter", "")),
            heading=str(r.get("heading", "")),
            subheading=str(r.get("subheading", "")),
            level=str(r.get("level", "")),
            category=str(r.get("category", "")),
            fefta_items=list(r.get("fefta_items", [])),
            has_fefta_mapping=bool(r.get("has_fefta_mapping", False)),
        ))
    return LayerCSearchResponse(query=q, top_k=top_k, hits=results)


@router.get("/search/layer-d", response_model=LayerDSearchResponse)
def search_layer_d_endpoint(
    q: str = Query(..., description="検索クエリ"),
    top_k: int = Query(5, ge=1, le=20),
    eccn: str = Query(None, description="ECCN フィルター (カンマ区切り, 例: 3A001,5A002)"),
) -> LayerDSearchResponse:
    """FAISS Layer D（学術論文アブストラクト）を検索する。"""
    try:
        from platform_core.services.faiss_e5_service import search_layer_d, layer_d_available, preload
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"FAISS service import error: {e}")

    if not layer_d_available():
        try:
            preload(layers=frozenset({"d"}))
        except Exception:
            pass

    eccn_filter = [e.strip() for e in eccn.split(",") if e.strip()] if eccn else None

    try:
        hits = search_layer_d(q, top_k=top_k, eccn_filter=eccn_filter)
    except Exception as e:
        logger.warning("Layer D search failed: %s", e)
        return LayerDSearchResponse(query=q, top_k=top_k, hits=[], error=str(e))

    results = []
    for h in hits:
        r = h.__dict__ if hasattr(h, "__dict__") else {}
        results.append(LayerDResult(
            score=float(r.get("score", 0.0)),
            faiss_id=int(r.get("faiss_id", 0)),
            paper_id=str(r.get("paper_id", "")),
            title=str(r.get("title", "")),
            abstract_snippet=str(r.get("abstract_snippet", "")),
            year=r.get("year"),
            citation_count=int(r.get("citation_count", 0)),
            source=str(r.get("source", "s2")),
            eccn_tags=list(r.get("eccn_tags", [])),
            keywords_matched=str(r.get("keywords_matched", "")),
        ))
    return LayerDSearchResponse(query=q, top_k=top_k, hits=results)


class LayerEResult(BaseModel):
    score: float
    doc_id: str
    source_type: str
    category: str
    title: str
    subtitle: str
    keywords: list[str]
    eccn_relevance: list[str]
    fefta_relevance: list[str]
    strategic_axis: list[str]
    full_text: str


class LayerESearchResponse(BaseModel):
    query: str
    top_k: int
    hits: list[LayerEResult]
    error: str | None = None


@router.get("/search/layer-e", response_model=LayerESearchResponse)
def search_layer_e_endpoint(
    q: str = Query(..., description="戦略的・哲学的クエリ（例: なぜ輸出管理が重要か、量子の安保意義）"),
    top_k: int = Query(5, ge=1, le=15),
    axis: str = Query(None, description="strategic_axis フィルター（例: 技術哲学,経済安保）"),
) -> LayerESearchResponse:
    """FAISS Layer E（政策・戦略文書 / 技術哲学）を検索する。

    DAP の「なぜ？」「背景を教えて」モードで使用する戦略知識レイヤー。
    通常の業務判定検索（Layer A/B/C）とは独立した on-demand 検索。
    """
    try:
        from platform_core.services.faiss_e5_service import search_layer_e, layer_e_available, preload
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"FAISS service import error: {e}")

    if not layer_e_available():
        try:
            preload(layers=frozenset({"e"}))
        except Exception:
            pass

    axis_filter = [a.strip() for a in axis.split(",") if a.strip()] if axis else None

    try:
        hits = search_layer_e(q, top_k=top_k, axis_filter=axis_filter)
    except Exception as e:
        logger.warning("Layer E search failed: %s", e)
        return LayerESearchResponse(query=q, top_k=top_k, hits=[], error=str(e))

    results = []
    for h in hits:
        r = h.__dict__ if hasattr(h, "__dict__") else {}
        results.append(LayerEResult(
            score=float(r.get("score", 0.0)),
            doc_id=str(r.get("doc_id", "")),
            source_type=str(r.get("source_type", "")),
            category=str(r.get("category", "")),
            title=str(r.get("title", "")),
            subtitle=str(r.get("subtitle", "")),
            keywords=list(r.get("keywords") or []),
            eccn_relevance=list(r.get("eccn_relevance") or []),
            fefta_relevance=list(r.get("fefta_relevance") or []),
            strategic_axis=list(r.get("strategic_axis") or []),
            full_text=str(r.get("full_text", ""))[:600],
        ))
    return LayerESearchResponse(query=q, top_k=top_k, hits=results)


# ── Layer F: 制裁執行事例（Phase 29） ─────────────────────────────────────────
class LayerFResult(BaseModel):
    score: float
    case_id: str
    source_type: str
    entity: str
    action_date: str
    authority: str
    risk_level: str
    penalty_usd: int | None
    keywords: list[str]
    related_eccn: list[str]
    full_text: str


class LayerFSearchResponse(BaseModel):
    query: str
    top_k: int
    hits: list[LayerFResult]
    error: str | None = None


@router.get("/search/layer-f", response_model=LayerFSearchResponse)
def search_layer_f_endpoint(
    q: str = Query(..., description="制裁事例クエリ（企業名・違反パターン・規制品目等）"),
    top_k: int = Query(5, ge=1, le=15),
    source_type: str = Query(None, description="ofac_sdn / bis_denial / bis_civil_penalty / itar_consent / meti_action"),
) -> LayerFSearchResponse:
    """FAISS Layer F（制裁執行事例・BIS Denial・ITAR同意令）を検索する。Phase 29。"""
    try:
        from platform_core.services.faiss_e5_service import search_layer_f, layer_f_available, preload
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"FAISS service import error: {e}")

    if not layer_f_available():
        try:
            preload(layers=frozenset({"f"}))
        except Exception:
            pass

    try:
        hits = search_layer_f(q, top_k=top_k, source_type_filter=source_type or None)
    except Exception as e:
        logger.warning("Layer F search failed: %s", e)
        return LayerFSearchResponse(query=q, top_k=top_k, hits=[], error=str(e))

    results = []
    for h in hits:
        r = h.__dict__ if hasattr(h, "__dict__") else {}
        results.append(LayerFResult(
            score=float(r.get("score", 0.0)),
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
    return LayerFSearchResponse(query=q, top_k=top_k, hits=results)
