"""Phase 3: コンプライアンス・オーバーライド管理 API。

オーバーライドとは: 通常は規制該当判定となる取引について、責任者承認のもと
例外的に輸出を認める証跡を記録するしくみ。外為法的には証跡保存が必須。

Routes:
  POST /api/transactions/{id}/overrides   — オーバーライド申請作成
  GET  /api/transactions/{id}/overrides   — オーバーライド一覧
  GET  /api/overrides/{override_id}       — 個別オーバーライド取得
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.db.models.transaction import Transaction

router = APIRouter(tags=["compliance-override"])

_MIN_REASON_LEN = 50  # 理由文字数最低要件


# ──────────────────────────────────────────────────────────────────────────────
# スキーマ
# ──────────────────────────────────────────────────────────────────────────────

class OverrideCreateRequest(BaseModel):
    overridden_by: str            # 申請者氏名
    approver_name: str            # 承認者氏名
    approver_title: str           # 承認者役職
    approver_email: str           # 承認者メールアドレス
    reason: str                   # 理由（最低50文字）
    scope: str                    # 'transaction' | 'product' | 'counterparty'
    valid_until: str              # ISO 8601 日付文字列 (YYYY-MM-DD)
    evidence_path: Optional[str] = None
    department_head_approval: bool = False

    @field_validator("reason")
    @classmethod
    def reason_min_length(cls, v: str) -> str:
        if len(v.strip()) < _MIN_REASON_LEN:
            raise ValueError(f"理由は {_MIN_REASON_LEN} 文字以上必要です（現在: {len(v.strip())} 文字）")
        return v.strip()

    @field_validator("scope")
    @classmethod
    def scope_valid(cls, v: str) -> str:
        allowed = {"transaction", "product", "counterparty"}
        if v not in allowed:
            raise ValueError(f"scope は {allowed} のいずれかを指定してください")
        return v

    @field_validator("valid_until")
    @classmethod
    def valid_until_future(cls, v: str) -> str:
        try:
            dt = datetime.date.fromisoformat(v)
        except ValueError:
            raise ValueError("valid_until は YYYY-MM-DD 形式で指定してください")
        if dt <= datetime.date.today():
            raise ValueError("valid_until は未来の日付を指定してください")
        return v


# ──────────────────────────────────────────────────────────────────────────────
# エンドポイント
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/api/transactions/{transaction_id}/overrides", status_code=201)
def create_override(
    transaction_id: int,
    body: OverrideCreateRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """コンプライアンス・オーバーライドを申請する。

    rejected (match) 判定の取引に対して、責任者の承認のもとで例外承認できる。
    ビジネスルール: scope='transaction' かつ status='rejected' の場合は
    department_head_approval=True が必須（設計書§13）。
    """
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="取引が見つかりません")

    # rejected 判定には部門長承認必須
    if tx.status == "rejected" and body.scope == "transaction" and not body.department_head_approval:
        raise HTTPException(
            status_code=422,
            detail="rejected 判定のオーバーライドには department_head_approval=true が必要です",
        )

    result = db.execute(
        text(
            "INSERT INTO compliance_override "
            "(transaction_id, overridden_by, approver_name, approver_title, approver_email, "
            " reason, scope, valid_until, evidence_path, department_head_approval) "
            "VALUES (:tid, :ob, :an, :at, :ae, :reason, :scope, :vu, :ep, :dha)"
        ),
        {
            "tid": transaction_id,
            "ob": body.overridden_by.strip(),
            "an": body.approver_name.strip(),
            "at": body.approver_title.strip(),
            "ae": body.approver_email.strip(),
            "reason": body.reason,
            "scope": body.scope,
            "vu": body.valid_until,
            "ep": body.evidence_path,
            "dha": 1 if body.department_head_approval else 0,
        },
    )
    override_id = result.lastrowid
    db.commit()

    return {
        "id": override_id,
        "transaction_id": transaction_id,
        "case_no": tx.case_no,
        "scope": body.scope,
        "valid_until": body.valid_until,
        "department_head_approval": body.department_head_approval,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }


@router.get("/api/transactions/{transaction_id}/overrides")
def list_overrides(
    transaction_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """指定取引のオーバーライド一覧を返す（有効期限が過ぎたものも含む）。"""
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="取引が見つかりません")

    rows = db.execute(
        text(
            "SELECT id, overridden_by, approver_name, approver_title, approver_email, "
            "  reason, scope, valid_until, evidence_path, department_head_approval, created_at "
            "FROM compliance_override "
            "WHERE transaction_id = :tid ORDER BY created_at DESC"
        ),
        {"tid": transaction_id},
    ).fetchall()

    now = datetime.datetime.utcnow()
    items: List[Dict[str, Any]] = []
    for r in rows:
        vu_raw = r[7]
        try:
            vu_dt = datetime.datetime.fromisoformat(str(vu_raw).replace(" ", "T"))
            is_active = vu_dt > now
        except (ValueError, TypeError):
            is_active = False
        items.append({
            "id": r[0],
            "overridden_by": r[1],
            "approver_name": r[2],
            "approver_title": r[3],
            "approver_email": r[4],
            "reason": r[5],
            "scope": r[6],
            "valid_until": str(vu_raw),
            "is_active": is_active,
            "evidence_path": r[8],
            "department_head_approval": bool(r[9]),
            "created_at": str(r[10]),
        })

    return {"transaction_id": transaction_id, "case_no": tx.case_no, "overrides": items, "total": len(items)}


@router.get("/api/overrides/{override_id}")
def get_override(
    override_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """個別オーバーライドを取得する。"""
    row = db.execute(
        text(
            "SELECT id, transaction_id, overridden_by, approver_name, approver_title, "
            "  approver_email, reason, scope, valid_until, evidence_path, "
            "  department_head_approval, created_at "
            "FROM compliance_override WHERE id = :oid"
        ),
        {"oid": override_id},
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="オーバーライドが見つかりません")

    now = datetime.datetime.utcnow()
    try:
        vu_dt = datetime.datetime.fromisoformat(str(row[8]).replace(" ", "T"))
        is_active = vu_dt > now
    except (ValueError, TypeError):
        is_active = False

    return {
        "id": row[0],
        "transaction_id": row[1],
        "overridden_by": row[2],
        "approver_name": row[3],
        "approver_title": row[4],
        "approver_email": row[5],
        "reason": row[6],
        "scope": row[7],
        "valid_until": str(row[8]),
        "is_active": is_active,
        "evidence_path": row[9],
        "department_head_approval": bool(row[10]),
        "created_at": str(row[11]),
    }
