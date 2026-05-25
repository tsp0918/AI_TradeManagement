"""PostgreSQL 非同期セッション — platform_db 共有接続。"""

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_DB_URL = os.environ.get(
    "EXPORT_LICENSE_PG_URL",
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


async def get_pg_db() -> AsyncGenerator[AsyncSession, None]:
    async with _AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
