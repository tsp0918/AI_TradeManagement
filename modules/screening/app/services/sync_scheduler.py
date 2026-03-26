"""制裁リスト自動同期スケジューラー。

月次（デフォルト30日）で OFAC SDN と BIS Entity List を取得してウォッチリストに反映する。
既存エントリと source_id で照合し、新規のみ INSERT・削除済みを is_active=False に更新する。
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 同期間隔（秒）。環境変数 SANCTIONS_SYNC_INTERVAL_DAYS で日数指定。デフォルト30日。
_INTERVAL_DAYS = int(os.environ.get("SANCTIONS_SYNC_INTERVAL_DAYS", "30"))
_INTERVAL_SEC  = _INTERVAL_DAYS * 86400

# 次回実行までの初回待機時間（秒）。起動直後の過負荷を避けるため 60 秒後に初回実行。
_INITIAL_DELAY = int(os.environ.get("SANCTIONS_SYNC_INITIAL_DELAY_SEC", "60"))


async def _do_sync() -> dict[str, Any]:
    """OFAC/BIS を取得して DB に反映する（非同期ラッパー）。"""
    from app.db.session import AsyncSessionLocal
    from app.models.screening import Watchlist
    from app.services import faiss_service
    from app.services.sanctions_sync import fetch_ofac_sdn, fetch_bis_entity_list
    from sqlalchemy import select

    logger.info("[sync_scheduler] 制裁リスト同期開始")
    stats: dict[str, int] = {"inserted": 0, "deactivated": 0, "sources": 0}

    try:
        # IO バウンドな HTTP 取得をスレッドで実行
        entries_ofac = await asyncio.to_thread(fetch_ofac_sdn)
        entries_bis  = await asyncio.to_thread(fetch_bis_entity_list)
        all_entries  = entries_ofac + entries_bis
        stats["sources"] = len(all_entries)
    except Exception as e:
        logger.error("[sync_scheduler] データ取得エラー: %s", e)
        return stats

    async with AsyncSessionLocal() as db:
        # 既存の source_id セットを取得
        result = await db.execute(
            select(Watchlist.source_id, Watchlist.id).where(Watchlist.is_active == True)  # noqa: E712
        )
        existing: dict[str, int] = {row.source_id: row.id for row in result if row.source_id}

        fetched_ids: set[str] = set()
        for entry in all_entries:
            sid = entry.get("source_id") or ""
            if sid:
                fetched_ids.add(sid)

            if sid and sid in existing:
                # 既存エントリはスキップ（更新は行わない）
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
            stats["inserted"] += 1

        # フェッチ結果に含まれなくなった既存エントリを非活性化
        for sid, wl_id in existing.items():
            if sid not in fetched_ids:
                wl_obj = await db.get(Watchlist, wl_id)
                if wl_obj:
                    wl_obj.is_active = False
                    stats["deactivated"] += 1

        await db.commit()

        # FAISS インデックスを再構築
        try:
            result2 = await db.execute(
                select(Watchlist).where(Watchlist.is_active == True)  # noqa: E712
            )
            entities = result2.scalars().all()
            await asyncio.to_thread(faiss_service.rebuild, entities)
        except Exception as e:
            logger.warning("[sync_scheduler] FAISS 再構築エラー: %s", e)

    logger.info(
        "[sync_scheduler] 同期完了 — 新規 %d 件 / 非活性化 %d 件 / 取得合計 %d 件",
        stats["inserted"], stats["deactivated"], stats["sources"],
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
