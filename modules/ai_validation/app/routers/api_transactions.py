"""
GET /api/transactions/recent  — ダッシュボード向け案件サマリー JSON API

直近 N 件の Transaction と各ステップの進捗・未完了アクションを返す。
platform-core の案件ダッシュボードから httpx で呼ばれる。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.db.models.ai_run import AiRun, RunType
from app.db.models.transaction import Transaction
from app.services.two_list import compute_two_lists

router = APIRouter(prefix="/api/transactions", tags=["api-transactions"])

# ai_validation 自身のベース URL（ダッシュボードのアクション URL 生成用）
_BASE = "http://localhost:8001"
_SCREENING_BASE = "http://localhost:8005"


# ──────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────

def _pending_actions(
    tx: Transaction,
    has_ai_run: bool,
    counts: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    各ステップの未完了アクションを優先度付きで返す。

    返却フィールド:
        step     : 1=スクリーニング / 2=AI判定 / 3=報告書
        key      : アクション識別子
        label    : ボタン表示文字列
        url      : 遷移先 URL
        method   : "GET" | "POST"
        priority : "danger" | "warn" | "info"
    """
    actions: List[Dict[str, Any]] = []

    # ── Step 1: スクリーニング ──────────────────
    if tx.counterparty_name:
        if not tx.screening_result_id:
            actions.append({
                "step":     1,
                "key":      "screening_run",
                "label":    "スクリーニングを実行",
                "url":      f"{_BASE}/ui/transactions/{tx.id}/run-screening",
                "method":   "POST",
                "priority": "warn",
            })
        elif tx.screening_status in ("match", "possible_match"):
            actions.append({
                "step":     1,
                "key":      "screening_review",
                "label":    "スクリーニング結果を確認",
                "url":      f"{_SCREENING_BASE}/ui/results",
                "method":   "GET",
                "priority": "danger",
            })

    # ── Step 2: AI 判定 ────────────────────────
    if not has_ai_run:
        actions.append({
            "step":     2,
            "key":      "ai_run",
            "label":    "AI解析を実行",
            "url":      f"{_BASE}/ui/transactions/{tx.id}/run?threshold=0.75",
            "method":   "POST",
            "priority": "info",
        })

    # ── Step 3: 報告書 ─────────────────────────
    if has_ai_run and counts:
        hit_count = (counts.get("intersection") or 0) + (counts.get("core_only") or 0)
        if hit_count > 0:
            actions.append({
                "step":     3,
                "key":      "export_pdf",
                "label":    "報告書を出力 (PDF)",
                "url":      f"{_BASE}/ui/transactions/{tx.id}/export/pdf",
                "method":   "GET",
                "priority": "info",
            })

    return actions


# ──────────────────────────────────────────────
# エンドポイント
# ──────────────────────────────────────────────

@router.get("/recent")
def get_recent_transactions(
    limit: int = Query(default=5, ge=1, le=20, description="取得件数（最大20）"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    直近 N 件の取引とステータスサマリーを返す。

    各案件に対して以下を含む:
      - スクリーニング状況 (screening_status, screening_result_id)
      - AI 判定状況 (has_ai_run, last_run_at, counts)
      - 未完了アクション (pending_actions)
    """
    txs = (
        db.query(Transaction)
        .order_by(desc(Transaction.id))
        .limit(limit)
        .all()
    )

    results: List[Dict[str, Any]] = []

    for tx in txs:
        # 最新 matrix_match run を確認
        latest_mm = (
            db.query(AiRun)
            .filter(
                AiRun.transaction_id == tx.id,
                AiRun.run_type == RunType.matrix_match.value,
            )
            .order_by(desc(AiRun.id))
            .first()
        )

        has_ai_run  = latest_mm is not None
        last_run_at = latest_mm.started_at if latest_mm else None

        # 2リスト件数（存在する場合のみ）
        counts: Optional[Dict[str, Any]] = None
        if latest_mm:
            try:
                tl     = compute_two_lists(db=db, transaction_id=tx.id, run_id=latest_mm.id)
                counts = tl.get("counts")
            except Exception:
                counts = {}

        results.append({
            "id":                   tx.id,
            "case_no":              tx.case_no,
            "title":                tx.title,
            "status":               tx.status,
            "counterparty_name":    tx.counterparty_name,
            "screening_result_id":  tx.screening_result_id,
            "screening_status":     tx.screening_status,
            "has_ai_run":           has_ai_run,
            "last_run_at":          last_run_at.isoformat() if last_run_at else None,
            "counts":               counts,
            "pending_actions":      _pending_actions(tx, has_ai_run, counts),
        })

    return {"transactions": results, "total": len(results)}
