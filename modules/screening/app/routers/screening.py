"""スクリーニング API ルーター。

エンドポイント:
  POST /api/screen              企業名スクリーニング実行
  POST /api/screen/batch        CSV一括スクリーニング
  GET  /api/results             スクリーニング結果一覧
  GET  /api/results/{id}        スクリーニング結果詳細
  GET  /api/watchlist           ウォッチリスト一覧
  POST /api/watchlist           ウォッチリストエントリ追加
  POST /api/watchlist/import    CSV/JSONバルクインポート
  DELETE /api/watchlist/{id}    エントリ無効化
"""

from __future__ import annotations

import csv
import io
import logging
import os
import threading
import uuid

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

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

_AI_VALIDATION_URL = os.environ.get("MODULE_AI_VALIDATION_URL", "http://localhost:8011")


def _flag_transaction_for_review(transaction_id: int) -> None:
    """スクリーニング再ヒット時に ai_validation へ取引の NEEDS_REVIEW 通知を fire-and-forget で送る。"""
    def _post():
        try:
            with httpx.Client(timeout=5.0) as c:
                c.post(f"{_AI_VALIDATION_URL}/api/transactions/{transaction_id}/flag-for-review")
        except Exception:
            pass
    threading.Thread(target=_post, daemon=True).start()


# ── スクリーニング実行 ──────────────────────────────────────────────────────


@router.post("/screen", response_model=ScreenResultResponse, status_code=status.HTTP_200_OK)
async def screen_company(
    request: ScreenRequest,
    db: AsyncSession = Depends(get_db),
):
    """企業名・住所をウォッチリストと照合する。ヒット時かつ transaction_id が設定されていれば ai_validation へ NEEDS_REVIEW を通知する。"""
    result = await run_screening(request, db)
    if result.result_status in ("match", "possible_match") and request.transaction_id:
        _flag_transaction_for_review(request.transaction_id)
    return result


@router.post("/screen/batch", status_code=status.HTTP_200_OK)
async def screen_batch(
    file: UploadFile = File(..., description="CSV: name, country(optional), address(optional)"),
    threshold: float = Form(0.75),
    db: AsyncSession = Depends(get_db),
):
    """CSV ファイルを受け取り、全行を一括スクリーニングする（最大 200 件）。

    CSV フォーマット（1行目はヘッダー）:
      name, country, address
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="CSVファイルを選択してください。")
    content = await file.read()
    try:
        text_io = io.StringIO(content.decode("utf-8-sig"))
    except UnicodeDecodeError:
        text_io = io.StringIO(content.decode("cp932", errors="replace"))

    reader = csv.DictReader(text_io)
    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=422, detail="CSVが空です。")
    if len(rows) > 200:
        raise HTTPException(status_code=422, detail="一度に処理できる上限は200行です。")

    results = []
    for i, row in enumerate(rows):
        name = (row.get("name") or row.get("企業名") or row.get("company_name") or "").strip()
        if not name:
            continue
        country = (row.get("country") or row.get("国コード") or "").strip() or None
        address = (row.get("address") or row.get("住所") or "").strip() or None
        req = ScreenRequest(company_name=name, country=country, address=address, threshold=threshold)
        try:
            result = await run_screening(req, db)
            results.append({
                "row": i + 1,
                "name": name,
                "country": country,
                "status": result.result_status,
                "max_score": result.max_score,
                "matches": len(result.matches),
                "result_id": str(result.id),
            })
        except Exception as exc:
            results.append({
                "row": i + 1,
                "name": name,
                "country": country,
                "status": "error",
                "max_score": None,
                "matches": 0,
                "error": str(exc),
            })

    total = len(results)
    hits = sum(1 for r in results if r["status"] in ("match", "possible_match"))
    return {
        "total": total,
        "hits": hits,
        "clean": total - hits,
        "results": results,
    }


# ── JSON バッチスクリーニング（ERP 連携用） ────────────────────────────────


class _BatchJsonEntity(BaseModel):
    name: str
    country: str | None = None
    address: str | None = None
    entity_type: str | None = None


class _BatchJsonRequest(BaseModel):
    entities: list[_BatchJsonEntity]
    sources: list[str] | None = None   # 現在は無視（全ソース使用）; 将来フィルタ用
    threshold: float = 0.75


@router.post("/screening/batch", status_code=status.HTTP_200_OK)
async def screen_batch_json(
    body: _BatchJsonRequest,
    db: AsyncSession = Depends(get_db),
):
    """ERP / 外部システム向け JSON バッチスクリーニング（最大 500 件）。

    ERP の `POST /api/screening/batch` 呼び出し形式に対応:
      {"entities": [{"name": "...", "country": "CN", "entity_type": "company"}], "sources": [...]}

    各エンティティに対してスクリーニングを実行し、結果を返す。
    `is_match: true` は "match" または "possible_match" 状態を示す。
    """
    if not body.entities:
        raise HTTPException(status_code=422, detail="entities が空です。")
    if len(body.entities) > 500:
        raise HTTPException(status_code=422, detail="一度に処理できる上限は 500 件です。")

    results = []
    for i, entity in enumerate(body.entities):
        name = entity.name.strip()
        if not name:
            continue
        req = ScreenRequest(
            company_name=name,
            country=entity.country,
            address=entity.address,
            threshold=body.threshold,
        )
        try:
            result = await run_screening(req, db)
            results.append({
                "index": i,
                "name": name,
                "country": entity.country,
                "entity_type": entity.entity_type,
                "result_status": result.result_status,
                "is_match": result.result_status in ("match", "possible_match"),
                "max_score": result.max_score,
                "list_name": result.matches[0].list_source if result.matches else None,
                "matches": len(result.matches),
                "result_id": str(result.id),
            })
        except Exception as exc:
            results.append({
                "index": i,
                "name": name,
                "country": entity.country,
                "entity_type": entity.entity_type,
                "result_status": "error",
                "is_match": False,
                "max_score": None,
                "list_name": None,
                "matches": 0,
                "error": str(exc),
            })

    total = len(results)
    flagged = [r for r in results if r["is_match"]]
    return {
        "total": total,
        "flagged_count": len(flagged),
        "clear_count": total - len(flagged),
        "flagged_list": flagged,
        "results": results,
    }


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
