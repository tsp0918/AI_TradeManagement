"""輸出許可申請管理モジュール FastAPI アプリ（port 8012）。

Phase 6B-1: platform-core から分離。
データは platform_db（PostgreSQL）の plat_export_license_application テーブルを使用。
"""

import os

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from platform_core.module_sdk import AuditMiddleware, ModuleInfo, build_lifespan, health_router

from .routers.export_license import router as export_license_router
from .routers.quota import router as quota_router

MODULE = ModuleInfo(
    key="export_license",
    name="輸出許可申請管理",
    base_url=os.environ.get("MODULE_EXPORT_LICENSE_URL", "http://localhost:8012"),
    description="EAR BIS-748P / 外為法 様式第1 ドラフト生成・申請ライフサイクル管理",
    capabilities=["license_draft", "license_lifecycle", "expiry_alert"],
    data_contracts={
        "input":  ["ExportLicenseApplication"],
        "output": ["ExportLicenseApplication"],
    },
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Export License Module",
        version="0.1.0",
        lifespan=build_lifespan(MODULE),
    )

    app.add_middleware(AuditMiddleware, module_key="export_license")

    app.include_router(health_router)
    app.include_router(export_license_router)
    app.include_router(quota_router)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/health")

    return app


app = create_app()
