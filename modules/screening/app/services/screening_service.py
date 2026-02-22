"""スクリーニングサービス。

シンプルな文字列正規化 + fuzzy マッチング (difflib) で
Watchlist に対して照合する。

本番では:
  - rapidfuzz, jellyfish などのより高精度ライブラリへの差し替えを推奨
  - BIS/OFAC のデータを定期バッチでインポートして scr_watchlist を最新化
"""

from __future__ import annotations

import uuid
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.screening import ScreeningResult, Watchlist
from app.schemas.screening import MatchDetail, ScreenRequest, ScreenResultResponse


def _normalize(name: str) -> str:
    """比較用に名前を正規化する (小文字、余分な空白を削除)。"""
    return " ".join(name.lower().split())


def _similarity(a: str, b: str) -> float:
    """文字列類似度を 0.0-1.0 で返す。"""
    return SequenceMatcher(None, a, b).ratio()


async def run_screening(
    request: ScreenRequest,
    db: AsyncSession,
    screened_by: uuid.UUID | None = None,
) -> ScreenResultResponse:
    """Watchlist に対してスクリーニングを実行し、結果を保存して返す。"""
    query_norm = _normalize(request.company_name)

    # アクティブなウォッチリストエントリを取得
    stmt = select(Watchlist).where(Watchlist.is_active == True)  # noqa: E712
    if request.country:
        # 国コードが一致するもの + 国コードが未設定のものを両方対象にする
        from sqlalchemy import or_
        stmt = stmt.where(
            or_(Watchlist.country == request.country, Watchlist.country.is_(None))
        )
    result = await db.execute(stmt)
    entries = result.scalars().all()

    matches: list[MatchDetail] = []
    for entry in entries:
        # 正規名でスコア計算
        score = _similarity(query_norm, _normalize(entry.entity_name))
        # エイリアスがあれば最高スコアを使用
        if entry.aliases:
            for alias in entry.aliases:
                alias_score = _similarity(query_norm, _normalize(alias))
                score = max(score, alias_score)

        if score >= request.threshold:
            matches.append(
                MatchDetail(
                    watchlist_id=entry.id,
                    entity_name=entry.entity_name,
                    list_source=entry.list_source,
                    score=round(score, 4),
                    country=entry.country,
                    risk_level=entry.risk_level,
                )
            )

    # スコア降順にソート
    matches.sort(key=lambda m: m.score, reverse=True)
    max_score = matches[0].score if matches else None

    if matches:
        # 閾値以上 → match / possible_match をスコアで区分
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
    await db.flush()  # id を確定させる

    return ScreenResultResponse(
        id=screening_result.id,
        query_name=screening_result.query_name,
        query_country=screening_result.query_country,
        result_status=screening_result.result_status,
        max_score=screening_result.max_score,
        matches=matches,
        screened_at=screening_result.screened_at,
    )
