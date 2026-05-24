"""PostgreSQL 同期セッション — 同期エンドポイント（BOM upload等）向け。

非同期エンドポイントは pg_session.py（asyncpg）を使用すること。
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_DB_URL = os.environ.get(
    "AI_CLASSIFICATION_PG_URL",
    "postgresql+psycopg2://platform_user:platform_pass@localhost:5432/platform_db",
)
# asyncpg URL が設定されている場合は psycopg2 に変換
if "+asyncpg" in _DB_URL:
    _DB_URL = _DB_URL.replace("+asyncpg", "+psycopg2")
elif _DB_URL.startswith("postgresql://") and "+psycopg2" not in _DB_URL and "+asyncpg" not in _DB_URL:
    _DB_URL = _DB_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

_engine_sync = create_engine(_DB_URL, pool_pre_ping=True)
_SyncSessionLocal = sessionmaker(bind=_engine_sync, autoflush=False, autocommit=False)


def get_pg_db_sync() -> Session:
    return _SyncSessionLocal()
