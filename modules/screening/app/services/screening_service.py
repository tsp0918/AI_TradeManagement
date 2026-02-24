"""スクリーニングサービス。

FAISS セマンティック類似度検索でウォッチリストと照合する。
FAISS インデックスが空（ntotal==0）の場合は difflib でフォールバック。
"""

from __future__ import annotations

import uuid
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.screening import ScreeningResult, Watchlist
from app.schemas.screening import MatchDetail, ScreenRequest, ScreenResultResponse
from app.services import faiss_service


def _normalize(name: str) -> str:
    return " ".join(name.lower().split())


def _difflib_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


async def run_screening(
    request: ScreenRequest,
    db: AsyncSession,
    screened_by: uuid.UUID | None = None,
) -> ScreenResultResponse:
    """Watchlist に対してスクリーニングを実行し、結果を保存して返す。"""

    matches: list[MatchDetail] = []

    # ── FAISS 検索 ─────────────────────────────────────────────────────────
    faiss_hits = faiss_service.search(request.company_name, top_k=30)

    if faiss_hits:
        # FAISS が有効: ID でエントリを取得
        hit_ids  = [h[0] for h in faiss_hits]
        score_by_id = {h[0]: h[1] for h in faiss_hits}

        result = await db.execute(
            select(Watchlist).where(
                Watchlist.id.in_([uuid.UUID(eid) for eid in hit_ids]),
                Watchlist.is_active == True,  # noqa: E712
            )
        )
        entries = result.scalars().all()

        for entry in entries:
            score = score_by_id.get(str(entry.id), 0.0)
            if score < request.threshold:
                continue
            matches.append(
                MatchDetail(
                    watchlist_id=entry.id,
                    entity_name=entry.entity_name,
                    list_source=entry.list_source,
                    score=round(score, 4),
                    country=entry.country,
                    risk_level=entry.risk_level,
                    source_id=entry.source_id,
                    reason=entry.reason,
                )
            )

    else:
        # フォールバック: FAISS が空 → difflib で全件スキャン
        stmt = select(Watchlist).where(Watchlist.is_active == True)  # noqa: E712
        if request.country:
            from sqlalchemy import or_
            stmt = stmt.where(
                or_(Watchlist.country == request.country, Watchlist.country.is_(None))
            )
        result = await db.execute(stmt)
        entries = result.scalars().all()

        query_norm = _normalize(request.company_name)
        for entry in entries:
            score = _difflib_similarity(query_norm, _normalize(entry.entity_name))
            if entry.aliases:
                for alias in (entry.aliases if isinstance(entry.aliases, list) else [entry.aliases]):
                    score = max(score, _difflib_similarity(query_norm, _normalize(str(alias))))
            if score >= request.threshold:
                matches.append(
                    MatchDetail(
                        watchlist_id=entry.id,
                        entity_name=entry.entity_name,
                        list_source=entry.list_source,
                        score=round(score, 4),
                        country=entry.country,
                        risk_level=entry.risk_level,
                        source_id=entry.source_id,
                        reason=entry.reason,
                    )
                )

    matches.sort(key=lambda m: m.score, reverse=True)
    max_score = matches[0].score if matches else None

    if matches:
        status = "match" if (max_score or 0) >= 0.9 else "possible_match"
    else:
        status = "clear"

    screening_result = ScreeningResult(
        company_id=request.company_id,
        query_name=request.company_name,
        query_country=request.country,
        query_address=request.address,
        result_status=status,
        max_score=max_score,
        matches=[m.model_dump(mode="json") for m in matches],
        screened_by=screened_by,
    )
    db.add(screening_result)
    await db.flush()

    return ScreenResultResponse(
        id=screening_result.id,
        query_name=screening_result.query_name,
        query_country=screening_result.query_country,
        result_status=screening_result.result_status,
        max_score=screening_result.max_score,
        matches=matches,
        screened_at=screening_result.screened_at,
    )
