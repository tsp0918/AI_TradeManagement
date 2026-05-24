"""
輸入品プロファイルモデル

plat_import_profile — 購入品・原材料・グループ内移管品の輸入情報管理

product_code（String）で ai_classification.products（SQLite）と参照する。
クロス DB のため外部キー制約なし。FTA 照合は plat_fta_agreement.code を参照。
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db.base import PlatformBase


class ImportProfile(PlatformBase):
    """輸入品プロファイル。

    品目マスター（ai_classification.products）の購入品・原材料・グループ内移管品に
    輸入情報（仕入先・HS・FTA・EAR再輸出・輸入規制チェック）を付加する。
    """

    __tablename__ = "plat_import_profile"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── 品目参照（クロス DB: ai_classification SQLite との紐付け）────────
    product_code: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── 輸入種別 ────────────────────────────────────────────────────────
    # "purchase"=外部購入品, "intra_group"=グループ内移管, "consignment"=委託品
    import_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="purchase", index=True
    )

    # ── 輸出者・仕入先情報 ───────────────────────────────────────────────
    exporter_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exporter_country: Mapped[str | None] = mapped_column(
        String(4), nullable=True, index=True
    )  # ISO2: US / CN / DE / KR 等

    # ── 輸入先国 ────────────────────────────────────────────────────────
    import_country: Mapped[str] = mapped_column(
        String(4), nullable=False, default="JP"
    )  # JP / US / EU 等

    # ── HS・税関情報 ─────────────────────────────────────────────────────
    hs_code_import: Mapped[str | None] = mapped_column(
        String(12), nullable=True
    )  # 輸入申告用HS（輸出用HSと異なる場合あり）
    customs_value_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    import_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    import_unit: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # each / kg / m / L 等

    # ── 輸入許可 ─────────────────────────────────────────────────────────
    import_license_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    import_license_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    import_license_expiry: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── FTA / EPA 特恵税率 ────────────────────────────────────────────────
    fta_applicable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    fta_agreement_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True, index=True
    )  # plat_fta_agreement.code へのソフト参照
    preferential_rate_pct: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )  # FTA 優遇税率 (%)
    # 原産地証明ステータス: not_required / pending / obtained / expired
    co_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_required"
    )

    # ── EAR 再輸出管理 ────────────────────────────────────────────────────
    eccn_claimed: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # 輸出者申告 ECCN（手動または ai_validation 取得）
    # ai_validation 連携（Phase III-1）
    eccn_validation_tx_id: Mapped[int | None] = mapped_column(
        nullable=True
    )  # ai_validation.transactions.id
    eccn_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )  # ECCN 判定依頼日時
    eccn_judgment_status: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # ai_validation の agent_judgment_status（controlled/not_controlled/requires_review）
    us_reexport_applicable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )  # EAR §736 再輸出管理対象か
    ear_license_exception: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # ATN / GOV / CIV 等

    # ── 輸入規制チェック結果 ──────────────────────────────────────────────
    import_restrictions_checked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    import_restrictions_result: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )
    # 例: {
    #   "kazinho":    {"status": "required", "details": "優先評価化学物質"},
    #   "reach_svhc": {"status": "clear"},
    #   "cites":      {"status": "clear"},
    #   "import_ban": {"status": "not_applicable"}
    # }

    # ── 運用情報 ──────────────────────────────────────────────────────────
    last_imported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # one_time / monthly / quarterly / annual / irregular
    import_frequency: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # サプライヤー原産性証明との紐付け（plat_supplier_attestation.id）
    supplier_attestation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # ── 組織フィルタ ─────────────────────────────────────────────────────
    org_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    # ── メタ ──────────────────────────────────────────────────────────────
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
