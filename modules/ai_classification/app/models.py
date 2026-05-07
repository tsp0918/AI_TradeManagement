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

    # 再輸出規制用：原産地（ISO 3166-1 alpha-2: JP/US/CN/KR など）
    country_of_origin = Column(String(10), nullable=True)

    # 既存：社内簡易該非判定（残すなら）
    export_control_status = Column(String(50), default="not_evaluated", nullable=False)
    export_control_reason = Column(Text, nullable=True)
    export_control_checked_at = Column(DateTime, nullable=True)

    # 外部AI判定用の用途概要（手動入力・外部判定リクエストに含める）
    usage_summary = Column(Text, nullable=True)

    # ★追加：外部アプリ判定の結果を格納する列（GETで返す）
    external_eval_status = Column(String(50), nullable=True)          # e.g. controlled / non_controlled / needs_review
    external_eval_reason = Column(Text, nullable=True)               # human-readable reason
    external_eval_payload = Column(Text, nullable=True)              # 生JSON（外部が返した全文）

    external_eval_requested_at = Column(DateTime, nullable=True)     # 外部へPOSTした時刻
    external_eval_received_at = Column(DateTime, nullable=True)      # Webhookで受け取った時刻

    # HSコード判定連携
    hs_classification_status = Column(String(30), nullable=True)   # pending / completed / failed
    hs_classification_result = Column(Text, nullable=True)         # JSON: list[HSCandidate]
    hs_classified_at         = Column(DateTime, nullable=True)
    hs_request_id            = Column(String(36), nullable=True)

    # R&D 由来リネージ（rnd_assessment 連携）
    source_rnd_case_id        = Column(String(64), nullable=True)   # rnd_assessment case_id (UUID)
    source_rnd_transaction_id = Column(Integer,    nullable=True)   # RND_xxx の ai_validation transaction ID

    # ai_validation 連携（Webhook で受け取る UI_xxx transaction ID）
    ui_validation_transaction_id = Column(Integer, nullable=True)   # UI_xxx / TX_xxx の transaction ID

    # 輸出管理 判定明細（JSON list: [{rule_item_no, decision, comment}, ...]）
    export_control_items = Column(Text, nullable=True)

    # 4象限マッピング用スコア
    regulation_score  = Column(Float, nullable=True)   # 0-100: 規制感度（自動算出）
    sovereignty_score = Column(Float, nullable=True)   # 0-100: 技術主権価値（手動入力）
    sovereignty_note  = Column(Text,  nullable=True)   # 主権価値の根拠メモ

    # 品目管理フラグ
    source        = Column(String(20), default="AI_TM", nullable=False)  # "AI_TM" | "ERP" | "RND"
    item_type     = Column(String(20), nullable=True)                    # "FINISHED_GOODS" | "BOM_COMPONENT"
    is_unconfirmed = Column(Boolean, default=False, nullable=False)      # True = ERP 受信・未確認

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ProductCountryProfile(Base):
    """品目×国別ローカル規制プロファイル。

    仕出国・仕向国それぞれについて、ローカルHSコード・関税率・輸入規制・
    再輸出規制・貿易統計を品目単位で管理する。
    対応国: JP（日本）/ US（米国）/ CN（中国）/ EU（EU）/ KR（韓国）
    """
    __tablename__ = "product_country_profiles"

    id         = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)

    # 国識別
    country_code = Column(String(4),  nullable=False)        # JP / US / CN / EU / KR
    country_name = Column(String(64), nullable=True)         # 表示用
    role         = Column(String(16), nullable=False)        # "origin" | "destination" | "both"

    # ローカルHSコード（手入力 Ph.1、自動補完 Ph.2〜）
    local_hs_code        = Column(String(12), nullable=True)   # 9〜10桁
    local_hs_description = Column(Text,       nullable=True)

    # 関税情報（Ph.3 で自動取得）
    tariff_rate  = Column(Float,       nullable=True)         # 例: 0.05 = 5%
    tariff_type  = Column(String(16),  nullable=True)         # MFN / FTA / GSP / 301 ...
    tariff_notes = Column(Text,        nullable=True)

    # 輸入規制・禁止品目フラグ（Ph.3 で自動取得）
    import_restrictions = Column(Text, nullable=True)         # JSON

    # 再輸出規制（Ph.5）
    re_export_control = Column(Text, nullable=True)           # JSON

    # 貿易統計（Ph.4 で自動取得）
    trade_stats = Column(Text, nullable=True)                 # JSON: {year, value_usd, qty, unit}

    # メタ
    data_source = Column(String(32), nullable=True)           # "manual" | "wto_api" | ...
    notes       = Column(Text,       nullable=True)           # 手動メモ
    fetched_at  = Column(DateTime,   nullable=True)
    created_at  = Column(DateTime,   server_default=func.now(), nullable=False)
    updated_at  = Column(DateTime,   server_default=func.now(), onupdate=func.now(), nullable=False)

    product = relationship("Product", back_populates="country_profiles")


# Product モデルに country_profiles リレーション追加（動的）
Product.country_profiles = relationship(
    "ProductCountryProfile",
    back_populates="product",
    cascade="all, delete-orphan",
    order_by="ProductCountryProfile.country_code",
)


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
