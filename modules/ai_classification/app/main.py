import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from platform_core.module_sdk import AuditMiddleware, ModuleInfo, build_lifespan, health_router

from .routers.products import router as products_router
from .routers.sds import router as sds_router
from .routers.integrations import router as integrations_router

MODULE = ModuleInfo(
    key="ai_classification",
    name="品目管理",
    base_url=os.environ.get("MODULE_AI_CLASSIFICATION_URL", "http://localhost:8002"),
    description="取り扱い品目の法規制情報管理・SDS解析・該非判定連携",
    capabilities=["product_manage", "sds_analyze", "regulation_check"],
    data_contracts={
        "input":  ["Product", "SDS"],
        "output": ["Product", "RegulationResult"],
    },
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Classification (Product Management)",
        version="0.1.0",
        lifespan=build_lifespan(MODULE),
    )

    app.add_middleware(AuditMiddleware, module_key="ai_classification")

    if os.path.isdir("static"):
        app.mount("/static", StaticFiles(directory="static"), name="static")

    app.include_router(health_router)
    app.include_router(products_router)
    app.include_router(sds_router)
    app.include_router(integrations_router)

    return app


app = create_app()
