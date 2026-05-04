"""
erp_integration — ERP ↔ AI_TradeManagement アダプター
port: 5001  (ERP の AI_TM_BASE_URL=http://localhost:5001 に合わせる)

ERP が期待するエンドポイント:
  POST /hs/classify
  POST /gaihi/judge
  POST /gaihi/judge-bom
  POST /screening/denied-party
  POST /export/precheck
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import export, gaihi, hs, screening, transaction, webhook

app = FastAPI(
    title="ERP Integration Adapter",
    version="1.0.0",
    description="AI_TradeManagement ↔ ERP インターフェース層。port 5001 で稼働。",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(hs.router)
app.include_router(gaihi.router)
app.include_router(screening.router)
app.include_router(export.router)
app.include_router(transaction.router)
app.include_router(webhook.router)


@app.get("/health")
async def health() -> dict:
    """各上流サービスの稼働状態を確認して返す。"""
    upstream: dict[str, str] = {}
    checks = {
        "hs_classifier":  f"{settings.HS_CLASSIFIER_URL}/health",
        "platform_core":  f"{settings.PLATFORM_CORE_URL}/health",
        "screening":      f"{settings.SCREENING_URL}/health",
        "ai_validation":  f"{settings.AI_VALIDATION_URL}/health",
    }
    async with httpx.AsyncClient(timeout=3.0) as client:
        for name, url in checks.items():
            try:
                r = await client.get(url)
                upstream[name] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
            except Exception:
                upstream[name] = "unreachable"

    overall = "ok" if all(v == "ok" for v in upstream.values()) else "degraded"
    return {"status": overall, "version": "1.0.0", "upstream": upstream}
