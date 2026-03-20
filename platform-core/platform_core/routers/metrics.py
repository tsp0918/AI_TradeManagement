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
from platform_core.ontology.db.schema import AgentSessionORM

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/summary")
async def metrics_summary(db: AsyncSession = Depends(get_db)):
    """
    プラットフォーム全体の成果評価メトリクスを返す。

    指標:
    - agent_sessions: NeuroSymbolic HanteiAgent セッション統計
    - llm_usage      : 直近 30 日間の LLM トークン使用量・コスト
    - generated_at   : 集計日時
    """
    now = datetime.now(timezone.utc)
    since_30d = now - timedelta(days=30)

    # ── HanteiAgent セッション統計 ──────────────────────────────
    session_rows = await db.execute(
        select(AgentSessionORM.status, func.count(AgentSessionORM.id).label("cnt"))
        .group_by(AgentSessionORM.status)
    )
    session_stats: dict[str, int] = {
        "active": 0, "ready_for_judgment": 0, "judged": 0, "abandoned": 0, "total": 0
    }
    for status, cnt in session_rows.all():
        session_stats["total"] += cnt
        if status in session_stats:
            session_stats[status] = cnt

    # 平均ターン数（対話深度）
    avg_turns_row = await db.execute(
        select(func.avg(AgentSessionORM.turn_count))
    )
    avg_turns = avg_turns_row.scalar() or 0.0

    # 判定完了率（judged / 全判定到達）
    completed = session_stats["judged"]
    reached   = session_stats["judged"] + session_stats["ready_for_judgment"]
    judgment_completion_rate = round(completed / reached * 100, 1) if reached > 0 else None

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
        "agent_sessions": {
            **session_stats,
            "avg_turns_per_session": round(float(avg_turns), 2),
            "judgment_completion_rate_pct": judgment_completion_rate,
        },
        "llm_usage_30d": {
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 4),
            "by_module": llm_by_module,
        },
    }
