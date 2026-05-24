"""輸入品プロファイル管理 API + UI — ai_classification モジュール。

品目（products）の購入品・原材料・グループ内移管品に輸入情報を付加する。
データは plat_import_profile テーブル（共有 PostgreSQL）。

エンドポイント:
  GET  /api/import-profiles              プロファイル一覧（フィルタ: product_code / import_type / org_id）
  POST /api/import-profiles              新規作成
  GET  /api/import-profiles/{id}         詳細取得
  PUT  /api/import-profiles/{id}         更新
  DELETE /api/import-profiles/{id}       削除
  GET  /api/import-profiles/product/{code}  品目コード別一覧
  POST /api/import-profiles/{id}/check-restrictions  輸入規制チェック実行
  GET  /import-profiles                  UI: 輸入品プロファイル一覧
  GET  /import-profiles/{id}             UI: 詳細・編集
"""

from __future__ import annotations

import uuid
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..pg_session import get_pg_db
from platform_core.models.import_profile import ImportProfile

import os
import httpx

logger = logging.getLogger(__name__)
router = APIRouter(tags=["import_profile"])
templates = Jinja2Templates(directory="templates")

_FTA_ORIGIN_URL = os.environ.get("MODULE_FTA_ORIGIN_URL", "http://localhost:8014")
_AI_VALIDATION_URL = os.environ.get("MODULE_AI_VALIDATION_URL", "http://localhost:8011")
_AI_VALIDATION_PUBLIC_URL = os.environ.get(
    "MODULE_AI_VALIDATION_PUBLIC_URL", "https://validation.tsp-aitrademanagement.com"
)
_EXPORT_LICENSE_URL = os.environ.get("MODULE_EXPORT_LICENSE_URL", "http://localhost:8012")


# ── スキーマ ──────────────────────────────────────────────────────────

class ImportProfileIn(BaseModel):
    product_code: str
    product_name: Optional[str] = None
    import_type: str = "purchase"           # purchase / intra_group / consignment
    exporter_name: Optional[str] = None
    exporter_country: Optional[str] = None  # ISO2
    import_country: str = "JP"
    hs_code_import: Optional[str] = None
    customs_value_usd: Optional[float] = None
    currency: str = "USD"
    import_quantity: Optional[float] = None
    import_unit: Optional[str] = None
    import_license_required: bool = False
    import_license_no: Optional[str] = None
    import_license_expiry: Optional[datetime] = None
    fta_applicable: bool = False
    fta_agreement_code: Optional[str] = None
    preferential_rate_pct: Optional[float] = None
    co_status: str = "not_required"         # not_required / pending / obtained / expired
    eccn_claimed: Optional[str] = None
    us_reexport_applicable: bool = False
    ear_license_exception: Optional[str] = None
    last_imported_at: Optional[datetime] = None
    import_frequency: Optional[str] = None  # one_time / monthly / quarterly / annual / irregular
    supplier_attestation_id: Optional[uuid.UUID] = None
    org_id: Optional[str] = None
    notes: Optional[str] = None


def _to_dict(p: ImportProfile) -> dict:
    return {
        "id":                          str(p.id),
        "product_code":                p.product_code,
        "product_name":                p.product_name,
        "import_type":                 p.import_type,
        "import_type_label":           _IMPORT_TYPE_LABEL.get(p.import_type, p.import_type),
        "exporter_name":               p.exporter_name,
        "exporter_country":            p.exporter_country,
        "import_country":              p.import_country,
        "hs_code_import":              p.hs_code_import,
        "customs_value_usd":           p.customs_value_usd,
        "currency":                    p.currency,
        "import_quantity":             p.import_quantity,
        "import_unit":                 p.import_unit,
        "import_license_required":     p.import_license_required,
        "import_license_no":           p.import_license_no,
        "import_license_expiry":       p.import_license_expiry.isoformat() if p.import_license_expiry else None,
        "fta_applicable":              p.fta_applicable,
        "fta_agreement_code":          p.fta_agreement_code,
        "preferential_rate_pct":       p.preferential_rate_pct,
        "co_status":                   p.co_status,
        "co_status_label":             _CO_STATUS_LABEL.get(p.co_status, p.co_status),
        "eccn_claimed":                p.eccn_claimed,
        "us_reexport_applicable":      p.us_reexport_applicable,
        "ear_license_exception":       p.ear_license_exception,
        "import_restrictions_checked": p.import_restrictions_checked,
        "import_restrictions_result":  p.import_restrictions_result,
        "last_imported_at":            p.last_imported_at.isoformat() if p.last_imported_at else None,
        "import_frequency":            p.import_frequency,
        "supplier_attestation_id":     str(p.supplier_attestation_id) if p.supplier_attestation_id else None,
        "org_id":                      p.org_id,
        "notes":                       p.notes,
        "created_at":                  p.created_at.isoformat() if p.created_at else None,
        "updated_at":                  p.updated_at.isoformat() if p.updated_at else None,
    }


_IMPORT_TYPE_LABEL = {
    "purchase":     "外部購入品",
    "intra_group":  "グループ内移管",
    "consignment":  "委託品",
}

_CO_STATUS_LABEL = {
    "not_required": "不要",
    "pending":      "取得中",
    "obtained":     "取得済",
    "expired":      "期限切れ",
}

_IMPORT_FREQ_LABEL = {
    "one_time":   "単発",
    "monthly":    "月次",
    "quarterly":  "四半期",
    "annual":     "年次",
    "irregular":  "不定期",
}


# ── API エンドポイント ────────────────────────────────────────────────

@router.get("/api/import-profiles")
async def list_import_profiles(
    product_code: Optional[str] = Query(None),
    import_type:  Optional[str] = Query(None),
    exporter_country: Optional[str] = Query(None),
    us_reexport: Optional[bool] = Query(None),
    org_id:       Optional[str] = Query(None),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_pg_db),
):
    """輸入品プロファイル一覧。"""
    stmt = select(ImportProfile).order_by(ImportProfile.created_at.desc())
    if product_code:
        stmt = stmt.where(ImportProfile.product_code == product_code)
    if import_type:
        stmt = stmt.where(ImportProfile.import_type == import_type)
    if exporter_country:
        stmt = stmt.where(ImportProfile.exporter_country == exporter_country.upper())
    if us_reexport is not None:
        stmt = stmt.where(ImportProfile.us_reexport_applicable == us_reexport)
    if org_id:
        stmt = stmt.where(ImportProfile.org_id == org_id)
    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return {"profiles": [_to_dict(p) for p in result.scalars().all()]}


@router.post("/api/import-profiles", status_code=201)
async def create_import_profile(
    body: ImportProfileIn,
    db: AsyncSession = Depends(get_pg_db),
):
    """輸入品プロファイルを新規作成する。"""
    profile = ImportProfile(**body.model_dump())
    db.add(profile)
    await db.flush()
    return _to_dict(profile)


@router.get("/api/import-profiles/product/{product_code}")
async def get_profiles_by_product(
    product_code: str,
    db: AsyncSession = Depends(get_pg_db),
):
    """品目コード別の輸入プロファイル一覧。"""
    result = await db.execute(
        select(ImportProfile)
        .where(ImportProfile.product_code == product_code)
        .order_by(ImportProfile.created_at.desc())
    )
    profiles = result.scalars().all()
    return {"product_code": product_code, "profiles": [_to_dict(p) for p in profiles]}


@router.get("/api/import-profiles/{profile_id}")
async def get_import_profile(
    profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_pg_db),
):
    """輸入品プロファイル詳細。"""
    p = await db.get(ImportProfile, profile_id)
    if p is None:
        raise HTTPException(status_code=404, detail="ImportProfile not found")
    return _to_dict(p)


@router.put("/api/import-profiles/{profile_id}")
async def update_import_profile(
    profile_id: uuid.UUID,
    body: ImportProfileIn,
    db: AsyncSession = Depends(get_pg_db),
):
    """輸入品プロファイルを更新する。"""
    p = await db.get(ImportProfile, profile_id)
    if p is None:
        raise HTTPException(status_code=404, detail="ImportProfile not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    await db.flush()
    return _to_dict(p)


@router.delete("/api/import-profiles/{profile_id}", status_code=200)
async def delete_import_profile(
    profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_pg_db),
):
    """輸入品プロファイルを削除する。"""
    p = await db.get(ImportProfile, profile_id)
    if p is None:
        raise HTTPException(status_code=404, detail="ImportProfile not found")
    await db.delete(p)
    return {"ok": True, "id": str(profile_id)}


@router.post("/api/import-profiles/{profile_id}/check-restrictions")
async def check_import_restrictions(
    profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_pg_db),
):
    """輸入規制チェックを実行して結果を保存する。

    チェック項目:
      - 化審法（優先評価化学物質・監視化学物質）
      - REACH SVHC（EU輸入時の高懸念物質）
      - CITES（ワシントン条約・絶滅危惧種）
      - 輸入禁止品目（核物質・拳銃等）
    現バージョン: HS コードと輸出者国からルールベースで簡易チェックを行う。
    """
    p = await db.get(ImportProfile, profile_id)
    if p is None:
        raise HTTPException(status_code=404, detail="ImportProfile not found")

    result = _run_restriction_check(p)
    p.import_restrictions_checked = True
    p.import_restrictions_result = result
    await db.flush()
    return {"profile_id": str(profile_id), "result": result}


@router.post("/api/import-profiles/{profile_id}/check-fta")
async def check_fta_rate(
    profile_id: uuid.UUID,
    origin_country: Optional[str] = None,
    db: AsyncSession = Depends(get_pg_db),
):
    """fta_origin モジュール（:8014）に照会して FTA 優遇税率を取得・保存する。

    - hs_code_import × exporter_country（または origin_country パラメータ）で照会
    - 最も優遇率の高い協定を fta_agreement_code / preferential_rate_pct に保存
    - fta_applicable = True に更新
    """
    p = await db.get(ImportProfile, profile_id)
    if p is None:
        raise HTTPException(status_code=404, detail="ImportProfile not found")

    hs = (p.hs_code_import or "").replace(".", "").replace(" ", "")
    if not hs:
        raise HTTPException(status_code=422, detail="hs_code_import is not set")

    country = (origin_country or p.exporter_country or "JP").upper()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_FTA_ORIGIN_URL}/api/fta/check",
                params={"hs_code": hs, "import_country": country, "origin_country": "JP"},
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"fta_origin returned {resp.status_code}")

        data = resp.json()
        rates = data.get("rates", [])

        if rates:
            best = min(rates, key=lambda r: r.get("preferential_rate_pct") or 999)
            p.fta_applicable = True
            p.fta_agreement_code = best.get("agreement_code")
            p.preferential_rate_pct = best.get("preferential_rate_pct")
        else:
            p.fta_applicable = False
            p.fta_agreement_code = None
            p.preferential_rate_pct = None

        await db.flush()
        return {
            "profile_id": str(profile_id),
            "hs_code": hs,
            "country": country,
            "rates_found": len(rates),
            "fta_applicable": p.fta_applicable,
            "fta_agreement_code": p.fta_agreement_code,
            "preferential_rate_pct": p.preferential_rate_pct,
            "all_rates": rates,
        }

    except httpx.RequestError as exc:
        logger.warning("FTA check failed for profile %s: %s", profile_id, exc)
        raise HTTPException(status_code=502, detail=f"fta_origin unreachable: {exc}")


def _run_restriction_check(p: ImportProfile) -> dict:
    """ルールベースの輸入規制簡易チェック。"""
    checks: dict = {}

    # ── 化審法 ──────────────────────────────────────────────────────
    # HS 28/29 章（化学品）かつ中国/韓国原産 → 要確認
    hs = (p.hs_code_import or "").strip()
    kazinho_status = "clear"
    kazinho_detail = None
    if hs and (hs.startswith("28") or hs.startswith("29") or hs.startswith("38")):
        if p.exporter_country in ("CN", "KR", "IN"):
            kazinho_status = "review_required"
            kazinho_detail = (
                f"HS {hs[:6]} は化学品（第{hs[:2]}類）です。"
                "化審法届出要否を確認してください（輸入数量 1t/年超で届出義務の可能性）。"
            )
        else:
            kazinho_status = "review_required"
            kazinho_detail = f"HS {hs[:6]} は化学品です。化審法対象外であることをご確認ください。"
    checks["kazinho"] = {"status": kazinho_status, "details": kazinho_detail}

    # ── REACH SVHC（EU向け・EU輸入時）──────────────────────────────
    reach_status = "not_applicable"
    if p.import_country in ("EU", "DE", "FR", "NL", "IT", "ES", "BE", "PL"):
        reach_status = "review_required" if (hs.startswith("28") or hs.startswith("29")) else "clear"
    checks["reach_svhc"] = {
        "status": reach_status,
        "details": "REACH Regulation SVHC (Article 33) 確認が必要です。" if reach_status == "review_required" else None,
    }

    # ── CITES（ワシントン条約）──────────────────────────────────────
    # HS 01-05（動植物）/ 44章（木材）/ 97章（美術品）が対象の可能性
    cites_status = "clear"
    if hs and any(hs.startswith(p) for p in ("01", "02", "03", "04", "05", "44", "97")):
        cites_status = "review_required"
    checks["cites"] = {
        "status": cites_status,
        "details": "CITES 対象種の可能性があります。輸入許可書を確認してください。" if cites_status == "review_required" else None,
    }

    # ── 輸入禁止品目（外為法・関税法）──────────────────────────────
    # HS 93章（銃砲）/ 2844（核物質）
    ban_status = "clear"
    if hs and (hs.startswith("93") or hs.startswith("2844")):
        ban_status = "prohibited"
    checks["import_ban"] = {
        "status": ban_status,
        "details": "輸入禁止または厳格な許可が必要な品目の可能性があります。" if ban_status == "prohibited" else None,
    }

    # ── EAR 再輸出管理 ────────────────────────────────────────────
    ear_status = "clear"
    if p.us_reexport_applicable or p.exporter_country == "US":
        ear_status = "applicable"
    checks["ear_reexport"] = {
        "status": ear_status,
        "details": "米国原産品の再輸出は EAR §736 の規制対象です。ECCN と仕向国を確認してください。" if ear_status == "applicable" else None,
    }

    return checks


# ── UI エンドポイント ────────────────────────────────────────────────

@router.get("/import-profiles", response_class=HTMLResponse)
async def import_profiles_page(
    request: Request,
    import_type: Optional[str] = Query(None),
    exporter_country: Optional[str] = Query(None),
    us_reexport: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_pg_db),
):
    """輸入品プロファイル一覧 UI。"""
    stmt = select(ImportProfile).order_by(ImportProfile.created_at.desc()).limit(200)
    if import_type:
        stmt = stmt.where(ImportProfile.import_type == import_type)
    if exporter_country:
        stmt = stmt.where(ImportProfile.exporter_country == exporter_country.upper())
    if us_reexport is not None:
        stmt = stmt.where(ImportProfile.us_reexport_applicable == us_reexport)

    result = await db.execute(stmt)
    profiles = result.scalars().all()

    # 統計
    count_result = await db.execute(
        select(
            ImportProfile.import_type,
            func.count(ImportProfile.id).label("cnt"),
        ).group_by(ImportProfile.import_type)
    )
    stats = {row.import_type: row.cnt for row in count_result}

    return templates.TemplateResponse(request, "import_profiles.html", {
        "profiles":          profiles,
        "stats":             stats,
        "import_type_label": _IMPORT_TYPE_LABEL,
        "co_status_label":   _CO_STATUS_LABEL,
        "freq_label":        _IMPORT_FREQ_LABEL,
        "filter_import_type":     import_type or "",
        "filter_exporter_country": exporter_country or "",
        "filter_us_reexport":     us_reexport,
    })


# ── Phase III-1: ECCN 付番フロー ──────────────────────────────────

@router.post("/api/import-profiles/{profile_id}/request-eccn")
async def request_eccn(
    profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_pg_db),
):
    """
    ai_validation:8011 にトランザクションを作成し、
    ECCN 判定を依頼する（Phase III-1）。
    profile.eccn_validation_tx_id に transaction ID を保存する。
    """
    result = await db.execute(select(ImportProfile).where(ImportProfile.id == profile_id))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="ImportProfile not found")

    product_desc = profile.product_name or profile.product_code
    hs = profile.hs_code_import or ""
    exporter = profile.exporter_country or ""

    payload = {
        "title": f"輸入品 ECCN 判定依頼: {profile.product_code}",
        "items": [
            {
                "item_name": product_desc,
                "item_description": (
                    f"輸入品コード: {profile.product_code}\n"
                    f"HS: {hs}\n"
                    f"仕入先国: {exporter}\n"
                    f"輸入種別: {profile.import_type}\n"
                    + (f"備考: {profile.notes}" if profile.notes else "")
                ),
            }
        ],
        "source_module": "ai_classification",
    }
    if profile.exporter_country:
        payload["destination_country"] = None  # 輸出先（自社なので None）

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_AI_VALIDATION_URL}/api/transactions",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ai_validation 連携エラー: {e}")

    profile.eccn_validation_tx_id = data["id"]
    profile.eccn_requested_at = datetime.utcnow()
    profile.eccn_judgment_status = "pending"
    await db.commit()

    return {
        "ok": True,
        "profile_id": str(profile_id),
        "tx_id": data["id"],
        "tx_case_no": data.get("case_no"),
        "tx_url": f"{_AI_VALIDATION_PUBLIC_URL}/ui/transactions/{data['id']}",
    }


@router.post("/api/import-profiles/{profile_id}/sync-eccn-status")
async def sync_eccn_status(
    profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_pg_db),
):
    """
    ai_validation トランザクションの最新状態を取得し、
    profile.eccn_judgment_status を更新する。
    判定完了（agent_judgment_status あり）の場合は eccn_claimed にも反映を促す。
    """
    result = await db.execute(select(ImportProfile).where(ImportProfile.id == profile_id))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="ImportProfile not found")

    if not profile.eccn_validation_tx_id:
        raise HTTPException(status_code=400, detail="ECCN 判定依頼が未実施です")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_AI_VALIDATION_URL}/api/transactions/{profile.eccn_validation_tx_id}"
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ai_validation 連携エラー: {e}")

    judgment = data.get("agent_judgment_status")
    profile.eccn_judgment_status = judgment or data.get("status", "pending")
    await db.commit()

    return {
        "ok": True,
        "tx_id": profile.eccn_validation_tx_id,
        "tx_status": data.get("status"),
        "agent_judgment_status": judgment,
        "tx_url": f"{_AI_VALIDATION_PUBLIC_URL}/ui/transactions/{profile.eccn_validation_tx_id}",
        "note": "ECCN が確定したら「ECCN申告値」欄を手動で更新してください。" if judgment else "",
    }


# ── Phase III-3: 再輸出許可申請自動トリガー ───────────────────────

@router.post("/api/import-profiles/{profile_id}/trigger-reexport-check")
async def trigger_reexport_check(
    profile_id: uuid.UUID,
    destination_country: Optional[str] = None,
    db: AsyncSession = Depends(get_pg_db),
):
    """
    US EAR 対象輸入品を再輸出する際に export_license:8012 で許可申請ドラフトを自動作成する。

    条件: us_reexport_applicable=True かつ eccn_claimed が設定済み。
    destination_country（再輸出先）を指定するとドラフトに反映される。
    """
    result = await db.execute(select(ImportProfile).where(ImportProfile.id == profile_id))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="ImportProfile not found")

    if not profile.us_reexport_applicable:
        raise HTTPException(
            status_code=400,
            detail="us_reexport_applicable が False のため再輸出申請は不要です",
        )

    if not profile.eccn_claimed:
        raise HTTPException(
            status_code=400,
            detail="eccn_claimed（ECCN申告値）が未設定です。先に ECCN 付番を完了してください",
        )

    product_desc = (
        f"輸入品（{profile.product_code}）の EAR 再輸出申請\n"
        f"仕入先国: {profile.exporter_country or '不明'}\n"
        f"HS: {profile.hs_code_import or '未設定'}\n"
        + (f"EARライセンス例外: {profile.ear_license_exception}" if profile.ear_license_exception else "")
    )

    payload = {
        "license_type": "EAR",
        "form_type": "BIS748P",
        "item_description": product_desc,
        "eccn": profile.eccn_claimed,
        "destination_country": destination_country or None,
        "value_usd": profile.customs_value_usd,
        "notes": (
            f"ImportProfile ID: {profile_id}\n"
            f"品目コード: {profile.product_code}\n"
            f"仕入先: {profile.exporter_name or '不明'} ({profile.exporter_country or '—'})\n"
            f"EAR再輸出対象。輸入品から自動トリガー（ai_classification Phase III-3）"
        ),
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_EXPORT_LICENSE_URL}/api/export-licenses",
                json=payload,
            )
            resp.raise_for_status()
            license_data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"export_license 連携エラー: {e}")

    import uuid as _uuid
    profile.reexport_license_id = _uuid.UUID(license_data["id"])
    profile.reexport_triggered_at = datetime.utcnow()
    await db.commit()

    return {
        "ok": True,
        "profile_id": str(profile_id),
        "license_id": license_data["id"],
        "license_no": license_data.get("application_no"),
        "license_url": f"{_EXPORT_LICENSE_URL}/export-licenses/{license_data['id']}",
        "status": license_data.get("status"),
        "note": "輸出許可申請ドラフトを作成しました。export_license モジュールで内容を確認してください。",
    }
