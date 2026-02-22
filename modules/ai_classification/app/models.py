# app/models.py
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Float, DateTime, ForeignKey, func
)
from sqlalchemy.orm import relationship

from .database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)

    code = Column(String(100), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    hs_code = Column(String(32), nullable=True)
    eccn = Column(String(32), nullable=True)

    # GHS関連
    ghs_signal_word = Column(String(50), nullable=True)
    ghs_pictograms = Column(String(255), nullable=True)
    ghs_h_statements = Column(Text, nullable=True)
    ghs_p_statements = Column(Text, nullable=True)
    ghs_classes = Column(Text, nullable=True)

    # 国内規制フラグ
    is_poison = Column(Boolean, default=False)
    is_deleterious = Column(Boolean, default=False)
    is_kashinho = Column(Boolean, default=False)
    is_kashinho_class_I = Column(Boolean, default=False)
    is_kashinho_class_II = Column(Boolean, default=False)
    is_roudou_anzen_eisei = Column(Boolean, default=False)
    is_prtr = Column(Boolean, default=False)
    is_shoubouho = Column(Boolean, default=False)
    is_high_pressure_gas = Column(Boolean, default=False)

    # AI 生JSON（SDS解析結果など）
    regulation_ai_raw = Column(Text, nullable=True)

    # SDS ファイルパス
    sds_file_path = Column(String(255), nullable=True)

    # 品目クラス / 原価関連
    item_class = Column(String(100), nullable=True)
    std_price = Column(Float, nullable=True)
    bom_json = Column(Text, nullable=True)

    # 既存：社内簡易該非判定（残すなら）
    export_control_status = Column(String(50), default="not_evaluated", nullable=False)
    export_control_reason = Column(Text, nullable=True)
    export_control_checked_at = Column(DateTime, nullable=True)

    # ★追加：外部アプリ判定の結果を格納する列（GETで返す）
    external_eval_status = Column(String(50), nullable=True)          # e.g. controlled / non_controlled / needs_review
    external_eval_reason = Column(Text, nullable=True)               # human-readable reason
    external_eval_payload = Column(Text, nullable=True)              # 生JSON（外部が返した全文）

    external_eval_requested_at = Column(DateTime, nullable=True)     # 外部へPOSTした時刻
    external_eval_received_at = Column(DateTime, nullable=True)      # Webhookで受け取った時刻

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class BomHistory(Base):
    __tablename__ = "bom_history"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), index=True, nullable=False)

    version = Column(Integer, nullable=False)
    bom_json = Column(Text, nullable=False)
    total_ratio = Column(Float, nullable=False)
    total_cost = Column(Float, nullable=False)

    note = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    product = relationship("Product", backref="bom_history")
