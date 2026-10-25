import os
from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from platform_core.config import settings

_ALLOWED_ENVS = {"production", "e2e_test"}

# エンジンキャッシュ
_async_engine_cache: dict[str, object] = {}
_sync_engine_cache: dict[str, object] = {}


def _pg_url_for_env(env: str, base_url: str) -> str:
    if env not in _ALLOWED_ENVS:
        env = "production"
    if env == "production":
        return base_url
    return base_url.replace("/platform_db", "/platform_db_e2e")


def _get_async_engine(url: str):
    if url not in _async_engine_cache:
        _async_engine_cache[url] = create_async_engine(
            url,
            echo=settings.platform_env == "development",
            pool_pre_ping=True,
        )
    return _async_engine_cache[url]


def _get_sync_engine(url: str):
    if url not in _sync_engine_cache:
        sync_url = url.replace("postgresql+asyncpg://", "postgresql://").replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        _sync_engine_cache[url] = create_engine(
            sync_url, pool_pre_ping=True, pool_size=3, max_overflow=2
        )
    return _sync_engine_cache[url]


# デフォルトエンジン（後方互換用）
engine = _get_async_engine(settings.platform_database_url)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)
_sync_engine = _get_sync_engine(settings.platform_database_url)
SyncSessionLocal = sessionmaker(bind=_sync_engine, autoflush=False, autocommit=False)


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依存注入用のDBセッション。X-AI-TM-Env ヘッダーまたは ai_tm_env cookie でDB切り替え。"""
    env = (request.query_params.get("env", "")
           or request.headers.get("X-AI-TM-Env", "")
           or request.cookies.get("ai_tm_env", "production"))
    if env not in _ALLOWED_ENVS:
        env = "production"
    url = _pg_url_for_env(env, settings.platform_database_url)
    eng = _get_async_engine(url)
    local = async_sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    async with local() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
