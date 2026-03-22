"""
faiss_search.py — FAISS 検索 HTTP エンドポイント

DAP や他モジュールが Layer A / C の検索を HTTP 経由で実行するための内部 API。
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


# ── エンドポイント ─────────────────────────────────────────────────────────

@router.get("/search/layer-a", response_model=LayerASearchResponse)
def search_layer_a_endpoint(
    q: str = Query(..., description="検索クエリ"),
    top_k: int = Query(5, ge=1, le=20),
) -> LayerASearchResponse:
    """
    FAISS Layer A（外為法 + ECCN 規制テキスト）を検索する。

    DAP などの他モジュールが Claude の RAG コンテキスト生成に使用する。
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
        hits = search_layer_a(q, top_k=top_k)
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
