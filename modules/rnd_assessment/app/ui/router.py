from __future__ import annotations

import re
import uuid
from typing import Optional, List, Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from datetime import datetime

from sqlalchemy import select

from app.api.deps import get_db
from app.crud import rd_case as crud
from app.services.fingerprint import make_fingerprint

from app.schemas.templates import UseRequirementV1, EndUserRequirementV1, DisclosureRequirementPhotoresistV1
from app.services.template_renderer import render_use_text, render_end_user_text, render_disclosure_text
from app.services.scoring import score_demo

from app.models.rd_case import RDCaseProfiles


router = APIRouter(prefix="/ui", tags=["ui"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_UPLOAD_EXT = {".pdf", ".txt", ".md", ".png", ".jpg", ".jpeg", ".xlsx", ".csv"}


def _split_csv(s: Optional[str]) -> List[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def _to_bool_checkbox(v: Optional[str]) -> bool:
    return v is not None


def _to_int(v: Optional[str]) -> Optional[int]:
    if v is None:
        return None
    v = v.strip()
    if v == "":
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _normalize_bool(v: Optional[str]) -> Optional[bool]:
    if v is None:
        return None
    if v in ("true", "True", "1", "yes", "Yes", "on"):
        return True
    if v in ("false", "False", "0", "no", "No", "off"):
        return False
    return None


def _safe_filename(name: str) -> str:
    name = name.strip().replace("\x00", "")
    name = re.sub(r"[^A-Za-z0-9.\-_() ]+", "_", name)
    return name[:150] if name else "file"


def _json_diff(old: dict | None, new: dict | None) -> list[dict]:
    old = old or {}
    new = new or {}

    def norm(v):
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return sorted(v)
        return v

    keys = sorted(set(old.keys()) | set(new.keys()))
    rows: list[dict] = []
    for k in keys:
        ov = norm(old.get(k))
        nv = norm(new.get(k))
        if ov != nv:
            rows.append({"field": k, "old": ov, "new": nv})
    return rows


@router.get("/")
def ui_root():
    return RedirectResponse(url="/ui/cases/new", status_code=303)


# -----------------------------
# Case: create
# -----------------------------
@router.get("/cases/new")
def cases_new(request: Request):
    return templates.TemplateResponse("cases_new.html", {"request": request})


@router.post("/cases/new")
def cases_create(
    request: Request,
    tenant_id: str = Form(...),
    external_project_id: Optional[str] = Form(None),
    title: str = Form(...),
    description: Optional[str] = Form(None),
    created_by_user_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    obj = crud.create_case(
        db,
        tenant_id=tenant_id,
        external_project_id=external_project_id,
        title=title,
        description=description,
        created_by_user_id=created_by_user_id,
    )
    return RedirectResponse(url=f"/ui/cases/{obj.case_id}/profiles/new", status_code=303)


# -----------------------------
# Profile: create with templates
# -----------------------------
@router.get("/cases/{case_id}/profiles/new")
def profiles_new(request: Request, case_id: str, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    latest = crud.get_latest_profile(db, case_id)
    next_version = 1 if not latest else latest.version_no + 1

    ctx = {
        "request": request,
        "case": case,
        "next_version": next_version,
    }
    return templates.TemplateResponse("profiles_new.html", ctx)


@router.post("/cases/{case_id}/profiles/new")
def profiles_create(
    request: Request,
    case_id: str,
    # -------------------------
    # Use template fields
    # -------------------------
    use_process: Optional[str] = Form(None),
    use_product_category: Optional[str] = Form(None),
    use_tech_node_nm: Optional[str] = Form(None),
    use_application: Optional[str] = Form(None),
    use_rd_phase: Optional[str] = Form(None),
    use_usage_description: Optional[str] = Form(None),
    use_dual_use_potential: Optional[str] = Form(None),
    use_military_end_use_possible: Optional[str] = Form(None),
    use_surveillance_end_use_possible: Optional[str] = Form(None),
    use_tags: Optional[str] = Form(None),  # CSV

    # -------------------------
    # End user template fields
    # -------------------------
    end_user_name: Optional[str] = Form(None),
    end_user_country: Optional[str] = Form(None),
    end_user_type: Optional[str] = Form("unknown"),
    intended_countries: Optional[str] = Form(None),  # CSV
    retransfer_possible: Optional[str] = Form(None),
    retransfer_notes: Optional[str] = Form(None),
    restricted_party_screened: Optional[str] = Form(None),
    screening_reference: Optional[str] = Form(None),
    end_user_notes: Optional[str] = Form(None),
    end_user_tags: Optional[str] = Form(None),  # CSV

    # -------------------------
    # Disclosure (Photoresist) template fields
    # -------------------------
    dis_resist_type: Optional[str] = Form(None),          # KrF / ArF / EUV / ...
    dis_target_node_nm: Optional[str] = Form(None),       # int-like
    dis_application: Optional[str] = Form(None),          # logic / memory / CIS
    dis_novelty_source: Optional[str] = Form(None),       # CSV of enum values
    dis_reproducibility_risk: Optional[str] = Form(None), # low/medium/high
    dis_reverse_engineering_risk: Optional[str] = Form(None),
    dis_requires_process_knowhow: Optional[str] = Form(None), # checkbox
    dis_strategic_importance: Optional[str] = Form(None),     # low/medium/high/critical
    dis_export_control_sensitivity: Optional[str] = Form(None), # checkbox
    dis_military_dual_use_potential: Optional[str] = Form(None), # checkbox
    dis_planned_patent_filing: Optional[str] = Form(None),       # checkbox
    dis_planned_filing_region: Optional[str] = Form(None),       # CSV
    dis_publication_intent: Optional[str] = Form(None),          # checkbox
    dis_external_collaboration: Optional[str] = Form(None),       # checkbox
    dis_partner_countries: Optional[str] = Form(None),            # CSV
    dis_nda_in_place: Optional[str] = Form(None),                 # checkbox
    dis_notes: Optional[str] = Form(None),

    created_by_user_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    latest = crud.get_latest_profile(db, case_id)
    next_version = 1 if not latest else latest.version_no + 1

    # normalize retransfer_possible
    rp = _normalize_bool(retransfer_possible)

    # templates
    use_template = {
        "schema_version": "use_v1",
        "process": use_process or None,
        "product_category": use_product_category or None,
        "tech_node_nm": _to_int(use_tech_node_nm),
        "application": use_application or None,
        "rd_phase": use_rd_phase or None,
        "usage_description": use_usage_description or None,
        "dual_use_potential": _to_bool_checkbox(use_dual_use_potential),
        "military_end_use_possible": _to_bool_checkbox(use_military_end_use_possible),
        "surveillance_end_use_possible": _to_bool_checkbox(use_surveillance_end_use_possible),
        "tags": _split_csv(use_tags),
    }

    end_user_template = {
        "schema_version": "end_user_v1",
        "end_user_name": end_user_name or None,
        "end_user_country": end_user_country or None,
        "end_user_type": end_user_type or "unknown",
        "intended_countries": _split_csv(intended_countries),
        "retransfer_possible": rp,
        "retransfer_notes": retransfer_notes or None,
        "restricted_party_screened": _to_bool_checkbox(restricted_party_screened),
        "screening_reference": screening_reference or None,
        "notes": end_user_notes or None,
        "tags": _split_csv(end_user_tags),
    }

    disclosure_template = {
        "schema_version": "disclosure_photoresist_v1",
        "resist_type": dis_resist_type or None,
        "target_node_nm": _to_int(dis_target_node_nm),
        "application": dis_application or None,
        "novelty_source": _split_csv(dis_novelty_source),
        "reproducibility_risk": (dis_reproducibility_risk or None),
        "reverse_engineering_risk": (dis_reverse_engineering_risk or None),
        "requires_process_knowhow": _to_bool_checkbox(dis_requires_process_knowhow),
        "strategic_importance": (dis_strategic_importance or None),
        "export_control_sensitivity": _to_bool_checkbox(dis_export_control_sensitivity),
        "military_dual_use_potential": _to_bool_checkbox(dis_military_dual_use_potential),
        "planned_patent_filing": _to_bool_checkbox(dis_planned_patent_filing),
        "planned_filing_region": _split_csv(dis_planned_filing_region),
        "publication_intent": _to_bool_checkbox(dis_publication_intent),
        "external_collaboration": _to_bool_checkbox(dis_external_collaboration),
        "partner_countries": _split_csv(dis_partner_countries),
        "nda_in_place": _to_bool_checkbox(dis_nda_in_place),
        "notes": dis_notes or None,
    }

    # validate
    use_obj = UseRequirementV1.model_validate(use_template)
    end_obj = EndUserRequirementV1.model_validate(end_user_template)
    dis_obj = DisclosureRequirementPhotoresistV1.model_validate(disclosure_template)

    use_json = use_obj.model_dump()
    end_json = end_obj.model_dump()
    dis_json = dis_obj.model_dump()

    # render raw
    use_raw = render_use_text(use_json)
    end_raw = render_end_user_text(end_json)
    dis_raw = render_disclosure_text(dis_json)

    # fingerprint (テンプレも含めて固定)
    fp_payload = {
        "case_id": case_id,
        "version_no": next_version,
        "use_requirements_raw": use_raw,
        "end_user_requirements_raw": end_raw,
        "disclosure_requirements_raw": dis_raw,
        "use_template_json": use_json,
        "end_user_template_json": end_json,
        "disclosure_template_json": dis_json,
        "intended_markets": [],
        "tech_domains": [],
        "project_country_risk": None,
    }
    fingerprint = make_fingerprint(fp_payload)

    crud.create_profile(
        db,
        case_id=case_id,
        version_no=next_version,
        use_requirements_raw=use_raw,
        end_user_requirements_raw=end_raw,
        disclosure_requirements_raw=dis_raw,
        intended_markets=None,
        tech_domains=None,
        project_country_risk=None,
        input_fingerprint=fingerprint,
        created_by_user_id=created_by_user_id,
        use_template_json=use_json,
        end_user_template_json=end_json,
        disclosure_template_json=dis_json,
    )

    return RedirectResponse(url=f"/ui/cases/{case_id}/profiles/latest", status_code=303)


# -----------------------------
# Assessment: run (demo)
# -----------------------------
@router.post("/cases/{case_id}/assessments/run")
def assessments_run(case_id: str, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    prof = crud.get_latest_profile(db, case_id)
    if not prof:
        raise HTTPException(status_code=404, detail="profile not found")

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
        "disclosure_result": scored.get("disclosure_result"),
        "security_result": scored.get("security_result"),
    }

    crud.create_assessment(
        db,
        profile_id=prof.profile_id,
        performed_by_user_id="ui",
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
        ruleset_version="ruleset-demo-0.3",
        model_version="model-demo-0.3",
        scoring_config_version="scoring-demo-0.3",
        external_screening_job=None,
    )

    return RedirectResponse(url=f"/ui/cases/{case_id}/profiles/latest", status_code=303)


# -----------------------------
# IP Review: update + upload evidence + download
# -----------------------------
@router.post("/cases/{case_id}/ip-review/update")
def ip_review_update(
    case_id: str,
    status: str = Form(...),
    reviewer_user_id: Optional[str] = Form(None),
    reviewer_comment: Optional[str] = Form(None),
    finalize: Optional[str] = Form(None),  # checkbox
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    prof = crud.get_latest_profile(db, case_id)
    if not prof:
        raise HTTPException(status_code=404, detail="profile not found")

    crud.upsert_ip_review(
        db,
        profile_id=prof.profile_id,
        status=status,
        reviewer_user_id=reviewer_user_id,
        reviewer_comment=reviewer_comment,
        finalize=(finalize is not None),
    )
    return RedirectResponse(url=f"/ui/cases/{case_id}/profiles/latest", status_code=303)


@router.post("/cases/{case_id}/ip-review/evidence/upload")
def ip_review_upload(
    case_id: str,
    file: UploadFile = File(...),
    note: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    prof = crud.get_latest_profile(db, case_id)
    if not prof:
        raise HTTPException(status_code=404, detail="profile not found")

    review = crud.get_ip_review_by_profile(db, prof.profile_id)
    if review is None:
        # 最初のアップロードでレビューを自動作成（draft）
        review = crud.upsert_ip_review(
            db,
            profile_id=prof.profile_id,
            status="draft",
            reviewer_user_id=None,
            reviewer_comment=None,
            finalize=False,
        )

    orig = _safe_filename(file.filename or "file")
    ext = Path(orig).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise HTTPException(status_code=400, detail=f"file type not allowed: {ext}")

    stored = f"{uuid.uuid4().hex}{ext}"
    out_path = UPLOAD_DIR / stored

    data = file.file.read()
    out_path.write_bytes(data)

    crud.add_ip_evidence(
        db,
        ip_review_id=review.ip_review_id,
        original_filename=orig,
        stored_filename=stored,
        content_type=file.content_type,
        size_bytes=len(data),
        note=note,
    )

    return RedirectResponse(url=f"/ui/cases/{case_id}/profiles/latest", status_code=303)


@router.get("/ip-review/evidence/{stored_filename}")
def ip_review_download(stored_filename: str):
    path = UPLOAD_DIR / stored_filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(str(path), filename=stored_filename)


# -----------------------------
# Latest page
# -----------------------------
@router.get("/cases/{case_id}/profiles/latest")
def profiles_latest(request: Request, case_id: str, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    prof = crud.get_latest_profile(db, case_id)
    if not prof:
        raise HTTPException(status_code=404, detail="profile not found")

    assessments = crud.list_assessments_by_case(db, case_id)
    latest_assessment = assessments[0] if assessments else None

    # compute score live too (for explanation even if assessment not run yet)
    scored_now = score_demo(prof)

    # ip review + evidences
    ip_review = crud.get_ip_review_by_profile(db, prof.profile_id)
    evidences = crud.list_ip_evidences(db, ip_review.ip_review_id) if ip_review else []

    # Compare link hint
    profiles = crud.list_profiles(db, case_id)
    can_compare = len(profiles) >= 2

    return templates.TemplateResponse(
        "profiles_latest.html",
        {
            "request": request,
            "case": case,
            "profile": prof,
            "assessment": latest_assessment,
            "scored_now": scored_now,
            "ip_review": ip_review,
            "evidences": evidences,
            "can_compare": can_compare,
        },
    )


# -----------------------------
# Compare page (diff + score impact)
# -----------------------------
@router.get("/cases/{case_id}/profiles/compare")
def profiles_compare(
    request: Request,
    case_id: str,
    from_version: int | None = None,
    to_version: int | None = None,
    db: Session = Depends(get_db),
):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    profiles = crud.list_profiles(db, case_id)
    if len(profiles) < 2:
        raise HTTPException(status_code=400, detail="need at least 2 profiles to compare")

    if to_version is None:
        to_version = profiles[-1].version_no
    if from_version is None:
        from_version = profiles[-2].version_no

    p_from = crud.get_profile_by_version(db, case_id, from_version)
    p_to = crud.get_profile_by_version(db, case_id, to_version)
    if not p_from or not p_to:
        raise HTTPException(status_code=404, detail="profile version not found")

    use_rows = _json_diff(p_from.use_template_json or {}, p_to.use_template_json or {})
    end_rows = _json_diff(p_from.end_user_template_json or {}, p_to.end_user_template_json or {})
    dis_rows = _json_diff(p_from.disclosure_template_json or {}, p_to.disclosure_template_json or {})

    # score impact (re-score both)
    score_from = score_demo(p_from)
    score_to = score_demo(p_to)

    delta = {
        "overall": score_to["overall_score"] - score_from["overall_score"],
        "tech": score_to["tech_risk_score"] - score_from["tech_risk_score"],
        "use": score_to["use_risk_score"] - score_from["use_risk_score"],
        "end_user": score_to["end_user_risk_score"] - score_from["end_user_risk_score"],
        "regulatory": score_to["regulatory_risk_score"] - score_from["regulatory_risk_score"],
    }

    return templates.TemplateResponse(
        "profiles_compare.html",
        {
            "request": request,
            "case": case,
            "profiles": profiles,
            "from_version": from_version,
            "to_version": to_version,
            "p_from": p_from,
            "p_to": p_to,
            "use_rows": use_rows,
            "end_rows": end_rows,
            "dis_rows": dis_rows,
            "score_from": score_from,
            "score_to": score_to,
            "delta": delta,
        },
    )