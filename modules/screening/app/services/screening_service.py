"""スクリーニングサービス。

検索優先順位:
  1. FAISS セマンティック類似度検索 → DB でエントリ照合
  2. FAISS/DB 不整合（FAISS がヒットするが DB に対応レコードなし）→ difflib フォールバック
  3. FAISS が空 → difflib で全件スキャン
"""

from __future__ import annotations

import uuid
from difflib import SequenceMatcher

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.screening import ScreeningResult, Watchlist
from app.schemas.screening import MatchDetail, ScreenRequest, ScreenResultResponse
from app.services import faiss_service


def _normalize(name: str) -> str:
    return " ".join(name.lower().split())


def _difflib_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _match_detail(entry: Watchlist, score: float) -> MatchDetail:
    return MatchDetail(
        watchlist_id=entry.id,
        entity_name=entry.entity_name,
        list_source=entry.list_source,
        score=round(score, 4),
        country=entry.country,
        risk_level=entry.risk_level,
        source_id=entry.source_id,
        reason=entry.reason,
    )


async def run_screening(
    request: ScreenRequest,
    db: AsyncSession,
    screened_by: uuid.UUID | None = None,
) -> ScreenResultResponse:
    """Watchlist に対してスクリーニングを実行し、結果を保存して返す。"""

    matches: list[MatchDetail] = []
    used_faiss = False

    # ── FAISS 検索 ─────────────────────────────────────────────────────────
    faiss_hits = faiss_service.search(request.company_name, top_k=30)

    if faiss_hits:
        hit_ids     = [h[0] for h in faiss_hits]
        score_by_id = {h[0]: h[1] for h in faiss_hits}

        result = await db.execute(
            select(Watchlist).where(
                Watchlist.id.in_([uuid.UUID(eid) for eid in hit_ids]),
                Watchlist.is_active == True,  # noqa: E712
            )
        )
        entries = result.scalars().all()

        if entries:
            used_faiss = True
            for entry in entries:
                score = score_by_id.get(str(entry.id), 0.0)
                if score >= request.threshold:
                    matches.append(_match_detail(entry, score))
        # entries が空 → FAISS/DB 不整合 → difflib フォールバックへ

    # ── Difflib フォールバック ─────────────────────────────────────────────
    # FAISS が空、または FAISS はヒットしたが DB にレコードが存在しない場合
    if not used_faiss:
        query_norm  = _normalize(request.company_name)
        # まず SQL ILIKE で候補を絞り込んで全件スキャンを避ける
        # (DB が大きい場合のパフォーマンス改善)
        pct = f"%{request.company_name.split()[0]}%"  # 先頭単語で絞り込み
        stmt = (
            select(Watchlist)
            .where(
                Watchlist.is_active == True,  # noqa: E712
                or_(
                    Watchlist.entity_name.ilike(pct),
                    # 先頭単語がヒットしない場合は全件返す (サブクエリ代替)
                )
            )
            .limit(2000)
        )
        result = await db.execute(stmt)
        candidates = result.scalars().all()

        # ILIKE で 0 件 → 全件スキャン（小規模 DB 想定）
        if not candidates:
            result = await db.execute(
                select(Watchlist).where(Watchlist.is_active == True)  # noqa: E712
            )
            candidates = result.scalars().all()

        for entry in candidates:
            score = _difflib_similarity(query_norm, _normalize(entry.entity_name))
            if entry.aliases:
                for alias in (entry.aliases if isinstance(entry.aliases, list) else [entry.aliases]):
                    score = max(score, _difflib_similarity(query_norm, _normalize(str(alias))))
            if score >= request.threshold:
                matches.append(_match_detail(entry, score))

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
