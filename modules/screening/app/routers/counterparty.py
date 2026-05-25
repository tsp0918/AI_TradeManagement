"""与信管理 API ルーター（screening モジュール内）。

エンドポイント:
  GET  /api/counterparties/stats         リスクダッシュボード集計
  GET  /api/counterparties               取引先一覧（フィルタ・ページネーション）
  POST /api/counterparties               取引先登録（自動スクリーニング付き）
  POST /api/counterparties/import-csv    CSV一括インポート（取引先マスター）
  POST /api/counterparties/import-batch  JSON一括インポート（ERP連携・同期スクリーニング）
  GET  /api/counterparties/{id}          取引先詳細
  PUT  /api/counterparties/{id}          取引先更新
  DELETE /api/counterparties/{id}        取引先削除
  POST /api/counterparties/{id}/screen   スクリーニング実行（手動再実行）
  GET  /api/counterparties/{id}/history  与信スコア変更履歴
"""

import csv
import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal, get_db
from app.schemas.screening import ScreenRequest
from app.services.screening_service import run_screening

# platform-core の共有モデル（同一 PostgreSQL DB）
from platform_core.models.company import (
    Company,
    CompanyScreeningHistory,
    CounterpartyCreditHistory,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/counterparties", tags=["counterparty"])

# ── 国別リスクスコア（0-100） ───────────────────────────────────────────────

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


# ── Pydantic スキーマ ───────────────────────────────────────────────────────

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
    tenant_id: str | None = None


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
        tenant = Tenant(name="default", slug="default", plan="standard")
        db.add(tenant)
        await db.flush()
    return tenant.id


# ── エンドポイント ─────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
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
    """取引先登録後にバックグラウンドでスクリーニングサービスを直接呼び出す。"""
    try:
        req = ScreenRequest(company_name=company_name, company_id=company_id)
        async with AsyncSessionLocal() as db:
            screen_result = await run_screening(req, db)

            company = await db.get(Company, company_id)
            if not company:
                return

            is_hit = screen_result.result_status in ("match", "possible_match")
            matched_lists = list({m.list_source for m in screen_result.matches if m.list_source})
            prev_level = company.overall_risk_level

            company.is_sanctioned = is_hit
            company.sanction_lists = matched_lists if is_hit else []
            company.last_screened_at = datetime.now(tz=timezone.utc)
            company.screening_detail = screen_result.model_dump(mode="json")
            company.overall_risk_level = _calc_overall_risk(
                company.credit_score, company.country_risk_score or 0, is_hit
            )

            hist = CompanyScreeningHistory(
                company_id=company.id,
                lists_checked=["OFAC_SDN", "BIS_EL", "EU_CONSOLIDATED", "UK_OFSI", "BIS_UVL", "BIS_MEU"],
                result_is_hit=is_hit,
                result_detail=screen_result.model_dump(mode="json"),
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
    if body.tenant_id:
        tenant_id = uuid.UUID(body.tenant_id)
    else:
        tenant_id = await _get_default_tenant_id(db)

    cr = _country_risk(body.country_code)
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
        overall_risk_level=_calc_overall_risk(body.credit_score, cr, False),
        is_end_user=body.is_end_user,
        is_consignee=body.is_consignee,
        end_use_note=body.end_use_note,
    )
    db.add(company)
    await db.commit()
    await db.refresh(company)

    background_tasks.add_task(
        _run_auto_screening,
        company.id,
        company.name,
        company.name_aliases or [],
    )

    result = _serialize(company)
    result["auto_screening"] = "queued"
    return result


# ── CSV 一括インポート ─────────────────────────────────────────────────────

# 対応 CSV ヘッダー（日本語/英語どちらも可）
_CSV_FIELD_MAP: dict[str, list[str]] = {
    "name":                ["name", "企業名", "会社名", "取引先名"],
    "country_code":        ["country_code", "国コード", "国"],
    "aliases":             ["aliases", "別名", "AKA"],
    "registration_number": ["registration_number", "法人番号", "登録番号"],
    "address":             ["address", "住所"],
    "roles":               ["roles", "役割"],
    "is_end_user":         ["is_end_user", "最終需要者"],
    "is_consignee":        ["is_consignee", "荷受人"],
    "end_use_note":        ["end_use_note", "最終用途備考"],
}


def _csv_get(row: dict, field: str, default: str = "") -> str:
    for col in _CSV_FIELD_MAP.get(field, [field]):
        if col in row:
            return (row[col] or "").strip()
    return default


@router.post("/import-csv", status_code=201)
async def import_counterparties_csv(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="取引先マスターCSV"),
    db: AsyncSession = Depends(get_db),
):
    """取引先をCSVファイルから一括インポートする（取引先マスター登録）。

    CSVヘッダー例（英語/日本語どちらも可）:
      name, country_code, aliases, registration_number, address, roles, is_end_user

    - aliases: セミコロン区切り（例: Huawei;HW Technologies）
    - roles:   セミコロン区切り（例: customer;vendor）
    - is_end_user/is_consignee: 1/true/yes → True
    - 最大 2000 行
    """
    if file.content_type not in ("text/csv", "application/csv", "text/plain", "application/octet-stream"):
        # content_type は信頼しすぎない（ブラウザによって異なるため）
        pass

    content = await file.read()
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="CSV にヘッダー行がありません")

    tenant_id = await _get_default_tenant_id(db)
    imported: list[dict] = []
    errors:   list[dict] = []
    row_num = 1

    for row in reader:
        row_num += 1
        if row_num > 2002:
            errors.append({"row": row_num, "error": "最大 2000 行を超えました。残りは無視されました。"})
            break

        name = _csv_get(row, "name")
        if not name:
            continue

        try:
            country_raw = _csv_get(row, "country_code")
            country_code = country_raw[:2].upper() if country_raw else None

            aliases_raw = _csv_get(row, "aliases")
            aliases = [a.strip() for a in aliases_raw.split(";") if a.strip()] or None

            roles_raw = _csv_get(row, "roles")
            roles = [r.strip() for r in roles_raw.split(";") if r.strip()] or None

            is_end_user_raw  = _csv_get(row, "is_end_user").lower()
            is_consignee_raw = _csv_get(row, "is_consignee").lower()
            is_end_user  = is_end_user_raw  in ("1", "true", "yes", "はい")
            is_consignee = is_consignee_raw in ("1", "true", "yes", "はい")

            cr = _country_risk(country_code)
            company = Company(
                tenant_id=tenant_id,
                name=name,
                name_aliases=aliases,
                country_code=country_code,
                registration_number=_csv_get(row, "registration_number") or None,
                address=_csv_get(row, "address") or None,
                roles=roles,
                country_risk_score=cr,
                overall_risk_level=_calc_overall_risk(None, cr, False),
                is_end_user=is_end_user,
                is_consignee=is_consignee,
                end_use_note=_csv_get(row, "end_use_note") or None,
            )
            db.add(company)
            await db.flush()

            background_tasks.add_task(
                _run_auto_screening, company.id, name, aliases or []
            )
            imported.append({"name": name, "id": str(company.id), "country_code": country_code})

        except Exception as exc:
            errors.append({"row": row_num, "name": name, "error": str(exc)})

    await db.commit()
    return {
        "imported": len(imported),
        "errors":   errors,
        "items":    imported,
        "screening": "queued for all imported entries",
    }


# ── ERP JSON 一括インポート ────────────────────────────────────────────────

class BatchCompanyItem(BaseModel):
    name: str
    country_code: str | None = None
    aliases: list[str] | None = None
    registration_number: str | None = None
    address: str | None = None
    roles: list[str] | None = None
    is_end_user: bool = False
    is_consignee: bool = False
    end_use_note: str | None = None
    external_id: str | None = None  # ERP 側の企業 ID（追跡用）


class BatchImportRequest(BaseModel):
    companies: list[BatchCompanyItem]
    run_screening: bool = True   # False にするとスクリーニングをスキップ
    upsert: bool = False         # True にすると同名企業があれば更新


@router.post("/import-batch", status_code=201)
async def import_counterparties_batch(
    body: BatchImportRequest,
    db: AsyncSession = Depends(get_db),
):
    """ERP 連携用 JSON 一括インポート。

    CSVアップロードと同等の取引先マスター登録を JSON で行い、
    スクリーニングを同期実行して各社の審査結果をそのままレスポンスに含む。

    - 最大 500 件/リクエスト
    - run_screening=true（デフォルト）のとき各社をその場でスクリーニングし結果を返す
    - upsert=true のとき同名企業が既存にあれば更新、なければ新規作成
    """
    if len(body.companies) > 500:
        raise HTTPException(status_code=422, detail="1リクエストあたり最大 500 件まで")

    tenant_id = await _get_default_tenant_id(db)
    results: list[dict] = []
    errors:  list[dict] = []

    for idx, item in enumerate(body.companies):
        if not item.name.strip():
            errors.append({"index": idx, "error": "企業名が空です"})
            continue
        try:
            company: Company | None = None

            if body.upsert:
                existing = await db.execute(
                    select(Company).where(Company.name == item.name.strip()).limit(1)
                )
                company = existing.scalar_one_or_none()

            if company is None:
                cr = _country_risk(item.country_code)
                company = Company(
                    tenant_id=tenant_id,
                    name=item.name.strip(),
                    name_aliases=item.aliases,
                    country_code=item.country_code,
                    registration_number=item.registration_number,
                    address=item.address,
                    roles=item.roles,
                    country_risk_score=cr,
                    overall_risk_level=_calc_overall_risk(None, cr, False),
                    is_end_user=item.is_end_user,
                    is_consignee=item.is_consignee,
                    end_use_note=item.end_use_note,
                )
                db.add(company)
                await db.flush()
            else:
                # upsert: フィールドを更新
                if item.country_code is not None:
                    company.country_code = item.country_code
                if item.aliases is not None:
                    company.name_aliases = item.aliases
                if item.address is not None:
                    company.address = item.address
                if item.roles is not None:
                    company.roles = item.roles
                company.country_risk_score = _country_risk(company.country_code)

            screening_status = None
            screening_score  = None
            screening_hits   = []

            if body.run_screening:
                req = ScreenRequest(
                    company_name=company.name,
                    country=company.country_code,
                    company_id=company.id,
                )
                screen_result = await run_screening(req, db)

                is_hit = screen_result.result_status in ("match", "possible_match")
                matched_lists = list({m.list_source for m in screen_result.matches if m.list_source})

                company.is_sanctioned    = is_hit
                company.sanction_lists   = matched_lists if is_hit else []
                company.last_screened_at = datetime.now(tz=timezone.utc)
                company.overall_risk_level = _calc_overall_risk(
                    company.credit_score, company.country_risk_score or 0, is_hit
                )

                db.add(CompanyScreeningHistory(
                    company_id=company.id,
                    lists_checked=["OFAC_SDN", "BIS_EL", "EU_CONSOLIDATED", "UK_OFSI"],
                    result_is_hit=is_hit,
                    result_detail=screen_result.model_dump(mode="json"),
                ))

                screening_status = screen_result.result_status
                screening_score  = screen_result.max_score
                screening_hits   = [
                    {"entity": m.entity_name, "score": m.score, "list": m.list_source}
                    for m in screen_result.matches[:3]
                ]

            results.append({
                "index":            idx,
                "external_id":      item.external_id,
                "name":             company.name,
                "id":               str(company.id),
                "country_code":     company.country_code,
                "overall_risk_level": company.overall_risk_level,
                "is_sanctioned":    company.is_sanctioned,
                "screening_status": screening_status,
                "screening_score":  round(screening_score, 4) if screening_score else None,
                "screening_hits":   screening_hits,
            })

        except Exception as exc:
            logger.warning("batch import error idx=%d name=%r: %s", idx, item.name, exc)
            errors.append({"index": idx, "name": item.name, "error": str(exc)})

    await db.commit()

    flagged = [r for r in results if r["is_sanctioned"]]
    return {
        "imported":     len(results),
        "errors":       errors,
        "flagged":      len(flagged),
        "results":      results,
        "flagged_list": flagged,
    }


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
        db.add(CounterpartyCreditHistory(
            company_id=company.id,
            previous_credit_score=prev_score,
            new_credit_score=company.credit_score,
            previous_risk_level=prev_level,
            new_risk_level=company.overall_risk_level,
            change_reason=body.change_reason,
        ))

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
    """スクリーニングサービスを直接呼び出して結果を保存する。"""
    result = await db.execute(select(Company).where(Company.id == uuid.UUID(company_id)))
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=404, detail="Not found")

    req = ScreenRequest(
        company_name=company.name,
        country=company.country_code,
        company_id=company.id,
    )
    screen_result = await run_screening(req, db)

    is_hit = screen_result.result_status in ("match", "possible_match")
    prev_level = company.overall_risk_level

    company.is_sanctioned = is_hit
    company.sanction_lists = list({m.list_source for m in screen_result.matches if m.list_source}) if is_hit else []
    company.last_screened_at = datetime.now(tz=timezone.utc)
    company.screening_detail = screen_result.model_dump(mode="json")
    company.overall_risk_level = _calc_overall_risk(
        company.credit_score, company.country_risk_score or 0, is_hit
    )

    db.add(CompanyScreeningHistory(
        company_id=company.id,
        lists_checked=["OFAC_SDN", "BIS_EL", "EU_CONSOLIDATED", "UK_OFSI", "BIS_UVL", "BIS_MEU"],
        result_is_hit=is_hit,
        result_detail=screen_result.model_dump(mode="json"),
    ))

    if prev_level != company.overall_risk_level:
        db.add(CounterpartyCreditHistory(
            company_id=company.id,
            previous_risk_level=prev_level,
            new_risk_level=company.overall_risk_level,
            change_reason="manual screening update",
        ))

    await db.commit()
    await db.refresh(company)
    return {
        "company": _serialize(company),
        "screening": screen_result.model_dump(mode="json"),
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
