"""懸念取引先スクリーニングモジュール FastAPI アプリ。

エンドポイント:
  GET  /health                  ヘルスチェック
  POST /api/screen              企業名スクリーニング
  GET  /api/results             スクリーニング結果一覧
  GET  /api/results/{id}        スクリーニング結果詳細
  GET  /api/watchlist           ウォッチリスト一覧
  POST /api/watchlist           ウォッチリストエントリ追加
  POST /api/watchlist/import    バルクインポート
  DELETE /api/watchlist/{id}    エントリ無効化
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from platform_core.module_sdk import AuditMiddleware, ModuleInfo, build_lifespan, health_router

from app.routers.screening import router as screening_router

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


def create_app() -> FastAPI:
    app = FastAPI(
        title="Screening Module",
        version="0.1.0",
        description="懸念取引先スクリーニング (BIS/OFAC等)",
        lifespan=build_lifespan(MODULE),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuditMiddleware, module_key="screening")

    app.include_router(health_router)
    app.include_router(screening_router)

    return app


app = create_app()
