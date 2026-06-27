"""
UX イベントログ収集・分析 API。

全モジュールから行動シグナルを受信し plat_ux_events テーブルに記録する。
チュートリアルファネル・チャット指標などの集計も提供する。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.ux_event import UxEvent

router = APIRouter(prefix="/api/ux-events", tags=["ux-events"])


# ── スキーマ ─────────────────────────────────────────────────────────────────

class UxEventIn(BaseModel):
    session_id: str | None = None
    module_key: str = "unknown"
    event_type: str
    event_name: str
    context: dict | None = None


# ── イベント収集 ─────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def collect_event(payload: UxEventIn, db: AsyncSession = Depends(get_db)) -> dict:
    """行動イベントを受信して記録する（全モジュール共通エンドポイント）。"""
    ev = UxEvent(
        session_id=payload.session_id,
        module_key=payload.module_key,
        event_type=payload.event_type,
        event_name=payload.event_name,
        context=payload.context,
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    return {"id": ev.id, "created_at": ev.created_at.isoformat()}


# ── 最近のイベント ────────────────────────────────────────────────────────────

@router.get("/recent")
async def recent_events(limit: int = 20, db: AsyncSession = Depends(get_db)) -> list[dict]:
    """直近 N 件のイベントを返す（管理画面ストリーム表示用）。"""
    rows = await db.execute(
        select(UxEvent).order_by(UxEvent.created_at.desc()).limit(limit)
    )
    return [
        {
            "id":         ev.id,
            "session_id": ev.session_id,
            "module_key": ev.module_key,
            "event_type": ev.event_type,
            "event_name": ev.event_name,
            "context":    ev.context,
            "created_at": ev.created_at.isoformat(),
        }
        for ev in rows.scalars()
    ]


# ── チュートリアル分析 ────────────────────────────────────────────────────────

@router.get("/analytics/tutorial")
async def tutorial_analytics(
    uc_id: str | None = None,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    チュートリアル行動ファネル集計。

    Returns:
        funnel:    UC×Step別の表示数/完了数/スキップ数/質問数/平均完了時間
        uc_summary: UC 別完了率サマリー
        faq_candidates: よく質問されるステップ Top10
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # ── ステップ別ファネル ──
    where_uc = "AND (context->>'uc_id') = :uc_id" if uc_id else ""
    funnel_sql = text(f"""
        SELECT
            context->>'uc_id'   AS uc_id,
            (context->>'step_num')::int AS step_num,
            COUNT(*) FILTER (WHERE event_name = 'tut_step_shown')  AS shown,
            COUNT(*) FILTER (WHERE event_name = 'tut_action_done') AS done,
            COUNT(*) FILTER (WHERE event_name = 'tut_skipped')     AS skipped,
            COUNT(*) FILTER (WHERE event_name = 'tut_question')    AS questioned,
            AVG((context->>'elapsed_ms')::float)
                FILTER (WHERE event_name = 'tut_action_done')      AS avg_ms
        FROM plat_ux_events
        WHERE event_type = 'tutorial'
          AND context->>'step_num' IS NOT NULL
          AND created_at > :since
          {where_uc}
        GROUP BY context->>'uc_id', (context->>'step_num')::int
        ORDER BY context->>'uc_id', (context->>'step_num')::int
    """)
    params: dict = {"since": since}
    if uc_id:
        params["uc_id"] = uc_id
    funnel_rows = (await db.execute(funnel_sql, params)).mappings().all()

    funnel = [
        {
            "uc_id":      r["uc_id"],
            "step_num":   r["step_num"],
            "shown":      r["shown"]      or 0,
            "done":       r["done"]       or 0,
            "skipped":    r["skipped"]    or 0,
            "questioned": r["questioned"] or 0,
            "avg_ms":     round(r["avg_ms"] or 0),
            "completion_rate": round(r["done"] / r["shown"] * 100, 1) if r["shown"] else None,
        }
        for r in funnel_rows
    ]

    # ── UC 別完了率サマリー ──
    summary_sql = text("""
        SELECT
            context->>'uc_id' AS uc_id,
            COUNT(*) FILTER (WHERE event_name = 'tut_completed') AS completed,
            COUNT(*) FILTER (WHERE event_name = 'tut_abandoned') AS abandoned
        FROM plat_ux_events
        WHERE event_type = 'tutorial'
          AND event_name IN ('tut_completed', 'tut_abandoned')
          AND created_at > :since
        GROUP BY context->>'uc_id'
        ORDER BY completed DESC
    """)
    summary_rows = (await db.execute(summary_sql, {"since": since})).mappings().all()
    uc_summary = [
        {
            "uc_id":     r["uc_id"],
            "completed": r["completed"] or 0,
            "abandoned": r["abandoned"] or 0,
            "total":     (r["completed"] or 0) + (r["abandoned"] or 0),
        }
        for r in summary_rows
    ]

    # ── FAQ 候補（よく質問されるステップ）──
    faq_sql = text("""
        SELECT
            context->>'uc_id'  AS uc_id,
            context->>'step_num' AS step_num,
            COUNT(*) AS question_count
        FROM plat_ux_events
        WHERE event_name = 'tut_question'
          AND created_at > :since
        GROUP BY context->>'uc_id', context->>'step_num'
        ORDER BY question_count DESC
        LIMIT 10
    """)
    faq_rows = (await db.execute(faq_sql, {"since": since})).mappings().all()
    faq_candidates = [
        {
            "uc_id":          r["uc_id"],
            "step_num":       r["step_num"],
            "question_count": r["question_count"],
        }
        for r in faq_rows
    ]

    return {
        "funnel":         funnel,
        "uc_summary":     uc_summary,
        "faq_candidates": faq_candidates,
        "days":           days,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
    }


# ── チャット分析 ──────────────────────────────────────────────────────────────

@router.get("/analytics/chat")
async def chat_analytics(days: int = 30, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """
    チャット対話のトークン効率・ソース分布を集計する。

    Returns:
        source_breakdown: cache/local/sonnet の件数・割合
        daily_sessions:   日別チャット件数
        total_questions:  集計期間内の総質問数
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    source_sql = text("""
        SELECT
            context->>'source' AS source,
            COUNT(*) AS cnt
        FROM plat_ux_events
        WHERE event_name IN ('tut_answer', 'chat_answered')
          AND context->>'source' IS NOT NULL
          AND created_at > :since
        GROUP BY context->>'source'
        ORDER BY cnt DESC
    """)
    source_rows = (await db.execute(source_sql, {"since": since})).mappings().all()
    total = sum(r["cnt"] for r in source_rows) or 1
    source_breakdown = [
        {
            "source": r["source"],
            "count":  r["cnt"],
            "pct":    round(r["cnt"] / total * 100, 1),
        }
        for r in source_rows
    ]

    daily_sql = text("""
        SELECT
            DATE(created_at AT TIME ZONE 'UTC') AS day,
            COUNT(*) AS cnt
        FROM plat_ux_events
        WHERE event_name IN ('tut_answer', 'chat_answered')
          AND created_at > :since
        GROUP BY DATE(created_at AT TIME ZONE 'UTC')
        ORDER BY day DESC
        LIMIT 14
    """)
    daily_rows = (await db.execute(daily_sql, {"since": since})).mappings().all()
    daily_sessions = [
        {"day": str(r["day"]), "count": r["cnt"]}
        for r in daily_rows
    ]

    total_q_row = await db.execute(
        select(func.count(UxEvent.id)).where(
            UxEvent.event_name.in_(["tut_answer", "chat_answered", "tut_question", "chat_sent"]),
            UxEvent.created_at > since,
        )
    )

    return {
        "source_breakdown": source_breakdown,
        "daily_sessions":   daily_sessions,
        "total_questions":  total_q_row.scalar() or 0,
        "days":             days,
        "generated_at":     datetime.now(timezone.utc).isoformat(),
    }
