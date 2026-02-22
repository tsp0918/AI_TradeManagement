"""Alembic 環境設定。非同期 PostgreSQL + platform-core モデルを使用。"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# platform-core の設定とモデルをインポート
from platform_core.config import settings
from platform_core.db.base import PlatformBase

# 全モデルを登録するために必ずインポートする
import platform_core.models  # noqa: F401

config = context.config

# alembic.ini のロギング設定を適用
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# マイグレーション対象のメタデータ
target_metadata = PlatformBase.metadata

# DB URL を settings から取得（alembic.ini の値を上書き）
config.set_main_option("sqlalchemy.url", settings.platform_database_url)


def run_migrations_offline() -> None:
    """オフラインモード: DBへの接続なしにSQLを生成。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """非同期エンジンでマイグレーションを実行。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
