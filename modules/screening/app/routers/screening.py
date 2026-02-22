"""スクリーニング API ルーター。

エンドポイント:
  POST /api/screen              企業名スクリーニング実行
  GET  /api/results             スクリーニング結果一覧
  GET  /api/results/{id}        スクリーニング結果詳細
  GET  /api/watchlist           ウォッチリスト一覧
  POST /api/watchlist           ウォッチリストエントリ追加
  POST /api/watchlist/import    CSV/JSONバルクインポート
  DELETE /api/watchlist/{id}    エントリ無効化
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.screening import ScreeningResult, Watchlist
from app.schemas.screening import (
    MatchDetail,
    ScreenRequest,
    ScreenResultResponse,
    WatchlistEntryResponse,
    WatchlistImportRow,
)
from app.services.screening_service import run_screening

router = APIRouter(prefix="/api", tags=["screening"])


# ── スクリーニング実行 ──────────────────────────────────────────────────────


@router.post("/screen", response_model=ScreenResultResponse, status_code=status.HTTP_200_OK)
async def screen_company(
    request: ScreenRequest,
    db: AsyncSession = Depends(get_db),
):
    """企業名・住所をウォッチリストと照合する。"""
    return await run_screening(request, db)


# ── スクリーニング結果 ─────────────────────────────────────────────────────


@router.get("/results", response_model=list[ScreenResultResponse])
async def list_results(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """最新のスクリーニング結果を返す。"""
    stmt = (
        select(ScreeningResult)
        .order_by(ScreeningResult.screened_at.desc())
        .limit(min(limit, 200))
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        ScreenResultResponse(
            id=r.id,
            query_name=r.query_name,
            query_country=r.query_country,
            result_status=r.result_status,
            max_score=r.max_score,
            matches=[MatchDetail(**m) for m in (r.matches or [])],
            screened_at=r.screened_at,
        )
        for r in rows
    ]


@router.get("/results/{result_id}", response_model=ScreenResultResponse)
async def get_result(
    result_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """スクリーニング結果を ID で取得する。"""
    result = await db.get(ScreeningResult, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Result not found")
    return ScreenResultResponse(
        id=result.id,
        query_name=result.query_name,
        query_country=result.query_country,
        result_status=result.result_status,
        max_score=result.max_score,
        matches=[MatchDetail(**m) for m in (result.matches or [])],
        screened_at=result.screened_at,
    )


# ── ウォッチリスト管理 ────────────────────────────────────────────────────


@router.get("/watchlist", response_model=list[WatchlistEntryResponse])
async def list_watchlist(
    active_only: bool = True,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """ウォッチリストエントリ一覧。"""
    stmt = select(Watchlist).order_by(Watchlist.entity_name)
    if active_only:
        stmt = stmt.where(Watchlist.is_active == True)  # noqa: E712
    stmt = stmt.limit(min(limit, 1000))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/watchlist", response_model=WatchlistEntryResponse, status_code=status.HTTP_201_CREATED)
async def add_watchlist_entry(
    row: WatchlistImportRow,
    db: AsyncSession = Depends(get_db),
):
    """ウォッチリストにエントリを 1 件追加する。"""
    entry = Watchlist(
        list_source=row.list_source,
        entity_name=row.entity_name,
        aliases=row.aliases,
        address=row.address,
        country=row.country,
        source_id=row.source_id,
        reason=row.reason,
        risk_level=row.risk_level,
        extra=row.extra,
    )
    db.add(entry)
    await db.flush()
    return entry


@router.post("/watchlist/import", status_code=status.HTTP_201_CREATED)
async def import_watchlist(
    rows: list[WatchlistImportRow],
    db: AsyncSession = Depends(get_db),
):
    """ウォッチリストをバルクインポートする (最大 5000 件)。"""
    if len(rows) > 5000:
        raise HTTPException(status_code=422, detail="Maximum 5000 rows per import")
    entries = [
        Watchlist(
            list_source=r.list_source,
            entity_name=r.entity_name,
            aliases=r.aliases,
            address=r.address,
            country=r.country,
            source_id=r.source_id,
            reason=r.reason,
            risk_level=r.risk_level,
            extra=r.extra,
        )
        for r in rows
    ]
    db.add_all(entries)
    await db.flush()
    return {"imported": len(entries)}


@router.delete("/watchlist/{entry_id}", status_code=status.HTTP_200_OK)
async def deactivate_watchlist_entry(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """ウォッチリストエントリを無効化する（物理削除ではなく論理削除）。"""
    entry = await db.get(Watchlist, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    entry.is_active = False
    return {"ok": True, "id": str(entry_id)}
