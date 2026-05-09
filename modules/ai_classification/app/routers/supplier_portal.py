"""サプライヤーポータル — ai_classification 統合版（SQLite/sync）。

Phase 6A-2: platform-core/routers/supplier_portal.py から移管。
データは aicls_supplier_portal_token / aicls_supplier_attestation テーブルに格納。
"""

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AiClsSupplierAttestation, AiClsSupplierPortalToken, AiClsSupplyChainNode

templates = Jinja2Templates(directory="templates")

_UPLOADS_DIR = Path(__file__).parent.parent.parent / "uploads" / "supplier"
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

_BASE_URL = "https://app.tsp-aitrademanagement.com"

router = APIRouter(tags=["supplier_portal"])


# ── スキーマ ──────────────────────────────────────────────────────

class TokenCreate(BaseModel):
    node_id: str
    supplier_name: str
    supplier_email: str | None = None
    note_for_supplier: str | None = None
    max_uses: int = 1
    expires_days: int = 30


def _serialize_token(t: AiClsSupplierPortalToken) -> dict:
    now = datetime.now(tz=timezone.utc)
    expires = t.expires_at if t.expires_at.tzinfo else t.expires_at.replace(tzinfo=timezone.utc)
    expired = expires < now
    exhausted = t.max_uses > 0 and t.use_count >= t.max_uses
    return {
        "id": t.id,
        "token": t.token,
        "portal_url": f"{_BASE_URL}/supplier-portal/{t.token}",
        "node_id": t.node_id,
        "node_name": t.node_name,
        "supplier_name": t.supplier_name,
        "supplier_email": t.supplier_email,
        "note_for_supplier": t.note_for_supplier,
        "is_active": t.is_active,
        "max_uses": t.max_uses,
        "use_count": t.use_count,
        "expires_at": t.expires_at.isoformat(),
        "is_valid": t.is_active and not expired and not exhausted,
        "created_at": t.created_at.isoformat(),
    }


def _resolve_token(token_str: str, db: Session) -> AiClsSupplierPortalToken:
    t = db.execute(
        select(AiClsSupplierPortalToken).where(AiClsSupplierPortalToken.token == token_str)
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="招待 URL が見つかりません")
    if not t.is_active:
        raise HTTPException(status_code=403, detail="この招待 URL は無効化されています")
    now = datetime.now(tz=timezone.utc)
    expires = t.expires_at if t.expires_at.tzinfo else t.expires_at.replace(tzinfo=timezone.utc)
    if expires < now:
        raise HTTPException(status_code=403, detail="この招待 URL は有効期限が切れています")
    if t.max_uses > 0 and t.use_count >= t.max_uses:
        raise HTTPException(status_code=403, detail="この招待 URL は使用回数の上限に達しています")
    return t


# ── 管理 API ─────────────────────────────────────────────────────

@router.get("/api/supplier-portal/tokens")
def list_tokens(
    node_id: str | None = Query(None),
    active_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    tokens = db.execute(
        select(AiClsSupplierPortalToken).order_by(AiClsSupplierPortalToken.created_at.desc())
    ).scalars().all()
    now = datetime.now(tz=timezone.utc)
    filtered = []
    for t in tokens:
        if node_id and t.node_id != node_id:
            continue
        if active_only:
            expires = t.expires_at if t.expires_at.tzinfo else t.expires_at.replace(tzinfo=timezone.utc)
            if not t.is_active or expires < now:
                continue
        filtered.append(t)
    return [_serialize_token(t) for t in filtered]


@router.post("/api/supplier-portal/tokens", status_code=201)
def create_token(body: TokenCreate, db: Session = Depends(get_db)):
    node = db.execute(
        select(AiClsSupplyChainNode).where(AiClsSupplyChainNode.id == body.node_id)
    ).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="SupplyChainNode not found")

    t = AiClsSupplierPortalToken(
        id=str(uuid.uuid4()),
        token=secrets.token_urlsafe(32),
        node_id=body.node_id,
        node_name=node.name,
        supplier_name=body.supplier_name,
        supplier_email=body.supplier_email,
        note_for_supplier=body.note_for_supplier,
        max_uses=body.max_uses,
        expires_at=datetime.now(tz=timezone.utc) + timedelta(days=body.expires_days),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _serialize_token(t)


@router.get("/api/supplier-portal/tokens/{token_id}")
def get_token(token_id: str, db: Session = Depends(get_db)):
    t = db.execute(
        select(AiClsSupplierPortalToken).where(AiClsSupplierPortalToken.id == token_id)
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _serialize_token(t)


@router.post("/api/supplier-portal/tokens/{token_id}/revoke")
def revoke_token(token_id: str, db: Session = Depends(get_db)):
    t = db.execute(
        select(AiClsSupplierPortalToken).where(AiClsSupplierPortalToken.id == token_id)
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Not found")
    t.is_active = False
    db.commit()
    return {"ok": True, "token_id": token_id}


# ── 公開ポータル（サプライヤー用） ───────────────────────────────

@router.get("/supplier-portal/{token_str}", response_class=HTMLResponse, include_in_schema=False)
def portal_form(request: Request, token_str: str, db: Session = Depends(get_db)):
    try:
        t = _resolve_token(token_str, db)
    except HTTPException as e:
        return templates.TemplateResponse(
            request, "supplier_portal_error.html",
            {"message": e.detail},
            status_code=e.status_code,
        )
    node = db.execute(
        select(AiClsSupplyChainNode).where(AiClsSupplyChainNode.id == t.node_id)
    ).scalar_one_or_none()
    node_info = {
        "name": node.name if node else t.node_name,
        "part_number": node.part_number if node else "",
        "hs_code": node.hs_code if node else "",
        "node_type": node.node_type if node else "",
    }
    expires = t.expires_at if t.expires_at.tzinfo else t.expires_at.replace(tzinfo=timezone.utc)
    remaining_days = (expires - datetime.now(tz=timezone.utc)).days
    return templates.TemplateResponse(
        request, "supplier_portal.html",
        {
            "token": token_str,
            "token_id": t.id,
            "supplier_name": t.supplier_name,
            "supplier_email": t.supplier_email or "",
            "note_for_supplier": t.note_for_supplier or "",
            "node_info": node_info,
            "remaining_days": remaining_days,
            "use_count": t.use_count,
            "max_uses": t.max_uses,
        },
    )


@router.post("/supplier-portal/{token_str}/submit", response_class=HTMLResponse, include_in_schema=False)
async def portal_submit(request: Request, token_str: str, db: Session = Depends(get_db)):
    """ファイルアップロードのため async。"""
    try:
        t = _resolve_token(token_str, db)
    except HTTPException as e:
        return templates.TemplateResponse(
            request, "supplier_portal_error.html",
            {"message": e.detail},
            status_code=e.status_code,
        )

    form = await request.form()

    def _fv(key: str) -> str | None:
        v = form.get(key, "")
        return str(v).strip() or None

    def _ff(key: str) -> float | None:
        v = _fv(key)
        try:
            return float(v) if v else None
        except ValueError:
            return None

    attest_id = str(uuid.uuid4())
    attest = AiClsSupplierAttestation(
        id=attest_id,
        node_id=t.node_id,
        supplier_name=t.supplier_name,
        supplier_contact=_fv("supplier_contact") or t.supplier_email,
        claimed_eccn=_fv("claimed_eccn"),
        claimed_country_of_origin=_fv("claimed_country_of_origin"),
        claimed_us_content_pct=_ff("claimed_us_content_pct"),
        is_us_origin_claimed=form.get("is_us_origin_claimed") == "on",
        certificate_reference=_fv("certificate_reference"),
        attestation_date=_fv("attestation_date"),
        expiry_date=_fv("expiry_date"),
        notes=_fv("notes"),
        status="pending",
    )
    db.add(attest)
    db.flush()

    _MAX_FILE_BYTES = 20 * 1024 * 1024
    supporting_docs = []
    upload_fields = form.getlist("documents")
    if upload_fields:
        attest_dir = _UPLOADS_DIR / attest_id
        attest_dir.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
        for uploaded in upload_fields:
            if not hasattr(uploaded, "filename") or not uploaded.filename:
                continue
            safe_name = f"{now_str}_{uploaded.filename.replace('/', '_').replace('..', '_')}"
            dest = attest_dir / safe_name
            content = await uploaded.read(_MAX_FILE_BYTES + 1)
            if len(content) > _MAX_FILE_BYTES:
                continue
            dest.write_bytes(content)
            supporting_docs.append({
                "filename": safe_name,
                "original": uploaded.filename,
                "size": len(content),
                "uploaded_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            })
        if supporting_docs:
            attest.supporting_docs = json.dumps(supporting_docs)

    t.use_count += 1
    if t.max_uses > 0 and t.use_count >= t.max_uses:
        t.is_active = False
    db.commit()

    return templates.TemplateResponse(
        request, "supplier_portal_confirm.html",
        {
            "supplier_name": t.supplier_name,
            "node_name": t.node_name,
            "claimed_eccn": attest.claimed_eccn or "—",
            "claimed_country": attest.claimed_country_of_origin or "—",
            "attestation_id": attest_id,
        },
    )


@router.get("/api/supplier-attestations/{attest_id}/documents/{filename}")
def download_document(attest_id: str, filename: str):
    path = _UPLOADS_DIR / attest_id / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="ファイルが見つかりません")
    try:
        path.resolve().relative_to(_UPLOADS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="不正なパスです")
    return FileResponse(str(path), filename=filename)
