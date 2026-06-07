"""
GET /api/transactions/recent  — ダッシュボード向け案件サマリー JSON API
POST /api/transactions        — DAP / 外部システムからの新規案件作成 JSON API

直近 N 件の Transaction と各ステップの進捗・未完了アクションを返す。
platform-core の案件ダッシュボードから httpx で呼ばれる。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.db.models.ai_run import AiRun, RunType
from app.db.models.catchall_assessment import CatchallAssessment
from app.db.models.transaction import Transaction, TransactionItem, UsageRequirement
from app.services.two_list import compute_two_lists

router = APIRouter(prefix="/api/transactions", tags=["api-transactions"])

import os as _os
_logger = logging.getLogger(__name__)
# ブラウザからのアクション URL → public URL（Cloudflare Tunnel 経由）
_BASE = _os.environ.get("MODULE_AI_VALIDATION_PUBLIC_URL", "https://validation.tsp-aitrademanagement.com")
# サーバー間通信（スクリーニング照合 POST）用内部 URL
_SCREENING_BASE = _os.environ.get("MODULE_SCREENING_URL", "http://localhost:8005")
# スクリーニング結果確認リンク（ブラウザ向け）
_SCREENING_PUBLIC = _os.environ.get("MODULE_SCREENING_PUBLIC_URL", "https://screening.tsp-aitrademanagement.com")


def _screen_counterparty_bg(transaction_id: int, counterparty_name: str) -> None:
    """取引先をバックグラウンドスレッドで screening モジュールに照合し、結果を transaction に保存する。"""
    try:
        resp = httpx.post(
            f"{_SCREENING_BASE}/api/screen",
            json={"company_name": counterparty_name, "threshold": 0.75},
            timeout=15.0,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            _logger.warning("screening API returned %d for %s", resp.status_code, counterparty_name)
            return
        data = resp.json()
        status    = data.get("result_status", "clear")   # clear / possible_match / match
        result_id = data.get("id")

        # DB を更新（同期セッションを直接取得）
        from app.db.session import SessionLocal
        with SessionLocal() as db:
            tx = db.get(Transaction, transaction_id)
            if tx:
                tx.screening_status    = status
                tx.screening_result_id = result_id
                db.commit()
        _logger.info(
            "auto-screening done tx=%d counterparty=%s → %s",
            transaction_id, counterparty_name, status,
        )
    except Exception as exc:
        _logger.warning("auto-screening failed tx=%d: %s", transaction_id, exc)


# ──────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────

def _pending_actions(
    tx: Transaction,
    has_ai_run: bool,
    counts: Optional[Dict[str, Any]],
    has_catchall: bool = False,
) -> List[Dict[str, Any]]:
    """
    各ステップの未完了アクションを優先度付きで返す。

    返却フィールド:
        step     : 1=スクリーニング / 2=AI判定 / 2.5=キャッチオール / 3=報告書
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
                "url":      f"{_SCREENING_PUBLIC}/ui/results",
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

    # ── Step 2.5: キャッチオール自己判定 ────────
    # AI判定が LOW（リスト規制なし）かつ仕向国あり → キャッチオール必須
    if has_ai_run and counts:
        hit_count = (counts.get("intersection") or 0) + (counts.get("core_only") or 0)
        is_low    = hit_count == 0
        if is_low and not has_catchall:
            # 仕向国が懸念国ならdanger、それ以外はwarn
            _CONCERN = {"CN", "RU", "KP", "IR", "SY", "BY", "CU", "VE", "MM", "SD", "LY", "YE"}
            dest = (tx.destination_country or "").upper()
            priority = "danger" if dest in _CONCERN else "warn"
            actions.append({
                "step":     2,
                "key":      "catchall_run",
                "label":    "キャッチオール自己判定を実行",
                "url":      f"{_BASE}/ui/transactions/{tx.id}#sec-catchall",
                "method":   "GET",
                "priority": priority,
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

        # キャッチオール判定済みか確認
        has_catchall = db.query(CatchallAssessment).filter(
            CatchallAssessment.transaction_id == tx.id
        ).first() is not None

        results.append({
            "id":                   tx.id,
            "case_no":              tx.case_no,
            "title":                tx.title,
            "status":               tx.status,
            "counterparty_name":    tx.counterparty_name,
            "screening_result_id":  tx.screening_result_id,
            "screening_status":     tx.screening_status,
            "has_ai_run":           has_ai_run,
            "has_catchall":         has_catchall,
            "last_run_at":          last_run_at.isoformat() if last_run_at else None,
            "counts":               counts,
            "pending_actions":      _pending_actions(tx, has_ai_run, counts, has_catchall),
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
    source_module: Optional[str] = None  # "dap" | "item_version" | "erp" | etc.

    # ERP 連携フィールド（任意）
    erp_case_no: Optional[str] = None         # ERP 側受注番号（指定時は内部 case_no とは別に保存）
    product_code: Optional[str] = None        # ai_classification の品目コード
    product_name: Optional[str] = None        # 品目名（items 未指定時に自動補完）
    total_value_usd: Optional[float] = None   # 取引総額 (USD)
    unit_price_usd: Optional[float] = None    # 単価 (USD)
    quantity: Optional[float] = None          # 数量
    end_user: Optional[str] = None            # 最終需要者名
    end_user_country: Optional[str] = None    # 最終需要者所在国 ISO alpha-2
    intended_use: Optional[str] = None        # 最終用途（AI 判定品質向上のため推奨）
    hs_code: Optional[str] = None             # HSコード
    incoterms: Optional[str] = None           # インコタームズ（CIF/FOB 等）


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
    # case_no: ERP が指定した場合はそれを使用、なければ自動生成
    internal_case_no = _make_case_no_api()
    tx = Transaction(
        case_no=internal_case_no,
        title=body.title.strip() or "新規審査",
        status="draft",
        counterparty_name=body.counterparty_name.strip() if body.counterparty_name else None,
        source_module=body.source_module or ("erp" if body.erp_case_no else "dap"),
        org_id=x_org_id,
    )
    if body.destination_country:
        tx.destination_country = body.destination_country
    # ERP フィールドのマッピング
    if body.erp_case_no:
        tx.erp_case_no = body.erp_case_no.strip()
    if body.product_code:
        tx.linked_product_code = body.product_code.strip()
    if body.end_user:
        tx.end_user_name = body.end_user.strip()
    if body.end_user_country:
        tx.end_user_country = body.end_user_country.strip().upper()
    if body.intended_use:
        tx.end_use_description = body.intended_use.strip()
    if body.total_value_usd is not None:
        tx.total_value_usd = body.total_value_usd
    if body.unit_price_usd is not None:
        tx.unit_price_usd = body.unit_price_usd
    if body.quantity is not None:
        tx.quantity = body.quantity
    if body.hs_code:
        tx.hs_code = body.hs_code.strip()
    if body.incoterms:
        tx.incoterms = body.incoterms.strip().upper()
    db.add(tx)
    db.flush()

    # ERP の product_name を items に補完（items が空の場合）
    effective_items = list(body.items)
    if body.product_name and not any(i.item_name.strip() for i in effective_items):
        effective_items.insert(0, _ItemIn(item_name=body.product_name))

    for item in effective_items:
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
                        created_by="erp" if body.erp_case_no else "dap",
                    ))
            # intended_use を UsageRequirement として自動登録
            if body.intended_use and not body.usage_requirements:
                db.add(UsageRequirement(
                    transaction_id=tx.id,
                    transaction_item_id=ti.id,
                    source="core",
                    text=body.intended_use.strip(),
                    risk_tags=[],
                    created_by="erp",
                ))
            break  # 先頭1品目のみ

    if not effective_items or not any(i.item_name.strip() for i in effective_items):
        # 品目なしでも UsageRequirement だけ追加
        for usage in body.usage_requirements:
            if usage.text.strip():
                db.add(UsageRequirement(
                    transaction_id=tx.id,
                    transaction_item_id=None,
                    source=usage.source or "core",
                    text=usage.text.strip(),
                    risk_tags=[],
                    created_by="erp" if body.erp_case_no else "dap",
                ))

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))

    # 取引先名があればバックグラウンドでスクリーニングを実行
    if tx.counterparty_name:
        t = threading.Thread(
            target=_screen_counterparty_bg,
            args=(tx.id, tx.counterparty_name),
            daemon=True,
        )
        t.start()

    return {
        "id":                tx.id,
        "case_no":           tx.case_no,
        "erp_case_no":       tx.erp_case_no,
        "title":             tx.title,
        "status":            tx.status,
        "linked_product_code": tx.linked_product_code,
        "url":               f"{_BASE}/ui/transactions/{tx.id}",
        "screening_queued":  bool(tx.counterparty_name),
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


# ── ERP Webhook 受信 ─────────────────────────────────────────────

_ERP_WEBHOOK_URL = _os.environ.get("ERP_WEBHOOK_URL", "")


class _WebhookPayload(BaseModel):
    """ERP が AI_TM に送信する審査結果通知ペイロード（オプション）。"""
    event: str = "judgment_updated"
    erp_case_no: Optional[str] = None
    transaction_id: Optional[int] = None
    judgment: Optional[str] = None          # ERP 側の正規化値
    agent_judgment_status: Optional[str] = None
    comment: Optional[str] = None


@router.post("/webhook/judgment-updated")
def receive_erp_webhook(
    payload: _WebhookPayload,
    db: Session = Depends(get_db),
    x_org_id: Optional[str] = Header(None, alias="X-Organization-Id"),
) -> Dict[str, Any]:
    """
    ERP → AI_TM への Webhook 受信。
    ERP が自社システムで判定ステータスを更新した際に通知する（将来拡張用）。
    現状は受信ログを残し、erp_case_no で取引を特定して comment を保存する。
    """
    tx: Optional[Transaction] = None
    if payload.transaction_id:
        tx = db.query(Transaction).filter(Transaction.id == payload.transaction_id).first()
    if tx is None and payload.erp_case_no:
        tx = db.query(Transaction).filter(
            Transaction.erp_case_no == payload.erp_case_no
        ).order_by(desc(Transaction.created_at)).first()

    if tx is None:
        _logger.warning("webhook received but transaction not found: %s", payload.dict())
        return {"ok": False, "detail": "transaction not found"}

    _logger.info(
        "ERP webhook received: tx=%d erp_case_no=%s judgment=%s",
        tx.id, payload.erp_case_no, payload.judgment,
    )
    return {
        "ok": True,
        "transaction_id": tx.id,
        "case_no": tx.case_no,
        "erp_case_no": tx.erp_case_no,
        "current_judgment": _normalize_judgment(tx.status, tx.agent_judgment_status),
    }


# ── ERP ポーリング用：PENDING 案件一覧 ──────────────────────────

@router.get("/pending-erp")
def get_pending_erp_transactions(
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    ERP ポーリングジョブ向け。
    erp_case_no が設定されていて judgment が PENDING（未判定）の案件を返す。
    ERP は 30 分おきにこのエンドポイントをポーリングして判定完了を検知する。
    """
    txs = (
        db.query(Transaction)
        .filter(
            Transaction.erp_case_no.isnot(None),
            Transaction.erp_case_no != "",
        )
        .order_by(desc(Transaction.created_at))
        .limit(limit)
        .all()
    )

    results = []
    for tx in txs:
        j = _normalize_judgment(tx.status, tx.agent_judgment_status)
        results.append({
            "id": tx.id,
            "case_no": tx.case_no,
            "erp_case_no": tx.erp_case_no,
            "status": tx.status,
            "judgment": j,
            "agent_judgment_status": tx.agent_judgment_status,
            "is_pending": j == "PENDING",
            "updated_at": tx.updated_at.isoformat(),
        })

    pending_count = sum(1 for r in results if r["is_pending"])
    return {
        "results": results,
        "total": len(results),
        "pending_count": pending_count,
    }


# ── 判定ステータス正規化 (ERP 向け) ──────────────────────────────
# /api/transactions/search は ui.py に実装済み（erp_case_no / q / 直近20件をサポート）

_JUDGMENT_MAP: Dict[str, str] = {
    "not_controlled":  "APPROVED",
    "controlled":      "NEEDS_REVIEW",
    "requires_review": "NEEDS_REVIEW",
    "requires_permit": "REQUIRES_PERMIT",
}


def _normalize_judgment(tx_status: str, agent_judgment_status: Optional[str]) -> str:
    """
    AI_TM 内部値を ERP 向け正規化値に変換する。
    APPROVED / NEEDS_REVIEW / REQUIRES_PERMIT / REJECTED / PENDING
    """
    if tx_status == "rejected":
        return "REJECTED"
    if not agent_judgment_status:
        return "PENDING"
    return _JUDGMENT_MAP.get(agent_judgment_status.lower(), "PENDING")


# ── De Minimis スナップショット保存 ───────────────────────────────

class SupplyChainLinkBody(BaseModel):
    supply_chain_node_id: Optional[str] = None
    de_minimis_result: Optional[Dict[str, Any]] = None


@router.get("/{tx_id}")
def get_transaction_detail(tx_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    単一取引の詳細を返す（ai_classification 等の外部モジュール向け）。
    status / agent_judgment_status / items[].item_name が主な参照フィールド。
    """
    tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
    if tx is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    latest_run = (
        db.query(AiRun)
        .filter(AiRun.transaction_id == tx_id)
        .order_by(AiRun.started_at.desc())
        .first()
    )
    return {
        "id": tx.id,
        "case_no": tx.case_no,
        "erp_case_no": tx.erp_case_no,
        "title": tx.title,
        "status": tx.status,
        "agent_judgment_status": tx.agent_judgment_status,
        # ERP 向け正規化判定値: APPROVED / NEEDS_REVIEW / REQUIRES_PERMIT / REJECTED / PENDING
        "judgment": _normalize_judgment(tx.status, tx.agent_judgment_status),
        "destination_country": tx.destination_country,
        "source_module": tx.source_module,
        "counterparty_name": tx.counterparty_name,
        "linked_product_code": tx.linked_product_code,
        "items": [
            {"item_name": i.item_name, "spec_text": i.spec_text}
            for i in tx.items
        ],
        "ai_run": {
            "status": latest_run.status,
            "run_type": latest_run.run_type,
            "finished_at": latest_run.finished_at.isoformat() if latest_run.finished_at else None,
        } if latest_run else None,
        "supply_chain_node_id": tx.supply_chain_node_id,
        "created_at": tx.created_at.isoformat(),
        "updated_at": tx.updated_at.isoformat(),
        "url": f"/ui/transactions/{tx.id}",
    }


class _CooChangedPayload(BaseModel):
    product_code: str
    old_country:  Optional[str] = None
    new_country:  str


@router.post("/coo-changed")
def coo_changed(
    body: _CooChangedPayload,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    ai_classification から COO（原産国）変更を受信し、
    当該品目を参照している審査済み・審査中の取引を再審査キューに戻す。

    対象: linked_product_code == body.product_code
          AND status IN ("in_review", "approved")
    処置: agent_judgment_status → NULL, status → draft,
          tier_reason に COO 変更メモを追記
    """
    from datetime import datetime as _dt
    from sqlalchemy import or_

    targets = (
        db.query(Transaction)
        .filter(
            Transaction.linked_product_code == body.product_code,
            Transaction.status.in_(["in_review", "approved"]),
        )
        .all()
    )

    reset_ids: List[int] = []
    for tx in targets:
        tx.agent_judgment_status = None
        tx.agent_judged_at       = None
        tx.status                = "draft"
        note = (
            f"[COO変更 {_dt.utcnow().strftime('%Y-%m-%d')}] "
            f"原産国変更 {body.old_country or '?'} → {body.new_country} により自動再審査"
        )
        tx.tier_reason = f"{tx.tier_reason}\n{note}" if tx.tier_reason else note
        reset_ids.append(tx.id)

    db.commit()
    _logger.info(
        "coo-changed product=%s %s→%s: reset %d transactions %s",
        body.product_code, body.old_country, body.new_country,
        len(reset_ids), reset_ids,
    )
    return {
        "ok": True,
        "product_code": body.product_code,
        "old_country": body.old_country,
        "new_country": body.new_country,
        "reset_count": len(reset_ids),
        "reset_transaction_ids": reset_ids,
    }


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
