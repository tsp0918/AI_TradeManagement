"""
モジュールレジストリ

プラットフォームに登録された各アプリモジュールの情報を管理する。
DAP オーケストレーターはこのレジストリを参照してモジュール間通信を行う。
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db.base import PlatformBase


class ModuleRegistry(PlatformBase):
    """登録済みモジュール一覧。"""

    __tablename__ = "plat_module_registry"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    # 例: "ai_validation", "ai_classification", "rnd_assessment"
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    version: Mapped[str] = mapped_column(String(50), default="0.1.0", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # このモジュールが持つ能力の宣言
    # 例: ["export_control", "matrix_run", "transaction_create"]
    capabilities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # DAP オーケストレーター用: このモジュールが入出力するデータ型
    # 例: {"input": ["Transaction"], "output": ["AiRun", "MatrixMatch"]}
    data_contracts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ヘルスチェックエンドポイント (省略時は base_url + "/health")
    health_check_path: Mapped[str] = mapped_column(String(255), default="/health", nullable=False)

    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
