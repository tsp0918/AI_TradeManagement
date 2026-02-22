from fastapi import APIRouter

from platform_core.routers.tenants import router as tenants_router
from platform_core.routers.users import router as users_router
from platform_core.routers.modules import router as modules_router

admin_router = APIRouter(tags=["admin"])
admin_router.include_router(tenants_router, prefix="/tenants")
admin_router.include_router(users_router, prefix="/users")
admin_router.include_router(modules_router, prefix="/modules")
