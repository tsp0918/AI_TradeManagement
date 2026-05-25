"""取引審査ゲートウェイモジュール FastAPI アプリ（port 8013）。

Phase 6C-1: platform-core から分離。
データは platform_db（PostgreSQL）の plat_transaction_review テーブルを使用。
ERP 取引伝票受付・AI 該非判定連携・出荷スクリーニング・GO/NOGO 管理。
"""

import os

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from platform_core.module_sdk import AuditMiddleware, ModuleInfo, build_lifespan, health_router

from .routers.transaction_review import router as transaction_review_router

MODULE = ModuleInfo(
    key="trade_gate",
    name="取引審査ゲートウェイ",
    base_url=os.environ.get("MODULE_TRADE_GATE_URL", "http://localhost:8013"),
    description="ERP 取引伝票受付・AI 該非判定・スクリーニング連携・出荷 GO/NOGO 管理",
    capabilities=["transaction_review", "shipment_rescreen", "erp_sync"],
    data_contracts={
        "input":  ["ErpTransaction", "ShipmentOrder"],
        "output": ["TransactionReview"],
    },
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Trade Gate Module",
        version="0.1.0",
        lifespan=build_lifespan(MODULE),
    )

    app.add_middleware(AuditMiddleware, module_key="trade_gate")

    app.include_router(health_router)
    app.include_router(transaction_review_router)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/api/transaction-reviews/stats")

    return app


app = create_app()
