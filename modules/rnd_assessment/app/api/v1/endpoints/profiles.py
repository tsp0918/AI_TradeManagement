from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud import rd_case as crud
from app.schemas.rd_case import ProfileCreate, ProfileRead
from app.services.fingerprint import make_fingerprint

from app.services.template_renderer import render_use_text, render_end_user_text, render_disclosure_text
from app.schemas.templates import UseRequirementV1, EndUserRequirementV1, DisclosureRequirementPhotoresistV1

router = APIRouter()


@router.post("/{case_id}/profiles", response_model=ProfileRead)
def create_profile(case_id: str, payload: ProfileCreate, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    latest = crud.get_latest_profile(db, case_id)
    next_version = 1 if not latest else latest.version_no + 1

    # 1) Render templates -> raw
    use_raw = payload.use_requirements_raw
    end_user_raw = payload.end_user_requirements_raw
    disclosure_raw = payload.disclosure_requirements_raw

    use_template_json = None
    end_user_template_json = None
    disclosure_template_json = None

    if payload.use_template:
        use_obj = UseRequirementV1.model_validate(payload.use_template)
        use_template_json = use_obj.model_dump()
        use_raw = render_use_text(use_template_json)

    if payload.end_user_template:
        end_obj = EndUserRequirementV1.model_validate(payload.end_user_template)
        end_user_template_json = end_obj.model_dump()
        end_user_raw = render_end_user_text(end_user_template_json)

    if payload.disclosure_template:
        dis_obj = DisclosureRequirementPhotoresistV1.model_validate(payload.disclosure_template)
        disclosure_template_json = dis_obj.model_dump()
        disclosure_raw = render_disclosure_text(disclosure_template_json)

    # 2) Fingerprint (render後 raw を固定)
    fp_payload = {
        "case_id": case_id,
        "version_no": next_version,
        "use_requirements_raw": use_raw,
        "end_user_requirements_raw": end_user_raw,
        "disclosure_requirements_raw": disclosure_raw,
        "intended_markets": payload.intended_markets or [],
        "tech_domains": payload.tech_domains or [],
        "project_country_risk": payload.project_country_risk,
        "use_template_json": use_template_json or {},
        "end_user_template_json": end_user_template_json or {},
        "disclosure_template_json": disclosure_template_json or {},
    }
    fingerprint = make_fingerprint(fp_payload)

    return crud.create_profile(
        db,
        case_id=case_id,
        version_no=next_version,
        use_requirements_raw=use_raw,
        end_user_requirements_raw=end_user_raw,
        disclosure_requirements_raw=disclosure_raw,
        intended_markets=payload.intended_markets,
        tech_domains=payload.tech_domains,
        project_country_risk=payload.project_country_risk,
        input_fingerprint=fingerprint,
        created_by_user_id=payload.created_by_user_id,
        use_template_json=use_template_json,
        end_user_template_json=end_user_template_json,
        disclosure_template_json=disclosure_template_json,
    )


@router.get("/{case_id}/profiles/latest", response_model=ProfileRead)
def get_latest(case_id: str, db: Session = Depends(get_db)):
    obj = crud.get_latest_profile(db, case_id)
    if not obj:
        raise HTTPException(status_code=404, detail="profile not found")
    return obj