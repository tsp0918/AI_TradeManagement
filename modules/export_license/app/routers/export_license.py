"""輸出許可申請 ルーター。

EAR BIS-748P / 外為法 様式第1 のドラフト自動生成・申請ライフサイクル管理・期限アラート。

管理 API:
  GET  /api/export-licenses/stats                  ダッシュボード集計
  GET  /api/export-licenses                        申請一覧
  POST /api/export-licenses                        新規申請ドラフト作成
  GET  /api/export-licenses/{id}                   詳細
  PUT  /api/export-licenses/{id}                   更新
  DELETE /api/export-licenses/{id}                 削除
  POST /api/export-licenses/{id}/submit            申請済みに変更
  POST /api/export-licenses/{id}/approve           承認・許可番号記録
  POST /api/export-licenses/{id}/deny              却下
  POST /api/export-licenses/draft-from-transaction 取引データからドラフト生成
  GET  /api/export-licenses/{id}/preview           HTML プレビュー
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..pg_session import get_pg_db
from platform_core.models.export_license import ExportLicenseApplication

router = APIRouter(tags=["export_license"])

_AI_VALIDATION_URL = os.environ.get("MODULE_AI_VALIDATION_URL", "http://localhost:8011")

# ── Pydantic スキーマ ─────────────────────────────────────────────

class LicenseCreate(BaseModel):
    license_type: str = "EAR"            # EAR | FEFTA
    form_type: str = "BIS748P"           # BIS748P | FEFTA_FORM
    item_description: str | None = None
    eccn: str | None = None
    quantity: float | None = None
    quantity_unit: str | None = None
    value_usd: float | None = None
    destination_country: str | None = None
    consignee_name: str | None = None
    consignee_address: str | None = None
    end_use_description: str | None = None
    end_user_name: str | None = None
    expires_at: str | None = None        # ISO date
    transaction_ids: list[str] = []
    supply_chain_node_id: str | None = None
    notes: str | None = None


class LicenseApprove(BaseModel):
    license_number: str
    issuing_authority: str               # BIS | METI
    approved_at: str | None = None      # ISO datetime
    expires_at: str | None = None       # ISO datetime（許可証有効期限）
    license_value_remaining_usd: float | None = None


class DraftFromTransaction(BaseModel):
    transaction_id: int
    license_type: str = "EAR"           # EAR | FEFTA


class UseValueBody(BaseModel):
    shipment_value_usd: float
    description: str | None = None


# ── ユーティリティ ────────────────────────────────────────────────

_STATUS_LABEL = {
    "draft": "ドラフト",
    "submitted": "申請済み",
    "approved": "承認済み",
    "denied": "却下",
    "expired": "期限切れ",
    "not_required": "許可不要",
}

_FEFTA_ECCN_BASE = {
    "0A": "輸出令別表第1 第1項（武器）",
    "0B": "輸出令別表第1 第1項（武器関連）",
    "1C": "輸出令別表第1 第9項（化学兵器関連）",
    "2B": "輸出令別表第1 第2項（原材料・製造設備）",
    "3A": "輸出令別表第1 第7項（電子部品・半導体）",
    "3B": "輸出令別表第1 第7項（半導体製造装置）",
    "3C": "輸出令別表第1 第7項（半導体材料）",
    "3D": "輸出令別表第1 第7項（ソフトウェア）",
    "3E": "輸出令別表第1 第7項（技術）",
    "4A": "輸出令別表第1 第8項（コンピュータ）",
    "5A": "輸出令別表第1 第10項（通信・情報セキュリティ）",
    "6A": "輸出令別表第1 第6項（センサー・レーザー）",
    "7A": "輸出令別表第1 第3項（航法・航空電子）",
    "9A": "輸出令別表第1 第5項（航空宇宙・推進）",
}


def _fefta_legal_basis(eccn: str | None) -> str:
    if not eccn:
        return "輸出令別表第1 該当性要確認"
    prefix = eccn[:2].upper()
    return _FEFTA_ECCN_BASE.get(prefix, "輸出令別表第1 該当性要確認")


def _serialize(app: ExportLicenseApplication) -> dict:
    now = datetime.now(tz=timezone.utc)
    expires = None
    days_remaining = None
    if app.expires_at:
        expires = app.expires_at if app.expires_at.tzinfo else app.expires_at.replace(tzinfo=timezone.utc)
        days_remaining = (expires - now).days

    return {
        "id": str(app.id),
        "application_number": app.application_number,
        "license_type": app.license_type,
        "form_type": app.form_type,
        "status": app.status,
        "status_label": _STATUS_LABEL.get(app.status, app.status),
        "item_description": app.item_description,
        "eccn": app.eccn,
        "quantity": app.quantity,
        "quantity_unit": app.quantity_unit,
        "value_usd": app.value_usd,
        "destination_country": app.destination_country,
        "consignee_name": app.consignee_name,
        "consignee_address": app.consignee_address,
        "end_use_description": app.end_use_description,
        "end_user_name": app.end_user_name,
        "draft_content": app.draft_content or {},
        "transaction_ids": app.transaction_ids or [],
        "supply_chain_node_id": str(app.supply_chain_node_id) if app.supply_chain_node_id else None,
        "license_number": app.license_number,
        "issuing_authority": app.issuing_authority,
        "license_value_remaining_usd": app.license_value_remaining_usd,
        "submitted_at": app.submitted_at.isoformat() if app.submitted_at else None,
        "approved_at": app.approved_at.isoformat() if app.approved_at else None,
        "expires_at": app.expires_at.isoformat() if app.expires_at else None,
        "days_remaining": days_remaining,
        "is_expiring_soon": days_remaining is not None and 0 <= days_remaining <= 30,
        "is_expired": days_remaining is not None and days_remaining < 0,
        "notes": app.notes,
        "created_at": app.created_at.isoformat(),
        "updated_at": app.updated_at.isoformat(),
    }


def _build_draft_content_bis748p(data: dict) -> dict:
    return {
        "block1_applicant_name": data.get("applicant_name", ""),
        "block1_applicant_address": data.get("applicant_address", ""),
        "block1_applicant_ein": data.get("applicant_ein", ""),
        "block3_contact_name": data.get("contact_name", ""),
        "block3_contact_phone": data.get("contact_phone", ""),
        "block3_contact_email": data.get("contact_email", ""),
        "block4_application_type": "Individual Validated License (IVL)",
        "block7_destination_country": data.get("destination_country", ""),
        "block8_consignee_name": data.get("consignee_name", ""),
        "block8_consignee_address": data.get("consignee_address", ""),
        "block9_end_user_name": data.get("end_user_name", "") or data.get("consignee_name", ""),
        "block9_end_user_address": data.get("end_user_address", "") or data.get("consignee_address", ""),
        "block11_item_description": data.get("item_description", ""),
        "block11_eccn": data.get("eccn", ""),
        "block11_quantity": data.get("quantity", ""),
        "block11_unit": data.get("quantity_unit", ""),
        "block11_value_usd": data.get("value_usd", ""),
        "block13_license_exception": "",
        "block21_end_use": data.get("end_use_description", ""),
        "block22_supporting_docs": "Technical Specifications, End-User Statement",
    }


def _build_draft_content_fefta(data: dict) -> dict:
    eccn = data.get("eccn", "")
    return {
        "applicant_company": data.get("applicant_name", ""),
        "applicant_address": data.get("applicant_address", ""),
        "applicant_representative": data.get("contact_name", ""),
        "applicant_phone": data.get("contact_phone", ""),
        "item_name_jp": data.get("item_description", ""),
        "item_eccn": eccn,
        "item_legal_basis": _fefta_legal_basis(eccn),
        "item_quantity": data.get("quantity", ""),
        "item_unit": data.get("quantity_unit", ""),
        "item_value_jpy": "",
        "item_value_usd": data.get("value_usd", ""),
        "destination_country": data.get("destination_country", ""),
        "consignee_name": data.get("consignee_name", ""),
        "consignee_address": data.get("consignee_address", ""),
        "end_user_name": data.get("end_user_name", "") or data.get("consignee_name", ""),
        "end_use_description": data.get("end_use_description", ""),
        "meti_bureau": "経済産業局（管轄地域を選択）",
        "application_date": datetime.now(tz=timezone.utc).strftime("%Y年%m月%d日"),
    }


# ── 内部ヘルパー ──────────────────────────────────────────────────

async def _get_or_404(app_id: str, db: AsyncSession) -> ExportLicenseApplication:
    r = await db.execute(
        select(ExportLicenseApplication).where(
            ExportLicenseApplication.id == uuid.UUID(app_id)
        )
    )
    a = r.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Not found")
    return a


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ── 統計 ─────────────────────────────────────────────────────────

@router.get("/api/export-licenses/stats")
async def get_stats(db: AsyncSession = Depends(get_pg_db)):
    result = await db.execute(select(ExportLicenseApplication))
    apps = result.scalars().all()
    now = datetime.now(tz=timezone.utc)

    def _days(a: ExportLicenseApplication) -> int | None:
        if not a.expires_at:
            return None
        e = a.expires_at if a.expires_at.tzinfo else a.expires_at.replace(tzinfo=timezone.utc)
        return (e - now).days

    by_status: dict[str, int] = {}
    for a in apps:
        by_status[a.status] = by_status.get(a.status, 0) + 1

    expiring_soon = [a for a in apps if a.status == "approved" and _days(a) is not None and 0 <= _days(a) <= 30]
    expired = [a for a in apps if a.status == "approved" and _days(a) is not None and _days(a) < 0]

    return {
        "total": len(apps),
        "by_status": by_status,
        "expiring_soon_count": len(expiring_soon),
        "expired_count": len(expired),
        "pending_submission": by_status.get("draft", 0),
        "active_licenses": by_status.get("approved", 0),
    }


# ── CRUD ──────────────────────────────────────────────────────────

@router.get("/api/export-licenses")
async def list_licenses(
    status: str | None = Query(None),
    license_type: str | None = Query(None),
    expiring_days: int | None = Query(None),
    db: AsyncSession = Depends(get_pg_db),
):
    result = await db.execute(
        select(ExportLicenseApplication).order_by(ExportLicenseApplication.created_at.desc())
    )
    apps = result.scalars().all()
    now = datetime.now(tz=timezone.utc)

    out = []
    for a in apps:
        if status and a.status != status:
            continue
        if license_type and a.license_type != license_type:
            continue
        if expiring_days is not None:
            if not a.expires_at:
                continue
            e = a.expires_at if a.expires_at.tzinfo else a.expires_at.replace(tzinfo=timezone.utc)
            if (e - now).days > expiring_days or (e - now).days < 0:
                continue
        out.append(_serialize(a))
    return out


@router.post("/api/export-licenses", status_code=201)
async def create_license(body: LicenseCreate, db: AsyncSession = Depends(get_pg_db)):
    form_type = body.form_type
    if body.license_type == "FEFTA":
        form_type = "FEFTA_FORM"

    params = {
        "destination_country": body.destination_country,
        "consignee_name": body.consignee_name,
        "consignee_address": body.consignee_address,
        "end_user_name": body.end_user_name,
        "item_description": body.item_description,
        "eccn": body.eccn,
        "quantity": body.quantity,
        "quantity_unit": body.quantity_unit,
        "value_usd": body.value_usd,
        "end_use_description": body.end_use_description,
    }
    draft = _build_draft_content_bis748p(params) if body.license_type == "EAR" \
        else _build_draft_content_fefta(params)

    expires_at = None
    if body.expires_at:
        try:
            expires_at = datetime.fromisoformat(body.expires_at).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    node_id = uuid.UUID(body.supply_chain_node_id) if body.supply_chain_node_id else None

    a = ExportLicenseApplication(
        license_type=body.license_type,
        form_type=form_type,
        item_description=body.item_description,
        eccn=body.eccn,
        quantity=body.quantity,
        quantity_unit=body.quantity_unit,
        value_usd=body.value_usd,
        destination_country=body.destination_country,
        consignee_name=body.consignee_name,
        consignee_address=body.consignee_address,
        end_use_description=body.end_use_description,
        end_user_name=body.end_user_name,
        draft_content=draft,
        transaction_ids=body.transaction_ids,
        supply_chain_node_id=node_id,
        expires_at=expires_at,
        notes=body.notes,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return _serialize(a)


@router.get("/api/export-licenses/{app_id}")
async def get_license(app_id: str, db: AsyncSession = Depends(get_pg_db)):
    a = await _get_or_404(app_id, db)
    return _serialize(a)


@router.put("/api/export-licenses/{app_id}")
async def update_license(app_id: str, body: LicenseCreate, db: AsyncSession = Depends(get_pg_db)):
    a = await _get_or_404(app_id, db)
    for field in ("item_description", "eccn", "quantity", "quantity_unit", "value_usd",
                  "destination_country", "consignee_name", "consignee_address",
                  "end_use_description", "end_user_name", "notes"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(a, field, val)

    params = {
        "destination_country": a.destination_country,
        "consignee_name": a.consignee_name,
        "consignee_address": a.consignee_address,
        "end_user_name": a.end_user_name,
        "item_description": a.item_description,
        "eccn": a.eccn,
        "quantity": a.quantity,
        "quantity_unit": a.quantity_unit,
        "value_usd": a.value_usd,
        "end_use_description": a.end_use_description,
    }
    a.draft_content = _build_draft_content_bis748p(params) if a.license_type == "EAR" \
        else _build_draft_content_fefta(params)

    if body.expires_at:
        try:
            a.expires_at = datetime.fromisoformat(body.expires_at).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    await db.commit()
    await db.refresh(a)
    return _serialize(a)


@router.delete("/api/export-licenses/{app_id}", status_code=204)
async def delete_license(app_id: str, db: AsyncSession = Depends(get_pg_db)):
    a = await _get_or_404(app_id, db)
    await db.delete(a)
    await db.commit()


# ── ライフサイクル ────────────────────────────────────────────────

@router.post("/api/export-licenses/{app_id}/submit")
async def submit_license(app_id: str, db: AsyncSession = Depends(get_pg_db)):
    a = await _get_or_404(app_id, db)
    a.status = "submitted"
    a.submitted_at = datetime.now(tz=timezone.utc)

    if not a.application_number:
        year = datetime.now().year
        prefix = a.license_type.upper()
        count_result = await db.execute(
            select(func.count()).select_from(ExportLicenseApplication).where(
                ExportLicenseApplication.license_type == a.license_type
            )
        )
        seq = (count_result.scalar() or 0) + 1
        a.application_number = f"EL-{prefix}-{year}-{seq:04d}"

    await db.commit()
    await db.refresh(a)
    return _serialize(a)


@router.post("/api/export-licenses/{app_id}/approve")
async def approve_license(app_id: str, body: LicenseApprove, db: AsyncSession = Depends(get_pg_db)):
    a = await _get_or_404(app_id, db)
    a.status = "approved"
    a.license_number = body.license_number
    a.issuing_authority = body.issuing_authority
    a.approved_at = _parse_dt(body.approved_at) or datetime.now(tz=timezone.utc)
    if body.expires_at:
        a.expires_at = _parse_dt(body.expires_at)
    if body.license_value_remaining_usd is not None:
        a.license_value_remaining_usd = body.license_value_remaining_usd
    await db.commit()
    await db.refresh(a)
    return _serialize(a)


@router.post("/api/export-licenses/{app_id}/deny")
async def deny_license(app_id: str, db: AsyncSession = Depends(get_pg_db)):
    a = await _get_or_404(app_id, db)
    a.status = "denied"
    await db.commit()
    await db.refresh(a)
    return _serialize(a)


@router.post("/api/export-licenses/{app_id}/use-value")
async def use_license_value(
    app_id: str,
    body: UseValueBody,
    db: AsyncSession = Depends(get_pg_db),
):
    """許可残存枠から出荷額を消費する。"""
    a = await _get_or_404(app_id, db)
    if a.status != "approved":
        raise HTTPException(status_code=400, detail="承認済み許可証にのみ適用できます")
    if a.license_value_remaining_usd is None:
        raise HTTPException(status_code=400, detail="残存枠が未設定です。まず許可証承認時に枠を設定してください")
    if body.shipment_value_usd <= 0:
        raise HTTPException(status_code=400, detail="出荷額は正の値を指定してください")
    if body.shipment_value_usd > a.license_value_remaining_usd:
        raise HTTPException(
            status_code=400,
            detail=f"残存枠不足: 残 USD {a.license_value_remaining_usd:,.0f} に対し USD {body.shipment_value_usd:,.0f} を申請"
        )

    a.license_value_remaining_usd -= body.shipment_value_usd
    used_note = (
        f"[{datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC] "
        f"出荷消費: USD {body.shipment_value_usd:,.0f}"
        + (f" — {body.description}" if body.description else "")
    )
    a.notes = (a.notes or "") + "\n" + used_note

    await db.commit()
    await db.refresh(a)
    return {
        "ok": True,
        "license_value_remaining_usd": a.license_value_remaining_usd,
        "used_usd": body.shipment_value_usd,
        "application_number": a.application_number,
    }


# ── 取引からドラフト生成 ──────────────────────────────────────────

@router.post("/api/export-licenses/draft-from-transaction", status_code=201)
async def draft_from_transaction(body: DraftFromTransaction, db: AsyncSession = Depends(get_pg_db)):
    """ai_validation の取引データを取得してドラフトを自動生成する。"""
    tx_data: dict = {}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{_AI_VALIDATION_URL}/api/transactions/{body.transaction_id}")
            if r.status_code == 200:
                tx_data = r.json()
    except Exception:
        pass

    params = {
        "item_description": tx_data.get("item_name") or tx_data.get("item_description") or "",
        "eccn": tx_data.get("eccn") or "",
        "destination_country": tx_data.get("destination_country") or "",
        "consignee_name": tx_data.get("consignee_name") or tx_data.get("end_user") or "",
        "end_use_description": tx_data.get("end_use") or tx_data.get("transaction_purpose") or "",
        "value_usd": tx_data.get("value_usd") or None,
    }
    form_type = "BIS748P" if body.license_type == "EAR" else "FEFTA_FORM"
    draft = _build_draft_content_bis748p(params) if body.license_type == "EAR" \
        else _build_draft_content_fefta(params)

    a = ExportLicenseApplication(
        license_type=body.license_type,
        form_type=form_type,
        item_description=params["item_description"],
        eccn=params["eccn"],
        destination_country=params["destination_country"],
        consignee_name=params["consignee_name"],
        end_use_description=params["end_use_description"],
        value_usd=params["value_usd"],
        transaction_ids=[str(body.transaction_id)],
        draft_content=draft,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return _serialize(a)


# ── HTML プレビュー ───────────────────────────────────────────────

@router.get("/api/export-licenses/{app_id}/preview", response_class=HTMLResponse)
async def preview_license(app_id: str, db: AsyncSession = Depends(get_pg_db)):
    a = await _get_or_404(app_id, db)
    d = a.draft_content or {}
    html = _render_bis748p(a, d) if a.license_type == "EAR" else _render_fefta(a, d)
    return HTMLResponse(content=html)


# ── HTML レンダラー ───────────────────────────────────────────────

def _field(label: str, value: str, wide: bool = False) -> str:
    span = 'style="grid-column:1/-1"' if wide else ""
    return f"""
    <div class="field" {span}>
      <div class="field-label">{label}</div>
      <div class="field-value">{value or '<span style="color:#aaa">—</span>'}</div>
    </div>"""


_PREVIEW_CSS = """
<style>
  body { font-family: Arial, sans-serif; font-size: 11pt; color: #000; background: #fff; margin: 0; }
  .doc { max-width: 800px; margin: 0 auto; padding: 24px; }
  .doc-title { text-align: center; font-size: 14pt; font-weight: bold; border: 2px solid #000; padding: 8px; margin-bottom: 16px; }
  .doc-subtitle { text-align: center; font-size: 10pt; margin-bottom: 20px; }
  .section { border: 1px solid #000; margin-bottom: 12px; }
  .section-header { background: #d0d0d0; font-weight: bold; padding: 4px 8px; font-size: 10pt; border-bottom: 1px solid #000; }
  .fields { display: grid; grid-template-columns: 1fr 1fr; gap: 0; }
  .field { border-right: 1px solid #ccc; border-bottom: 1px solid #ccc; padding: 6px 8px; }
  .field:nth-child(even) { border-right: none; }
  .field-label { font-size: 8pt; color: #555; margin-bottom: 2px; }
  .field-value { font-size: 10pt; min-height: 18px; }
  .watermark { position: fixed; top: 40%; left: 50%; transform: translate(-50%,-50%) rotate(-30deg); font-size: 60pt; color: rgba(200,0,0,0.08); font-weight: bold; pointer-events: none; z-index: 0; }
  @media print { .watermark { color: rgba(200,0,0,0.12); } }
</style>"""


def _render_bis748p(a: ExportLicenseApplication, d: dict) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>BIS-748P Draft</title>{_PREVIEW_CSS}</head>
<body>
<div class="watermark">DRAFT</div>
<div class="doc">
  <div class="doc-title">BUREAU OF INDUSTRY AND SECURITY<br>MULTIPURPOSE APPLICATION (BIS-748P)</div>
  <div class="doc-subtitle">U.S. DEPARTMENT OF COMMERCE — FOR U.S. GOVERNMENT USE ONLY<br>
    Application ID: {str(a.id)[:8].upper()} &nbsp;|&nbsp; Generated: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}
  </div>
  <div class="section">
    <div class="section-header">BLOCK 1 — U.S. PRINCIPAL PARTY IN INTEREST (APPLICANT)</div>
    <div class="fields">
      {_field("Company Name", d.get("block1_applicant_name",""))}
      {_field("EIN / Tax ID", d.get("block1_applicant_ein",""))}
      {_field("Address", d.get("block1_applicant_address",""), wide=True)}
    </div>
  </div>
  <div class="section">
    <div class="section-header">BLOCK 3 — POINT OF CONTACT</div>
    <div class="fields">
      {_field("Name", d.get("block3_contact_name",""))}
      {_field("Phone", d.get("block3_contact_phone",""))}
      {_field("Email", d.get("block3_contact_email",""))}
    </div>
  </div>
  <div class="section">
    <div class="section-header">BLOCK 4 — TYPE OF APPLICATION</div>
    <div class="fields">
      {_field("Application Type", d.get("block4_application_type","Individual Validated License (IVL)"), wide=True)}
    </div>
  </div>
  <div class="section">
    <div class="section-header">BLOCK 7 — COUNTRY OF ULTIMATE DESTINATION</div>
    <div class="fields">
      {_field("Destination Country (ISO)", d.get("block7_destination_country",""), wide=True)}
    </div>
  </div>
  <div class="section">
    <div class="section-header">BLOCK 8 — CONSIGNEE</div>
    <div class="fields">
      {_field("Name", d.get("block8_consignee_name",""))}
      {_field("Address", d.get("block8_consignee_address",""))}
    </div>
  </div>
  <div class="section">
    <div class="section-header">BLOCK 9 — END USER</div>
    <div class="fields">
      {_field("End User Name", d.get("block9_end_user_name",""))}
      {_field("End User Address", d.get("block9_end_user_address",""))}
    </div>
  </div>
  <div class="section">
    <div class="section-header">BLOCK 11 — ITEMS TO BE EXPORTED</div>
    <div class="fields">
      {_field("Item Description", d.get("block11_item_description",""), wide=True)}
      {_field("ECCN", d.get("block11_eccn",""))}
      {_field("License Exception", d.get("block13_license_exception",""))}
      {_field("Quantity", str(d.get("block11_quantity","")))}
      {_field("Unit", d.get("block11_unit",""))}
      {_field("Value (USD)", str(d.get("block11_value_usd","")))}
    </div>
  </div>
  <div class="section">
    <div class="section-header">BLOCK 21 — END USE DESCRIPTION</div>
    <div class="fields">
      {_field("End Use", d.get("block21_end_use",""), wide=True)}
    </div>
  </div>
  <div class="section">
    <div class="section-header">BLOCK 22 — SUPPORTING DOCUMENTS</div>
    <div class="fields">
      {_field("Documents", d.get("block22_supporting_docs",""), wide=True)}
    </div>
  </div>
  <p style="font-size:8pt;color:#888;margin-top:16px;text-align:center;">
    This is an AI-generated DRAFT for internal review. Not a legally valid submission.
    Complete and submit through the official BIS SNAP-R system.
  </p>
</div>
</body>
</html>"""


def _render_fefta(a: ExportLicenseApplication, d: dict) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><title>輸出許可申請書（ドラフト）</title>{_PREVIEW_CSS}</head>
<body>
<div class="watermark">ドラフト</div>
<div class="doc">
  <div class="doc-title">輸出許可申請書（様式第１）<br>外国為替及び外国貿易法 第48条第1項</div>
  <div class="doc-subtitle">
    申請管理番号: {str(a.id)[:8].upper()} &nbsp;|&nbsp; 生成日: {datetime.now(tz=timezone.utc).strftime('%Y年%m月%d日')}
  </div>
  <div class="section">
    <div class="section-header">申請者情報</div>
    <div class="fields">
      {_field("会社名・氏名", d.get("applicant_company",""))}
      {_field("代表者氏名", d.get("applicant_representative",""))}
      {_field("住所", d.get("applicant_address",""), wide=True)}
      {_field("電話番号", d.get("applicant_phone",""))}
      {_field("申請日", d.get("application_date",""))}
    </div>
  </div>
  <div class="section">
    <div class="section-header">貨物の表示</div>
    <div class="fields">
      {_field("品名", d.get("item_name_jp",""), wide=True)}
      {_field("ECCN（参考）", d.get("item_eccn",""))}
      {_field("外為法規制根拠", d.get("item_legal_basis",""), wide=True)}
      {_field("数量", str(d.get("item_quantity","")))}
      {_field("単位", d.get("item_unit",""))}
      {_field("価格（円）", d.get("item_value_jpy",""))}
      {_field("価格（USD参考）", str(d.get("item_value_usd","")))}
    </div>
  </div>
  <div class="section">
    <div class="section-header">仕向地・需要者</div>
    <div class="fields">
      {_field("仕向地（国コード）", d.get("destination_country",""))}
      {_field("需要者名（荷受人）", d.get("consignee_name",""))}
      {_field("需要者住所", d.get("consignee_address",""), wide=True)}
    </div>
  </div>
  <div class="section">
    <div class="section-header">最終需要者・最終用途</div>
    <div class="fields">
      {_field("最終需要者名", d.get("end_user_name",""))}
      {_field("最終用途", d.get("end_use_description",""), wide=True)}
    </div>
  </div>
  <div class="section">
    <div class="section-header">申請先</div>
    <div class="fields">
      {_field("経済産業局", d.get("meti_bureau","経済産業局（管轄地域を選択）"), wide=True)}
    </div>
  </div>
  <p style="font-size:8pt;color:#888;margin-top:16px;text-align:center;">
    本書面は AI 自動生成によるドラフトです。法的効力を持ちません。<br>
    経済産業省 CISTEC 様式または管轄経済産業局の指定様式にて正式申請してください。
  </p>
</div>
</body>
</html>"""
