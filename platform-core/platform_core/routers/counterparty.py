"""与信管理 API ルーター。

エンドポイント:
- GET  /api/counterparties          取引先一覧（フィルタ・ページネーション）
- POST /api/counterparties          取引先登録（自動スクリーニング付き）
- GET  /api/counterparties/stats    リスクダッシュボード集計
- GET  /api/counterparties/{id}     取引先詳細
- PUT  /api/counterparties/{id}     取引先更新
- DELETE /api/counterparties/{id}   取引先削除
- POST /api/counterparties/{id}/screen   スクリーニング実行（手動再実行）
- GET  /api/counterparties/{id}/history  与信スコア変更履歴

自動スクリーニング:
  取引先登録時（POST /api/counterparties）に screening モジュール（port 8005）
  への照合を非同期で自動実行する。timeout=5s で応答がなければ background で継続。
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db, AsyncSessionLocal
from platform_core.models.company import Company, CompanyScreeningHistory, CounterpartyCreditHistory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/counterparties", tags=["counterparty"])

_SCREENING_URL = "http://localhost:8005"

# 国別リスクスコア（0-100）
_COUNTRY_RISK: dict[str, int] = {
    "CN": 75, "RU": 90, "KP": 100, "IR": 100, "SY": 95, "CU": 85, "VE": 70,
    "BY": 80, "SD": 85, "MM": 75, "LY": 70, "YE": 80, "ZW": 60,
    "US": 5, "GB": 5, "DE": 5, "JP": 5, "FR": 5, "AU": 5, "CA": 5,
}
_COUNTRY_RISK_DEFAULT = 20


def _country_risk(country_code: str | None) -> int:
    if not country_code:
        return _COUNTRY_RISK_DEFAULT
    return _COUNTRY_RISK.get(country_code.upper(), _COUNTRY_RISK_DEFAULT)


def _calc_overall_risk(credit_score: int | None, country_risk: int, is_sanctioned: bool) -> str:
    if is_sanctioned:
        return "CRITICAL"
    score = 0
    if credit_score is not None:
        score += (100 - credit_score) * 0.4
    score += country_risk * 0.6
    if score >= 75:
        return "HIGH"
    if score >= 50:
        return "MEDIUM"
    return "LOW"


# ── Pydantic スキーマ ─────────────────────────────────────────────

class CounterpartyCreate(BaseModel):
    name: str
    name_aliases: list[str] | None = None
    country_code: str | None = None
    registration_number: str | None = None
    address: str | None = None
    website: str | None = None
    roles: list[str] | None = None
    credit_score: int | None = None
    credit_data: dict | None = None
    is_end_user: bool = False
    is_consignee: bool = False
    end_use_note: str | None = None
    tenant_id: str | None = None  # 省略時は default tenant


class CounterpartyUpdate(BaseModel):
    name: str | None = None
    name_aliases: list[str] | None = None
    country_code: str | None = None
    registration_number: str | None = None
    address: str | None = None
    website: str | None = None
    roles: list[str] | None = None
    credit_score: int | None = None
    credit_data: dict | None = None
    is_end_user: bool | None = None
    is_consignee: bool | None = None
    end_use_note: str | None = None
    change_reason: str | None = None


def _serialize(c: Company) -> dict:
    return {
        "id": str(c.id),
        "name": c.name,
        "name_aliases": c.name_aliases,
        "country_code": c.country_code,
        "registration_number": c.registration_number,
        "address": c.address,
        "website": c.website,
        "roles": c.roles,
        "credit_score": c.credit_score,
        "credit_data": c.credit_data,
        "country_risk_score": c.country_risk_score,
        "overall_risk_level": c.overall_risk_level,
        "is_sanctioned": c.is_sanctioned,
        "sanction_lists": c.sanction_lists,
        "last_screened_at": c.last_screened_at.isoformat() if c.last_screened_at else None,
        "is_end_user": c.is_end_user,
        "is_consignee": c.is_consignee,
        "end_use_note": c.end_use_note,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


async def _get_default_tenant_id(db: AsyncSession) -> uuid.UUID:
    from platform_core.models.tenant import Tenant
    result = await db.execute(select(Tenant).limit(1))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        # 初期セットアップ: デフォルトテナントを自動作成
        tenant = Tenant(name="default", slug="default", plan="standard")
        db.add(tenant)
        await db.flush()
    return tenant.id


# ── エンドポイント ─────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """リスクダッシュボード集計。"""
    result = await db.execute(select(Company))
    companies = result.scalars().all()
    total = len(companies)
    by_risk: dict[str, int] = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "UNKNOWN": 0}
    sanctioned = 0
    by_role: dict[str, int] = {}
    by_country: dict[str, int] = {}
    for c in companies:
        level = c.overall_risk_level or "UNKNOWN"
        by_risk[level] = by_risk.get(level, 0) + 1
        if c.is_sanctioned:
            sanctioned += 1
        for role in (c.roles or []):
            by_role[role] = by_role.get(role, 0) + 1
        if c.country_code:
            by_country[c.country_code] = by_country.get(c.country_code, 0) + 1
    return {
        "total": total,
        "sanctioned": sanctioned,
        "by_risk_level": by_risk,
        "by_role": by_role,
        "top_countries": sorted(by_country.items(), key=lambda x: -x[1])[:10],
    }


@router.get("")
async def list_counterparties(
    q: str | None = Query(None),
    risk_level: str | None = Query(None),
    role: str | None = Query(None),
    country_code: str | None = Query(None),
    is_sanctioned: bool | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Company).order_by(Company.updated_at.desc())
    result = await db.execute(stmt)
    companies = result.scalars().all()

    # Python-side フィルタ（小規模DB向け）
    filtered = []
    for c in companies:
        if q:
            if q.lower() not in c.name.lower() and not any(
                q.lower() in a.lower() for a in (c.name_aliases or [])
            ):
                continue
        if risk_level and c.overall_risk_level != risk_level.upper():
            continue
        if role and role not in (c.roles or []):
            continue
        if country_code and (c.country_code or "").upper() != country_code.upper():
            continue
        if is_sanctioned is not None and c.is_sanctioned != is_sanctioned:
            continue
        filtered.append(c)

    total = len(filtered)
    page = filtered[offset: offset + limit]
    return {"total": total, "items": [_serialize(c) for c in page]}


async def _run_auto_screening(company_id: uuid.UUID, company_name: str, aliases: list[str]) -> None:
    """取引先登録後にバックグラウンドで自動スクリーニングを実行する。"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_SCREENING_URL}/api/screen",
                json={"company_name": company_name, "aliases": aliases},
            )
            if resp.status_code != 200:
                logger.warning("auto-screening HTTP %s for %s", resp.status_code, company_name)
                return
            screening_result = resp.json()
            is_hit = screening_result.get("result_status") == "hit"
            matched_lists = list({m.get("list_type", "") for m in screening_result.get("matches", []) if m.get("list_type")})

        async with AsyncSessionLocal() as db:
            company = await db.get(Company, company_id)
            if not company:
                return

            prev_level = company.overall_risk_level
            company.is_sanctioned       = is_hit
            company.sanction_lists      = matched_lists if is_hit else []
            company.last_screened_at    = datetime.now(tz=timezone.utc)
            company.screening_detail    = screening_result
            company.overall_risk_level  = _calc_overall_risk(
                company.credit_score, company.country_risk_score or 0, is_hit
            )

            hist = CompanyScreeningHistory(
                company_id=company.id,
                lists_checked=["OFAC_SDN", "BIS_EL", "EU_CONSOLIDATED", "UK_OFSI", "BIS_UVL", "BIS_MEU"],
                result_is_hit=is_hit,
                result_detail=screening_result,
            )
            db.add(hist)

            if prev_level != company.overall_risk_level:
                db.add(CounterpartyCreditHistory(
                    company_id=company.id,
                    previous_risk_level=prev_level,
                    new_risk_level=company.overall_risk_level,
                    change_reason="auto-screening on create",
                ))

            await db.commit()
            logger.info(
                "auto-screening done for %s: is_hit=%s risk=%s",
                company_name, is_hit, company.overall_risk_level,
            )
    except Exception as exc:
        logger.warning("auto-screening failed for %s: %s", company_name, exc)


@router.post("", status_code=201)
async def create_counterparty(
    body: CounterpartyCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """取引先を登録し、自動スクリーニングをバックグラウンドで実行する。"""
    if body.tenant_id:
        tenant_id = uuid.UUID(body.tenant_id)
    else:
        tenant_id = await _get_default_tenant_id(db)

    cr = _country_risk(body.country_code)
    risk_level = _calc_overall_risk(body.credit_score, cr, False)
    company = Company(
        tenant_id=tenant_id,
        name=body.name,
        name_aliases=body.name_aliases,
        country_code=body.country_code,
        registration_number=body.registration_number,
        address=body.address,
        website=body.website,
        roles=body.roles,
        credit_score=body.credit_score,
        credit_data=body.credit_data,
        country_risk_score=cr,
        overall_risk_level=risk_level,
        is_end_user=body.is_end_user,
        is_consignee=body.is_consignee,
        end_use_note=body.end_use_note,
    )
    db.add(company)
    await db.commit()
    await db.refresh(company)

    # 自動スクリーニングをバックグラウンドで起動（応答を待たない）
    background_tasks.add_task(
        _run_auto_screening,
        company.id,
        company.name,
        company.name_aliases or [],
    )

    result = _serialize(company)
    result["auto_screening"] = "queued"
    return result


@router.get("/{company_id}")
async def get_counterparty(company_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Company).where(Company.id == uuid.UUID(company_id)))
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _serialize(company)


@router.put("/{company_id}")
async def update_counterparty(
    company_id: str, body: CounterpartyUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Company).where(Company.id == uuid.UUID(company_id)))
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=404, detail="Not found")

    prev_score = company.credit_score
    prev_level = company.overall_risk_level

    for field in ("name", "name_aliases", "country_code", "registration_number",
                  "address", "website", "roles", "credit_score", "credit_data",
                  "is_end_user", "is_consignee", "end_use_note"):
        val = getattr(body, field)
        if val is not None:
            setattr(company, field, val)

    cr = _country_risk(company.country_code)
    company.country_risk_score = cr
    company.overall_risk_level = _calc_overall_risk(company.credit_score, cr, company.is_sanctioned)

    if prev_score != company.credit_score or prev_level != company.overall_risk_level:
        hist = CounterpartyCreditHistory(
            company_id=company.id,
            previous_credit_score=prev_score,
            new_credit_score=company.credit_score,
            previous_risk_level=prev_level,
            new_risk_level=company.overall_risk_level,
            change_reason=body.change_reason,
        )
        db.add(hist)

    await db.commit()
    await db.refresh(company)
    return _serialize(company)


@router.delete("/{company_id}", status_code=204)
async def delete_counterparty(company_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Company).where(Company.id == uuid.UUID(company_id)))
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(company)
    await db.commit()


@router.post("/{company_id}/screen")
async def screen_counterparty(company_id: str, db: AsyncSession = Depends(get_db)):
    """screening モジュール（port 8005）に対してスクリーニングを実行し結果を保存する。"""
    result = await db.execute(select(Company).where(Company.id == uuid.UUID(company_id)))
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=404, detail="Not found")

    screening_result: dict[str, Any] = {}
    is_hit = False
    lists_checked = ["OFAC_SDN", "BIS_EL"]
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "http://localhost:8005/api/screen",
                json={"name": company.name, "aliases": company.name_aliases or []},
            )
            if resp.status_code == 200:
                screening_result = resp.json()
                is_hit = screening_result.get("is_hit", False)
    except Exception as exc:
        screening_result = {"error": str(exc), "note": "screening module unreachable"}

    prev_level = company.overall_risk_level
    company.is_sanctioned = is_hit
    company.sanction_lists = screening_result.get("matched_lists", []) if is_hit else []
    company.last_screened_at = datetime.now(tz=timezone.utc)
    company.screening_detail = screening_result
    company.overall_risk_level = _calc_overall_risk(
        company.credit_score, company.country_risk_score or 0, is_hit
    )

    hist_screen = CompanyScreeningHistory(
        company_id=company.id,
        lists_checked=lists_checked,
        result_is_hit=is_hit,
        result_detail=screening_result,
    )
    db.add(hist_screen)

    if prev_level != company.overall_risk_level:
        hist_credit = CounterpartyCreditHistory(
            company_id=company.id,
            previous_risk_level=prev_level,
            new_risk_level=company.overall_risk_level,
            change_reason="screening result update",
        )
        db.add(hist_credit)

    await db.commit()
    await db.refresh(company)
    return {
        "company": _serialize(company),
        "screening": screening_result,
        "is_hit": is_hit,
    }


@router.get("/{company_id}/history")
async def get_credit_history(company_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Company).where(Company.id == uuid.UUID(company_id)))
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=404, detail="Not found")

    hist_result = await db.execute(
        select(CounterpartyCreditHistory)
        .where(CounterpartyCreditHistory.company_id == uuid.UUID(company_id))
        .order_by(CounterpartyCreditHistory.changed_at.desc())
        .limit(50)
    )
    histories = hist_result.scalars().all()
    return {
        "company_id": company_id,
        "histories": [
            {
                "id": h.id,
                "previous_credit_score": h.previous_credit_score,
                "new_credit_score": h.new_credit_score,
                "previous_risk_level": h.previous_risk_level,
                "new_risk_level": h.new_risk_level,
                "change_reason": h.change_reason,
                "changed_at": h.changed_at.isoformat(),
            }
            for h in histories
        ],
    }
