"""PostgreSQL 非同期セッション — platform_db 共有接続。

platform-core と同じ platform_db に接続し、plat_* テーブルを直接操作する。
screening モジュールと同じパターン（SCREENING_DATABASE_URL → platform_db）。
"""

import os
from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_DB_URL = os.environ.get(
    "AI_CLASSIFICATION_PG_URL",
    "postgresql+asyncpg://platform_user:platform_pass@localhost:5432/platform_db",
)
if "+psycopg2" in _DB_URL:
    _DB_URL = _DB_URL.replace("+psycopg2", "+asyncpg")

_engine = create_async_engine(_DB_URL, pool_pre_ping=True)

_AsyncSessionLocal = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


_ALLOWED_ENVS = {"production", "e2e_test"}
_engine_cache: dict[str, object] = {}


def _get_engine(url: str):
    if url not in _engine_cache:
        _engine_cache[url] = create_async_engine(url, pool_pre_ping=True)
    return _engine_cache[url]


async def get_pg_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依存注入用の PostgreSQL 非同期セッション（env対応）。"""
    env = (request.query_params.get("env", "")
           or request.headers.get("X-AI-TM-Env", "")
           or request.cookies.get("ai_tm_env", "production"))
    if env not in _ALLOWED_ENVS:
        env = "production"
    url = _DB_URL if env == "production" else _DB_URL.replace("/platform_db", "/platform_db_e2e")
    eng = _get_engine(url)
    local = async_sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    async with local() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
