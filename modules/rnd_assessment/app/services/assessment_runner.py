from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.crud import rd_case as crud
from app.services.scoring import score_demo


def run_assessment(db: Session, *, case_id: str, performed_by_user_id: str = "system") -> Dict[str, Any]:
    """
    Caseの最新Profileを対象にAssessmentを実行し、DB保存して返す。
    """
    case = crud.get_case(db, case_id)
    if not case:
        raise ValueError("case not found")

    prof = crud.get_latest_profile(db, case_id)
    if not prof:
        raise ValueError("profile not found")

    scored = score_demo(prof)

    snapshot = {
        "case_id": case_id,
        "profile_id": prof.profile_id,
        "performed_at": datetime.utcnow().isoformat(),
        "use_requirements_raw": prof.use_requirements_raw,
        "end_user_requirements_raw": prof.end_user_requirements_raw,
        "disclosure_requirements_raw": prof.disclosure_requirements_raw,
        "use_template_json": prof.use_template_json,
        "end_user_template_json": prof.end_user_template_json,
        "disclosure_template_json": prof.disclosure_template_json,
        # NEW: ここに「Open/Close と 安保ポスチャ」結果を保存（説明可能性の核）
        "disclosure_result": scored.get("disclosure_result"),
        "security_result": scored.get("security_result"),
    }

    obj = crud.create_assessment(
        db,
        profile_id=prof.profile_id,
        performed_by_user_id=performed_by_user_id,
        overall_score=scored["overall_score"],
        risk_level=scored["risk_level"],
        recommendation=scored["recommendation"],
        tech_risk_score=scored["tech_risk_score"],
        use_risk_score=scored["use_risk_score"],
        end_user_risk_score=scored["end_user_risk_score"],
        regulatory_risk_score=scored["regulatory_risk_score"],
        rules_triggered=scored.get("rules_triggered"),
        top_factors=scored.get("top_factors"),
        input_snapshot=snapshot,
        input_fingerprint=prof.input_fingerprint,
        ruleset_version="ruleset-demo-0.2",
        model_version="model-demo-0.2",
        scoring_config_version="scoring-demo-0.2",
        external_screening_job=None,
    )
    return {"assessment_id": obj.assessment_id}