import os
from pathlib import Path
from typing import Generator

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool

BASE_DIR = Path(__file__).resolve().parents[2]  # .../ai_validation
DEFAULT_DB = BASE_DIR / "app.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB}")

_ALLOWED_ENVS = {"production", "e2e_test"}

# エンジンキャッシュ（URL -> engine）
_engine_cache: dict[str, object] = {}


def _make_engine(db_url: str):
    if db_url in _engine_cache:
        return _engine_cache[db_url]

    engine = create_engine(
        db_url,
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    _engine_cache[db_url] = engine
    return engine


def _db_url_for_env(env: str) -> str:
    if env not in _ALLOWED_ENVS:
        env = "production"
    if env == "production":
        return DATABASE_URL
    # e2e_test → app_e2e.db（本番と同じディレクトリ）
    base_path = Path(DATABASE_URL.replace("sqlite:///", "")).parent
    return f"sqlite:///{base_path}/app_e2e.db"


def resolve_env(request: Request) -> str:
    """リクエストから環境を解決する。ヘッダー優先、次にcookie。"""
    env = request.headers.get("X-AI-TM-Env", "")
    if not env:
        env = request.cookies.get("ai_tm_env", "production")
    return env if env in _ALLOWED_ENVS else "production"


def make_session_local_for_env(env: str) -> sessionmaker:
    """バックグラウンドスレッド用: envを指定してSessionLocalを取得。"""
    url = _db_url_for_env(env)
    engine = _make_engine(url)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


# デフォルト（後方互換用）
engine = _make_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db(request: Request) -> Generator[Session, None, None]:
    """FastAPI dependency: X-AI-TM-Env ヘッダーまたは ai_tm_env cookie でDB切り替え。"""
    env = resolve_env(request)
    url = _db_url_for_env(env)
    eng = _make_engine(url)
    local = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    db = local()
    try:
        yield db
    finally:
        db.close()
