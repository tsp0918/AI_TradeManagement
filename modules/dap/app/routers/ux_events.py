"""DAP — UX イベントプロキシ & プラットフォームヘルス集計。

UX イベントを platform_core の plat_ux_events テーブルへ転送する。
管理画面はこのモジュール経由で platform_core の分析 API を呼ぶ。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ux-events"])

_PLATFORM_URL = os.environ.get("MODULE_PLATFORM_URL", "http://localhost:8000")

# モジュール定義（健全性チェック対象）
_MODULES = [
    {"key": "platform_core",    "name": "Platform Core",    "port": 8000},
    {"key": "ai_validation",    "name": "AI 取引審査",       "port": 8011},
    {"key": "ai_classification","name": "品目管理",          "port": 8002},
    {"key": "rnd_assessment",   "name": "R&D リスク管理",   "port": 8003},
    {"key": "patent_search",    "name": "特許検索",          "port": 8004},
    {"key": "screening",        "name": "スクリーニング",     "port": 8005},
    {"key": "hs_classifier",    "name": "HS コード判定",     "port": 8006},
    {"key": "export_license",   "name": "輸出許可申請",      "port": 8012},
    {"key": "dap",              "name": "DAP",               "port": 8010},
]


# ── UX イベントプロキシ ───────────────────────────────────────────────────────

class UxEventIn(BaseModel):
    session_id: str | None = None
    module_key: str = "dap"
    event_type: str
    event_name: str
    context: dict | None = None


@router.post("/api/ux-events", status_code=201)
async def proxy_ux_event(payload: UxEventIn) -> dict:
    """
    ブラウザ（tutorial-widget.js / chat-widget.js）から受信した
    UX イベントを platform_core へ非同期転送する。
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{_PLATFORM_URL}/api/ux-events",
                json=payload.model_dump(),
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("[ux-events] forward failed: %s", e)
        return {"id": None, "error": str(e)}


# ── 分析プロキシ（管理画面から呼ばれる）────────────────────────────────────────

@router.get("/api/ux-events/analytics/tutorial")
async def proxy_tutorial_analytics(
    uc_id: str | None = None, days: int = 30
) -> Any:
    params: dict = {"days": days}
    if uc_id:
        params["uc_id"] = uc_id
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_PLATFORM_URL}/api/ux-events/analytics/tutorial", params=params
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"error": str(e), "funnel": [], "uc_summary": [], "faq_candidates": []}


@router.get("/api/ux-events/analytics/chat")
async def proxy_chat_analytics(days: int = 30) -> Any:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_PLATFORM_URL}/api/ux-events/analytics/chat", params={"days": days}
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"error": str(e), "source_breakdown": [], "daily_sessions": [], "total_questions": 0}


@router.get("/api/ux-events/recent")
async def proxy_recent_events(limit: int = 20) -> Any:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_PLATFORM_URL}/api/ux-events/recent", params={"limit": limit}
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return []


# ── Platform KPI サマリー プロキシ ────────────────────────────────────────────

@router.get("/proxy/platform/api/metrics/summary")
async def proxy_metrics_summary() -> Any:
    """管理画面ダッシュボードの KPI カード用 — platform_core metrics/summary をプロキシ。"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_PLATFORM_URL}/api/metrics/summary")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"error": str(e)}


# ── プラットフォームヘルス集計 ────────────────────────────────────────────────

@router.get("/api/platform/health")
async def platform_health() -> dict:
    """
    全モジュールの /health を並列チェックしてステータスを集計する。
    管理画面のダッシュボードから呼ばれる。
    """
    async def check(module: dict) -> dict:
        url = f"http://localhost:{module['port']}/health"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url)
                ok = resp.status_code == 200
                try:
                    body = resp.json()
                    status = body.get("status", "ok" if ok else "error")
                except Exception:
                    status = "ok" if ok else "error"
            return {**module, "status": status, "reachable": ok}
        except Exception:
            return {**module, "status": "down", "reachable": False}

    results = await asyncio.gather(*[check(m) for m in _MODULES])
    up   = sum(1 for r in results if r["reachable"])
    down = len(results) - up
    return {
        "modules": list(results),
        "summary": {"total": len(results), "up": up, "down": down},
    }
