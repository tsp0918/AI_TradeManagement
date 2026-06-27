"""サプライヤーポータル — ai_classification 統合版。

Phase 6A-2: platform-core から移管。データは plat_supplier_portal_token テーブル（共有 PostgreSQL）。
"""

import logging
import os
import pathlib
import secrets
import smtplib
import ssl
import threading
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..pg_session import get_pg_db
from platform_core.models.supplier_attestation import SupplierAttestation
from platform_core.models.supplier_portal_token import SupplierPortalToken

logger = logging.getLogger(__name__)

# ── SMTP 設定（未設定時はメール送信をスキップ） ──────────────────────
_SMTP_HOST     = os.environ.get("SMTP_HOST", "")
_SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
_SMTP_USER     = os.environ.get("SMTP_USER", "")
_SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
_SMTP_FROM     = os.environ.get("SMTP_FROM", _SMTP_USER)

_AI_VALIDATION_URL = os.environ.get("MODULE_AI_VALIDATION_URL", "http://localhost:8011")
# ユーザー向けリンクに使う公開URL（ブラウザから開ける必要がある）
_AI_VALIDATION_PUBLIC_URL = os.environ.get(
    "MODULE_AI_VALIDATION_PUBLIC_URL", "https://validation.tsp-aitrademanagement.com"
)
from platform_core.models.supply_chain import SupplyChainNode

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
_UPLOADS_DIR = pathlib.Path(__file__).parent.parent.parent / "uploads" / "supplier"
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(tags=["supplier_portal"])

_BASE_URL = "https://app.tsp-aitrademanagement.com"


# ── スキーマ ──────────────────────────────────────────────────────

class TokenCreate(BaseModel):
    node_id: str
    supplier_name: str
    supplier_email: str | None = None
    note_for_supplier: str | None = None
    max_uses: int = 1
    expires_days: int = 30


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


# ── メール送信 ────────────────────────────────────────────────────

def _send_supplier_invitation_bg(
    to_email: str,
    supplier_name: str,
    node_name: str,
    portal_url: str,
    note_for_supplier: str | None,
    expires_days: int,
) -> None:
    """サプライヤー招待メールをバックグラウンド送信（SMTP 未設定時はスキップ）。"""
    if not (_SMTP_HOST and _SMTP_USER and _SMTP_PASSWORD and to_email):
        return
    note_block = (
        f'<div style="background:#fef9c3; border:1px solid #fde68a; border-radius:6px; '
        f'padding:12px; margin:12px 0;"><strong>ご担当者様へのメッセージ:</strong><br>'
        f'{note_for_supplier}</div>'
    ) if note_for_supplier else ""
    html = f"""
<html><body style="font-family:sans-serif; color:#1e293b; margin:0; padding:0;">
<div style="max-width:560px; margin:0 auto; padding:28px 24px;">
  <div style="border-bottom:3px solid #0369a1; padding-bottom:12px; margin-bottom:20px;">
    <h2 style="margin:0; color:#0369a1; font-size:1.15rem;">サプライヤー情報提供のお願い</h2>
  </div>
  <p style="margin:0 0 12px;">{supplier_name} 様</p>
  <p style="margin:0 0 12px;">
    お世話になっております。貿易コンプライアンス管理の一環として、
    下記品目に関するサプライヤー情報（ECCN・原産国等）のご提供をお願い申し上げます。
  </p>
  <div style="background:#f0f9ff; border:1px solid #bae6fd; border-radius:8px;
              padding:14px 18px; margin:16px 0;">
    <span style="font-size:.8rem; color:#0369a1; font-weight:700;">対象品目</span><br>
    <span style="font-size:1rem; font-weight:800; color:#0c4a6e;">{node_name}</span>
  </div>
  {note_block}
  <p style="margin:16px 0 8px;">下記リンクよりご回答ください（有効期限: {expires_days}日）:</p>
  <div style="text-align:center; margin:20px 0;">
    <a href="{portal_url}"
       style="display:inline-block; padding:13px 32px; background:#0369a1; color:#fff;
              border-radius:7px; text-decoration:none; font-weight:700; font-size:.95rem;
              letter-spacing:.02em;">
      情報提供フォームを開く →
    </a>
  </div>
  <hr style="border:none; border-top:1px solid #e2e8f0; margin:20px 0;">
  <p style="font-size:.78rem; color:#94a3b8; margin:0;">
    ※ このリンクは{expires_days}日間有効です。<br>
    ※ ご不明点は担当者までお問い合わせください。
  </p>
</div>
</body></html>
"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"【サプライヤー情報提供依頼】{node_name}"
    msg["From"]    = _SMTP_FROM
    msg["To"]      = to_email
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=15) as srv:
            srv.starttls(context=ctx)
            srv.login(_SMTP_USER, _SMTP_PASSWORD)
            srv.sendmail(_SMTP_FROM, [to_email], msg.as_string())
        logger.info("サプライヤー招待メール送信完了: %s", to_email)
    except Exception as exc:
        logger.warning("サプライヤー招待メール送信失敗: %s", exc)


# ── 管理 API ─────────────────────────────────────────────────────

@router.get("/api/supplier-portal/tokens")
async def list_tokens(
    node_id: str | None = Query(None),
    active_only: bool = Query(False),
    db: AsyncSession = Depends(get_pg_db),
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
async def create_token(body: TokenCreate, db: AsyncSession = Depends(get_pg_db)):
    node_id = uuid.UUID(body.node_id)
    rn = await db.execute(select(SupplyChainNode).where(SupplyChainNode.id == node_id))
    node = rn.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="SupplyChainNode not found")

    t = SupplierPortalToken(
        token=secrets.token_urlsafe(32),
        node_id=node_id,
        node_name=node.name,
        supplier_name=body.supplier_name,
        supplier_email=body.supplier_email,
        note_for_supplier=body.note_for_supplier,
        max_uses=body.max_uses,
        expires_at=datetime.now(tz=timezone.utc) + timedelta(days=body.expires_days),
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)

    # 招待メール送信（supplier_email が設定されていれば非同期で送信）
    if body.supplier_email:
        portal_url = f"{_BASE_URL}/supplier-portal/{t.token}"
        threading.Thread(
            target=_send_supplier_invitation_bg,
            args=(
                body.supplier_email,
                body.supplier_name,
                node.name,
                portal_url,
                body.note_for_supplier,
                body.expires_days,
            ),
            daemon=True,
        ).start()

    result = _serialize_token(t)
    result["email_queued"] = bool(body.supplier_email and _SMTP_HOST)
    return result


@router.get("/api/supplier-portal/tokens/{token_id}")
async def get_token(token_id: str, db: AsyncSession = Depends(get_pg_db)):
    r = await db.execute(
        select(SupplierPortalToken).where(SupplierPortalToken.id == uuid.UUID(token_id))
    )
    t = r.scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _serialize_token(t)


@router.post("/api/supplier-portal/tokens/{token_id}/revoke")
async def revoke_token(token_id: str, db: AsyncSession = Depends(get_pg_db)):
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
async def portal_form(request: Request, token_str: str, db: AsyncSession = Depends(get_pg_db)):
    try:
        t = await _resolve_token(token_str, db)
    except HTTPException as e:
        return templates.TemplateResponse(
            request, "supplier_portal_error.html",
            {"message": e.detail},
            status_code=e.status_code,
        )
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
async def portal_submit(request: Request, token_str: str, db: AsyncSession = Depends(get_pg_db)):
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

    from datetime import date
    def _parse_date(s: str | None) -> date | None:
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None

    attest = SupplierAttestation(
        node_id=t.node_id,
        supplier_name=t.supplier_name,
        supplier_contact=_fv("supplier_contact") or t.supplier_email,
        claimed_eccn=_fv("claimed_eccn"),
        claimed_hs_code=_fv("claimed_hs_code"),
        claimed_country_of_origin=_fv("claimed_country_of_origin"),
        claimed_us_content_pct=_ff("claimed_us_content_pct"),
        is_us_origin_claimed=form.get("is_us_origin_claimed") == "on",
        certificate_reference=_fv("certificate_reference"),
        attestation_date=_parse_date(_fv("attestation_date")),
        expiry_date=_parse_date(_fv("expiry_date")),
        notes=_fv("notes"),
        status="pending",
        history=[{
            "action": "submitted",
            "actor": f"supplier:{t.supplier_name}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "note": f"HS申告: {_fv('claimed_hs_code') or '未入力'} / ECCN申告: {_fv('claimed_eccn') or '未入力'} / 原産国: {_fv('claimed_country_of_origin') or '未入力'}",
        }],
    )
    db.add(attest)
    await db.flush()

    _MAX_FILE_BYTES = 20 * 1024 * 1024
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
            attest.supporting_docs = supporting_docs

    t.use_count += 1
    if t.max_uses > 0 and t.use_count >= t.max_uses:
        t.is_active = False
    await db.commit()

    # ── AI 該非判定を自動トリガー ──────────────────────────────────
    tx_id = None
    tx_case_no = None
    tx_url = None
    try:
        rn2 = await db.execute(select(SupplyChainNode).where(SupplyChainNode.id == t.node_id))
        node = rn2.scalar_one_or_none()
        if node:
            desc_lines = [
                f"品目コード: {node.product_code or '不明'}",
                f"部品番号: {node.part_number or '不明'}",
                f"ノード種別: {node.node_type}",
                f"原産国（申告）: {attest.claimed_country_of_origin or node.country_of_origin or '不明'}",
                f"ECCN（申告）: {attest.claimed_eccn or '未申告'}",
                f"HSコード（申告）: {attest.claimed_hs_code or node.hs_code or '不明'}",
                f"サプライヤー: {t.supplier_name}",
            ]
            if attest.notes:
                desc_lines.append(f"サプライヤーコメント: {attest.notes}")

            payload = {
                "title": f"BOM構成品 ECCN 該非判定: {node.product_code or node.name}",
                "items": [{"item_name": node.name, "item_description": "\n".join(desc_lines)}],
                "source_module": "ai_classification",
                "destination_country": "JP",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(f"{_AI_VALIDATION_URL}/api/transactions", json=payload)
                resp.raise_for_status()
                tx_data = resp.json()

            tx_id = tx_data["id"]
            tx_case_no = tx_data.get("case_no")
            tx_url = f"{_AI_VALIDATION_PUBLIC_URL}/ui/transactions/{tx_id}"

            node.eccn_validation_tx_id = tx_id
            node.eccn_judgment_status = "pending"
            node.eccn_source = "supplier_attestation"
            node.judgment_evidence = {
                "tx_id": tx_id,
                "case_no": tx_case_no,
                "tx_url": tx_url,
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "supplier_claimed_eccn": attest.claimed_eccn,
                "supplier_claimed_hs_code": attest.claimed_hs_code,
                "attestation_id": str(attest.id),
            }
            # 履歴にAI判定トリガーを記録
            ai_hist_entry = {
                "action": "ai_judged",
                "actor": "system:ai_validation",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "note": f"取引審査案件番号: {tx_case_no or tx_id}",
            }
            attest.history = (attest.history or []) + [ai_hist_entry]
            await db.commit()

            # ── supply_chain_node_id を Transaction に紐付け（De Minimis・BOM文脈表示用）
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f"{_AI_VALIDATION_URL}/api/transactions/{tx_id}/supply-chain",
                        json={"supply_chain_node_id": str(t.node_id)},
                    )
            except Exception:
                pass

            # ── FAISS + 2リスト解析を自動起動（非同期 fire-and-forget）
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    await client.post(
                        f"{_AI_VALIDATION_URL}/decision/{tx_id}/run-and-two-lists",
                    )
            except Exception:
                pass

    except Exception as exc:
        logger.warning("AI 該非判定トリガー失敗（非致命的）: %s", exc)

    return templates.TemplateResponse(
        request, "supplier_portal_confirm.html",
        {
            "supplier_name": t.supplier_name,
            "node_name": t.node_name,
            "claimed_eccn": attest.claimed_eccn or "—",
            "claimed_hs_code": attest.claimed_hs_code or "—",
            "claimed_country": attest.claimed_country_of_origin or "—",
            "attestation_id": str(attest.id),
            "tx_id": tx_id,
            "tx_case_no": tx_case_no,
            "tx_url": tx_url,
        },
    )


@router.get("/api/supplier-attestations/{attest_id}/documents/{filename}")
async def download_document(attest_id: str, filename: str):
    path = _UPLOADS_DIR / attest_id / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="ファイルが見つかりません")
    try:
        path.resolve().relative_to(_UPLOADS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="不正なパスです")
    return FileResponse(str(path), filename=filename)
