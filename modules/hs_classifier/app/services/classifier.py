"""HS コード判定ロジック。"""
from __future__ import annotations

from app.models.schemas import ClassifyRequest, HSCandidate
from app.services import faiss_index


def classify(request: ClassifyRequest) -> list[HSCandidate]:
    """品目情報から HS コード候補を返す。"""
    parts = [request.item_name, request.item_description]
    if request.additional_keywords:
        parts.append(" ".join(request.additional_keywords))
    query = " ".join(p.strip() for p in parts if p.strip())

    results = faiss_index.search(query, top_k=request.top_k)

    return [
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
