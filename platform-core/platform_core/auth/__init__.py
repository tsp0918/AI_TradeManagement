from platform_core.auth.dependencies import (
    CurrentUser,
    get_current_user,
    require_module_permission,
    require_superadmin,
    require_tenant_admin,
)
from platform_core.auth.jwt import create_access_token, create_refresh_token, verify_access_token
from platform_core.auth.password import hash_password, verify_password
from platform_core.auth.router import router as auth_router

__all__ = [
    "CurrentUser",
    "get_current_user",
    "require_module_permission",
    "require_superadmin",
    "require_tenant_admin",
    "create_access_token",
    "create_refresh_token",
    "verify_access_token",
    "hash_password",
    "verify_password",
    "auth_router",
]
