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

    # 輸出規制（国別 ECCN / 許可要否）
    local_eccn           = Column(String(32), nullable=True)   # 仕向国における ECCN 相当分類
    license_required     = Column(String(20), nullable=True)   # no_license / nLR / license_required / prohibited

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


# ═══════════════════════════════════════════════════════════════════
# Phase 6A-2: サプライチェーン / 品目バージョン / サプライヤー管理 (SQLite)
# ═══════════════════════════════════════════════════════════════════

import uuid as _uuid_mod


class AiClsItem(Base):
    """品目（輸出管理対象品）— plat_item の ai_classification 側複製。"""
    __tablename__ = "aicls_item"

    id = Column(String(36), primary_key=True, default=lambda: str(_uuid_mod.uuid4()))
    item_code = Column(String(100), unique=True, nullable=True, index=True)
    name = Column(String(512), nullable=False)
    description = Column(Text, nullable=True)
    hs_code = Column(String(20), nullable=True)
    eccn = Column(String(20), nullable=True)
    export_control_status = Column(String(50), default="pending", nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class AiClsItemVersion(Base):
    __tablename__ = "aicls_item_version"

    id = Column(String(36), primary_key=True, default=lambda: str(_uuid_mod.uuid4()))
    item_id = Column(String(36), ForeignKey("aicls_item.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False, default=1)
    version_label = Column(String(128), nullable=True)
    eccn = Column(String(32), nullable=True)
    hs_code = Column(String(20), nullable=True)
    country_of_origin = Column(String(4), nullable=True)
    manufacturer = Column(String(512), nullable=True)
    us_content_pct = Column(Float, nullable=True)
    supplier_ids = Column(Text, nullable=True)       # JSON list
    manufacturing_process = Column(Text, nullable=True)
    chemical_composition = Column(Text, nullable=True)
    spec_snapshot = Column(Text, nullable=True)      # JSON dict
    change_category = Column(String(64), nullable=True)
    change_reason = Column(Text, nullable=True)
    source_system = Column(String(64), nullable=True)
    source_ref = Column(String(256), nullable=True)
    is_current = Column(Boolean, nullable=False, default=True)
    created_by_user_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class AiClsComplianceChangeEvent(Base):
    __tablename__ = "aicls_compliance_change_event"

    id = Column(String(36), primary_key=True, default=lambda: str(_uuid_mod.uuid4()))
    item_id = Column(String(36), ForeignKey("aicls_item.id", ondelete="CASCADE"), nullable=False, index=True)
    from_version_id = Column(String(36), nullable=True)
    to_version_id = Column(String(36), nullable=False)
    change_category = Column(String(64), nullable=False)
    impact_level = Column(String(16), nullable=False)
    impact_details = Column(Text, nullable=True)    # JSON dict
    action_required = Column(Text, nullable=True)   # JSON list
    status = Column(String(32), nullable=False, default="open")
    assessed_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by_user_id = Column(String(36), nullable=True)
    resolution_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class AiClsSupplyChainNode(Base):
    __tablename__ = "aicls_supply_chain_node"

    id = Column(String(36), primary_key=True, default=lambda: str(_uuid_mod.uuid4()))
    tenant_id = Column(String(36), nullable=True)
    name = Column(String(512), nullable=False, index=True)
    part_number = Column(String(200), nullable=True)
    node_type = Column(String(50), nullable=False, default="component")
    country_of_origin = Column(String(3), nullable=True)
    is_us_origin = Column(Boolean, default=False, nullable=False)
    hs_code = Column(String(20), nullable=True)
    eccn = Column(String(20), nullable=True)
    unit_value_usd = Column(Float, nullable=True)
    us_controlled_value_usd = Column(Float, nullable=True)
    item_id = Column(String(36), nullable=True)     # soft ref to aicls_item.id
    description = Column(Text, nullable=True)
    extra = Column(Text, nullable=True)             # JSON dict
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class AiClsSupplyChainEdge(Base):
    __tablename__ = "aicls_supply_chain_edge"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_node_id = Column(String(36), ForeignKey("aicls_supply_chain_node.id", ondelete="CASCADE"), nullable=False, index=True)
    child_node_id = Column(String(36), ForeignKey("aicls_supply_chain_node.id", ondelete="CASCADE"), nullable=False, index=True)
    quantity = Column(Float, nullable=False, default=1.0)
    unit = Column(String(20), nullable=False, default="each")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class AiClsSupplierAttestation(Base):
    __tablename__ = "aicls_supplier_attestation"

    id = Column(String(36), primary_key=True, default=lambda: str(_uuid_mod.uuid4()))
    node_id = Column(String(36), ForeignKey("aicls_supply_chain_node.id", ondelete="CASCADE"), nullable=False, index=True)
    supplier_name = Column(String(512), nullable=False)
    supplier_contact = Column(String(512), nullable=True)
    claimed_eccn = Column(String(30), nullable=True)
    claimed_country_of_origin = Column(String(3), nullable=True)
    claimed_us_content_pct = Column(Float, nullable=True)
    is_us_origin_claimed = Column(Boolean, default=False, nullable=False)
    certificate_reference = Column(String(200), nullable=True)
    attestation_date = Column(String(10), nullable=True)  # YYYY-MM-DD
    expiry_date = Column(String(10), nullable=True)
    supporting_docs = Column(Text, nullable=True)   # JSON list
    notes = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    ai_suggested_eccn = Column(String(30), nullable=True)
    ai_confidence = Column(Float, nullable=True)
    ai_verdict = Column(String(20), nullable=True)
    ai_review_detail = Column(Text, nullable=True)  # JSON dict
    ai_reviewed_at = Column(DateTime, nullable=True)
    reviewed_by_user_id = Column(String(36), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_comment = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class AiClsSupplierPortalToken(Base):
    __tablename__ = "aicls_supplier_portal_token"

    id = Column(String(36), primary_key=True, default=lambda: str(_uuid_mod.uuid4()))
    token = Column(String(64), unique=True, nullable=False, index=True)
    node_id = Column(String(36), nullable=False, index=True)
    node_name = Column(String(512), nullable=False)
    supplier_name = Column(String(512), nullable=False)
    supplier_email = Column(String(512), nullable=True)
    note_for_supplier = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    max_uses = Column(Integer, nullable=False, default=1)
    use_count = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=False)
    created_by_user_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
