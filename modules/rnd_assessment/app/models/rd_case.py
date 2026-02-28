from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Column,
    String,
    Text,
    DateTime,
    Integer,
    ForeignKey,
    JSON,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class RDCases(Base):
    __tablename__ = "rd_cases"

    case_id = Column(String, primary_key=True, default=_uuid)
    tenant_id = Column(String, nullable=False, index=True)
    external_project_id = Column(String, nullable=True, index=True)

    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    status = Column(String, nullable=False, default="draft", index=True)
    created_by_user_id = Column(String, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    profiles = relationship("RDCaseProfiles", back_populates="case", cascade="all, delete-orphan")
    items = relationship("RDCaseItems", back_populates="case", cascade="all, delete-orphan")


class RDCaseProfiles(Base):
    __tablename__ = "rd_case_profiles"

    profile_id = Column(String, primary_key=True, default=_uuid)
    case_id = Column(String, ForeignKey("rd_cases.case_id", ondelete="CASCADE"), nullable=False, index=True)

    version_no = Column(Integer, nullable=False, default=1, index=True)

    # human-readable raw
    use_requirements_raw = Column(Text, nullable=True)
    end_user_requirements_raw = Column(Text, nullable=True)
    disclosure_requirements_raw = Column(Text, nullable=True)

    intended_markets = Column(JSON, nullable=True)
    tech_domains = Column(JSON, nullable=True)
    project_country_risk = Column(String, nullable=True)

    input_fingerprint = Column(String, nullable=False, index=True)
    created_by_user_id = Column(String, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # structured templates
    use_template_json = Column(JSON, nullable=True)
    end_user_template_json = Column(JSON, nullable=True)
    disclosure_template_json = Column(JSON, nullable=True)

    # ai_validation 連携
    ai_validation_transaction_id = Column(Integer, nullable=True, index=True)
    ai_validation_risk            = Column(String,  nullable=True)
    ai_validation_run_at          = Column(DateTime, nullable=True)

    # スクリーニング連携
    screening_result_id = Column(String(36), nullable=True)
    screening_status    = Column(String(30), nullable=True)

    # 品目管理への昇格（ai_classification 連携）
    promoted_product_id = Column(Integer,  nullable=True)   # ai_classification product.id
    promoted_at         = Column(DateTime, nullable=True)

    case = relationship("RDCases", back_populates="profiles")
    ip_review = relationship("RDIPReviews", back_populates="profile", uselist=False, cascade="all, delete-orphan")



class RDCaseItems(Base):
    __tablename__ = "rd_case_items"

    case_item_id = Column(String, primary_key=True, default=_uuid)
    case_id = Column(String, ForeignKey("rd_cases.case_id", ondelete="CASCADE"), nullable=False, index=True)

    external_item_id = Column(String, nullable=False, index=True)
    internal_item_code = Column(String, nullable=True, index=True)

    intended_use_in_project = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    case = relationship("RDCases", back_populates="items")


class RDAssessments(Base):
    __tablename__ = "rd_assessments"

    assessment_id = Column(String, primary_key=True, default=_uuid)
    profile_id = Column(String, ForeignKey("rd_case_profiles.profile_id", ondelete="CASCADE"), nullable=False, index=True)

    performed_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    performed_by_user_id = Column(String, nullable=False, default="system")

    overall_score = Column(Integer, nullable=False)
    risk_level = Column(String, nullable=False, index=True)
    recommendation = Column(String, nullable=False)

    tech_risk_score = Column(Integer, nullable=False)
    use_risk_score = Column(Integer, nullable=False)
    end_user_risk_score = Column(Integer, nullable=False)
    regulatory_risk_score = Column(Integer, nullable=False)

    rules_triggered = Column(JSON, nullable=True)
    top_factors = Column(JSON, nullable=True)

    input_snapshot = Column(JSON, nullable=True)
    input_fingerprint = Column(String, nullable=False, index=True)

    ruleset_version = Column(String, nullable=False)
    model_version = Column(String, nullable=False)
    scoring_config_version = Column(String, nullable=False)

    external_screening_job = Column(JSON, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# =========================================================
# NEW: IP review + evidence (PoC: local file store + DB metadata)
# =========================================================
class RDIPReviews(Base):
    __tablename__ = "rd_ip_reviews"

    ip_review_id = Column(String, primary_key=True, default=_uuid)
    profile_id = Column(String, ForeignKey("rd_case_profiles.profile_id"), nullable=False, unique=True, index=True)

    # workflow
    status = Column(String, nullable=False, default="draft", index=True)
    reviewer_user_id = Column(String, nullable=True)
    reviewer_comment = Column(Text, nullable=True)

    # when finalized
    finalized_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    profile = relationship("RDCaseProfiles", back_populates="ip_review")
    evidences = relationship("RDIPReviewEvidence", back_populates="ip_review", cascade="all, delete-orphan")


class RDIPReviewEvidence(Base):
    __tablename__ = "rd_ip_review_evidence"

    evidence_id = Column(String, primary_key=True, default=_uuid)
    ip_review_id = Column(String, ForeignKey("rd_ip_reviews.ip_review_id"), nullable=False, index=True)

    original_filename = Column(String, nullable=False)
    stored_filename = Column(String, nullable=False, unique=True, index=True)
    content_type = Column(String, nullable=True)
    size_bytes = Column(Integer, nullable=True)

    note = Column(Text, nullable=True)  # optional per-file note

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    ip_review = relationship("RDIPReviews", back_populates="evidences")