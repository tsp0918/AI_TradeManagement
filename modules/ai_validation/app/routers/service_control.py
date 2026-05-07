"""
service_control.py — 役務取引管理ルーター（外為法 第25条）

エンドポイント:
  GET  /ui/services            一覧
  GET  /ui/services/new        新規フォーム
  POST /ui/services/new        登録
  GET  /ui/services/{id}       詳細
  POST /ui/services/{id}/submit  正式審査提出（7年保存期限設定）
  POST /ui/services/{id}/screen  スクリーニング実行
  POST /ui/services/{id}/license  許可番号・判定記録
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.db.models.service_transaction import (
    SERVICE_TYPE_LABELS, STATUS_LABELS,
    ServiceControlStatus, ServiceTransaction, ServiceType,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["service_control"])

SCREENING_BASE = os.environ.get("MODULE_SCREENING_URL", "http://localhost:8005")

_SERVICE_TYPES = [
    {"value": t.value, "label": SERVICE_TYPE_LABELS[t.value]}
    for t in ServiceType
]

_STATUS_ORDER = [
    "draft", "under_review", "approved", "license_required",
    "license_granted", "rejected", "withdrawn",
]


def _make_case_no() -> str:
    return f"SVC-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')[:18]}"


def _enrich(tx: ServiceTransaction) -> dict:
    """テンプレート向けに表示用フィールドを付加する。"""
    return {
        "obj": tx,
        "service_type_label": SERVICE_TYPE_LABELS.get(tx.service_type or "", "—"),
        "status_label":       STATUS_LABELS.get(tx.status, tx.status),
        "is_deemed":          bool(tx.is_deemed_export),
        "license_required_label": (
            "必要" if tx.license_required == 1
            else "不要" if tx.license_required == 0
            else "未判定"
        ),
    }


# ── 一覧 ─────────────────────────────────────────────────────────────────────────
@router.get("/ui/services", response_class=HTMLResponse)
def services_list(request: Request, db: Session = Depends(get_db)):
    rows = db.query(ServiceTransaction).order_by(desc(ServiceTransaction.id)).all()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "services.html", {
        "rows": [_enrich(r) for r in rows],
        "status_labels": STATUS_LABELS,
    })


# ── 新規フォーム ──────────────────────────────────────────────────────────────────
@router.get("/ui/services/new", response_class=HTMLResponse)
def service_new_form(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "service_new.html", {
        "service_types": _SERVICE_TYPES,
        "error": None,
    })


# ── 新規登録 ──────────────────────────────────────────────────────────────────────
@router.post("/ui/services/new", response_class=HTMLResponse)
def service_new_submit(
    request: Request,
    db: Session = Depends(get_db),
    title: str = Form(""),
    service_type: str = Form("other"),
    provider_name: Optional[str] = Form(None),
    provider_dept: Optional[str] = Form(None),
    recipient_name: Optional[str] = Form(None),
    recipient_organization: Optional[str] = Form(None),
    recipient_country: Optional[str] = Form(None),
    recipient_nationality: Optional[str] = Form(None),
    technology_description: Optional[str] = Form(None),
    eccn: Optional[str] = Form(None),
    fefta_category: Optional[str] = Form(None),
    contract_start: Optional[str] = Form(None),
    contract_end: Optional[str] = Form(None),
    contract_value_jpy: Optional[float] = Form(None),
    evaluator_name: Optional[str] = Form(None),
    evaluator_title: Optional[str] = Form(None),
    is_deemed_export: Optional[str] = Form(None),
    evaluator_note: Optional[str] = Form(None),
):
    templates = request.app.state.templates
    if not title.strip():
        return templates.TemplateResponse(request, "service_new.html", {
            "service_types": _SERVICE_TYPES, "error": "役務タイトルを入力してください。",
        })

    def _parse_date(s: Optional[str]) -> Optional[datetime]:
        if not s or not s.strip():
            return None
        try:
            return datetime.strptime(s.strip(), "%Y-%m-%d")
        except ValueError:
            return None

    tx = ServiceTransaction(
        case_no=_make_case_no(),
        title=title.strip(),
        service_type=service_type,
        provider_name=(provider_name or "").strip() or None,
        provider_dept=(provider_dept or "").strip() or None,
        recipient_name=(recipient_name or "").strip() or None,
        recipient_organization=(recipient_organization or "").strip() or None,
        recipient_country=((recipient_country or "").strip().upper() or None),
        recipient_nationality=((recipient_nationality or "").strip().upper() or None),
        technology_description=(technology_description or "").strip() or None,
        eccn=(eccn or "").strip().upper() or None,
        fefta_category=(fefta_category or "").strip() or None,
        contract_start=_parse_date(contract_start),
        contract_end=_parse_date(contract_end),
        contract_value_jpy=contract_value_jpy,
        evaluator_name=(evaluator_name or "").strip() or None,
        evaluator_title=(evaluator_title or "").strip() or None,
        is_deemed_export=1 if is_deemed_export else 0,
        evaluator_note=(evaluator_note or "").strip() or None,
        status=ServiceControlStatus.draft.value,
    )
    try:
        db.add(tx)
        db.flush()
        # 受領者名が設定されていれば自動スクリーニング
        _auto_screen(db, tx)
        db.commit()
    except Exception as exc:
        db.rollback()
        return templates.TemplateResponse(request, "service_new.html", {
            "service_types": _SERVICE_TYPES, "error": f"登録エラー: {exc}",
        })
    return RedirectResponse(url=f"/ui/services/{tx.id}", status_code=303)


# ── 詳細 ─────────────────────────────────────────────────────────────────────────
@router.get("/ui/services/{service_id}", response_class=HTMLResponse)
def service_detail(service_id: int, request: Request, db: Session = Depends(get_db)):
    tx = db.get(ServiceTransaction, service_id)
    if not tx:
        raise HTTPException(status_code=404, detail="役務案件が見つかりません")
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "service_detail.html", {
        **_enrich(tx),
        "service_types":  _SERVICE_TYPES,
        "status_labels":  STATUS_LABELS,
        "status_options": _STATUS_ORDER,
    })


# ── 正式審査提出（7年保存期限・判定書番号 自動設定）────────────────────────────────
@router.post("/ui/services/{service_id}/submit")
def service_submit(service_id: int, db: Session = Depends(get_db)):
    tx = db.get(ServiceTransaction, service_id)
    if not tx:
        raise HTTPException(status_code=404, detail="not found")

    submitted_at = datetime.utcnow()
    tx.submitted_at = submitted_at
    tx.status = ServiceControlStatus.under_review.value

    if not tx.retention_until:
        tx.retention_until = submitted_at + timedelta(days=365 * 7 + 2)
    if not tx.judgment_no and tx.case_no:
        tx.judgment_no = f"SVC-JDG-{tx.case_no}-{submitted_at.strftime('%Y%m%d')}"

    db.commit()
    return RedirectResponse(url=f"/ui/services/{service_id}", status_code=303)


# ── スクリーニング ────────────────────────────────────────────────────────────────
@router.post("/ui/services/{service_id}/screen")
def service_screen(service_id: int, db: Session = Depends(get_db)):
    tx = db.get(ServiceTransaction, service_id)
    if not tx:
        raise HTTPException(status_code=404, detail="not found")
    target = tx.recipient_name or tx.recipient_organization
    if not target:
        raise HTTPException(status_code=400, detail="受領者名が未設定です")

    _auto_screen(db, tx, force=True)
    db.commit()
    return RedirectResponse(url=f"/ui/services/{service_id}", status_code=303)


# ── 許可判定記録 ──────────────────────────────────────────────────────────────────
@router.post("/ui/services/{service_id}/license")
def service_license(
    service_id: int,
    db: Session = Depends(get_db),
    license_required: int = Form(...),   # 0 or 1
    license_no: Optional[str] = Form(None),
    new_status: Optional[str] = Form(None),
    evaluator_note: Optional[str] = Form(None),
):
    tx = db.get(ServiceTransaction, service_id)
    if not tx:
        raise HTTPException(status_code=404, detail="not found")

    tx.license_required = license_required
    if license_no:
        tx.license_no = license_no.strip()
    if new_status and new_status in _STATUS_ORDER:
        tx.status = new_status
    if evaluator_note:
        tx.evaluator_note = evaluator_note.strip()

    db.commit()
    return RedirectResponse(url=f"/ui/services/{service_id}", status_code=303)


# ── 内部ヘルパー ──────────────────────────────────────────────────────────────────
def _auto_screen(db: Session, tx: ServiceTransaction, force: bool = False) -> None:
    target = (tx.recipient_name or tx.recipient_organization or "").strip()
    if not target:
        return
    if not force and tx.screening_status:
        return
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.post(
                f"{SCREENING_BASE}/api/screen",
                json={"company_name": target, "threshold": 0.75},
            )
        if r.status_code == 200:
            data = r.json()
            tx.screening_result_id = str(data.get("id", ""))
            tx.screening_status = data.get("result_status", "")
            db.flush()
    except Exception as exc:
        logger.warning("Service screening failed for svc_id=%s: %s", tx.id, exc)
