"""EPA/FTA 特恵税率管理モジュール FastAPI アプリ（port 8014）。

Phase 6B-2: platform-core から分離。
データは platform_db（PostgreSQL）の plat_fta_* テーブルを使用。
"""

import os

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from platform_core.module_sdk import AuditMiddleware, ModuleInfo, build_lifespan, health_router

from .routers.fta import router as fta_router

MODULE = ModuleInfo(
    key="fta_origin",
    name="FTA/EPA 特恵税率管理",
    base_url=os.environ.get("MODULE_FTA_ORIGIN_URL", "http://localhost:8014"),
    description="EPA/FTA 協定税率照会・原産性ルール管理",
    capabilities=["fta_check", "agreement_manage", "rate_lookup"],
    data_contracts={
        "input":  ["HsCode", "DestinationCountry"],
        "output": ["FtaRate", "FtaAgreement"],
    },
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="FTA Origin Module",
        version="0.1.0",
        lifespan=build_lifespan(MODULE),
    )

    app.add_middleware(AuditMiddleware, module_key="fta_origin")

    app.include_router(health_router)
    app.include_router(fta_router)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/ui/fta-check")

    return app


app = create_app()
