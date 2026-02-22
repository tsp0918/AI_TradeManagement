"""ユーザー管理 API。テナント管理者が操作可能。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.auth.dependencies import CurrentUser, require_tenant_admin
from platform_core.auth.password import hash_password
from platform_core.db.session import get_db
from platform_core.models.user import AuthProvider, User, UserModulePermission, UserRole

router = APIRouter()


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str
    role: UserRole = UserRole.MEMBER


class UserRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str
    role: str
    auth_provider: str
    is_active: bool

    model_config = {"from_attributes": True}


class ModulePermissionSet(BaseModel):
    module_key: str
    can_read: bool = True
    can_write: bool = False
    can_admin: bool = False


@router.get("/", response_model=list[UserRead])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_tenant_admin),
) -> list[UserRead]:
    result = await db.execute(
        select(User)
        .where(User.tenant_id == current_user.tenant_id)
        .order_by(User.created_at)
    )
    return [UserRead.model_validate(u) for u in result.scalars().all()]


@router.post("/", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_tenant_admin),
) -> UserRead:
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{body.email}' already registered",
        )
    user = User(
        tenant_id=current_user.tenant_id,
        email=body.email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        role=body.role,
        auth_provider=AuthProvider.LOCAL,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserRead.model_validate(user)


@router.put("/{user_id}/permissions")
async def set_module_permissions(
    user_id: uuid.UUID,
    permissions: list[ModulePermissionSet],
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_tenant_admin),
) -> dict:
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == current_user.tenant_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # 既存のパーミッションを削除して再作成
    existing = await db.execute(
        select(UserModulePermission).where(UserModulePermission.user_id == user_id)
    )
    for perm in existing.scalars().all():
        await db.delete(perm)

    for p in permissions:
        db.add(UserModulePermission(
            user_id=user_id,
            module_key=p.module_key,
            can_read=p.can_read,
            can_write=p.can_write,
            can_admin=p.can_admin,
        ))

    await db.flush()
    return {"updated": len(permissions)}


@router.patch("/{user_id}/deactivate", response_model=UserRead)
async def deactivate_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_tenant_admin),
) -> UserRead:
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == current_user.tenant_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    await db.flush()
    await db.refresh(user)
    return UserRead.model_validate(user)
