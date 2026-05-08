from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud import rd_case as crud
from app.models.rd_case import RDCases, RndAccessLog
from app.schemas.rd_case import CaseCreate, CaseRead

router = APIRouter()

_SENSITIVITY_ORDER = {"public": 0, "controlled": 1, "restricted": 2, "secret": 3}


def _record_access(
    db: Session,
    case: RDCases,
    org_id: Optional[str],
    user_id: Optional[str],
    action: str = "view",
) -> None:
    """アクセスログを記録し、みなし輸出条件に該当する場合はフラグを立てる。"""
    sensitivity_level = _SENSITIVITY_ORDER.get(case.tech_sensitivity or "public", 0)
    # controlled 以上 かつ 異なるorg = みなし輸出候補
    deemed = (
        sensitivity_level >= 1
        and org_id
        and org_id != case.tenant_id
    )
    log = RndAccessLog(
        case_id=case.case_id,
        user_id=user_id,
        org_id=org_id,
        action=action,
        sensitivity_at_access=case.tech_sensitivity,
        deemed_export_flagged="true" if deemed else "false",
        created_at=datetime.utcnow(),
    )
    db.add(log)
    db.commit()


class SensitivityUpdate(BaseModel):
    tech_sensitivity: str
    access_org_ids: Optional[list[str]] = None


@router.post("", response_model=CaseRead)
def create_case(payload: CaseCreate, db: Session = Depends(get_db)):
    return crud.create_case(
        db,
        tenant_id=payload.tenant_id,
        external_project_id=payload.external_project_id,
        title=payload.title,
        description=payload.description,
        created_by_user_id=payload.created_by_user_id,
    )


@router.get("/{case_id}", response_model=CaseRead)
def get_case(
    case_id: str,
    db: Session = Depends(get_db),
    x_org_id: Optional[str] = Header(None, alias="X-Organization-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    obj = crud.get_case(db, case_id)
    if not obj:
        raise HTTPException(status_code=404, detail="case not found")
    _record_access(db, obj, x_org_id, x_user_id, "view")
    return obj


@router.patch("/{case_id}/sensitivity")
def update_sensitivity(
    case_id: str,
    payload: SensitivityUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """tech_sensitivity と access_org_ids を更新する。"""
    if payload.tech_sensitivity not in _SENSITIVITY_ORDER:
        raise HTTPException(status_code=400, detail="sensitivity は public/controlled/restricted/secret のいずれか")
    obj = db.query(RDCases).filter(RDCases.case_id == case_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="case not found")
    obj.tech_sensitivity = payload.tech_sensitivity
    obj.access_org_ids = payload.access_org_ids
    obj.updated_at = datetime.utcnow()
    db.commit()
    return {"case_id": case_id, "tech_sensitivity": obj.tech_sensitivity, "access_org_ids": obj.access_org_ids}


@router.get("/{case_id}/access-logs")
def get_access_logs(
    case_id: str,
    db: Session = Depends(get_db),
    limit: int = 50,
) -> list[dict[str, Any]]:
    """アクセスログ一覧（最新順）。みなし輸出フラグ付き。"""
    logs = (
        db.query(RndAccessLog)
        .filter(RndAccessLog.case_id == case_id)
        .order_by(RndAccessLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "log_id": l.log_id,
            "user_id": l.user_id,
            "org_id": l.org_id,
            "action": l.action,
            "sensitivity_at_access": l.sensitivity_at_access,
            "deemed_export_flagged": l.deemed_export_flagged,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in logs
    ]


@router.get("/{case_id}/deemed-export-alerts")
def get_deemed_export_alerts(
    case_id: str,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """みなし輸出フラグが立ったアクセスのみ返す。"""
    logs = (
        db.query(RndAccessLog)
        .filter(
            RndAccessLog.case_id == case_id,
            RndAccessLog.deemed_export_flagged == "true",
        )
        .order_by(RndAccessLog.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "log_id": l.log_id,
            "user_id": l.user_id,
            "org_id": l.org_id,
            "sensitivity_at_access": l.sensitivity_at_access,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in logs
    ]
