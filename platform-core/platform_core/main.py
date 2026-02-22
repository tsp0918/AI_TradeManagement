"""platform-core FastAPI アプリケーション。

エンドポイント:
- /auth/*           認証 (ローカルJWT / Google SSO / Microsoft SSO)
- /admin/*          管理 (tenants / users / modules)
- /internal/*       内部 API (モジュール自動登録・モジュール間通信)
- /health           ヘルスチェック
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from platform_core.auth.router import router as auth_router
from platform_core.config import settings
from platform_core.routers import admin_router
from platform_core.routers.internal import router as internal_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 起動時処理
    yield
    # 終了時処理


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Trade Management - Platform Core",
        version="0.1.0",
        description="共通プラットフォーム基盤 (認証・テナント・共有データ・モジュールレジストリ)",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.platform_env == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ルーター登録
    app.include_router(auth_router)
    app.include_router(admin_router, prefix="/admin")
    app.include_router(internal_router)

    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok", "env": settings.platform_env}

    return app


app = create_app()
