import os

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from platform_core.module_sdk import AuditMiddleware, ModuleInfo, build_lifespan

from app.core.logging import setup_logging
from app.api.v1.api import api_router
from app.ui.router import router as ui_router
from app.db.base import Base
from app.db.session import engine
import app.models  # noqa: F401 — モデルを登録してから create_all を呼ぶ


async def _init_db() -> None:
    """SQLite 開発環境向け: 未作成テーブルを自動生成する。"""
    Base.metadata.create_all(bind=engine)


MODULE = ModuleInfo(
    key="rnd_assessment",
    name="R&Dリスク評価",
    base_url=os.environ.get("MODULE_RND_ASSESSMENT_URL", "http://localhost:8003"),
    description="R&Dプロジェクトの知財オープン/クローズ戦略と安全保障貿易管理の統合リスク評価",
    capabilities=["rnd_case_create", "risk_assess", "ip_review"],
    data_contracts={
        "input":  ["RdCase"],
        "output": ["Assessment", "IpReview"],
    },
)


def create_app() -> FastAPI:
    setup_logging()

    app = FastAPI(
        title="R&D Risk App",
        version="0.1.0",
        lifespan=build_lifespan(MODULE, on_startup=_init_db),
    )

    app.add_middleware(AuditMiddleware, module_key="rnd_assessment")
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(ui_router)

    @app.get("/", include_in_schema=False)
    def _root():
        return RedirectResponse(url="/ui/")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
