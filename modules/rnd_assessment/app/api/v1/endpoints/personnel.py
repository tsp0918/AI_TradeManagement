"""Personnel JSON API — みなし輸出管理 人物登録・照会エンドポイント。

ERP・外部システムからのプログラマティック登録を可能にする。
POST /api/v1/personnel     — 登録（登録直後に自動スクリーニング実行）
GET  /api/v1/personnel     — 一覧（case_id フィルタ可）
GET  /api/v1/personnel/{id} — 詳細
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.personnel import Personnel

router = APIRouter()


# ── スキーマ ──────────────────────────────────────────────────────────────────

class PersonnelCreate(BaseModel):
    case_id:               Optional[str]   = None
    tenant_id:             Optional[str]   = None
    name:                  str
    role:                  Optional[str]   = None  # researcher / employee / contractor
    affiliation:           Optional[str]   = None
    nationality:           Optional[str]   = None  # ISO 2-letter
    residence_country:     Optional[str]   = None
    years_in_japan:        Optional[float] = None
    dual_employment_flag:  bool            = False
    dual_employer_name:    Optional[str]   = None
    dual_employer_country: Optional[str]   = None
    tech_access_eccn:      Optional[str]   = None
    tech_access_fefta:     Optional[str]   = None
    note:                  Optional[str]   = None


class PersonnelRead(BaseModel):
    personnel_id:          str
    case_id:               Optional[str]
    tenant_id:             Optional[str]
    name:                  str
    role:                  Optional[str]
    affiliation:           Optional[str]
    nationality:           Optional[str]
    residence_country:     Optional[str]
    years_in_japan:        Optional[float]
    dual_employment_flag:  bool
    dual_employer_name:    Optional[str]
    dual_employer_country: Optional[str]
    tech_access_eccn:      Optional[str]
    tech_access_fefta:     Optional[str]
    deemed_export_category: Optional[str]
    deemed_export_risk:    Optional[str]
    deemed_export_reason:  Optional[str]
    screened_at:           Optional[datetime]
    note:                  Optional[str]
    created_at:            datetime

    class Config:
        from_attributes = True


# ── エンドポイント ────────────────────────────────────────────────────────────

@router.post("", response_model=PersonnelRead, status_code=201)
def create_personnel(payload: PersonnelCreate, db: Session = Depends(get_db)):
    """みなし輸出対象人物を登録し、登録直後に自動スクリーニングを実行する。"""
    person = Personnel(
        case_id               = payload.case_id,
        tenant_id             = payload.tenant_id,
        name                  = payload.name.strip(),
        role                  = payload.role,
        affiliation           = payload.affiliation,
        nationality           = (payload.nationality or "").upper() or None,
        residence_country     = (payload.residence_country or "").upper() or None,
        years_in_japan        = payload.years_in_japan,
        dual_employment_flag  = payload.dual_employment_flag,
        dual_employer_name    = payload.dual_employer_name,
        dual_employer_country = (payload.dual_employer_country or "").upper() or None,
        tech_access_eccn      = payload.tech_access_eccn,
        tech_access_fefta     = payload.tech_access_fefta,
        note                  = payload.note,
    )
    db.add(person)
    db.commit()
    db.refresh(person)

    # 自動スクリーニング（UI router の _run_deemed_screening と同ロジック）
    try:
        from app.ui.router import _run_deemed_screening
        _run_deemed_screening(person)
        db.commit()
        db.refresh(person)
    except Exception:
        pass  # スクリーニング失敗でも登録自体は成功とする

    return person


@router.get("", response_model=list[PersonnelRead])
def list_personnel(
    case_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """人物一覧を取得。case_id / tenant_id でフィルタ可能。"""
    q = db.query(Personnel)
    if case_id:
        q = q.filter(Personnel.case_id == case_id)
    if tenant_id:
        q = q.filter(Personnel.tenant_id == tenant_id)
    return q.order_by(Personnel.created_at.desc()).all()


@router.get("/{personnel_id}", response_model=PersonnelRead)
def get_personnel(personnel_id: str, db: Session = Depends(get_db)):
    """人物詳細を取得。"""
    person = db.query(Personnel).filter_by(personnel_id=personnel_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="personnel not found")
    return person
