from __future__ import annotations

from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rd_case import (
    RDCases,
    RDCaseProfiles,
    RDCaseItems,
    RDAssessments,
    RDIPReviews,
    RDIPReviewEvidence,
)


def create_case(
    db: Session,
    *,
    tenant_id: str,
    external_project_id: str | None,
    title: str,
    description: str | None,
    created_by_user_id: str | None,
) -> RDCases:
    obj = RDCases(
        tenant_id=tenant_id,
        external_project_id=external_project_id,
        title=title,
        description=description,
        created_by_user_id=created_by_user_id,
        status="draft",
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def get_case(db: Session, case_id: str) -> RDCases | None:
    return db.get(RDCases, case_id)


def get_latest_profile(db: Session, case_id: str) -> RDCaseProfiles | None:
    stmt = (
        select(RDCaseProfiles)
        .where(RDCaseProfiles.case_id == case_id)
        .order_by(RDCaseProfiles.version_no.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def list_profiles(db: Session, case_id: str) -> list[RDCaseProfiles]:
    stmt = (
        select(RDCaseProfiles)
        .where(RDCaseProfiles.case_id == case_id)
        .order_by(RDCaseProfiles.version_no.asc())
    )
    return list(db.scalars(stmt).all())


def get_profile_by_version(db: Session, case_id: str, version_no: int) -> RDCaseProfiles | None:
    stmt = (
        select(RDCaseProfiles)
        .where(RDCaseProfiles.case_id == case_id, RDCaseProfiles.version_no == version_no)
        .limit(1)
    )
    return db.scalars(stmt).first()


def create_profile(
    db: Session,
    *,
    case_id: str,
    version_no: int,
    use_requirements_raw: str | None,
    end_user_requirements_raw: str | None,
    disclosure_requirements_raw: str | None,
    intended_markets: list[str] | None,
    tech_domains: list[str] | None,
    project_country_risk: str | None,
    input_fingerprint: str,
    created_by_user_id: str | None,
    use_template_json: dict | None = None,
    end_user_template_json: dict | None = None,
    disclosure_template_json: dict | None = None,
) -> RDCaseProfiles:
    obj = RDCaseProfiles(
        case_id=case_id,
        version_no=version_no,
        use_requirements_raw=use_requirements_raw,
        end_user_requirements_raw=end_user_requirements_raw,
        disclosure_requirements_raw=disclosure_requirements_raw,
        intended_markets=intended_markets,
        tech_domains=tech_domains,
        project_country_risk=project_country_risk,
        input_fingerprint=input_fingerprint,
        created_by_user_id=created_by_user_id,
        use_template_json=use_template_json,
        end_user_template_json=end_user_template_json,
        disclosure_template_json=disclosure_template_json,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def add_case_item(
    db: Session,
    *,
    case_id: str,
    external_item_id: str,
    internal_item_code: str | None,
    intended_use_in_project: str | None,
) -> RDCaseItems:
    obj = RDCaseItems(
        case_id=case_id,
        external_item_id=external_item_id,
        internal_item_code=internal_item_code,
        intended_use_in_project=intended_use_in_project,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def list_case_items(db: Session, case_id: str) -> list[RDCaseItems]:
    stmt = (
        select(RDCaseItems)
        .where(RDCaseItems.case_id == case_id)
        .order_by(RDCaseItems.created_at.asc())
    )
    return list(db.scalars(stmt).all())


def create_assessment(
    db: Session,
    *,
    profile_id: str,
    performed_by_user_id: str,
    overall_score: int,
    risk_level: str,
    recommendation: str,
    tech_risk_score: int,
    use_risk_score: int,
    end_user_risk_score: int,
    regulatory_risk_score: int,
    rules_triggered: list[dict] | None,
    top_factors: list[str] | None,
    input_snapshot: dict | None,
    input_fingerprint: str,
    ruleset_version: str,
    model_version: str,
    scoring_config_version: str,
    external_screening_job: dict | None,
) -> RDAssessments:
    obj = RDAssessments(
        profile_id=profile_id,
        performed_by_user_id=performed_by_user_id,
        overall_score=overall_score,
        risk_level=risk_level,
        recommendation=recommendation,
        tech_risk_score=tech_risk_score,
        use_risk_score=use_risk_score,
        end_user_risk_score=end_user_risk_score,
        regulatory_risk_score=regulatory_risk_score,
        rules_triggered=rules_triggered,
        top_factors=top_factors,
        input_snapshot=input_snapshot,
        input_fingerprint=input_fingerprint,
        ruleset_version=ruleset_version,
        model_version=model_version,
        scoring_config_version=scoring_config_version,
        external_screening_job=external_screening_job,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def list_assessments_by_case(db: Session, case_id: str) -> list[RDAssessments]:
    stmt = (
        select(RDAssessments)
        .join(RDCaseProfiles, RDCaseProfiles.profile_id == RDAssessments.profile_id)
        .where(RDCaseProfiles.case_id == case_id)
        .order_by(RDAssessments.performed_at.desc())
    )
    return list(db.scalars(stmt).all())


def get_assessment(db: Session, assessment_id: str) -> RDAssessments | None:
    return db.get(RDAssessments, assessment_id)


# =========================================================
# NEW: IP review + evidence
# =========================================================
def get_ip_review_by_profile(db: Session, profile_id: str) -> RDIPReviews | None:
    stmt = select(RDIPReviews).where(RDIPReviews.profile_id == profile_id).limit(1)
    return db.scalars(stmt).first()


def upsert_ip_review(
    db: Session,
    *,
    profile_id: str,
    status: str,
    reviewer_user_id: str | None,
    reviewer_comment: str | None,
    finalize: bool = False,
) -> RDIPReviews:
    obj = get_ip_review_by_profile(db, profile_id)
    if obj is None:
        obj = RDIPReviews(
            profile_id=profile_id,
            status=status,
            reviewer_user_id=reviewer_user_id,
            reviewer_comment=reviewer_comment,
            finalized_at=(datetime.utcnow() if finalize else None),
        )
        db.add(obj)
    else:
        obj.status = status
        obj.reviewer_user_id = reviewer_user_id
        obj.reviewer_comment = reviewer_comment
        if finalize:
            obj.finalized_at = datetime.utcnow()

    db.commit()
    db.refresh(obj)
    return obj


def add_ip_evidence(
    db: Session,
    *,
    ip_review_id: str,
    original_filename: str,
    stored_filename: str,
    content_type: str | None,
    size_bytes: int | None,
    note: str | None,
) -> RDIPReviewEvidence:
    obj = RDIPReviewEvidence(
        ip_review_id=ip_review_id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        content_type=content_type,
        size_bytes=size_bytes,
        note=note,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def list_ip_evidences(db: Session, ip_review_id: str) -> list[RDIPReviewEvidence]:
    stmt = (
        select(RDIPReviewEvidence)
        .where(RDIPReviewEvidence.ip_review_id == ip_review_id)
        .order_by(RDIPReviewEvidence.created_at.desc())
    )
    return list(db.scalars(stmt).all())