"""platform-core FastAPI アプリケーション。

エンドポイント:
- /                 → /ui へリダイレクト
- /ui               ポータルホーム画面 (Jinja2)
- /ui/health/{key}  モジュールヘルスチェックプロキシ
- /auth/*           認証 (ローカルJWT / Google SSO / Microsoft SSO)
- /admin/*          管理 (tenants / users / modules)
- /api/projects/*   案件管理 (Project / PatentLink)
- /internal/*       内部 API (モジュール自動登録・モジュール間通信)
- /health           ヘルスチェック
"""

import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from platform_core.auth.router import router as auth_router
from platform_core.config import settings
from platform_core.middleware.audit import AuditMiddleware
from platform_core.routers import admin_router
from platform_core.routers.internal import router as internal_router
from platform_core.routers.projects import router as projects_router
from platform_core.routers.ui import router as ui_router
from platform_core.routers.ui import start_all_modules, stop_all_modules

_STATIC_DIR = pathlib.Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 全モジュールを並行起動 (fire-and-forget)
    start_all_modules()
    yield
    # 終了時: 全モジュールサブプロセスを停止
    stop_all_modules()


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
    app.add_middleware(AuditMiddleware, module_key="platform-core")

    # 静的ファイル (CSS / JS)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ルーター登録
    app.include_router(ui_router)
    app.include_router(auth_router)
    app.include_router(admin_router, prefix="/admin")
    app.include_router(projects_router, prefix="/api/projects", tags=["projects"])
    app.include_router(internal_router)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/ui")

    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok", "env": settings.platform_env}

    return app


app = create_app()
