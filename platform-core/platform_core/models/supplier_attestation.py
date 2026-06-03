"""
サプライヤー原産性証明モデル（SupplierAttestation）

サプライチェーンの各ノードに対してサプライヤーが申告した
ECCN・原産地・US コンテンツ比率を記録し、AI 検証と審査証跡を管理する。

コンプライアンス上の位置づけ:
  Certificate of Origin（CoO）はサプライヤーの自己申告にとどまるが、
  本モデルは申告内容を FAISS Layer A で AI 検証し、
  審査承認証跡（reviewed_by / reviewed_at）を付与することで
  De Minimis 計算の証明力を強化する。
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.db.base import PlatformBase


class SupplierAttestation(PlatformBase):
    """サプライヤー原産性証明（申告・AI検証・審査証跡）。"""

    __tablename__ = "plat_supplier_attestation"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plat_supply_chain_node.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── サプライヤー申告内容 ──────────────────────────────────────
    supplier_name: Mapped[str] = mapped_column(String(512), nullable=False)
    supplier_contact: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 申告 ECCN（"EAR99" / "3A001" / "EAR未判定" 等）
    claimed_eccn: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # 申告 HS コード（サプライヤー付番）
    claimed_hs_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 申告原産国（ISO 3166-1 alpha-2/3）
    claimed_country_of_origin: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # サプライヤー自身が算出した US コンテンツ比率（任意）
    claimed_us_content_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_us_origin_claimed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 証明書参照番号・番号
    certificate_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attestation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    supporting_docs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 例: [{"filename": "CoO_2026.pdf", "uploaded_at": "2026-04-26"}]
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── 監査ログ（不変タイムスタンプ履歴）────────────────────────
    # 各エントリ: {"action": str, "actor": str, "timestamp": ISO8601, "note": str}
    # action: "submitted" | "ai_judged" | "accepted" | "returned" | "ai_validate_requested"
    history: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)

    # ── AI 検証結果 ──────────────────────────────────────────────
    # "pending" | "ai_reviewed" | "accepted" | "rejected" | "expired"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # AI が示唆した ECCN（Layer A 検索上位ヒット）
    ai_suggested_eccn: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # AI の一致スコア（0.0〜1.0）
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # "match" | "warning" | "mismatch" | "unverifiable"
    ai_verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ai_review_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ai_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── 人手審査 ─────────────────────────────────────────────────
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    node: Mapped["SupplyChainNode"] = relationship(
        "SupplyChainNode", back_populates="attestations"
    )
