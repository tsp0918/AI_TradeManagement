from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings

_ALLOWED_ENVS = {"production", "e2e_test"}
_engine_cache: dict[str, object] = {}


def _make_engine(url: str):
    if url in _engine_cache:
        return _engine_cache[url]
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False}, poolclass=NullPool, future=True)
        @event.listens_for(eng, "connect")
        def _set_pragma(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    else:
        eng = create_engine(url, future=True)
    _engine_cache[url] = eng
    return eng


def _url_for_env(env: str) -> str:
    base = settings.database_url
    if env == "e2e_test" and ".db" in base:
        return base.replace(".db", "_e2e.db")
    return base


# デフォルトエンジン（後方互換用）
engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def get_db(request: Request):
    env = (request.query_params.get("env", "")
           or request.headers.get("X-AI-TM-Env", "")
           or request.cookies.get("ai_tm_env", "production"))
    if env not in _ALLOWED_ENVS:
        env = "production"
    eng = _make_engine(_url_for_env(env))
    local = sessionmaker(bind=eng, autocommit=False, autoflush=False, future=True)
    db = local()
    try:
        yield db
    finally:
        db.close()
