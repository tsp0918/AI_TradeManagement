from collections.abc import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from platform_core.config import settings

engine = create_async_engine(
    settings.platform_database_url,
    echo=settings.platform_env == "development",
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# 同期エンジン（threading.Thread 内から PostgreSQL を更新するために使用）
# asyncpg URL → psycopg2 URL に変換
_sync_url = settings.platform_database_url.replace(
    "postgresql+asyncpg://", "postgresql://"
).replace("postgresql+psycopg2://", "postgresql://")
_sync_engine = create_engine(_sync_url, pool_pre_ping=True, pool_size=3, max_overflow=2)
SyncSessionLocal = sessionmaker(bind=_sync_engine, autoflush=False, autocommit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依存注入用のDBセッション。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
