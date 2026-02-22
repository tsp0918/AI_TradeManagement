"""ポータル UI ルーター。

エンドポイント:
- GET  /ui                      ホーム画面 (Jinja2)
- GET  /ui/health/{module_key}  モジュールヘルスチェックプロキシ
- POST /ui/launch/{module_key}  モジュール起動 (uvicorn サブプロセス)
- POST /ui/stop/{module_key}    モジュール停止
"""

import asyncio
import os
import pathlib as _pathlib
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.module_registry import ModuleRegistry

router = APIRouter(prefix="/ui", tags=["ui"])

_TEMPLATES_DIR = _pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# ── アイコン定義 ───────────────────────────────────────────────
_MODULE_ICONS: dict[str, str] = {
    "platform-core":   "⚙️",
    "ai_validation":   "🔐",
    "ai_classification": "📦",
    "rnd_assessment":  "🔬",
    "patent_search":   "📋",
    "dap":             "🔗",
    "screening":       "🛡️",
}

# platform-core 自身の固定エントリ
_PLATFORM_ENTRY = {
    "key":              "platform-core",
    "name":             "プラットフォーム管理",
    "description":      "テナント・ユーザー・モジュール管理",
    "base_url":         "",
    "iframe_url":       "/admin",
    "icon":             _MODULE_ICONS["platform-core"],
    "health_check_path": "/health",
}


def _build_module_entry(m: ModuleRegistry) -> dict:
    return {
        "key":              m.key,
        "name":             m.name,
        "description":      m.description or "",
        "base_url":         m.base_url,
        "iframe_url":       m.base_url,
        "icon":             _MODULE_ICONS.get(m.key, "📌"),
        "health_check_path": m.health_check_path,
    }


# ── サブプロセス管理 ────────────────────────────────────────────
_PROCESSES: dict[str, subprocess.Popen] = {}


def _project_root() -> Path:
    """ui.py から 4 段上がるとプロジェクトルート。"""
    return Path(__file__).parent.parent.parent.parent


def _module_port(base_url: str) -> int | None:
    return urlparse(base_url).port


# ── ルーター ────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    """ポータルホーム画面。"""
    result = await db.execute(
        select(ModuleRegistry)
        .where(ModuleRegistry.is_active == True)  # noqa: E712
        .order_by(ModuleRegistry.registered_at)
    )
    db_modules = [_build_module_entry(m) for m in result.scalars().all()]
    modules = [_PLATFORM_ENTRY] + db_modules

    return templates.TemplateResponse(
        "home.html",
        {"request": request, "modules": modules, "version": "0.1.0"},
    )


@router.get("/health/{module_key}", include_in_schema=False)
async def module_health(module_key: str, db: AsyncSession = Depends(get_db)):
    """モジュールヘルスチェックのサーバー側プロキシ (CORS 回避)。"""
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


@router.post("/launch/{module_key}", include_in_schema=False)
async def launch_module(module_key: str, db: AsyncSession = Depends(get_db)):
    """指定モジュールを uvicorn サブプロセスとして起動する。"""
    # 既存プロセスが生きていれば即返す
    proc = _PROCESSES.get(module_key)
    if proc and proc.poll() is None:
        return {"status": "already_running", "pid": proc.pid}

    result = await db.execute(
        select(ModuleRegistry).where(ModuleRegistry.key == module_key)
    )
    module = result.scalar_one_or_none()
    if module is None:
        raise HTTPException(status_code=404, detail=f"Module '{module_key}' not found")

    port = _module_port(module.base_url)
    if not port:
        raise HTTPException(status_code=400, detail="Cannot determine port from base_url")

    root       = _project_root()
    module_dir = root / "modules" / module_key
    uvicorn    = root / ".venv" / "bin" / "uvicorn"

    if not module_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Module directory not found: {module_dir}")

    env = os.environ.copy()
    # platform_core は venv 未インストールのため module_dir と platform-core を両方含める
    platform_core_dir = root / "platform-core"
    env["PYTHONPATH"] = f"{module_dir}{os.pathsep}{platform_core_dir}"

    proc = subprocess.Popen(
        [str(uvicorn), "app.main:app", "--host", "0.0.0.0", "--port", str(port)],
        cwd=str(module_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _PROCESSES[module_key] = proc

    # ヘルスチェックで起動完了を待つ (最大 10 秒)
    health_url = module.base_url.rstrip("/") + module.health_check_path
    for _ in range(20):
        await asyncio.sleep(0.5)
        if proc.poll() is not None:
            _PROCESSES.pop(module_key, None)
            return {"status": "failed", "returncode": proc.returncode}
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                r = await client.get(health_url)
            if r.status_code == 200:
                return {"status": "online", "pid": proc.pid}
        except Exception:
            pass

    return {"status": "launching", "pid": proc.pid}


@router.post("/stop/{module_key}", include_in_schema=False)
async def stop_module(module_key: str):
    """指定モジュールのサブプロセスを停止する。"""
    proc = _PROCESSES.pop(module_key, None)
    if not proc or proc.poll() is not None:
        return {"status": "not_running"}
    proc.terminate()
    return {"status": "stopped"}
