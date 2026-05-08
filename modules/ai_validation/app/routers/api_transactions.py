"""
GET /api/transactions/recent  — ダッシュボード向け案件サマリー JSON API
POST /api/transactions        — DAP / 外部システムからの新規案件作成 JSON API

直近 N 件の Transaction と各ステップの進捗・未完了アクションを返す。
platform-core の案件ダッシュボードから httpx で呼ばれる。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.db.models.ai_run import AiRun, RunType
from app.db.models.transaction import Transaction, TransactionItem, UsageRequirement
from app.services.two_list import compute_two_lists

router = APIRouter(prefix="/api/transactions", tags=["api-transactions"])

import os as _os
# ai_validation 自身のベース URL（ダッシュボードのアクション URL 生成用）
_BASE = _os.environ.get("MODULE_AI_VALIDATION_URL", "http://localhost:8001")
_SCREENING_BASE = _os.environ.get("MODULE_SCREENING_URL", "http://localhost:8005")


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
    all_orgs: bool = Query(default=False, description="True = 全拠点表示"),
    db: Session = Depends(get_db),
    x_org_id: Optional[str] = Header(None, alias="X-Organization-Id"),
) -> Dict[str, Any]:
    """
    直近 N 件の取引とステータスサマリーを返す。

    各案件に対して以下を含む:
      - スクリーニング状況 (screening_status, screening_result_id)
      - AI 判定状況 (has_ai_run, last_run_at, counts)
      - 未完了アクション (pending_actions)
    """
    q = db.query(Transaction)
    if x_org_id and not all_orgs:
        q = q.filter(
            (Transaction.org_id == x_org_id) | (Transaction.org_id == None)  # noqa: E711
        )
    txs = q.order_by(desc(Transaction.id)).limit(limit).all()

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


# ──────────────────────────────────────────────
# POST /api/transactions  (DAP / 外部 JSON API)
# ──────────────────────────────────────────────

class _ItemIn(BaseModel):
    item_name: str = ""
    item_description: str = ""


class _UsageIn(BaseModel):
    source: str = "core"
    text: str = ""


class TransactionCreateRequest(BaseModel):
    title: str
    counterparty_name: Optional[str] = None
    destination_country: Optional[str] = None
    items: List[_ItemIn] = []
    usage_requirements: List[_UsageIn] = []
    source_module: Optional[str] = None  # "dap" | "item_version" | etc.


def _make_case_no_api() -> str:
    import datetime
    import random
    today = datetime.date.today().strftime("%Y%m%d")
    return f"API-{today}-{random.randint(1000, 9999)}"


@router.post("", status_code=201)
def create_transaction_api(
    body: TransactionCreateRequest,
    db: Session = Depends(get_db),
    x_org_id: Optional[str] = Header(None, alias="X-Organization-Id"),
) -> Dict[str, Any]:
    """
    DAP ヒアリング完了後に案件を JSON API 経由で新規作成する。

    Returns: {id, case_no, title, status, url}
    """
    tx = Transaction(
        case_no=_make_case_no_api(),
        title=body.title.strip() or "新規審査",
        status="draft",
        counterparty_name=body.counterparty_name.strip() if body.counterparty_name else None,
        source_module=body.source_module or "dap",
        org_id=x_org_id,
    )
    # destination_country は extra_info 等に保存（モデルにフィールドがない場合は title に付与）
    if body.destination_country and not hasattr(Transaction, "destination_country"):
        tx.title = f"{tx.title}（仕向地: {body.destination_country}）"
    db.add(tx)
    db.flush()

    for item in body.items:
        if item.item_name.strip() or item.item_description.strip():
            ti = TransactionItem(
                transaction_id=tx.id,
                item_name=item.item_name.strip() or tx.title,
                spec_text=item.item_description.strip() or None,
                attachments_meta={"files": []},
            )
            db.add(ti)
            db.flush()
            for usage in body.usage_requirements:
                if usage.text.strip():
                    db.add(UsageRequirement(
                        transaction_id=tx.id,
                        transaction_item_id=ti.id,
                        source=usage.source or "core",
                        text=usage.text.strip(),
                        risk_tags=[],
                        created_by="dap",
                    ))
            break  # 現状は先頭1品目のみ

    if not body.items or not any(i.item_name.strip() for i in body.items):
        # 品目なしでも UsageRequirement だけ追加
        for usage in body.usage_requirements:
            if usage.text.strip():
                db.add(UsageRequirement(
                    transaction_id=tx.id,
                    transaction_item_id=None,
                    source=usage.source or "core",
                    text=usage.text.strip(),
                    risk_tags=[],
                    created_by="dap",
                ))

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "id":       tx.id,
        "case_no":  tx.case_no,
        "title":    tx.title,
        "status":   tx.status,
        "url":      f"{_BASE}/ui/transactions/{tx.id}",
    }


# ──────────────────────────────────────────────
# 審査が止まっている案件の検出
# ──────────────────────────────────────────────

@router.get("/stuck")
def get_stuck_transactions(
    db: Session = Depends(get_db),
    limit: int = Query(default=10),
) -> Dict[str, Any]:
    """
    審査チェックリストが未完了のまま放置されている案件を返す。
    条件:
      - status が draft または in_review
      - AI判定（matrix_match run）が実行済みだがエージェント判定未完了
      - または status が draft でスクリーニング未実施かつ AI 判定未実施
    DAP の先輩担当者がプロアクティブ通知するために使用する。
    """
    txs = (
        db.query(Transaction)
        .filter(Transaction.status.in_(["draft", "in_review"]))
        .order_by(desc(Transaction.updated_at))
        .limit(50)
        .all()
    )

    stuck = []
    for tx in txs:
        has_ai_run = db.query(AiRun).filter(
            AiRun.transaction_id == tx.id,
            AiRun.run_type == RunType.matrix_match.value,
        ).first() is not None
        has_agent = bool(tx.agent_judged_at)

        # AI判定済みだがエージェント判定未完了 → 止まっている
        if has_ai_run and not has_agent:
            stuck.append({
                "id":       tx.id,
                "case_no":  tx.case_no,
                "title":    tx.title,
                "status":   tx.status,
                "reason":   "agent_judgment_pending",
                "url":      f"{_BASE}/ui/transactions/{tx.id}",
            })
        # AI判定もスクリーニングも未実施の古い案件（draft が続いている）
        elif not has_ai_run and not tx.screening_status:
            stuck.append({
                "id":       tx.id,
                "case_no":  tx.case_no,
                "title":    tx.title,
                "status":   tx.status,
                "reason":   "not_started",
                "url":      f"{_BASE}/ui/transactions/{tx.id}",
            })

        if len(stuck) >= limit:
            break

    return {"stuck_transactions": stuck, "total": len(stuck)}


# ── De Minimis スナップショット保存 ───────────────────────────────

class SupplyChainLinkBody(BaseModel):
    supply_chain_node_id: Optional[str] = None
    de_minimis_result: Optional[Dict[str, Any]] = None


@router.post("/{tx_id}/supply-chain")
def save_supply_chain_link(
    tx_id: int,
    body: SupplyChainLinkBody,
    db: Session = Depends(get_db),
):
    """サプライチェーンノードと De Minimis 計算結果を取引に紐付ける。"""
    tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
    if tx is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    tx.supply_chain_node_id = body.supply_chain_node_id
    tx.de_minimis_result = body.de_minimis_result
    db.commit()
    return {"ok": True, "tx_id": tx_id}
