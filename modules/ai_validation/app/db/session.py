import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool, StaticPool

# =========================
# Database configuration
# =========================

BASE_DIR = Path(__file__).resolve().parents[2]  # .../AI_validation
DEFAULT_DB = BASE_DIR / "app.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB}")

if DATABASE_URL.startswith("sqlite"):
    # SQLite: NullPool でコネクション共有を完全に排除し "database is locked" を解消
    # NullPool = リクエストごとに新規コネクション作成・クローズ（SQLite では十分高速）
    engine = create_engine(
        DATABASE_URL,
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
else:
    engine = create_engine(DATABASE_URL, echo=False, future=True)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)

# =========================
# FastAPI dependency
# =========================

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency.
    Yields a SQLAlchemy Session and ensures it is closed.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
