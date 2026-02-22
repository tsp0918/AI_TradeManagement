"""モジュールレジストリ管理 API。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.auth.dependencies import CurrentUser, require_tenant_admin
from platform_core.db.session import get_db
from platform_core.models.module_registry import ModuleRegistry

router = APIRouter()


class ModuleRegister(BaseModel):
    key: str
    name: str
    description: str | None = None
    base_url: str
    version: str = "0.1.0"
    capabilities: list[str] = []
    data_contracts: dict | None = None
    health_check_path: str = "/health"


class ModuleRead(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str | None
    base_url: str
    version: str
    is_active: bool
    capabilities: list
    health_check_path: str

    model_config = {"from_attributes": True}


@router.get("/", response_model=list[ModuleRead])
async def list_modules(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_tenant_admin),
) -> list[ModuleRead]:
    result = await db.execute(select(ModuleRegistry).order_by(ModuleRegistry.registered_at))
    return [ModuleRead.model_validate(m) for m in result.scalars().all()]


@router.post("/", response_model=ModuleRead, status_code=status.HTTP_201_CREATED)
async def register_module(
    body: ModuleRegister,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_tenant_admin),
) -> ModuleRead:
    existing = await db.execute(
        select(ModuleRegistry).where(ModuleRegistry.key == body.key)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Module key '{body.key}' already registered",
        )
    module = ModuleRegistry(**body.model_dump())
    db.add(module)
    await db.flush()
    await db.refresh(module)
    return ModuleRead.model_validate(module)


@router.put("/{module_key}", response_model=ModuleRead)
async def update_module(
    module_key: str,
    body: ModuleRegister,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_tenant_admin),
) -> ModuleRead:
    result = await db.execute(
        select(ModuleRegistry).where(ModuleRegistry.key == module_key)
    )
    module = result.scalar_one_or_none()
    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")

    for field, value in body.model_dump().items():
        setattr(module, field, value)

    await db.flush()
    await db.refresh(module)
    return ModuleRead.model_validate(module)
