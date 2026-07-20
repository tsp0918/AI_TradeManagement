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
# ERP outbound webhook
_ERP_WEBHOOK_URL    = _os.environ.get("ERP_WEBHOOK_URL",    "http://localhost:8888/gts/webhook/judgment-updated")
_ERP_WEBHOOK_BEARER = _os.environ.get("ERP_WEBHOOK_BEARER", "dev-erp-integration-key")


def _push_erp_status(tx: Transaction, new_judgment: str, rationale: str = "") -> None:
    """ERP /gts/webhook/judgment-updated へ非同期で状態を通知する。erp_case_no がない場合はスキップ。"""
    if not tx.erp_case_no:
        return
    payload = {
        "material_code": tx.linked_product_code or tx.erp_case_no or tx.case_no,
        "new_judgment":  new_judgment,
        "new_eccn":      getattr(tx, "linked_product_eccn", None),
        "rationale":     rationale,
        "client_id":     "DEMO",
    }
    def _post():
        try:
            with httpx.Client(timeout=10) as c:
                c.post(_ERP_WEBHOOK_URL, json=payload,
                       headers={"Authorization": f"Bearer {_ERP_WEBHOOK_BEARER}"})
        except Exception:
            pass
    threading.Thread(target=_post, daemon=True).start()


def _screen_counterparty_bg(transaction_id: int, counterparty_name: str) -> None:
    """取引先をバックグラウンドスレッドで screening モジュールに照合し、結果を transaction に保存する。"""
    try:
        resp = httpx.post(
            f"{_SCREENING_BASE}/api/screen",
            json={"company_name": counterparty_name, "threshold": 0.75, "transaction_id": transaction_id},
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
        # AI判定もスクリーニングも未実施の古い案件（draft が続いている）
        if not has_ai_run and not tx.screening_status:
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


class _WebhookPayload(BaseModel):
    """ERP が AI_TM に送信する審査結果通知ペイロード（オプション）。"""
    event: str = "judgment_updated"
    erp_case_no: Optional[str] = None
    transaction_id: Optional[int] = None
    judgment: Optional[str] = None          # ERP 側の正規化値
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
        "current_judgment": _normalize_judgment(tx.status),
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
        j = _normalize_judgment(tx.status)
        results.append({
            "id": tx.id,
            "case_no": tx.case_no,
            "erp_case_no": tx.erp_case_no,
            "status": tx.status,
            "judgment": j,
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

_STATUS_TO_JUDGMENT: Dict[str, str] = {
    "approved": "APPROVED",
    "rejected": "REJECTED",
}


def _normalize_judgment(tx_status: str) -> str:
    """
    AI_TM 内部ステータスを ERP 向け正規化値に変換する。
    APPROVED / REJECTED / PENDING
    """
    return _STATUS_TO_JUDGMENT.get(tx_status, "PENDING")


# ── De Minimis スナップショット保存 ───────────────────────────────

class SupplyChainLinkBody(BaseModel):
    supply_chain_node_id: Optional[str] = None
    de_minimis_result: Optional[Dict[str, Any]] = None


@router.get("/{tx_id}")
def get_transaction_detail(tx_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    単一取引の詳細を返す（ai_classification 等の外部モジュール向け）。
    status / items[].item_name が主な参照フィールド。
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
        # ERP 向け正規化判定値: APPROVED / REJECTED / PENDING
        "judgment": _normalize_judgment(tx.status),
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
    処置: status → draft, tier_reason に COO 変更メモを追記
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
        tx.status = "draft"
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


# ── ERP イベント受信（MATERIAL_ORIGIN_CHANGE 等） ──────────────────────────

class _ErpEventPayload(BaseModel):
    """ERP → AI_TM 汎用イベントペイロード。"""
    event_type: str
    # MATERIAL_ORIGIN_CHANGE 専用フィールド
    material_code: Optional[str] = None
    from_country: Optional[str] = None
    to_country: Optional[str] = None
    effective_date: Optional[str] = None
    max_us_content_pct: Optional[float] = None
    exceeds_deminimis_threshold: Optional[bool] = None
    affected_products: Optional[List[str]] = None
    breach_lot_count: Optional[int] = None
    breach_lots: Optional[List[Dict[str, Any]]] = None
    # DEEMED_EXPORT_RISK 専用フィールド（rnd_assessment から）
    deemed_export_risk_level: Optional[str] = None
    person_name: Optional[str] = None
    case_title: Optional[str] = None
    top_factors: Optional[List[Any]] = None
    recommendation: Optional[str] = None


@router.post("/events")
def receive_erp_event(
    body: _ErpEventPayload,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    モジュール間汎用イベント受信エンドポイント。

    対応イベントタイプ:
      MATERIAL_ORIGIN_CHANGE — 原産国切り替えイベント（ERP から）
      DEEMED_EXPORT_RISK     — みなし輸出 HIGH/CRITICAL リスク（rnd_assessment から）
    """
    if body.event_type == "MATERIAL_ORIGIN_CHANGE":
        return _handle_origin_change_event(body, db)
    if body.event_type == "DEEMED_EXPORT_RISK":
        return _handle_deemed_export_risk_event(body, db)

    _logger.info("ERP event received (unhandled type): %s", body.event_type)
    return {"ok": True, "event_type": body.event_type, "case_ref": None}


def _handle_origin_change_event(body: _ErpEventPayload, db: Session) -> Dict[str, Any]:
    """原産国切り替えイベントを処理し、BREACH 時は取引審査案件を作成する。"""
    mat = body.material_code or "UNKNOWN"
    pct = body.max_us_content_pct or 0.0
    is_breach = bool(body.exceeds_deminimis_threshold)

    affected = ", ".join(body.affected_products or []) or "不明"
    lot_count = body.breach_lot_count or 0

    if is_breach:
        # De Minimis 閾値超過 → 審査案件を自動作成
        title = (
            f"[ERP De Minimis BREACH] {mat} 原産国変更 "
            f"{body.from_country or '?'}→{body.to_country or '?'} "
            f"US含有率 {pct:.1f}%"
        )
        usage_text = (
            f"ERP 原産国切り替えイベント (MATERIAL_ORIGIN_CHANGE)\n"
            f"原料コード: {mat}\n"
            f"切り替え: {body.from_country}→{body.to_country} @ {body.effective_date or '不明'}\n"
            f"US含有率: {pct:.1f}% (閾値 25% 超過)\n"
            f"影響品目: {affected}\n"
            f"BREACH ロット数: {lot_count}"
        )
        tx = Transaction(
            title=title,
            case_no=_generate_case_no(db),
            status="draft",
            source_module="ERP",
            erp_case_no=f"OCL-{mat}-{body.effective_date or 'NA'}",
        )
        db.add(tx)
        db.flush()

        db.add(UsageRequirement(
            transaction_id=tx.id,
            transaction_item_id=None,
            source="ERP",
            text=usage_text,
            risk_tags=["de_minimis_breach", "us_origin_change"],
            created_by="erp",
        ))
        db.commit()

        _logger.info(
            "ERP MATERIAL_ORIGIN_CHANGE BREACH: mat=%s pct=%.1f case=%s",
            mat, pct, tx.case_no,
        )
        return {
            "ok": True,
            "event_type": body.event_type,
            "case_ref": tx.case_no,
            "transaction_id": tx.id,
            "breach": True,
            "us_content_pct": pct,
            "message": f"De Minimis BREACH案件を自動作成しました: {tx.case_no}",
        }
    else:
        # 閾値以下 → ログのみ
        _logger.info(
            "ERP MATERIAL_ORIGIN_CHANGE (no breach): mat=%s pct=%.1f from=%s to=%s",
            mat, pct, body.from_country, body.to_country,
        )
        return {
            "ok": True,
            "event_type": body.event_type,
            "case_ref": None,
            "breach": False,
            "us_content_pct": pct,
            "message": f"原産国変更を記録しました（閾値以下のため案件未作成）: {mat}",
        }


def _handle_deemed_export_risk_event(body: _ErpEventPayload, db: Session) -> Dict[str, Any]:
    """みなし輸出 HIGH/CRITICAL リスク確定時に取引審査案件を自動作成する。"""
    case_id = body.material_code or "UNKNOWN"
    risk = body.deemed_export_risk_level or "HIGH"
    person = body.person_name or "不明"
    title_text = body.case_title or case_id
    factors = ", ".join(str(f) for f in (body.top_factors or []))

    title = f"[みなし輸出リスク {risk}] {title_text} 関係者: {person}"
    usage_text = (
        f"rnd_assessment みなし輸出リスク通知\n"
        f"R&D案件: {title_text} (case_id={case_id})\n"
        f"関係者: {person}\n"
        f"リスクレベル: {risk}\n"
        f"主要因: {factors or '詳細なし'}\n"
        f"推奨対応: {body.recommendation or '要確認'}"
    )
    tx = Transaction(
        title=title,
        case_no=_generate_case_no(db),
        status="draft",
        source_module="rnd_assessment",
        erp_case_no=f"RND-{case_id}",
    )
    db.add(tx)
    db.flush()
    db.add(UsageRequirement(
        transaction_id=tx.id,
        transaction_item_id=None,
        source="rnd_assessment",
        text=usage_text,
        risk_tags=["deemed_export", f"risk_{risk.lower()}"],
        created_by="rnd_assessment",
    ))
    db.commit()
    _logger.info("DEEMED_EXPORT_RISK case created: %s risk=%s person=%s", tx.case_no, risk, person)
    return {
        "ok": True,
        "event_type": body.event_type,
        "case_ref": tx.case_no,
        "transaction_id": tx.id,
        "risk_level": risk,
        "message": f"みなし輸出リスク審査案件を自動作成しました: {tx.case_no}",
    }


def _generate_case_no(db: Session) -> str:
    """CASE-YYYY-NNNN 形式のケース番号を生成する。"""
    from datetime import datetime as _dt
    year = _dt.utcnow().year
    prefix = f"CASE-{year}-"
    last = (
        db.query(Transaction.case_no)
        .filter(Transaction.case_no.like(f"{prefix}%"))
        .order_by(desc(Transaction.case_no))
        .first()
    )
    if last:
        try:
            seq = int(last[0].split("-")[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


@router.post("/{tx_id}/flag-for-review")
def flag_transaction_for_review(
    tx_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """スクリーニング再ヒット・コンプライアンスイベント発生時に取引を NEEDS_REVIEW へ強制遷移し ERP へ push 通知する。"""
    from datetime import datetime as _dt
    tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
    if tx is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    note = f"[再スクリーニングヒット {_dt.utcnow().strftime('%Y-%m-%d')}] 取引先スクリーニング再判定により審査中断"
    tx.tier_reason = f"{tx.tier_reason}\n{note}" if tx.tier_reason else note
    if tx.status == "approved":
        tx.status = "in_review"
    db.commit()

    _push_erp_status(tx, "NEEDS_REVIEW", "re_screening_hit")
    _logger.info("flag-for-review: tx=%d case=%s erp=%s", tx.id, tx.case_no, tx.erp_case_no)
    return {"ok": True, "tx_id": tx_id, "case_no": tx.case_no, "erp_notified": bool(tx.erp_case_no)}


class _ComplianceClearedBody(BaseModel):
    item_code: str


@router.post("/product-compliance-cleared")
def notify_product_compliance_cleared(
    body: _ComplianceClearedBody,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """item_version イベント解決後に呼ばれる。品目コードに紐付く ERP 連携取引へ APPROVED を再通知する。"""
    targets = (
        db.query(Transaction)
        .filter(
            Transaction.linked_product_code == body.item_code,
            Transaction.erp_case_no.isnot(None),
            Transaction.erp_case_no != "",
            Transaction.status.in_(["in_review", "approved", "draft"]),
        )
        .all()
    )
    notified = []
    for tx in targets:
        _push_erp_status(tx, "APPROVED", f"compliance_event_resolved:{body.item_code}")
        notified.append({"tx_id": tx.id, "case_no": tx.case_no, "erp_case_no": tx.erp_case_no})
    _logger.info("product-compliance-cleared: item=%s → notified %d txns", body.item_code, len(notified))
    return {"ok": True, "item_code": body.item_code, "notified_transactions": notified}


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
