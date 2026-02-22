"""ポータル UI ルーター。

エンドポイント:
- GET /ui                     ホーム画面 (Jinja2)
- GET /ui/health/{module_key}  モジュールヘルスチェックプロキシ
"""

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.module_registry import ModuleRegistry

router = APIRouter(prefix="/ui", tags=["ui"])

# テンプレートディレクトリは main.py で設定するが、
# ここでは遅延バインディングで参照できるよう関数に渡す。
# main.py が templates を DI 経由で渡す設計にすると複雑になるため、
# ここでは Path(__file__) 基準で直接初期化する。
import pathlib as _pathlib

_TEMPLATES_DIR = _pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# モジュールアイコン (key → 絵文字)
_MODULE_ICONS: dict[str, str] = {
    "platform-core": "⚙️",
    "ai_validation": "🔐",
    "ai_classification": "📦",
    "rnd_assessment": "🔬",
    "patent_search": "📋",
    "dap": "🔗",
    "screening": "🛡️",
}

# platform-core 自身の固定エントリ (DB に登録されていない)
_PLATFORM_ENTRY = {
    "key": "platform-core",
    "name": "プラットフォーム管理",
    "description": "テナント・ユーザー・モジュール管理",
    "base_url": "",          # 同一オリジン → /admin
    "iframe_url": "/admin",  # iframe に表示する初期 URL
    "icon": _MODULE_ICONS["platform-core"],
    "health_check_path": "/health",
}


def _build_module_entry(m: ModuleRegistry) -> dict:
    return {
        "key": m.key,
        "name": m.name,
        "description": m.description or "",
        "base_url": m.base_url,
        "iframe_url": m.base_url,
        "icon": _MODULE_ICONS.get(m.key, "📌"),
        "health_check_path": m.health_check_path,
    }


@router.get("", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    """ポータルホーム画面。"""
    result = await db.execute(
        select(ModuleRegistry)
        .where(ModuleRegistry.is_active == True)  # noqa: E712
        .order_by(ModuleRegistry.registered_at)
    )
    db_modules = [_build_module_entry(m) for m in result.scalars().all()]

    # platform-core 自身を先頭に固定、残りは DB から取得
    modules = [_PLATFORM_ENTRY] + db_modules

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "modules": modules,
            "version": "0.1.0",
        },
    )


@router.get("/health/{module_key}", include_in_schema=False)
async def module_health(module_key: str, db: AsyncSession = Depends(get_db)):
    """モジュールヘルスチェックのサーバー側プロキシ。

    JS は同一オリジン (/ui/health/<key>) を叩くことで CORS を回避できる。
    """
    # platform-core 自身は内部で直接チェック
    if module_key == "platform-core":
        return {"status": "online"}

    result = await db.execute(
        select(ModuleRegistry).where(ModuleRegistry.key == module_key)
    )
    module = result.scalar_one_or_none()
    if module is None:
        return {"status": "unknown"}

    health_url = module.base_url.rstrip("/") + module.health_check_path
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(health_url)
        if resp.status_code == 200:
            return {"status": "online"}
        return {"status": "offline", "code": resp.status_code}
    except Exception:
        return {"status": "offline"}
