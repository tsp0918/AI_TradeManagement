"""ai_validation Alembic env - monorepo対応版。"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# monorepo ルートの .env を読み込む
# このファイルの位置: modules/ai_validation/alembic/env.py
_ROOT = Path(__file__).resolve().parents[3]  # AI_TradeManagement/
load_dotenv(_ROOT / ".env", override=False)

# app/ を import パスに追加
_MODULE_DIR = Path(__file__).resolve().parents[1]
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

config = context.config

if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        pass

from app.db.base import Base  # noqa: E402
import app.db.models  # noqa: F401, E402 — 全モデルを Base に登録

target_metadata = Base.metadata


def get_url() -> str:
    return os.environ.get(
        "VALIDATION_DATABASE_URL",
        os.environ.get("DATABASE_URL", "sqlite:///./app.db"),
    )


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version_validation",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section) or {}
    cfg["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table="alembic_version_validation",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
