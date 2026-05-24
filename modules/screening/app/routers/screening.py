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

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status

logger = logging.getLogger(__name__)
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
from app.services import faiss_service
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
    await db.commit()

    # FAISS インデックスを自動再構築
    all_result = await db.execute(select(Watchlist).where(Watchlist.is_active == True))  # noqa: E712
    faiss_service.rebuild(all_result.scalars().all())

    return {"imported": len(entries), "ntotal": faiss_service.ntotal()}


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


# ── 制裁リスト同期 ────────────────────────────────────────────────────────


@router.post("/admin/sync-sanctions", status_code=status.HTTP_200_OK)
async def sync_sanctions(
    sources: str = "all",
    csl_api_key: str = "DEMO_KEY",
    db: AsyncSession = Depends(get_db),
):
    """全制裁リストを公式ソースから取得してウォッチリストを同期する。

    sources: カンマ区切りでソースを指定。"all" で全7ソース。
      ofac_sdn, bis_entity, bis_uvl, bis_meu, bis_dpl, eu_consolidated, uk_ofsi

    処理フロー:
      1. 各公式ソースからエントリを取得
      2. 同ソースの既存エントリを論理削除
      3. 新エントリを一括挿入
      4. FAISS インデックスを再構築
    """
    from sqlalchemy import update as sa_update
    from app.services.sanctions_sync import (
        fetch_ofac_sdn,
        fetch_ofac_sdn_csv,
        fetch_un_consolidated,
        fetch_bis_entity_list,
        fetch_bis_unverified,
        fetch_bis_meu,
        fetch_bis_dpl,
        fetch_eu_consolidated,
        fetch_uk_ofsi,
    )

    # APIキー不要ソース（"free" で一括指定可）
    FREE_SOURCES = {"ofac_sdn_csv", "un_sc", "eu_consolidated"}

    ALL_SOURCES = {
        "ofac_sdn":        fetch_ofac_sdn,       # OFAC SDN XML（28MB・低速）
        "ofac_sdn_csv":    fetch_ofac_sdn_csv,    # OFAC SDN CSV（5.5MB・高速）※推奨
        "un_sc":           fetch_un_consolidated, # UN SC Consolidated（APIキー不要）
        "eu_consolidated": fetch_eu_consolidated, # EU Consolidated（APIキー不要）
        "uk_ofsi":         fetch_uk_ofsi,         # UK OFSI（APIキー不要）
        "bis_entity":      lambda: fetch_bis_entity_list(api_key=csl_api_key),
        "bis_uvl":         lambda: fetch_bis_unverified(api_key=csl_api_key),
        "bis_meu":         lambda: fetch_bis_meu(api_key=csl_api_key),
        "bis_dpl":         lambda: fetch_bis_dpl(api_key=csl_api_key),
    }

    # "all" は全ソース、"free" は APIキー不要ソースのみ
    src_lower = sources.strip().lower()
    if src_lower == "all":
        requested = set(ALL_SOURCES.keys())
    elif src_lower == "free":
        requested = FREE_SOURCES
    else:
        requested = {s.strip() for s in sources.split(",") if s.strip() in ALL_SOURCES}

    if not requested:
        raise HTTPException(status_code=422, detail=f"Invalid sources: {sources}")

    result_stats: dict = {"sources_requested": sorted(requested)}
    all_new: list[dict] = []
    synced_sources: list[str] = []

    # ── 1. データ取得 ─────────────────────────────────────────────────────
    for src_key in sorted(requested):
        try:
            entries = ALL_SOURCES[src_key]()
            all_new.extend(entries)
            result_stats[f"{src_key}_fetched"] = len(entries)
            synced_sources.append(src_key)
            logger.info("sync-sanctions %s: %d entries", src_key, len(entries))
        except Exception as exc:
            logger.error("sync-sanctions %s fetch failed: %s", src_key, exc)
            result_stats[f"{src_key}_error"] = str(exc)

    if not all_new:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No entries fetched from any source",
        )

    # ── 2. 既存エントリを論理削除 ─────────────────────────────────────────
    if synced_sources:
        await db.execute(
            sa_update(Watchlist)
            .where(Watchlist.list_source.in_(synced_sources))
            .values(is_active=False)
        )

    # ── 3. 新エントリを一括挿入 ────────────────────────────────────────────
    new_entries = [
        Watchlist(
            list_source=e["list_source"],
            entity_name=e["entity_name"],
            aliases=e.get("aliases"),
            address=e.get("address"),
            country=e.get("country"),
            source_id=e.get("source_id"),
            reason=e.get("reason"),
            risk_level=e.get("risk_level", "high"),
            extra=e.get("extra"),
        )
        for e in all_new
    ]
    db.add_all(new_entries)
    await db.commit()

    # ── 4. FAISS 再構築 ────────────────────────────────────────────────────
    all_result = await db.execute(
        select(Watchlist).where(Watchlist.is_active == True)  # noqa: E712
    )
    faiss_service.rebuild(all_result.scalars().all())

    result_stats["total_inserted"] = len(new_entries)
    result_stats["faiss_ntotal"]   = faiss_service.ntotal()
    logger.info("sync-sanctions complete: %s", result_stats)
    return result_stats


# ── FAISS インデックス管理 ──────────────────────────────────────────────────


@router.post("/rebuild-index", status_code=status.HTTP_200_OK)
async def rebuild_index(db: AsyncSession = Depends(get_db)):
    """ウォッチリスト更新後に FAISS インデックスを再構築する。"""
    result = await db.execute(select(Watchlist).where(Watchlist.is_active == True))  # noqa: E712
    entities = result.scalars().all()
    faiss_service.rebuild(entities)
    return {"ok": True, "ntotal": faiss_service.ntotal(), "message": f"{faiss_service.ntotal()} entities indexed"}


@router.get("/index-status", status_code=status.HTTP_200_OK)
async def index_status():
    """FAISS インデックスの現在のエンティティ数を返す。"""
    return {"ntotal": faiss_service.ntotal()}
