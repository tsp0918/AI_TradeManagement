"""PostgreSQL 非同期セッション — platform_db 共有接続。"""

import os
from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_BASE_URL = os.environ.get(
    "EXPORT_LICENSE_PG_URL",
    "postgresql+asyncpg://platform_user:platform_pass@localhost:5432/platform_db",
)
if "+psycopg2" in _BASE_URL:
    _BASE_URL = _BASE_URL.replace("+psycopg2", "+asyncpg")

_ALLOWED_ENVS = {"production", "e2e_test"}
_engine_cache: dict[str, object] = {}


def _get_engine(url: str):
    if url not in _engine_cache:
        _engine_cache[url] = create_async_engine(url, pool_pre_ping=True)
    return _engine_cache[url]


# デフォルトエンジン（後方互換用）
_engine = _get_engine(_BASE_URL)
_AsyncSessionLocal = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_pg_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """X-AI-TM-Env ヘッダーまたは ai_tm_env cookie でDB切り替え。"""
    env = (request.query_params.get("env", "")
           or request.headers.get("X-AI-TM-Env", "")
           or request.cookies.get("ai_tm_env", "production"))
    if env not in _ALLOWED_ENVS:
        env = "production"
    url = _BASE_URL if env == "production" else _BASE_URL.replace("/platform_db", "/platform_db_e2e")
    eng = _get_engine(url)
    local = async_sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    async with local() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
