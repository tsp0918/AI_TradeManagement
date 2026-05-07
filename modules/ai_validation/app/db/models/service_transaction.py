"""ServiceTransaction — 役務取引管理（外為法 第25条）

対象役務:
  技術指導・コンサルティング / ソフトウェアライセンス供与 /
  設計・製造支援 / クラウドサービス / 教育・訓練 / 情報提供

保存義務: 外為法 第67条（7年）
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ServiceType(str, enum.Enum):
    tech_guidance    = "tech_guidance"     # 技術指導・コンサルティング
    software_license = "software_license"  # ソフトウェアライセンス供与
    manufacturing    = "manufacturing"     # 設計・製造支援
    cloud_service    = "cloud_service"     # クラウドサービス提供
    training         = "training"          # 教育・訓練
    information      = "information"       # 情報提供（技術移転を伴うもの）
    other            = "other"


SERVICE_TYPE_LABELS: dict[str, str] = {
    "tech_guidance":    "技術指導・コンサルティング",
    "software_license": "ソフトウェアライセンス供与",
    "manufacturing":    "設計・製造支援",
    "cloud_service":    "クラウドサービス提供",
    "training":         "教育・訓練",
    "information":      "情報提供",
    "other":            "その他",
}


class ServiceControlStatus(str, enum.Enum):
    draft           = "draft"
    under_review    = "under_review"
    approved        = "approved"        # 許可不要（自社審査）
    license_required = "license_required"  # 第25条許可申請必要
    license_granted  = "license_granted"   # 許可取得済
    rejected        = "rejected"
    withdrawn       = "withdrawn"


STATUS_LABELS: dict[str, str] = {
    "draft":            "起案中",
    "under_review":     "審査中",
    "approved":         "許可不要（承認）",
    "license_required": "許可申請必要",
    "license_granted":  "許可取得済",
    "rejected":         "否認",
    "withdrawn":        "取り下げ",
}


class ServiceTransaction(Base):
    __tablename__ = "service_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_no: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255))

    # 役務の種別
    service_type: Mapped[Optional[str]] = mapped_column(String(32))

    # 提供者情報（自社）
    provider_name: Mapped[Optional[str]] = mapped_column(String(255))
    provider_dept: Mapped[Optional[str]] = mapped_column(String(255))

    # 受領者情報（相手方）
    recipient_name: Mapped[Optional[str]] = mapped_column(String(255))
    recipient_organization: Mapped[Optional[str]] = mapped_column(String(255))
    recipient_country: Mapped[Optional[str]] = mapped_column(String(8))   # ISO alpha-2 仕向国
    recipient_nationality: Mapped[Optional[str]] = mapped_column(String(8))  # 国籍（みなし輸出）

    # 役務内容
    technology_description: Mapped[Optional[str]] = mapped_column(Text)
    eccn: Mapped[Optional[str]] = mapped_column(String(32))               # 技術 ECCN
    fefta_category: Mapped[Optional[str]] = mapped_column(String(64))     # 外為法 別表番号・条項

    # 契約情報
    contract_start: Mapped[Optional[datetime]] = mapped_column(DateTime)
    contract_end: Mapped[Optional[datetime]] = mapped_column(DateTime)
    contract_value_jpy: Mapped[Optional[float]] = mapped_column(Float)

    # 審査状態
    status: Mapped[str] = mapped_column(
        String(32), default=ServiceControlStatus.draft.value, nullable=False
    )
    screening_status: Mapped[Optional[str]] = mapped_column(String(30))   # OFAC/BIS スクリーニング
    screening_result_id: Mapped[Optional[str]] = mapped_column(String(36))
    license_required: Mapped[Optional[int]] = mapped_column(Integer)      # 0=不要 1=必要 NULL=未判定
    license_no: Mapped[Optional[str]] = mapped_column(String(64))         # 外為法 第25条許可番号
    evaluator_note: Mapped[Optional[str]] = mapped_column(Text)           # 判定根拠メモ

    # みなし輸出フラグ（外為法 第25条第1項ただし書き）
    is_deemed_export: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 輸出審査記録（外為法 第67条・CISTEC様式）
    evaluator_name: Mapped[Optional[str]] = mapped_column(String(128))
    evaluator_title: Mapped[Optional[str]] = mapped_column(String(128))
    judgment_no: Mapped[Optional[str]] = mapped_column(String(64))
    retention_until: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # タイムスタンプ
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
