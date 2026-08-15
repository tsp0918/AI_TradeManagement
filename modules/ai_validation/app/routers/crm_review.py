"""CRM 連携 2段階審査エンドポイント。

IF-01  POST /api/crm/provisional-review    — 仮審査起票（30日有効・ハッシュ一致で再利用）
IF-02  POST /api/crm/formal-review         — 本審査起票（仮審査継承チェック付き）
IF-03  GET  /api/crm/review-status/{case_no} — 審査ステータス照会

認証: HMAC-SHA256 (X-Timestamp + X-Signature ヘッダー)
      開発環境は CRM_SKIP_HMAC=1 でバイパス可能
"""
from __future__ import annotations

import datetime
import hashlib
import hmac as _hmac
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.db.models.transaction import Transaction
from app.db.session import SessionLocal
from app.utils.review_hash import compute_review_key_hash
from app.services.pipeline.orchestrator import run_until_matrix_match
from .api_transactions import _make_case_no_crm

router = APIRouter(prefix="/api/crm", tags=["crm-review"])
_logger = logging.getLogger(__name__)

_CRM_SECRET = os.environ.get("CRM_SIGNING_SECRET", "dev-crm-signing-secret-000")
_SKIP_HMAC = os.environ.get("CRM_SKIP_HMAC", "") == "1"
_PROV_VALID_DAYS = 30
_PLATFORM_CORE_URL = os.environ.get("MODULE_PLATFORM_URL", "http://localhost:8000")


def _run_matrix_match_bg(tx_id: int) -> None:
    """仮審査・本審査の新規起票後に AI 判定パイプライン（matrix_match まで）をバックグラウンド実行する。"""
    def _run():
        db = SessionLocal()
        try:
            run_until_matrix_match(db=db, transaction_id=tx_id)
            _logger.info("auto matrix_match completed: tx_id=%d", tx_id)
        except Exception as exc:
            _logger.warning("auto matrix_match failed: tx_id=%d exc=%s", tx_id, exc)
        finally:
            db.close()
    threading.Thread(target=_run, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────────
# HMAC 検証
# ──────────────────────────────────────────────────────────────────────────────

def _verify_crm_signature(request: Request, body: bytes) -> None:
    if _SKIP_HMAC:
        return
    ts = request.headers.get("X-Timestamp", "")
    sig = request.headers.get("X-Signature", "")
    try:
        if abs(time.time() - int(ts)) > 300:
            raise HTTPException(status_code=401, detail="Timestamp expired")
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Missing or invalid X-Timestamp")
    mac = _hmac.new(_CRM_SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256)
    expected = f"sha256={mac.hexdigest()}"
    if not _hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")


# ──────────────────────────────────────────────────────────────────────────────
# CRM Webhook 通知（IF-15: review.status_changed）
# ──────────────────────────────────────────────────────────────────────────────

def _notify_crm_webhook(event_type: str, payload: Dict[str, Any]) -> None:
    """platform-core の /internal/webhooks/dispatch 経由で CRM に非同期通知する。
    fire-and-forget（失敗しても審査フローは止めない）。
    """
    def _post() -> None:
        try:
            import httpx
            with httpx.Client(timeout=5) as client:
                client.post(
                    f"{_PLATFORM_CORE_URL}/internal/webhooks/dispatch",
                    json={
                        "event_type": event_type,
                        "payload": payload,
                        "target_system": "crm",
                    },
                )
        except Exception as exc:
            _logger.debug("CRM webhook dispatch failed (non-critical): %s", exc)

    threading.Thread(target=_post, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────────
# リクエストスキーマ
# ──────────────────────────────────────────────────────────────────────────────

class _ProductIn(BaseModel):
    product_code: str
    quantity: float = 1.0


class ProvisionalReviewRequest(BaseModel):
    title: str
    counterparty_name: Optional[str] = None
    destination_country: str
    end_user_party_id: Optional[str] = None
    end_use: str
    total_value_usd: Optional[float] = None
    products: List[_ProductIn] = []
    revision: int = 1
    crm_quote_id: Optional[str] = None
    crm_engagement_id: Optional[str] = None


class FormalReviewRequest(BaseModel):
    title: str
    counterparty_name: Optional[str] = None
    destination_country: str
    end_user_party_id: Optional[str] = None
    end_use: str
    total_value_usd: Optional[float] = None
    products: List[_ProductIn] = []
    revision: int = 1
    parent_case_no: Optional[str] = None   # 仮審査 case_no（省略時はハッシュで自動検索）
    crm_contract_id: Optional[str] = None
    crm_quote_id: Optional[str] = None
    crm_engagement_id: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────────────────────────────────────

def _build_hash(body: ProvisionalReviewRequest | FormalReviewRequest) -> str:
    codes = [p.product_code for p in body.products]
    qtys = [p.quantity for p in body.products]
    return compute_review_key_hash(
        product_codes=codes or [""],
        quantities=qtys or [1.0],
        destination_country=body.destination_country,
        end_user_party_id=body.end_user_party_id,
        end_use=body.end_use,
        total_value_usd=body.total_value_usd,
    )


def _find_valid_provisional(db: Session, review_key_hash: str) -> Optional[Dict[str, Any]]:
    """同一ハッシュの有効期限内の仮審査レコードを返す。"""
    now = datetime.datetime.utcnow()
    row = db.execute(
        text(
            "SELECT id, case_no, status, valid_until, agent_judgment_status "
            "FROM transactions "
            "WHERE review_key_hash = :h "
            "  AND review_type = 'provisional' "
            "  AND valid_until > :now "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"h": review_key_hash, "now": now},
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "case_no": row[1],
        "status": row[2],
        "valid_until": row[3],
        "agent_judgment_status": row[4],
    }


def _create_crm_tx(
    db: Session,
    *,
    review_type: str,
    body: ProvisionalReviewRequest | FormalReviewRequest,
    review_key_hash: str,
    parent_case_no: Optional[str] = None,
    inherited_from: Optional[str] = None,
) -> Transaction:
    case_no = _make_case_no_crm(review_type)
    tx = Transaction(
        case_no=case_no,
        title=body.title.strip() or f"CRM 審査 ({review_type})",
        status="draft",
        counterparty_name=body.counterparty_name,
        destination_country=body.destination_country,
        source_module="crm",
    )
    if body.products:
        tx.linked_product_code = body.products[0].product_code
        tx.quantity = body.products[0].quantity
    if body.total_value_usd is not None:
        tx.total_value_usd = body.total_value_usd
    if body.end_user_party_id:
        tx.end_user_name = body.end_user_party_id
    tx.end_use_description = body.end_use

    db.add(tx)
    db.flush()

    valid_until: Optional[datetime.datetime] = (
        datetime.datetime.utcnow() + datetime.timedelta(days=_PROV_VALID_DAYS)
        if review_type == "provisional"
        else None
    )
    crm_quote_id = getattr(body, "crm_quote_id", None)
    crm_contract_id = getattr(body, "crm_contract_id", None)
    crm_engagement_id = getattr(body, "crm_engagement_id", None)

    # CRM 専用列は _ensure_columns() で追加された列なので raw SQL で更新
    db.execute(
        text(
            "UPDATE transactions SET"
            "  review_type = :rt,"
            "  review_key_hash = :h,"
            "  parent_case_no = :pcn,"
            "  valid_until = :vu,"
            "  inherited_from = :inh,"
            "  revision = :rev,"
            "  crm_quote_id = :qid,"
            "  crm_contract_id = :cid,"
            "  crm_engagement_id = :eid"
            "  WHERE id = :txid"
        ),
        {
            "rt": review_type,
            "h": review_key_hash,
            "pcn": parent_case_no,
            "vu": valid_until,
            "inh": inherited_from,
            "rev": body.revision,
            "qid": crm_quote_id,
            "cid": crm_contract_id,
            "eid": crm_engagement_id,
            "txid": tx.id,
        },
    )
    db.commit()
    return tx


# ──────────────────────────────────────────────────────────────────────────────
# IF-01: 仮審査起票
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/provisional-review", status_code=201)
async def provisional_review(
    request: Request,
    body: ProvisionalReviewRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """CRM からの仮審査起票。

    - 同一内容（review_key_hash 一致）の有効な仮審査が存在する場合: 新規作成せず既存を返す
    - 存在しない場合: 新規 TX（case_no=CRM-PROV-...）を作成し valid_until=+30日を設定
    """
    raw = await request.body()
    _verify_crm_signature(request, raw)

    review_key_hash = _build_hash(body)
    existing = _find_valid_provisional(db, review_key_hash)
    if existing:
        _logger.info(
            "IF-01 hash match — reuse provisional case_no=%s", existing["case_no"]
        )
        return {
            "case_no": existing["case_no"],
            "review_type": "provisional",
            "status": existing["status"],
            "agent_judgment_status": existing["agent_judgment_status"],
            "valid_until": str(existing["valid_until"]) if existing["valid_until"] else None,
            "created": False,
            "review_key_hash": review_key_hash,
        }

    tx = _create_crm_tx(
        db,
        review_type="provisional",
        body=body,
        review_key_hash=review_key_hash,
    )
    valid_until_dt = datetime.datetime.utcnow() + datetime.timedelta(days=_PROV_VALID_DAYS)
    _logger.info("IF-01 new provisional created case_no=%s", tx.case_no)
    _run_matrix_match_bg(tx.id)

    resp = {
        "case_no": tx.case_no,
        "review_type": "provisional",
        "status": tx.status,
        "agent_judgment_status": None,
        "valid_until": valid_until_dt.isoformat(),
        "created": True,
        "review_key_hash": review_key_hash,
    }
    _notify_crm_webhook("review.provisional.created", {
        "case_no": tx.case_no,
        "crm_quote_id": body.crm_quote_id,
        "review_key_hash": review_key_hash,
        "valid_until": valid_until_dt.isoformat(),
        "status": tx.status,
    })
    return resp


# ──────────────────────────────────────────────────────────────────────────────
# IF-02: 本審査起票
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/formal-review", status_code=201)
async def formal_review(
    request: Request,
    body: FormalReviewRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """CRM からの本審査起票。

    仮審査継承チェック:
    1. parent_case_no が指定されている場合: その仮審査を直接参照
    2. 省略された場合: review_key_hash で有効な仮審査を自動検索
    3. 仮審査が有効 + ハッシュ一致 → inherited_from に仮審査 case_no を設定（スムーズ完了）
    4. 仮審査なし or 期限切れ → 通常の本審査として新規起票
    """
    raw = await request.body()
    _verify_crm_signature(request, raw)

    review_key_hash = _build_hash(body)
    inherited_from: Optional[str] = None
    provisional_info: Optional[Dict[str, Any]] = None

    # parent_case_no 直接指定の場合
    if body.parent_case_no:
        now = datetime.datetime.utcnow()
        row = db.execute(
            text(
                "SELECT id, case_no, status, valid_until, review_key_hash, agent_judgment_status "
                "FROM transactions "
                "WHERE case_no = :cn AND review_type = 'provisional' LIMIT 1"
            ),
            {"cn": body.parent_case_no},
        ).fetchone()
        if row:
            prov_until_raw = row[3]
            # SQLite は DATETIME を文字列で返す場合があるのでパース
            if isinstance(prov_until_raw, str):
                try:
                    prov_until = datetime.datetime.fromisoformat(prov_until_raw.replace(" ", "T"))
                except ValueError:
                    prov_until = None
            else:
                prov_until = prov_until_raw
            prov_hash = row[4]
            if prov_until and prov_until > now and prov_hash == review_key_hash:
                inherited_from = row[1]
                provisional_info = {
                    "case_no": row[1],
                    "status": row[2],
                    "agent_judgment_status": row[5],
                }
    else:
        # ハッシュで自動検索
        existing = _find_valid_provisional(db, review_key_hash)
        if existing:
            inherited_from = existing["case_no"]
            provisional_info = existing

    tx = _create_crm_tx(
        db,
        review_type="formal",
        body=body,
        review_key_hash=review_key_hash,
        parent_case_no=body.parent_case_no or (provisional_info["case_no"] if provisional_info else None),
        inherited_from=inherited_from,
    )

    smooth_completion = inherited_from is not None
    _logger.info(
        "IF-02 formal created case_no=%s smooth=%s inherited_from=%s",
        tx.case_no, smooth_completion, inherited_from,
    )
    # 仮審査判定を継承しない場合のみ新規 AI 判定を実行
    if not smooth_completion:
        _run_matrix_match_bg(tx.id)
    resp = {
        "case_no": tx.case_no,
        "review_type": "formal",
        "status": tx.status,
        "agent_judgment_status": None,
        "created": True,
        "smooth_completion": smooth_completion,
        "inherited_from": inherited_from,
        "provisional_judgment": provisional_info.get("agent_judgment_status") if provisional_info else None,
        "review_key_hash": review_key_hash,
    }
    _notify_crm_webhook("review.formal.created", {
        "case_no": tx.case_no,
        "crm_contract_id": body.crm_contract_id,
        "smooth_completion": smooth_completion,
        "inherited_from": inherited_from,
        "review_key_hash": review_key_hash,
        "status": tx.status,
    })
    return resp


# ──────────────────────────────────────────────────────────────────────────────
# IF-03: 審査ステータス照会
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/review-status/{case_no}")
def review_status(
    case_no: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """CRM からの審査ステータス照会（IF-03）。

    仮審査・本審査いずれの case_no でも参照可能。
    valid_until を超えた仮審査は expired_provisional を返す。
    """
    row = db.execute(
        text(
            "SELECT id, case_no, status, review_type, valid_until, "
            "  agent_judgment_status, review_key_hash, inherited_from, "
            "  parent_case_no, crm_quote_id, crm_contract_id, crm_engagement_id "
            "FROM transactions "
            "WHERE case_no = :cn LIMIT 1"
        ),
        {"cn": case_no},
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"審査案件が見つかりません: {case_no}")

    review_type = row[3]
    valid_until_raw = row[4]
    valid_until_str: Optional[str] = None
    is_expired = False

    if valid_until_raw:
        valid_until_str = str(valid_until_raw)
        try:
            vu_dt = datetime.datetime.fromisoformat(valid_until_raw.replace(" ", "T")) \
                if isinstance(valid_until_raw, str) else valid_until_raw
            is_expired = vu_dt < datetime.datetime.utcnow()
        except (ValueError, TypeError):
            pass

    effective_status = row[2]
    if review_type == "provisional" and is_expired:
        effective_status = "expired_provisional"

    return {
        "case_no": row[1],
        "review_type": review_type,
        "status": effective_status,
        "is_expired": is_expired,
        "agent_judgment_status": row[5],
        "review_key_hash": row[6],
        "inherited_from": row[7],
        "parent_case_no": row[8],
        "valid_until": valid_until_str,
        "crm_quote_id": row[9],
        "crm_contract_id": row[10],
        "crm_engagement_id": row[11],
    }
