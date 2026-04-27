"""制裁リスト自動同期スケジューラー。

月次（デフォルト30日）で全制裁ソースを取得してウォッチリストに反映する。
既存エントリと source_id で照合し、新規のみ INSERT・削除済みを is_active=False に更新する。

対応ソース:
  - ofac_sdn       : OFAC SDN XML (~12,000件)
  - bis_entity     : BIS Entity List (~1,700件)
  - bis_uvl        : BIS Unverified List (~100件)
  - bis_meu        : BIS Military End-User List (~80件)
  - bis_dpl        : BIS Denied Persons List (~40件)
  - eu_consolidated: EU Consolidated Sanctions (~4,000件)
  - uk_ofsi        : UK OFSI Consolidated List (~3,500件)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_INTERVAL_DAYS = int(os.environ.get("SANCTIONS_SYNC_INTERVAL_DAYS", "30"))
_INTERVAL_SEC  = _INTERVAL_DAYS * 86400
_INITIAL_DELAY = int(os.environ.get("SANCTIONS_SYNC_INITIAL_DELAY_SEC", "60"))
_CSL_API_KEY   = os.environ.get("TRADE_GOV_API_KEY", "DEMO_KEY")


async def _do_sync() -> dict[str, Any]:
    """全制裁ソースを取得して DB に反映する（非同期ラッパー）。"""
    from app.db.session import AsyncSessionLocal
    from app.models.screening import Watchlist
    from app.services import faiss_service
    from app.services.sanctions_sync import (
        fetch_ofac_sdn,
        fetch_bis_entity_list,
        fetch_bis_unverified,
        fetch_bis_meu,
        fetch_bis_dpl,
        fetch_eu_consolidated,
        fetch_uk_ofsi,
    )
    from sqlalchemy import select

    logger.info("[sync_scheduler] 制裁リスト同期開始（全7ソース）")
    stats: dict[str, Any] = {}
    all_entries: list[dict] = []

    fetch_tasks = [
        ("ofac_sdn",        fetch_ofac_sdn,        {}),
        ("bis_entity",      fetch_bis_entity_list,  {"api_key": _CSL_API_KEY}),
        ("bis_uvl",         fetch_bis_unverified,   {"api_key": _CSL_API_KEY}),
        ("bis_meu",         fetch_bis_meu,          {"api_key": _CSL_API_KEY}),
        ("bis_dpl",         fetch_bis_dpl,          {"api_key": _CSL_API_KEY}),
        ("eu_consolidated", fetch_eu_consolidated,  {}),
        ("uk_ofsi",         fetch_uk_ofsi,          {}),
    ]

    for key, fn, kwargs in fetch_tasks:
        try:
            result = await asyncio.to_thread(fn, **kwargs)
            all_entries.extend(result)
            stats[f"{key}_fetched"] = len(result)
            logger.info("[sync_scheduler] %s: %d件", key, len(result))
        except Exception as e:
            logger.error("[sync_scheduler] %s 取得エラー: %s", key, e)
            stats[f"{key}_error"] = str(e)

    stats["total_fetched"] = len(all_entries)

    if not all_entries:
        logger.error("[sync_scheduler] 全ソースからデータ取得失敗")
        return stats

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Watchlist.source_id, Watchlist.id).where(Watchlist.is_active == True)  # noqa: E712
        )
        existing: dict[str, int] = {row.source_id: row.id for row in result if row.source_id}

        fetched_ids: set[str] = set()
        inserted = 0
        for entry in all_entries:
            sid = entry.get("source_id") or ""
            if sid:
                fetched_ids.add(sid)

            if sid and sid in existing:
                continue

            wl = Watchlist(
                entity_name=entry["entity_name"],
                aliases=entry.get("aliases"),
                country=entry.get("country"),
                list_source=entry.get("list_source", "unknown"),
                source_id=sid or None,
                reason=entry.get("reason"),
                risk_level=entry.get("risk_level", "high"),
                extra=entry.get("extra"),
                is_active=True,
                created_at=datetime.now(timezone.utc),
            )
            db.add(wl)
            inserted += 1

        deactivated = 0
        for sid, wl_id in existing.items():
            if sid not in fetched_ids:
                wl_obj = await db.get(Watchlist, wl_id)
                if wl_obj:
                    wl_obj.is_active = False
                    deactivated += 1

        await db.commit()
        stats["inserted"]    = inserted
        stats["deactivated"] = deactivated

        try:
            result2 = await db.execute(
                select(Watchlist).where(Watchlist.is_active == True)  # noqa: E712
            )
            entities = result2.scalars().all()
            await asyncio.to_thread(faiss_service.rebuild, entities)
            stats["faiss_ntotal"] = faiss_service.ntotal()
        except Exception as e:
            logger.warning("[sync_scheduler] FAISS 再構築エラー: %s", e)

    logger.info(
        "[sync_scheduler] 同期完了 — 新規 %d 件 / 非活性化 %d 件 / 取得合計 %d 件",
        inserted, deactivated, len(all_entries),
    )
    return stats


async def run_sync_loop() -> None:
    """バックグラウンドで定期同期を実行するループ。"""
    logger.info(
        "[sync_scheduler] 制裁リスト自動同期スケジューラー起動 "
        "(初回 %d 秒後, 以降 %d 日ごと)",
        _INITIAL_DELAY, _INTERVAL_DAYS,
    )
    await asyncio.sleep(_INITIAL_DELAY)

    while True:
        try:
            await _do_sync()
        except Exception as e:
            logger.error("[sync_scheduler] 予期せぬエラー: %s", e)

        logger.info("[sync_scheduler] 次回同期まで %d 日待機", _INTERVAL_DAYS)
        await asyncio.sleep(_INTERVAL_SEC)
