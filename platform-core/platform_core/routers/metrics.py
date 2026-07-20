"""
platform-core 成果評価メトリクス API。

GET /api/metrics/summary  — クロスモジュール KPI スナップショット
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.audit import LlmUsageLog

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/summary")
async def metrics_summary(db: AsyncSession = Depends(get_db)):
    """
    プラットフォーム全体の成果評価メトリクスを返す。

    指標:
    - llm_usage: 直近 30 日間の LLM トークン使用量・コスト
    - generated_at: 集計日時
    """
    now = datetime.now(timezone.utc)
    since_30d = now - timedelta(days=30)

    # ── LLM 使用量（直近 30 日） ────────────────────────────────
    llm_rows = await db.execute(
        select(
            LlmUsageLog.module_key,
            func.sum(LlmUsageLog.total_tokens).label("tokens"),
            func.sum(LlmUsageLog.cost_usd).label("cost_usd"),
            func.count(LlmUsageLog.id).label("calls"),
        )
        .where(LlmUsageLog.created_at >= since_30d)
        .group_by(LlmUsageLog.module_key)
    )
    llm_by_module: list[dict] = []
    total_tokens = 0
    total_cost   = 0.0
    for module_key, tokens, cost, calls in llm_rows.all():
        t = int(tokens or 0)
        c = float(cost or 0.0)
        total_tokens += t
        total_cost   += c
        llm_by_module.append({
            "module_key": module_key,
            "total_tokens": t,
            "cost_usd": round(c, 4),
            "api_calls": int(calls),
        })
    llm_by_module.sort(key=lambda x: x["total_tokens"], reverse=True)

    return {
        "generated_at": now.isoformat(),
        "period_days": 30,
        "llm_usage_30d": {
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 4),
            "by_module": llm_by_module,
        },
    }
