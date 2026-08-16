"""ライセンスクォータ管理 API — Phase 4。

IF-06: POST /api/licenses/quota-check          残枠照会（見積段階）
IF-07: POST /api/licenses/allocations          仮引当（契約発行時）
IF-07: DELETE /api/licenses/allocations/{id}  解放（失注・キャンセル）
IF-21: POST /api/licenses/allocations/{id}/consume  消費確定（出荷時、ERP呼び出し）
管理:  POST /api/licenses/quotas              許可証クォータ登録
管理:  GET  /api/licenses/quotas             一覧
管理:  GET  /api/licenses/quotas/{id}        詳細 + 引当状況
管理:  GET  /api/licenses/allocations        引当一覧

行レベルロック: SELECT ... FOR UPDATE で同時アロケーション競合を防止。
"""
from __future__ import annotations

import os
import random
import string
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..pg_session import get_pg_db
from platform_core.models.license_quota import ExportLicenseQuota, LicenseAllocation

router = APIRouter(tags=["license-quota"])

_ALLOC_VALID_DAYS = int(os.environ.get("LICENSE_ALLOCATION_VALID_DAYS", "90"))
_LEAD_TIME_WEEKS_DEFAULT = 8


# ── Pydantic スキーマ ──────────────────────────────────────────────

class QuotaCreateRequest(BaseModel):
    license_no: str
    license_type: Optional[str] = None         # EAR | FEFTA | individual
    product_code: Optional[str] = None
    eccn: Optional[str] = None
    destination_country: Optional[str] = None
    total_value_usd: Optional[float] = None
    total_unit: Optional[int] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    application_id: Optional[str] = None       # plat_export_license_application.id


class QuotaCheckItem(BaseModel):
    product_code: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    amount_usd: Optional[float] = None


class QuotaCheckRequest(BaseModel):
    items: list[QuotaCheckItem]
    destination_country: Optional[str] = None
    end_user_party_id: Optional[str] = None
    contract_start_date: Optional[str] = None
    contract_end_date: Optional[str] = None
    context: Optional[dict] = None


class AllocationItem(BaseModel):
    product_code: str
    quantity: Optional[float] = None
    amount_usd: Optional[float] = None


class AllocationCreateRequest(BaseModel):
    transaction_id: Optional[str] = None
    case_no: Optional[str] = None
    items: list[AllocationItem]
    destination_country: Optional[str] = None
    end_user_party_id: Optional[str] = None
    valid_until: Optional[str] = None         # ISO date。未指定時は _ALLOC_VALID_DAYS 日後


class ConsumeRequest(BaseModel):
    consumed_quantity: Optional[float] = None
    consumed_amount_usd: Optional[float] = None
    shipment_ref: Optional[str] = None


# ── ユーティリティ ────────────────────────────────────────────────

def _alloc_no() -> str:
    suffix = "".join(random.choices(string.digits, k=6))
    return f"ALC-{suffix}"


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _serialize_quota(q: ExportLicenseQuota) -> dict:
    return {
        "id": str(q.id),
        "license_no": q.license_no,
        "license_type": q.license_type,
        "product_code": q.product_code,
        "eccn": q.eccn,
        "destination_country": q.destination_country,
        "total_value_usd": float(q.total_value_usd) if q.total_value_usd else None,
        "consumed_value_usd": float(q.consumed_value_usd),
        "allocated_value_usd": float(q.allocated_value_usd),
        "available_value_usd": float(q.available_value_usd) if q.available_value_usd is not None else None,
        "total_unit": q.total_unit,
        "consumed_unit": q.consumed_unit,
        "allocated_unit": q.allocated_unit,
        "available_unit": q.available_unit,
        "valid_from": q.valid_from.isoformat() if q.valid_from else None,
        "valid_until": q.valid_until.isoformat() if q.valid_until else None,
        "status": q.status,
        "created_at": q.created_at.isoformat(),
    }


def _serialize_alloc(a: LicenseAllocation) -> dict:
    return {
        "id": str(a.id),
        "allocation_no": a.allocation_no,
        "quota_id": str(a.quota_id),
        "transaction_id": a.transaction_id,
        "case_no": a.case_no,
        "product_code": a.product_code,
        "quantity": float(a.quantity) if a.quantity else None,
        "amount_usd": float(a.amount_usd) if a.amount_usd else None,
        "status": a.status,
        "valid_until": a.valid_until.isoformat() if a.valid_until else None,
        "allocated_at": a.allocated_at.isoformat(),
        "consumed_at": a.consumed_at.isoformat() if a.consumed_at else None,
        "released_at": a.released_at.isoformat() if a.released_at else None,
    }


# ── クォータ管理 API ──────────────────────────────────────────────

@router.post("/api/licenses/quotas", status_code=201, response_model=dict)
async def create_quota(body: QuotaCheckRequest, db: AsyncSession = Depends(get_pg_db)) -> Any:
    """新規輸出許可証クォータを登録する（輸出管理部門が承認後に入力）。"""
    # 同一ルートで混乱しないよう別ハンドラー
    raise HTTPException(status_code=501, detail="Use POST /api/licenses/quotas/register")


@router.post("/api/licenses/quotas/register", status_code=201, response_model=dict)
async def register_quota(body: QuotaCreateRequest, db: AsyncSession = Depends(get_pg_db)) -> Any:
    """許可証クォータを新規登録する。"""
    existing = await db.execute(
        select(ExportLicenseQuota).where(ExportLicenseQuota.license_no == body.license_no).limit(1)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"license_no '{body.license_no}' は既に登録されています")

    quota = ExportLicenseQuota(
        license_no=body.license_no,
        license_type=body.license_type,
        product_code=body.product_code,
        eccn=body.eccn,
        destination_country=body.destination_country,
        total_value_usd=Decimal(str(body.total_value_usd)) if body.total_value_usd else None,
        total_unit=body.total_unit,
        valid_from=_parse_date(body.valid_from),
        valid_until=_parse_date(body.valid_until),
        application_id=uuid.UUID(body.application_id) if body.application_id else None,
    )
    db.add(quota)
    await db.flush()
    quota_id = quota.id
    await db.commit()

    result = await db.execute(
        select(ExportLicenseQuota).options(selectinload(ExportLicenseQuota.allocations))
        .where(ExportLicenseQuota.id == quota_id)
    )
    quota = result.scalar_one()
    return _serialize_quota(quota)


@router.get("/api/licenses/quotas", response_model=dict)
async def list_quotas(
    product_code: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_pg_db),
) -> Any:
    stmt = (
        select(ExportLicenseQuota)
        .options(selectinload(ExportLicenseQuota.allocations))
        .order_by(ExportLicenseQuota.created_at.desc())
        .limit(limit)
    )
    if product_code:
        stmt = stmt.where(ExportLicenseQuota.product_code == product_code)
    if status:
        stmt = stmt.where(ExportLicenseQuota.status == status)
    result = await db.execute(stmt)
    quotas = result.scalars().all()
    return {"quotas": [_serialize_quota(q) for q in quotas], "total": len(quotas)}


@router.get("/api/licenses/quotas/{quota_id}", response_model=dict)
async def get_quota(quota_id: str, db: AsyncSession = Depends(get_pg_db)) -> Any:
    result = await db.execute(
        select(ExportLicenseQuota)
        .options(selectinload(ExportLicenseQuota.allocations))
        .where(ExportLicenseQuota.id == uuid.UUID(quota_id))
        .limit(1)
    )
    q = result.scalar_one_or_none()
    if not q:
        raise HTTPException(status_code=404, detail="Quota not found")
    allocs = sorted(q.allocations, key=lambda a: a.allocated_at, reverse=True)
    return {**_serialize_quota(q), "allocations": [_serialize_alloc(a) for a in allocs]}


# ── IF-06: クォータ残枠照会 ──────────────────────────────────────

@router.post("/api/licenses/quota-check", response_model=dict)
async def quota_check(body: QuotaCheckRequest, db: AsyncSession = Depends(get_pg_db)) -> Any:
    """IF-06: 品目リストに対するライセンス残枠を照会する（枠は押さえない）。"""
    results = []
    warnings = []
    overall = "not_required"
    contract_end = _parse_date(body.contract_end_date)

    for item in body.items:
        stmt = (
            select(ExportLicenseQuota)
            .options(selectinload(ExportLicenseQuota.allocations))
            .where(ExportLicenseQuota.product_code == item.product_code)
            .where(ExportLicenseQuota.status == "active")
        )
        if body.destination_country:
            stmt = stmt.where(
                (ExportLicenseQuota.destination_country == body.destination_country)
                | (ExportLicenseQuota.destination_country.is_(None))
            )
        q_result = await db.execute(stmt.limit(1))
        quota: Optional[ExportLicenseQuota] = q_result.scalar_one_or_none()

        if quota is None:
            results.append({
                "product_code": item.product_code,
                "license_required": False,
                "sufficient": True,
                "warnings": [],
            })
            continue

        avail_value = quota.available_value_usd
        avail_unit = quota.available_unit

        item_warnings = []
        sufficient = True
        shortfall_qty = None
        shortfall_val = None
        expires_before_end = False

        # 数量チェック
        if item.quantity is not None and avail_unit is not None:
            if item.quantity > avail_unit:
                sufficient = False
                shortfall_qty = item.quantity - avail_unit
                w_msg = (
                    f"許可証 {quota.license_no} の残枠が {shortfall_qty:.0f} 不足します。"
                    f"新規申請の想定リードタイムは {_LEAD_TIME_WEEKS_DEFAULT} 週間です。"
                )
                item_warnings.append({"code": "QUOTA_SHORTFALL", "message": w_msg})
                warnings.append({"code": "QUOTA_SHORTFALL", "message": w_msg})

        # 金額チェック
        if item.amount_usd is not None and avail_value is not None:
            if Decimal(str(item.amount_usd)) > avail_value:
                sufficient = False
                shortfall_val = float(Decimal(str(item.amount_usd)) - avail_value)
                w_msg = f"許可証 {quota.license_no} の残枠金額が USD {shortfall_val:,.0f} 不足します。"
                item_warnings.append({"code": "QUOTA_VALUE_SHORTFALL", "message": w_msg})
                warnings.append({"code": "QUOTA_VALUE_SHORTFALL", "message": w_msg})

        # 有効期限 vs 契約終了日チェック
        if quota.valid_until and contract_end and quota.valid_until < contract_end:
            expires_before_end = True
            w_msg = (
                f"許可証の有効期限（{quota.valid_until}）が"
                f"契約終了日（{contract_end}）より前に到来します。"
            )
            item_warnings.append({"code": "LICENSE_EXPIRES_DURING_CONTRACT", "message": w_msg})
            warnings.append({"code": "LICENSE_EXPIRES_DURING_CONTRACT", "message": w_msg})

        results.append({
            "product_code": item.product_code,
            "license_required": True,
            "license_number": quota.license_no,
            "license_type": quota.license_type,
            "quantity_total": quota.total_unit,
            "quantity_consumed": quota.consumed_unit,
            "quantity_allocated": quota.allocated_unit,
            "quantity_available": avail_unit,
            "value_total_usd": float(quota.total_value_usd) if quota.total_value_usd else None,
            "value_consumed_usd": float(quota.consumed_value_usd),
            "value_allocated_usd": float(quota.allocated_value_usd),
            "value_available_usd": float(avail_value) if avail_value is not None else None,
            "valid_until": quota.valid_until.isoformat() if quota.valid_until else None,
            "requested_quantity": item.quantity,
            "sufficient": sufficient,
            "shortfall_quantity": shortfall_qty,
            "shortfall_value_usd": shortfall_val,
            "expires_before_contract_end": expires_before_end,
            "new_application_lead_time_weeks": _LEAD_TIME_WEEKS_DEFAULT,
            "warnings": item_warnings,
        })

        if not sufficient:
            overall = "insufficient"
        elif expires_before_end and overall != "insufficient":
            overall = "expiring"
        elif overall == "not_required":
            overall = "sufficient"

    return {"overall": overall, "items": results, "warnings": warnings}


# ── IF-07: 仮引当 ─────────────────────────────────────────────────

@router.post("/api/licenses/allocations", status_code=201, response_model=dict)
async def create_allocation(body: AllocationCreateRequest, db: AsyncSession = Depends(get_pg_db)) -> Any:
    """IF-07: ライセンス枠を仮引当する（行レベルロック使用）。"""
    valid_until = _parse_date(body.valid_until) or (date.today() + timedelta(days=_ALLOC_VALID_DAYS))
    created_allocs = []
    conflict_detail = None

    for item in body.items:
        # FOR UPDATE で同時アロケーション競合防止（selectinload との併用不可のため 2 クエリ構成）
        result = await db.execute(
            select(ExportLicenseQuota)
            .where(ExportLicenseQuota.product_code == item.product_code)
            .where(ExportLicenseQuota.status == "active")
            .with_for_update()
            .limit(1)
        )
        quota: Optional[ExportLicenseQuota] = result.scalar_one_or_none()

        if quota is None:
            # ライセンス不要品目はスキップ
            continue

        # 有効な仮引当を集計
        alloc_result = await db.execute(
            select(LicenseAllocation)
            .where(LicenseAllocation.quota_id == quota.id)
            .where(LicenseAllocation.status == "allocated")
        )
        active_allocs = alloc_result.scalars().all()

        allocated_val = sum((a.amount_usd or Decimal("0")) for a in active_allocs)
        allocated_qty = sum(int(a.quantity or 0) for a in active_allocs)

        avail_val = (quota.total_value_usd - quota.consumed_value_usd - allocated_val) if quota.total_value_usd else None
        avail_qty = (quota.total_unit - quota.consumed_unit - allocated_qty) if quota.total_unit else None

        # 残枠チェック
        if item.amount_usd is not None and avail_val is not None:
            if Decimal(str(item.amount_usd)) > avail_val:
                conflict_detail = {
                    "product_code": item.product_code,
                    "requested": item.amount_usd,
                    "available": float(avail_val),
                }
                await db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": "LICENSE_QUOTA_CONFLICT",
                            "message": "引当可能な残枠がありません",
                            "detail": conflict_detail,
                        }
                    },
                )

        if item.quantity is not None and avail_qty is not None:
            if item.quantity > avail_qty:
                await db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": "LICENSE_QUOTA_CONFLICT",
                            "message": "引当可能な残数量がありません",
                            "detail": {"product_code": item.product_code, "requested": item.quantity, "available": avail_qty},
                        }
                    },
                )

        alloc = LicenseAllocation(
            allocation_no=_alloc_no(),
            quota_id=quota.id,
            transaction_id=body.transaction_id,
            case_no=body.case_no,
            product_code=item.product_code,
            quantity=Decimal(str(item.quantity)) if item.quantity else None,
            amount_usd=Decimal(str(item.amount_usd)) if item.amount_usd else None,
            valid_until=valid_until,
        )
        db.add(alloc)
        created_allocs.append((alloc, quota.license_no))

    await db.commit()

    # 引当が1件も作成されなかった場合 = 対象許可証が存在しない（ライセンス不要品目）
    if not created_allocs:
        return {
            "allocation_id": None,
            "status": "not_required",
            "allocations": [],
            "valid_until": valid_until.isoformat(),
            "note": "対象品目のライセンスが存在しないため引当不要と判定しました",
        }

    return {
        "allocation_id": created_allocs[0][0].allocation_no,
        "status": "allocated",
        "allocations": [
            {
                "product_code": a.product_code,
                "license_number": ln,
                "quantity": float(a.quantity) if a.quantity else None,
                "amount_usd": float(a.amount_usd) if a.amount_usd else None,
                "allocation_no": a.allocation_no,
            }
            for a, ln in created_allocs
        ],
        "valid_until": valid_until.isoformat(),
    }


@router.delete("/api/licenses/allocations/{allocation_no}", status_code=200, response_model=dict)
async def release_allocation(allocation_no: str, db: AsyncSession = Depends(get_pg_db)) -> Any:
    """IF-07 解放: 仮引当を解放する（失注・キャンセル時）。"""
    result = await db.execute(
        select(LicenseAllocation).where(LicenseAllocation.allocation_no == allocation_no).limit(1)
    )
    alloc: Optional[LicenseAllocation] = result.scalar_one_or_none()
    if not alloc:
        raise HTTPException(status_code=404, detail="Allocation not found")
    if alloc.status != "allocated":
        raise HTTPException(status_code=409, detail=f"引当状態が '{alloc.status}' のため解放できません")

    alloc.status = "released"
    alloc.released_at = datetime.now(timezone.utc)
    await db.commit()
    return {"result": "released", "allocation_no": allocation_no}


@router.post("/api/licenses/allocations/{allocation_no}/consume", response_model=dict)
async def consume_allocation(
    allocation_no: str,
    body: ConsumeRequest,
    db: AsyncSession = Depends(get_pg_db),
) -> Any:
    """IF-21 消費確定: 仮引当から消費に振り替える（出荷時に ERP から呼ぶ）。"""
    result = await db.execute(
        select(LicenseAllocation).where(LicenseAllocation.allocation_no == allocation_no)
        .with_for_update().limit(1)
    )
    alloc: Optional[LicenseAllocation] = result.scalar_one_or_none()
    if not alloc:
        raise HTTPException(status_code=404, detail="Allocation not found")
    if alloc.status != "allocated":
        raise HTTPException(status_code=409, detail=f"status='{alloc.status}' の引当は消費確定できません")

    quota_result = await db.execute(
        select(ExportLicenseQuota)
        .options(selectinload(ExportLicenseQuota.allocations))
        .where(ExportLicenseQuota.id == alloc.quota_id)
        .with_for_update()
        .limit(1)
    )
    quota = quota_result.scalar_one_or_none()
    if quota is None:
        raise HTTPException(status_code=404, detail="Quota not found")

    consume_qty = Decimal(str(body.consumed_quantity)) if body.consumed_quantity else (alloc.quantity or Decimal("0"))
    consume_val = Decimal(str(body.consumed_amount_usd)) if body.consumed_amount_usd else (alloc.amount_usd or Decimal("0"))

    quota.consumed_value_usd = (quota.consumed_value_usd or Decimal("0")) + consume_val
    quota.consumed_unit = (quota.consumed_unit or 0) + int(consume_qty)

    alloc.status = "consumed"
    alloc.consumed_at = datetime.now(timezone.utc)

    await db.commit()
    remaining = None
    if quota.total_value_usd is not None:
        remaining = float(quota.total_value_usd) - float(quota.consumed_value_usd) - float(quota.allocated_value_usd)
    return {
        "result": "consumed",
        "allocation_no": allocation_no,
        "quota_remaining_value_usd": remaining,
    }


class ReturnRequest(BaseModel):
    returned_quantity: Optional[float] = None
    returned_amount_usd: Optional[float] = None


@router.post("/api/licenses/allocations/{allocation_no}/return", response_model=dict)
async def return_allocation(
    allocation_no: str,
    body: ReturnRequest,
    db: AsyncSession = Depends(get_pg_db),
) -> Any:
    """IF-22 消費枠の戻し入れ: 返品時に出荷済み消費枠を減算する。

    - allocation.status が 'consumed' であること（二重戻し入れ防止）
    - quota.consumed_value_usd と consumed_unit を減算
    - allocation.status を 'returned' に変更
    """
    result = await db.execute(
        select(LicenseAllocation).where(LicenseAllocation.allocation_no == allocation_no)
        .with_for_update().limit(1)
    )
    alloc: Optional[LicenseAllocation] = result.scalar_one_or_none()
    if not alloc:
        raise HTTPException(status_code=404, detail="Allocation not found")
    if alloc.status != "consumed":
        raise HTTPException(status_code=409, detail=f"status='{alloc.status}' の引当は戻し入れできません（consumed のみ可）")

    quota_result = await db.execute(
        select(ExportLicenseQuota).where(ExportLicenseQuota.id == alloc.quota_id).with_for_update().limit(1)
    )
    quota = quota_result.scalar_one_or_none()
    if quota is None:
        raise HTTPException(status_code=404, detail="Quota not found")

    ret_qty = Decimal(str(body.returned_quantity)) if body.returned_quantity else (alloc.quantity or Decimal("0"))
    ret_val = Decimal(str(body.returned_amount_usd)) if body.returned_amount_usd else (alloc.amount_usd or Decimal("0"))

    quota.consumed_unit = max(0, (quota.consumed_unit or 0) - int(ret_qty))
    quota.consumed_value_usd = max(Decimal("0"), (quota.consumed_value_usd or Decimal("0")) - ret_val)

    alloc.status = "returned"
    alloc.released_at = datetime.now(timezone.utc)

    await db.commit()
    remaining = None
    if quota.total_value_usd is not None:
        remaining = float(quota.total_value_usd) - float(quota.consumed_value_usd)
    return {
        "result": "returned",
        "allocation_no": allocation_no,
        "returned_quantity": float(ret_qty),
        "returned_amount_usd": float(ret_val),
        "quota_remaining_value_usd": remaining,
    }


@router.get("/api/licenses/allocations", response_model=dict)
async def list_allocations(
    status: Optional[str] = Query(None),
    product_code: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_pg_db),
) -> Any:
    """引当一覧（輸出管理部門向け管理画面用）。"""
    stmt = select(LicenseAllocation).order_by(LicenseAllocation.allocated_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(LicenseAllocation.status == status)
    if product_code:
        stmt = stmt.where(LicenseAllocation.product_code == product_code)
    result = await db.execute(stmt)
    allocs = result.scalars().all()
    return {"allocations": [_serialize_alloc(a) for a in allocs], "total": len(allocs)}
