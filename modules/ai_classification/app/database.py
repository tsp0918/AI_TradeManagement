# app/database.py
from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import NullPool

from .settings import settings

_ALLOWED_ENVS = {"production", "e2e_test"}
_engine_cache: dict[str, object] = {}


def _make_engine(url: str):
    if url in _engine_cache:
        return _engine_cache[url]
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False}, poolclass=NullPool)
        @event.listens_for(eng, "connect")
        def _set_pragma(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    else:
        eng = create_engine(url)
    _engine_cache[url] = eng
    return eng


def _url_for_env(env: str) -> str:
    import re
    base = settings.DATABASE_URL
    if env != "e2e_test":
        return base
    if ".db" in base:  # SQLite
        return base.replace(".db", "_e2e.db")
    # PostgreSQL: append _e2e to database name (platform_db → platform_db_e2e)
    return re.sub(r'/([^/?#]+)(\?.*)?$', lambda m: f'/{m.group(1)}_e2e{m.group(2) or ""}', base)


# デフォルトエンジン（後方互換用）
engine = _make_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db(request: Request):
    env = (request.query_params.get("env", "")
           or request.headers.get("X-AI-TM-Env", "")
           or request.cookies.get("ai_tm_env", "production"))
    if env not in _ALLOWED_ENVS:
        env = "production"
    eng = _make_engine(_url_for_env(env))
    local = sessionmaker(autocommit=False, autoflush=False, bind=eng)
    db = local()
    try:
        yield db
    finally:
        db.close()
