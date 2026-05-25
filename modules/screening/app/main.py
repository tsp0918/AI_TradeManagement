"""懸念取引先スクリーニングモジュール FastAPI アプリ。

エンドポイント:
  GET  /health                  ヘルスチェック
  GET  /ui/screen               スクリーニング実行画面 (HTML)
  GET  /ui/results              結果履歴画面 (HTML)
  GET  /ui/watchlist            ウォッチリスト画面 (HTML)
  POST /api/screen              企業名スクリーニング
  GET  /api/results             スクリーニング結果一覧
  GET  /api/results/{id}        スクリーニング結果詳細
  GET  /api/watchlist           ウォッチリスト一覧
  POST /api/watchlist           ウォッチリストエントリ追加
  POST /api/watchlist/import    バルクインポート
  DELETE /api/watchlist/{id}    エントリ無効化

  [与信管理 - Phase 6A-1 統合]
  GET  /api/counterparties/stats       リスクダッシュボード集計
  GET  /api/counterparties             取引先一覧
  POST /api/counterparties             取引先登録（自動スクリーニング付き）
  GET  /api/counterparties/{id}        取引先詳細
  PUT  /api/counterparties/{id}        取引先更新
  DELETE /api/counterparties/{id}      取引先削除
  POST /api/counterparties/{id}/screen スクリーニング実行
  GET  /api/counterparties/{id}/history 与信スコア変更履歴
"""

import asyncio
import os
from pathlib import Path

# HuggingFace tokenizer のマルチプロセスを無効化（semaphore リークによるプロセス終了を防ぐ）
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from platform_core.module_sdk import AuditMiddleware, ModuleInfo, build_lifespan, health_router

_STATIC_DIR = Path(__file__).parent / "static"

from app.db.session import AsyncSessionLocal
from app.models.screening import Watchlist
from app.routers.counterparty import router as counterparty_router
from app.routers.screening import router as screening_router
from app.routers.ui import router as ui_router
from app.services import faiss_service

MODULE = ModuleInfo(
    key="screening",
    name="懸念取引先スクリーニング",
    base_url=os.environ.get("MODULE_SCREENING_URL", "http://localhost:8005"),
    description="BIS Entity List / OFAC SDN 等を用いた懸念取引先スクリーニング",
    capabilities=["screen_company", "watchlist_manage", "result_history"],
    data_contracts={
        "input":  ["Company", "ScreenRequest"],
        "output": ["ScreeningResult"],
    },
)


import logging as _logging
_logger = _logging.getLogger(__name__)


async def _init_faiss() -> None:
    """起動時に FAISS インデックスをロードまたは構築する。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Watchlist).where(Watchlist.is_active == True))  # noqa: E712
        entities = result.scalars().all()
    faiss_service.get_or_build(entities)
    return len(entities)


async def _auto_sync_sanctions() -> None:
    """DB が空の場合に OFAC SDN CSV + UN SC + EU Consolidated を自動同期する（APIキー不要）。"""
    from app.services.sanctions_sync import (
        fetch_ofac_sdn_csv,
        fetch_un_consolidated,
        fetch_eu_consolidated,
    )

    _logger.info("Watchlist DB empty — starting auto-sync (OFAC SDN CSV + UN SC + EU Consolidated)")
    try:
        import concurrent.futures, asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            ofac_fut = loop.run_in_executor(pool, lambda: fetch_ofac_sdn_csv(timeout=60))
            un_fut   = loop.run_in_executor(pool, lambda: fetch_un_consolidated(timeout=60))
            eu_fut   = loop.run_in_executor(pool, lambda: fetch_eu_consolidated(timeout=60))
            ofac_entries, un_entries, eu_entries = await _asyncio.gather(
                ofac_fut, un_fut, eu_fut, return_exceptions=True
            )

        all_entries: list[dict] = []
        for label, result in [
            ("OFAC SDN CSV", ofac_entries),
            ("UN SC Consolidated", un_entries),
            ("EU Consolidated", eu_entries),
        ]:
            if isinstance(result, list):
                all_entries.extend(result)
                _logger.info("%s: %d entries", label, len(result))
            else:
                _logger.warning("%s fetch failed: %s", label, result)

        if not all_entries:
            _logger.warning("Auto-sync: no entries fetched, skipping")
            return

        async with AsyncSessionLocal() as db:
            new_wl = [
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
                for e in all_entries
            ]
            db.add_all(new_wl)
            await db.commit()

        # FAISS 再構築
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Watchlist).where(Watchlist.is_active == True))  # noqa: E712
            entities = result.scalars().all()
        faiss_service.rebuild(entities)
        _logger.info("Auto-sync complete: %d entries indexed in FAISS", faiss_service.ntotal())

    except Exception as exc:
        _logger.error("Auto-sync failed: %s", exc, exc_info=True)


async def _startup() -> None:
    """FAISS 初期化 + DB 空なら自動同期 + 定期同期スケジューラー起動。"""
    from app.services.sync_scheduler import run_sync_loop
    db_count = await _init_faiss()
    if db_count == 0:
        asyncio.create_task(_auto_sync_sanctions())
    asyncio.create_task(run_sync_loop())


def create_app() -> FastAPI:
    app = FastAPI(
        title="Screening Module",
        version="0.1.0",
        description="懸念取引先スクリーニング (BIS/OFAC等)",
        lifespan=build_lifespan(MODULE, on_startup=_startup),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuditMiddleware, module_key="screening")

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    app.include_router(health_router)
    app.include_router(ui_router)
    app.include_router(screening_router)
    app.include_router(counterparty_router)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/ui/screen")

    return app


app = create_app()
