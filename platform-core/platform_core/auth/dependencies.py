"""FastAPI 依存注入: 認証済みユーザーの取得。"""

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.auth.jwt import verify_access_token
from platform_core.db.session import get_db
from platform_core.models.user import User, UserRole

bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser:
    """依存注入で渡されるユーザー情報の軽量DTO。"""

    def __init__(
        self,
        user: User,
    ) -> None:
        self.id: uuid.UUID = user.id
        self.tenant_id: uuid.UUID = user.tenant_id
        self.email: str = user.email
        self.full_name: str = user.full_name
        self.role: str = user.role
        self.is_active: bool = user.is_active
        self._user = user

    @property
    def is_tenant_admin(self) -> bool:
        return self.role in (UserRole.TENANT_ADMIN, UserRole.SUPERADMIN)

    @property
    def is_superadmin(self) -> bool:
        return self.role == UserRole.SUPERADMIN


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """アクセストークンを検証してユーザーを返す。"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise credentials_exception

    try:
        payload = verify_access_token(credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise credentials_exception

    return CurrentUser(user)


async def require_tenant_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """テナント管理者以上を要求する依存。"""
    if not current_user.is_tenant_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin privilege required",
        )
    return current_user


async def require_superadmin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """スーパー管理者を要求する依存。"""
    if not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin privilege required",
        )
    return current_user


def require_module_permission(module_key: str, need_write: bool = False):
    """指定モジュールへのアクセス権を確認する依存ファクトリ。

    使い方:
        @router.post("/run")
        async def run(user = Depends(require_module_permission("ai_validation", need_write=True))):
            ...
    """

    async def _check(
        current_user: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> CurrentUser:
        # superadmin / tenant_admin は常に許可
        if current_user.is_tenant_admin:
            return current_user

        from platform_core.models.user import UserModulePermission
        from sqlalchemy import select, and_

        result = await db.execute(
            select(UserModulePermission).where(
                and_(
                    UserModulePermission.user_id == current_user.id,
                    UserModulePermission.module_key == module_key,
                )
            )
        )
        perm = result.scalar_one_or_none()

        if perm is None or not perm.can_read:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No access to module: {module_key}",
            )
        if need_write and not perm.can_write:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Write permission required for module: {module_key}",
            )
        return current_user

    return _check
