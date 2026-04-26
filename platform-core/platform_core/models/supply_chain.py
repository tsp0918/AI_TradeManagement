"""
サプライチェーン管理モデル（BOM構造 + De Minimis 計算基盤）

plat_supply_chain_node  — 製品・部品・素材ノード
plat_supply_chain_edge  — 親子（BOM）リレーションシップ
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.db.base import PlatformBase


class SupplyChainNode(PlatformBase):
    """サプライチェーンのノード（製品/サブアッセンブリ/部品/素材）。"""

    __tablename__ = "plat_supply_chain_node"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plat_tenant.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    part_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    node_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="component"
    )
    # "product" | "subassembly" | "component" | "material"

    # 原産地・規制情報
    country_of_origin: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # ISO 3166-1 alpha-2 or alpha-3
    is_us_origin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hs_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    eccn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # EAR99 / 3A001 / None（未判定）等

    # 価値情報（De Minimis 計算用）
    unit_value_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 1単位あたりの価格（USD）
    us_controlled_value_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # unit_value_usd のうちEAR規制対象のUS原産価値。通常は is_us_origin=True かつ eccn≠EAR99 の場合に unit_value_usd と同値

    # plat_item との正式紐付け (Integration C)
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plat_item.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant")
    # このノードが親となる BOM エッジ（自分が組み立て品）
    bom_children: Mapped[list["SupplyChainEdge"]] = relationship(
        "SupplyChainEdge",
        foreign_keys="SupplyChainEdge.parent_node_id",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    # このノードが子となる BOM エッジ（自分が部品）
    bom_parents: Mapped[list["SupplyChainEdge"]] = relationship(
        "SupplyChainEdge",
        foreign_keys="SupplyChainEdge.child_node_id",
        back_populates="child",
    )
    # サプライヤー原産性証明
    attestations: Mapped[list["SupplierAttestation"]] = relationship(
        "SupplierAttestation", back_populates="node", cascade="all, delete-orphan"
    )


class SupplyChainEdge(PlatformBase):
    """BOM（Bill of Materials）のエッジ。親ノードが子ノードを何個使うかを表す。"""

    __tablename__ = "plat_supply_chain_edge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plat_supply_chain_node.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    child_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plat_supply_chain_node.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="each")
    # "each" | "kg" | "m" | "L" 等

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    parent: Mapped["SupplyChainNode"] = relationship(
        "SupplyChainNode", foreign_keys=[parent_node_id], back_populates="bom_children"
    )
    child: Mapped["SupplyChainNode"] = relationship(
        "SupplyChainNode", foreign_keys=[child_node_id], back_populates="bom_parents"
    )
