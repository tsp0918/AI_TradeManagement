"""サプライヤーポータル ルーター。

管理 API（担当者用）:
  GET  /api/supplier-portal/tokens              トークン一覧
  POST /api/supplier-portal/tokens              トークン発行
  GET  /api/supplier-portal/tokens/{id}         トークン詳細
  POST /api/supplier-portal/tokens/{id}/revoke  無効化

公開ポータル（サプライヤー用・認証不要）:
  GET  /supplier-portal/{token}          申告フォーム
  POST /supplier-portal/{token}/submit   申告送信 → SupplierAttestation 自動作成
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.supplier_attestation import SupplierAttestation
from platform_core.models.supplier_portal_token import SupplierPortalToken
from platform_core.models.supply_chain import SupplyChainNode

import pathlib
import shutil
from fastapi import UploadFile, File
from fastapi.responses import FileResponse

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "templates"
_UPLOADS_DIR   = pathlib.Path(__file__).parent.parent.parent / "uploads" / "supplier"
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(tags=["supplier_portal"])

_BASE_URL = "https://app.tsp-aitrademanagement.com"  # 外部公開 URL


# ── スキーマ ──────────────────────────────────────────────────────

class TokenCreate(BaseModel):
    node_id: str
    supplier_name: str
    supplier_email: str | None = None
    note_for_supplier: str | None = None
    max_uses: int = 1          # 0 = 無制限
    expires_days: int = 30     # 有効日数


class PortalSubmit(BaseModel):
    claimed_eccn: str | None = None
    claimed_country_of_origin: str | None = None
    claimed_us_content_pct: float | None = None
    is_us_origin_claimed: bool = False
    certificate_reference: str | None = None
    attestation_date: str | None = None   # YYYY-MM-DD
    expiry_date: str | None = None
    notes: str | None = None


def _serialize_token(t: SupplierPortalToken) -> dict:
    now = datetime.now(tz=timezone.utc)
    expired = t.expires_at.replace(tzinfo=timezone.utc) < now if t.expires_at.tzinfo is None \
        else t.expires_at < now
    exhausted = t.max_uses > 0 and t.use_count >= t.max_uses
    return {
        "id": str(t.id),
        "token": t.token,
        "portal_url": f"{_BASE_URL}/supplier-portal/{t.token}",
        "node_id": str(t.node_id),
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


async def _resolve_token(token_str: str, db: AsyncSession) -> SupplierPortalToken:
    """トークン文字列を検索し、有効性を検証して返す。"""
    r = await db.execute(
        select(SupplierPortalToken).where(SupplierPortalToken.token == token_str)
    )
    t = r.scalar_one_or_none()
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
async def list_tokens(
    node_id: str | None = Query(None),
    active_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SupplierPortalToken).order_by(SupplierPortalToken.created_at.desc())
    )
    tokens = result.scalars().all()
    now = datetime.now(tz=timezone.utc)
    filtered = []
    for t in tokens:
        if node_id and str(t.node_id) != node_id:
            continue
        if active_only:
            expires = t.expires_at if t.expires_at.tzinfo else t.expires_at.replace(tzinfo=timezone.utc)
            if not t.is_active or expires < now:
                continue
        filtered.append(t)
    return [_serialize_token(t) for t in filtered]


@router.post("/api/supplier-portal/tokens", status_code=201)
async def create_token(body: TokenCreate, db: AsyncSession = Depends(get_db)):
    node_id = uuid.UUID(body.node_id)
    rn = await db.execute(select(SupplyChainNode).where(SupplyChainNode.id == node_id))
    node = rn.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="SupplyChainNode not found")

    token_str = secrets.token_urlsafe(32)   # 43 文字の URL-safe 文字列
    expires_at = datetime.now(tz=timezone.utc) + timedelta(days=body.expires_days)

    t = SupplierPortalToken(
        token=token_str,
        node_id=node_id,
        node_name=node.name,
        supplier_name=body.supplier_name,
        supplier_email=body.supplier_email,
        note_for_supplier=body.note_for_supplier,
        max_uses=body.max_uses,
        expires_at=expires_at,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _serialize_token(t)


@router.get("/api/supplier-portal/tokens/{token_id}")
async def get_token(token_id: str, db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(SupplierPortalToken).where(SupplierPortalToken.id == uuid.UUID(token_id))
    )
    t = r.scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _serialize_token(t)


@router.post("/api/supplier-portal/tokens/{token_id}/revoke")
async def revoke_token(token_id: str, db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(SupplierPortalToken).where(SupplierPortalToken.id == uuid.UUID(token_id))
    )
    t = r.scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Not found")
    t.is_active = False
    await db.commit()
    return {"ok": True, "token_id": token_id}


# ── 公開ポータル（サプライヤー用） ───────────────────────────────

@router.get("/supplier-portal/{token_str}", response_class=HTMLResponse, include_in_schema=False)
async def portal_form(request: Request, token_str: str, db: AsyncSession = Depends(get_db)):
    """招待 URL にアクセスした外部サプライヤーへの申告フォーム画面。"""
    try:
        t = await _resolve_token(token_str, db)
    except HTTPException as e:
        return templates.TemplateResponse(
            request, "supplier_portal_error.html",
            {"message": e.detail},
            status_code=e.status_code,
        )
    # ノード情報（品名・HSコード・ECCNのヒント）
    rn = await db.execute(select(SupplyChainNode).where(SupplyChainNode.id == t.node_id))
    node = rn.scalar_one_or_none()
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
            "token_id": str(t.id),
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
async def portal_submit(request: Request, token_str: str, db: AsyncSession = Depends(get_db)):
    """サプライヤーが申告フォームを送信する。"""
    try:
        t = await _resolve_token(token_str, db)
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

    # SupplierAttestation 自動作成
    attest = SupplierAttestation(
        node_id=t.node_id,
        supplier_name=t.supplier_name,
        supplier_contact=_fv("supplier_contact") or t.supplier_email,
        claimed_eccn=_fv("claimed_eccn"),
        claimed_country_of_origin=_fv("claimed_country_of_origin"),
        claimed_us_content_pct=_ff("claimed_us_content_pct"),
        is_us_origin_claimed=form.get("is_us_origin_claimed") == "on",
        certificate_reference=_fv("certificate_reference"),
        attestation_date=_parse_date(_fv("attestation_date")),
        expiry_date=_parse_date(_fv("expiry_date")),
        notes=_fv("notes"),
        status="pending",
    )
    db.add(attest)
    await db.flush()  # ID 確定

    # ファイルアップロード処理
    supporting_docs = []
    upload_fields = form.getlist("documents")
    if upload_fields:
        attest_dir = _UPLOADS_DIR / str(attest.id)
        attest_dir.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
        for uploaded in upload_fields:
            if not hasattr(uploaded, "filename") or not uploaded.filename:
                continue
            safe_name = f"{now_str}_{uploaded.filename.replace('/', '_').replace('..', '_')}"
            dest = attest_dir / safe_name
            content = await uploaded.read()
            dest.write_bytes(content)
            supporting_docs.append({
                "filename":    safe_name,
                "original":    uploaded.filename,
                "size":        len(content),
                "uploaded_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            })
        if supporting_docs:
            attest.supporting_docs = supporting_docs

    # トークン使用回数を増やす
    t.use_count += 1
    if t.max_uses > 0 and t.use_count >= t.max_uses:
        t.is_active = False  # 使い切ったら自動無効化

    await db.commit()

    return templates.TemplateResponse(
        request, "supplier_portal_confirm.html",
        {
            "supplier_name": t.supplier_name,
            "node_name": t.node_name,
            "claimed_eccn": attest.claimed_eccn or "—",
            "claimed_country": attest.claimed_country_of_origin or "—",
            "attestation_id": str(attest.id),
        },
    )


@router.get("/api/supplier-attestations/{attest_id}/documents/{filename}")
async def download_document(attest_id: str, filename: str):
    """アップロード済み証明書をダウンロードする。"""
    path = _UPLOADS_DIR / attest_id / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="ファイルが見つかりません")
    # Path traversal 防止: 解決済みパスが _UPLOADS_DIR 配下であることを確認
    try:
        path.resolve().relative_to(_UPLOADS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="不正なパスです")
    return FileResponse(str(path), filename=filename)


def _parse_date(s: str | None):
    from datetime import date
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None
