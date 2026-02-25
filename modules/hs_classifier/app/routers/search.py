"""GET /search — セマンティック検索（同期）、GET /hs/{hs_code} — 詳細取得"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.models.schemas import HSCandidate, SearchResult
from app.services import faiss_index
from app.services import embedding as emb_svc

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResult)
def search_hs(
    q: str = Query(..., description="検索クエリ（日本語・英語どちらでも可）"),
    top_k: int = Query(default=10, ge=1, le=50),
):
    """HSコードをセマンティック検索する（同期・開発デバッグ用）。"""
    if not faiss_index._is_built():
        raise HTTPException(status_code=503, detail="FAISS インデックスが未構築です")

    results = faiss_index.search(q, top_k=top_k)
    candidates = [
        HSCandidate(
            rank=i + 1,
            hs_code=r["hs_code"],
            description_en=r["description_en"],
            section=r["section"],
            chapter=r["chapter"],
            chapter_description=r.get("chapter_description"),
            heading=r["heading"],
            heading_description=r.get("heading_description"),
            atlas_short_name=r.get("atlas_short_name"),
            similarity_score=round(r["similarity_score"], 4),
            hs2017_codes=r.get("hs2017_codes", []),
        )
        for i, r in enumerate(results)
    ]
    return SearchResult(query=q, results=candidates, index_size=faiss_index.ntotal())


@router.get("/hs/{hs_code}")
def get_hs_detail(hs_code: str):
    """HS コードの詳細情報を返す。"""
    if faiss_index._hs_data is None:
        raise HTTPException(status_code=503, detail="データが未ロードです")
    entry = next((e for e in faiss_index._hs_data if e.get("hs_code") == hs_code), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"HS コード {hs_code!r} が見つかりません")
    return entry
