"""スクリーニングサービス。

検索優先順位:
  0. SQL ILIKE 直接一致（短名・頭字語 → "ZTE" が "ZTE Corporation" を捕捉）
  1. FAISS セマンティック類似度検索 → DB でエントリ照合
  2. FAISS/DB 不整合（FAISS がヒットするが DB に対応レコードなし）→ difflib フォールバック
  3. FAISS が空 → difflib で全件スキャン

スコアリングは法人格サフィックス（Corp/Ltd/Inc など）を除去した文字列で行い
"Sony Corporation" vs "ZTE Corporation" のような偽陽性を防ぐ。
"""

from __future__ import annotations

import uuid
from difflib import SequenceMatcher

from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.screening import ScreeningResult, Watchlist
from app.schemas.screening import MatchDetail, ScreenRequest, ScreenResultResponse
from app.services import faiss_service


def _strip(name: str) -> str:
    """faiss_service の _normalize_query と同じロジックで法人格サフィックスを除去する。"""
    return " ".join(faiss_service._normalize_query(name).lower().split())


def _difflib_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _best_score(query_stripped: str, entity_name: str, aliases) -> float:
    """
    法人格サフィックス除去後の文字列で比較することで
    "Sony Corporation" vs "ZTE Corporation" のような偽陽性を防ぐ。
    エイリアスがある場合はそちらも比較対象に含める。
    """
    entry_stripped = _strip(entity_name)
    score = _difflib_similarity(query_stripped, entry_stripped)

    if aliases:
        alias_list = aliases if isinstance(aliases, list) else [aliases]
        for alias in alias_list:
            score = max(score, _difflib_similarity(query_stripped, _strip(str(alias))))

    return score


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
    matched_ids: set[str] = set()

    query_stripped = _strip(request.company_name)

    # ── Step 0: SQL ILIKE 直接一致 ────────────────────────────────────────────
    # 「ZTE」→「ZTE Corporation」のような短名・頭字語検索を確実に捕捉する。
    # 法人格サフィックス語（Corporation / Ltd など）を除いた単語でILIKE OR 検索し
    # スコアはサフィックス除去後の文字列で算出することで偽陽性を防ぐ。
    stripped_words = [w for w in query_stripped.split() if len(w) >= 2]
    if not stripped_words:
        # サフィックスのみの入力（"Co., Ltd."など）は元の単語で検索
        stripped_words = [w for w in request.company_name.split() if len(w) >= 2]
    if stripped_words:
        ilike_conds = [
            cond
            for w in stripped_words
            for cond in (
                Watchlist.entity_name.ilike(f"%{w}%"),
                cast(Watchlist.aliases, String).ilike(f"%{w}%"),
            )
        ]
        direct_result = await db.execute(
            select(Watchlist).where(
                Watchlist.is_active == True,  # noqa: E712
                or_(*ilike_conds),
            ).limit(100)
        )
        for entry in direct_result.scalars().all():
            score = _best_score(query_stripped, entry.entity_name, entry.aliases)
            if score >= request.threshold:
                matches.append(_match_detail(entry, score))
                matched_ids.add(str(entry.id))

    # ── Step 1: FAISS セマンティック検索 ─────────────────────────────────────
    used_faiss  = False
    faiss_hits  = faiss_service.search(request.company_name, top_k=30)

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
                if str(entry.id) in matched_ids:
                    continue  # Step 0 で既に取得済み
                score = score_by_id.get(str(entry.id), 0.0)
                if score >= request.threshold:
                    matches.append(_match_detail(entry, score))
                    matched_ids.add(str(entry.id))
        # entries が空 → FAISS/DB 不整合 → difflib フォールバックへ

    # ── Step 2: Difflib フォールバック ────────────────────────────────────────
    # FAISS が空、または FAISS はヒットしたが DB にレコードが存在しない場合
    if not used_faiss:
        first_word = (stripped_words[0] if stripped_words else request.company_name.split()[0])
        pct  = f"%{first_word}%"
        stmt = (
            select(Watchlist)
            .where(
                Watchlist.is_active == True,  # noqa: E712
                or_(Watchlist.entity_name.ilike(pct)),
            )
            .limit(2000)
        )
        result     = await db.execute(stmt)
        candidates = result.scalars().all()

        if not candidates:
            result     = await db.execute(
                select(Watchlist).where(Watchlist.is_active == True)  # noqa: E712
            )
            candidates = result.scalars().all()

        for entry in candidates:
            if str(entry.id) in matched_ids:
                continue
            score = _best_score(query_stripped, entry.entity_name, entry.aliases)
            if score >= request.threshold:
                matches.append(_match_detail(entry, score))
                matched_ids.add(str(entry.id))

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
        transaction_id=getattr(request, "transaction_id", None),
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
