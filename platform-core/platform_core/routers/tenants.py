"""テナント管理 API。スーパー管理者のみ操作可能。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.auth.dependencies import CurrentUser, require_superadmin
from platform_core.db.session import get_db
from platform_core.models.tenant import Tenant

router = APIRouter()


class TenantCreate(BaseModel):
    name: str
    slug: str
    plan: str = "standard"


class TenantRead(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    plan: str
    is_active: bool

    model_config = {"from_attributes": True}


@router.get("/", response_model=list[TenantRead])
async def list_tenants(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_superadmin),
) -> list[TenantRead]:
    result = await db.execute(select(Tenant).order_by(Tenant.created_at))
    return [TenantRead.model_validate(t) for t in result.scalars().all()]


@router.post("/", response_model=TenantRead, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreate,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_superadmin),
) -> TenantRead:
    existing = await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tenant slug '{body.slug}' already exists",
        )
    tenant = Tenant(name=body.name, slug=body.slug, plan=body.plan)
    db.add(tenant)
    await db.flush()
    await db.refresh(tenant)
    return TenantRead.model_validate(tenant)


@router.patch("/{tenant_id}/deactivate", response_model=TenantRead)
async def deactivate_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_superadmin),
) -> TenantRead:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant.is_active = False
    await db.flush()
    await db.refresh(tenant)
    return TenantRead.model_validate(tenant)
