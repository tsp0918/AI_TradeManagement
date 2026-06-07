"""
Transaction model
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, Index, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


if TYPE_CHECKING:
    from app.db.models.ai_run import AiRun, PatentRetrieval, MatrixMatch


class TransactionStatus(str, enum.Enum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    rejected = "rejected"


class UsageSource(str, enum.Enum):
    core = "core"
    expanded = "expanded"
    analyst_added = "analyst_added"


class CreatedBy(str, enum.Enum):
    user = "user"
    ai = "ai"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_no: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default=TransactionStatus.draft.value, nullable=False)

    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 多拠点管理: 担当拠点 org_id（platform-core plat_tenant UUID）
    org_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)

    # スクリーニング連携
    counterparty_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    screening_result_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    screening_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # 審査連鎖（証跡管理）
    # エージェント判定結果
    agent_judgment_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)   # controlled / not_controlled / requires_review
    agent_judged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    formal_submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # 正式審査提出日時

    # 審査連鎖（証跡管理）
    source_module: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)           # "rnd_assessment" | "ai_classification" | "manual"
    parent_transaction_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True)
    rnd_case_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)             # RND case_id（表示用・非正規化）

    # サプライチェーン連携（De Minimis）
    supply_chain_node_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)    # platform-core plat_supply_chain_node UUID
    de_minimis_result: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)  # 取引審査時点の De Minimis 計算スナップショット

    # リスク分岐型承認ティア（Phase redesign）
    approval_tier: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)              # 1=自動承認 2=標準 3=輸出許可確認
    required_steps: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)          # ["screening","ai_run","catchall"] etc.
    tier_reason: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)            # ティア判定理由
    tier_determined_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)   # ティア確定日時
    linked_product_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)     # 品目管理の product.code
    linked_product_eccn: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)     # 品目管理から取得したECCN
    is_new_product_entry: Mapped[Optional[bool]] = mapped_column(Integer, nullable=True)      # 1=品目同時登録モード

    # 輸出審査記録の法的要件（外為法 7年保存・CISTEC様式準拠）
    evaluator_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)         # 判定者氏名
    evaluator_title: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)        # 判定者役職
    judgment_no: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)             # 判定書番号
    retention_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)      # 保存期限（+7年）
    destination_country: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)      # 仕向国 ISO alpha-2
    end_user_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)          # 最終需要者名
    end_user_country: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)         # 最終需要者所在国 ISO alpha-2
    end_use_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)           # 最終用途

    # ERP 連携フィールド（受注・出荷情報）
    erp_case_no: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True) # ERP 側の受注番号（case_no と分離）
    total_value_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)            # 取引総額 (USD)
    unit_price_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)             # 単価 (USD)
    quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)                   # 数量
    hs_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)                 # HSコード
    incoterms: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)               # インコタームズ（CIF/FOB 等）

    items: Mapped[List["TransactionItem"]] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    usage_requirements: Mapped[List["UsageRequirement"]] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    ai_runs: Mapped[List["AiRun"]] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class TransactionItem(Base, TimestampMixin):
    __tablename__ = "transaction_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id", ondelete="CASCADE"), index=True)

    item_name: Mapped[Optional[str]] = mapped_column(String(255))
    item_model: Mapped[Optional[str]] = mapped_column(String(255))
    spec_text: Mapped[Optional[str]] = mapped_column(Text)

    attachments_meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    transaction: Mapped["Transaction"] = relationship(back_populates="items")

    # usage_requirements が item 単位で紐づく設計なら使える（DBに列がある前提）
    usage_requirements: Mapped[List["UsageRequirement"]] = relationship(
        back_populates="transaction_item",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UsageRequirement(Base):
    """
    usage_requirements テーブルに合わせる。
    DB 側で NOT NULL の updated_at / risk_tags がある前提。
    """
    __tablename__ = "usage_requirements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    transaction_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("transaction_items.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # ★ DB NOT NULL 対応
    risk_tags: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)

    normalized_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(nullable=True)

    created_by: Mapped[str] = mapped_column(String(32), default="user", nullable=False)

    # ★今回のエラー原因：DB NOT NULL なのにモデル未定義だった
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    transaction: Mapped["Transaction"] = relationship(back_populates="usage_requirements")

    # UIで item から usage を辿りたいなら（不要なら消してOK）
    transaction_item: Mapped[Optional["TransactionItem"]] = relationship(back_populates="usage_requirements")

    patent_retrievals: Mapped[List["PatentRetrieval"]] = relationship(
        back_populates="usage_requirement",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    matrix_matches: Mapped[List["MatrixMatch"]] = relationship(
        back_populates="usage_requirement",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_usage_requirements_tx_source", "transaction_id", "source"),
    )
