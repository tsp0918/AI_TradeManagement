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
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from platform_core.module_sdk import AuditMiddleware, ModuleInfo, build_lifespan, health_router

_STATIC_DIR = Path(__file__).parent / "static"

from app.db.session import AsyncSessionLocal
from app.models.screening import Watchlist
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


async def _init_faiss() -> None:
    """起動時に FAISS インデックスをロードまたは構築する。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Watchlist).where(Watchlist.is_active == True))  # noqa: E712
        entities = result.scalars().all()
    faiss_service.get_or_build(entities)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Screening Module",
        version="0.1.0",
        description="懸念取引先スクリーニング (BIS/OFAC等)",
        lifespan=build_lifespan(MODULE, on_startup=_init_faiss),
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

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/ui/screen")

    return app


app = create_app()
