"""拠点（Organization / Tenant）管理 API。

エンドポイント:
    GET  /api/organizations          → 全拠点一覧
    GET  /api/organizations/tree     → 親子階層ツリー
    POST /api/organizations          → 新規拠点作成
    GET  /api/organizations/{id}     → 拠点詳細
    PATCH /api/organizations/{id}    → 拠点更新
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.tenant import Tenant

router = APIRouter(prefix="/api/organizations", tags=["organizations"])


# ── Schemas ──────────────────────────────────────────────────────────

class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    country_code: str | None
    export_regime: str | None
    org_type: str | None
    timezone: str | None
    flag_emoji: str | None
    parent_id: uuid.UUID | None
    is_active: bool

    model_config = {"from_attributes": True}


class OrgCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r'^[a-z0-9_-]+$')
    country_code: str | None = Field(None, min_length=2, max_length=2)
    export_regime: str | None = None
    org_type: str | None = None
    timezone: str | None = None
    flag_emoji: str | None = None
    parent_id: uuid.UUID | None = None


class OrgUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    country_code: str | None = Field(None, min_length=2, max_length=2)
    export_regime: str | None = None
    org_type: str | None = None
    timezone: str | None = None
    flag_emoji: str | None = None
    parent_id: uuid.UUID | None = None
    is_active: bool | None = None


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("", response_model=list[OrgOut])
async def list_organizations(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
) -> list[OrgOut]:
    """全拠点一覧を返す。active_only=true（デフォルト）で有効拠点のみ。"""
    q = select(Tenant).order_by(Tenant.country_code, Tenant.name)
    if active_only:
        q = q.where(Tenant.is_active == True)
    result = await db.execute(q)
    return [OrgOut.model_validate(t) for t in result.scalars().all()]


@router.get("/tree")
async def organization_tree(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    """親子階層ツリーを返す。HQ → 子会社 → 支店の順で入れ子になる。"""
    result = await db.execute(
        select(Tenant).where(Tenant.is_active == True).order_by(Tenant.created_at)
    )
    all_orgs = result.scalars().all()
    by_id = {t.id: t for t in all_orgs}

    def _node(t: Tenant) -> dict[str, Any]:
        return {
            "id": str(t.id),
            "name": t.name,
            "slug": t.slug,
            "country_code": t.country_code,
            "export_regime": t.export_regime,
            "org_type": t.org_type,
            "flag_emoji": t.flag_emoji,
            "children": [
                _node(child)
                for child in all_orgs
                if child.parent_id == t.id
            ],
        }

    roots = [t for t in all_orgs if t.parent_id is None]
    return [_node(r) for r in roots]


@router.post("", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
async def create_organization(
    body: OrgCreate,
    db: AsyncSession = Depends(get_db),
) -> OrgOut:
    """新規拠点を作成する。slug は全体でユニーク。"""
    existing = await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"slug '{body.slug}' は既に使用されています",
        )
    if body.parent_id:
        parent = await db.get(Tenant, body.parent_id)
        if not parent:
            raise HTTPException(status_code=404, detail="親拠点が見つかりません")

    tenant = Tenant(**body.model_dump())
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return OrgOut.model_validate(tenant)


@router.get("/{org_id}", response_model=OrgOut)
async def get_organization(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> OrgOut:
    tenant = await db.get(Tenant, org_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="拠点が見つかりません")
    return OrgOut.model_validate(tenant)


@router.patch("/{org_id}", response_model=OrgOut)
async def update_organization(
    org_id: uuid.UUID,
    body: OrgUpdate,
    db: AsyncSession = Depends(get_db),
) -> OrgOut:
    tenant = await db.get(Tenant, org_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="拠点が見つかりません")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(tenant, field, value)

    await db.commit()
    await db.refresh(tenant)
    return OrgOut.model_validate(tenant)
