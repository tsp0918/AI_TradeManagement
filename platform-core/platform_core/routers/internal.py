"""
内部 API ルーター (/internal/*)

モジュールからのみ呼ばれる。X-Internal-Service-Key で保護。
ユーザー JWT は不要。
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.module_registry import ModuleRegistry
from platform_core.module_sdk.internal_auth import verify_internal_key

router = APIRouter(prefix="/internal", tags=["internal"])


class ModuleRegisterPayload(BaseModel):
    key: str
    name: str
    description: str = ""
    base_url: str
    version: str = "0.1.0"
    capabilities: list[str] = []
    data_contracts: dict | None = None
    health_check_path: str = "/health"


class ModuleReadInternal(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    base_url: str
    version: str
    is_active: bool
    capabilities: list
    health_check_path: str

    model_config = {"from_attributes": True}


@router.post("/modules/register", response_model=ModuleReadInternal)
async def register_module(
    payload: ModuleRegisterPayload,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_key),
) -> ModuleReadInternal:
    """
    モジュール起動時の自動登録。存在すれば更新、なければ新規作成（upsert）。
    """
    result = await db.execute(
        select(ModuleRegistry).where(ModuleRegistry.key == payload.key)
    )
    module = result.scalar_one_or_none()

    if module is None:
        module = ModuleRegistry(**payload.model_dump())
        db.add(module)
    else:
        for field, value in payload.model_dump().items():
            setattr(module, field, value)
        module.is_active = True

    await db.flush()
    await db.refresh(module)
    return ModuleReadInternal.model_validate(module)


@router.get("/modules/{module_key}", response_model=ModuleReadInternal)
async def get_module(
    module_key: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_key),
) -> ModuleReadInternal:
    """モジュール間通信用: 指定モジュールの情報を返す。"""
    result = await db.execute(
        select(ModuleRegistry).where(
            ModuleRegistry.key == module_key,
            ModuleRegistry.is_active == True,  # noqa: E712
        )
    )
    module = result.scalar_one_or_none()
    if module is None:
        raise HTTPException(status_code=404, detail=f"Module '{module_key}' not found or inactive")
    return ModuleReadInternal.model_validate(module)


@router.get("/modules", response_model=list[ModuleReadInternal])
async def list_active_modules(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_internal_key),
) -> list[ModuleReadInternal]:
    """アクティブなモジュール一覧（DAP オーケストレーター向け）。"""
    result = await db.execute(
        select(ModuleRegistry).where(ModuleRegistry.is_active == True)  # noqa: E712
    )
    return [ModuleReadInternal.model_validate(m) for m in result.scalars().all()]
