from __future__ import annotations

import re
import uuid
from typing import Optional, List, Any, Dict

import httpx
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

from app.models.rd_case import RDCaseProfiles, RDAssessments
from app.models.personnel import Personnel
from app.services.deemed_export import run_screening as _run_deemed_screening

import os as _os
import threading
AI_VALIDATION_BASE      = _os.environ.get("MODULE_AI_VALIDATION_URL",      "http://localhost:8011")
SCREENING_BASE          = _os.environ.get("MODULE_SCREENING_URL",           "http://localhost:8005")
AI_CLASSIFICATION_BASE  = _os.environ.get("MODULE_AI_CLASSIFICATION_URL",   "http://localhost:8002")


def _register_rnd_product_bg(case_id: str, case_title: str, use_raw: str | None) -> None:
    """R&D アセスメント完了後、ai_classification へ品目を自動登録する。
    code=RND-{case_id} で upsert（409 = 既存なので無視）。fire-and-forget。
    """
    code = f"RND-{case_id}"
    desc = (use_raw or "")[:500] or None

    def _post():
        try:
            with httpx.Client(timeout=5.0) as c:
                c.post(
                    f"{AI_CLASSIFICATION_BASE}/api/products",
                    json={
                        "code": code,
                        "name": case_title[:200],
                        "description": desc,
                        "item_type": None,
                        "usage_summary": desc,
                    },
                )
        except Exception:
            pass
    threading.Thread(target=_post, daemon=True).start()


def _notify_ai_validation_deemed_export(
    case_id: str,
    case_title: str,
    risk_level: str,
    top_factors: list,
    recommendation: str,
    person_name: str,
) -> None:
    """HIGH/CRITICAL みなし輸出リスク確定時に ai_validation へ DEEMED_EXPORT_RISK イベントを送る。
    fire-and-forget — 失敗してもアセスメントフローは止めない。
    """
    def _post():
        try:
            payload = {
                "event_type": "DEEMED_EXPORT_RISK",
                "material_code": case_id,
                "from_country": None,
                "to_country": None,
                "deemed_export_risk_level": risk_level,
                "person_name": person_name,
                "case_title": case_title,
                "top_factors": top_factors or [],
                "recommendation": recommendation or "",
            }
            with httpx.Client(timeout=5.0) as client:
                client.post(f"{AI_VALIDATION_BASE}/api/transactions/events", json=payload)
        except Exception:
            pass
    threading.Thread(target=_post, daemon=True).start()


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
    return RedirectResponse(url="/ui/cases", status_code=303)


# -----------------------------
# Case: list (プロジェクト一覧)
# -----------------------------
@router.get("/cases")
def cases_list(request: Request, db: Session = Depends(get_db)):
    cases = crud.list_cases(db)  # テナントフィルタなし（デモ環境では全件表示）
    enriched = []
    for case in cases:
        latest = crud.get_latest_profile(db, case.case_id)
        latest_assessment = None
        if latest:
            assmts = crud.list_assessments_by_case(db, case.case_id)
            latest_assessment = assmts[0] if assmts else None
        enriched.append({
            "case": case,
            "latest_profile": latest,
            "latest_assessment": latest_assessment,
        })
    return templates.TemplateResponse(request, "cases.html", { "enriched": enriched})


# -----------------------------
# Case: create
# -----------------------------
@router.get("/cases/new")
def cases_new(request: Request):
    return templates.TemplateResponse(request, "cases_new.html", {})


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
# Case: detail (バージョン履歴)
# -----------------------------
@router.get("/cases/{case_id}")
def case_detail(case_id: str, request: Request, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    profiles = crud.list_profiles(db, case_id)  # 昇順
    enriched_profiles = []
    for p in reversed(profiles):
        latest_a = db.scalars(
            select(RDAssessments)
            .where(RDAssessments.profile_id == p.profile_id)
            .order_by(RDAssessments.performed_at.desc())
            .limit(1)
        ).first()
        enriched_profiles.append({"profile": p, "assessment": latest_a})
    # みなし輸出: この案件に紐付いた人物一覧
    personnel = db.scalars(
        select(Personnel)
        .where(Personnel.case_id == case_id)
        .order_by(Personnel.created_at.desc())
    ).all()

    # 特許非公開リスクチェック（経済安全保障推進法 第4章）
    from app.services.patent_disclosure_check import check_patent_disclosure_risk
    patent_risk = check_patent_disclosure_risk(
        title=case.title or "",
        description=case.description or "",
    )

    return templates.TemplateResponse(request, "case_detail.html", {
        "case": case,
        "enriched_profiles": enriched_profiles,
        "personnel": personnel,
        "patent_risk": patent_risk,
    })


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
        "case": case,
        "next_version": next_version,
    }
    return templates.TemplateResponse(request, "profiles_new.html", ctx)


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

    new_profile = crud.create_profile(
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

    # エンドユーザー企業名が入力されていれば自動スクリーニング実行
    eu_name = (end_json.get("end_user_name") or "").strip() if end_json else ""
    if eu_name and new_profile:
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.post(
                    f"{SCREENING_BASE}/api/screen",
                    json={"company_name": eu_name, "threshold": 0.75},
                )
            if r.status_code == 200:
                data = r.json()
                crud.update_profile_screening(db, new_profile.profile_id, str(data["id"]), data["result_status"])
        except Exception:
            pass  # 自動スクリーニング失敗は非致命的

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

    # 全リスクレベルで ai_classification に品目登録（R&D技術のトレーサビリティ確保）
    _register_rnd_product_bg(
        case_id=case_id,
        case_title=case.title,
        use_raw=prof.use_requirements_raw,
    )

    # HIGH/CRITICAL リスク → ai_validation へ DEEMED_EXPORT_RISK 通知
    if scored["risk_level"] in ("HIGH", "CRITICAL"):
        import json as _json
        end_json = {}
        try:
            end_json = _json.loads(prof.end_user_template_json or "{}")
        except Exception:
            pass
        _notify_ai_validation_deemed_export(
            case_id=case_id,
            case_title=case.title,
            risk_level=scored["risk_level"],
            top_factors=scored.get("top_factors") or [],
            recommendation=scored.get("recommendation") or "",
            person_name=end_json.get("end_user_name") or "",
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

    # AI 該非判定 2リスト結果（キャッシュなし・都度取得）
    av_two_lists = None
    if prof.ai_validation_transaction_id:
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(
                    f"{AI_VALIDATION_BASE}/decision/{prof.ai_validation_transaction_id}/two-lists"
                )
            if r.status_code == 200:
                av_two_lists = r.json()
        except Exception:
            pass

    # 品目管理からUI審査ステータスを取得
    product_eval: dict | None = None
    if prof.promoted_product_id:
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(
                    f"{AI_CLASSIFICATION_BASE}/integrations/export-control/result/{prof.promoted_product_id}"
                )
            if r.status_code == 200:
                product_eval = r.json()
        except Exception:
            pass

    return templates.TemplateResponse(
        request, "profiles_latest.html",
        {
            "case": case,
            "profile": prof,
            "assessment": latest_assessment,
            "scored_now": scored_now,
            "ip_review": ip_review,
            "evidences": evidences,
            "can_compare": can_compare,
            "av_two_lists": av_two_lists,
            "product_eval": product_eval,
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
        request, "profiles_compare.html",
        {
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


# -----------------------------
# AI 該非判定 連携
# -----------------------------
@router.post("/cases/{case_id}/profiles/{profile_id}/run-ai-validation")
def run_ai_validation(
    case_id: str,
    profile_id: str,
    db: Session = Depends(get_db),
):
    """プロファイルの用途情報を ai_validation モジュールに送り、Transaction を作成 & パイプライン実行する。"""
    profile = crud.get_profile_by_id(db, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="profile not found")
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    # end_user_template_json から企業名を取得（取引先スクリーニング連携用）
    eu = profile.end_user_template_json or {}
    counterparty_name = (eu.get("end_user_name") or "").strip() or None

    # 用途テキストを結合してitem descriptionに使う
    desc_parts = []
    if profile.use_requirements_raw:
        desc_parts.append(f"用途要件: {profile.use_requirements_raw}")
    if profile.end_user_requirements_raw:
        desc_parts.append(f"需要者要件: {profile.end_user_requirements_raw}")

    # 1. ai_validation に Transaction を作成（JSON API 使用）
    payload = {
        "title": f"[R&D] {case.title} ver.{profile.version_no}",
        "counterparty_name": counterparty_name,
        "items": [
            {
                "item_name": case.title,
                "item_description": "\n".join(desc_parts),
            }
        ],
        "usage_requirements": [
            {"source": "rnd_assessment", "text": profile.use_requirements_raw}
        ] if profile.use_requirements_raw else [],
        "source_module": "rnd_assessment",
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{AI_VALIDATION_BASE}/api/transactions",
                json=payload,
            )
            resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ai_validation への接続に失敗しました: {exc}")

    tx_data = resp.json()
    transaction_id = tx_data["id"]

    # 2. FAISS + 2リスト解析を自動起動（fire-and-forget）
    try:
        with httpx.Client(timeout=60.0) as client:
            client.post(f"{AI_VALIDATION_BASE}/decision/{transaction_id}/run-and-two-lists")
    except Exception:
        pass

    # 3. 2リスト結果からリスクレベルを取得
    risk = "none"
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(f"{AI_VALIDATION_BASE}/decision/{transaction_id}/two-lists")
        if r.status_code == 200:
            counts = r.json().get("counts", {})
            if counts.get("intersection", 0) > 0:
                risk = "danger"
            elif counts.get("core_only", 0) > 0:
                risk = "warn"
            elif counts.get("expanded_only", 0) > 0:
                risk = "info"
            else:
                risk = "safe"
    except Exception:
        pass

    # 4. プロファイルに保存
    crud.update_profile_ai_validation(db, profile_id, transaction_id, risk)

    return RedirectResponse(url=f"/ui/cases/{case_id}/profiles/latest", status_code=303)


@router.post("/cases/{case_id}/profiles/{profile_id}/run-screening")
def run_screening(
    case_id: str,
    profile_id: str,
    db: Session = Depends(get_db),
):
    """エンドユーザー名をスクリーニングモジュールに送信して結果を保存する。"""
    profile = crud.get_profile_by_id(db, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="profile not found")

    eu = profile.end_user_template_json or {}
    company_name = (eu.get("end_user_name") or "").strip()
    if not company_name:
        raise HTTPException(status_code=400, detail="end_user_name が未設定です")

    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(
                f"{SCREENING_BASE}/api/screen",
                json={"company_name": company_name, "threshold": 0.75},
            )
        if r.status_code == 200:
            data = r.json()
            crud.update_profile_screening(db, profile_id, str(data["id"]), data["result_status"])
    except Exception:
        pass  # スクリーニング失敗は非致命的

    return RedirectResponse(url=f"/ui/cases/{case_id}/profiles/latest", status_code=303)


@router.post("/cases/{case_id}/profiles/{profile_id}/promote-to-item")
def promote_to_item(
    case_id: str,
    profile_id: str,
    db: Session = Depends(get_db),
):
    """プロファイルの品目情報を ai_classification へワンクリック登録し、即座に外部AI判定を発火する。"""
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    profile = crud.get_profile_by_id(db, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="profile not found")

    if profile.promoted_product_id:
        # すでに登録済みなら詳細ページへ
        return RedirectResponse(url=f"/ui/cases/{case_id}/profiles/latest", status_code=303)

    usage = "\n".join(filter(None, [
        profile.use_requirements_raw or "",
        profile.end_user_requirements_raw or "",
    ])).strip()

    # product_code: ケースの external_project_id があれば優先使用（ERP コード引き継ぎ）
    product_code = (case.external_project_id or "").strip() or None

    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"{AI_CLASSIFICATION_BASE}/integrations/products/from-rnd",
                json={
                    "case_id":            case_id,
                    "case_title":         case.title,
                    "rnd_transaction_id": profile.ai_validation_transaction_id,
                    "usage_summary":      usage,
                    "product_code":       product_code,
                },
            )
        if r.status_code not in (200, 201):
            raise ValueError(f"サービスが HTTP {r.status_code} を返しました")
        data = r.json()
        crud.update_profile_promotion(db, profile_id, data["product_id"])
    except httpx.ConnectError:
        return RedirectResponse(
            url=f"/ui/cases/{case_id}/profiles/latest?promote_error=connection",
            status_code=303,
        )
    except Exception:
        return RedirectResponse(
            url=f"/ui/cases/{case_id}/profiles/latest?promote_error=server",
            status_code=303,
        )

    return RedirectResponse(url=f"/ui/cases/{case_id}/profiles/latest?promoted=1", status_code=303)


# ──────────────────────────────────────────────────────────────────────────────
# みなし輸出スクリーニング (Personnel)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/personnel")
def personnel_list(request: Request, db: Session = Depends(get_db)):
    """人物一覧（みなし輸出管理対象者）"""
    persons = db.query(Personnel).order_by(Personnel.created_at.desc()).all()
    # 案件IDから案件タイトルをマップ
    case_ids = {p.case_id for p in persons if p.case_id}
    from app.crud.rd_case import get_case
    case_map = {cid: get_case(db, cid) for cid in case_ids}
    return templates.TemplateResponse(request, "personnel.html", {"persons": persons, "case_map": case_map},
    )


@router.get("/personnel/new")
def personnel_new(request: Request, case_id: Optional[str] = None, db: Session = Depends(get_db)):
    cases = db.query(RDCaseProfiles.__class__).all() if False else []
    from app.crud import rd_case as rd_crud
    cases = rd_crud.list_cases(db)
    return templates.TemplateResponse(request, "personnel_new.html", {"cases": cases, "selected_case_id": case_id},
    )


@router.post("/personnel/new")
def personnel_create(
    request: Request,
    case_id:               Optional[str]   = Form(None),
    tenant_id:             Optional[str]   = Form(None),
    name:                  str             = Form(...),
    role:                  Optional[str]   = Form(None),
    affiliation:           Optional[str]   = Form(None),
    nationality:           Optional[str]   = Form(None),
    residence_country:     Optional[str]   = Form(None),
    years_in_japan:        Optional[str]   = Form(None),
    dual_employment_flag:  Optional[str]   = Form(None),
    dual_employer_name:    Optional[str]   = Form(None),
    dual_employer_country: Optional[str]   = Form(None),
    tech_access_eccn:      Optional[str]   = Form(None),
    tech_access_fefta:     Optional[str]   = Form(None),
    note:                  Optional[str]   = Form(None),
    db: Session = Depends(get_db),
):
    def _float(v: Optional[str]) -> Optional[float]:
        if not v or not v.strip():
            return None
        try:
            return float(v.strip())
        except ValueError:
            return None

    person = Personnel(
        case_id               = case_id or None,
        tenant_id             = tenant_id or None,
        name                  = name.strip(),
        role                  = role or None,
        affiliation           = affiliation or None,
        nationality           = (nationality or "").strip().upper() or None,
        residence_country     = (residence_country or "").strip().upper() or None,
        years_in_japan        = _float(years_in_japan),
        dual_employment_flag  = dual_employment_flag is not None,
        dual_employer_name    = dual_employer_name or None,
        dual_employer_country = (dual_employer_country or "").strip().upper() or None,
        tech_access_eccn      = tech_access_eccn or None,
        tech_access_fefta     = tech_access_fefta or None,
        note                  = note or None,
    )
    db.add(person)
    db.commit()
    db.refresh(person)
    # 登録直後に自動スクリーニング実行
    _run_deemed_screening(person)
    db.commit()
    return RedirectResponse(url="/ui/personnel", status_code=303)


@router.get("/personnel/{personnel_id}")
def personnel_detail(personnel_id: str, request: Request, db: Session = Depends(get_db)):
    person = db.query(Personnel).filter_by(personnel_id=personnel_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="person not found")
    import json as _json
    reasons = []
    if person.deemed_export_reason:
        try:
            reasons = _json.loads(person.deemed_export_reason)
        except Exception:
            reasons = [person.deemed_export_reason]
    return templates.TemplateResponse(request, "personnel_detail.html", {"person": person, "reasons": reasons},
    )


@router.post("/personnel/{personnel_id}/screen")
def personnel_screen(personnel_id: str, db: Session = Depends(get_db)):
    person = db.query(Personnel).filter_by(personnel_id=personnel_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="person not found")
    _run_deemed_screening(person)
    db.commit()
    return RedirectResponse(url=f"/ui/personnel/{personnel_id}", status_code=303)


@router.post("/personnel/{personnel_id}/delete")
def personnel_delete(personnel_id: str, db: Session = Depends(get_db)):
    person = db.query(Personnel).filter_by(personnel_id=personnel_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="person not found")
    db.delete(person)
    db.commit()
    return RedirectResponse(url="/ui/personnel", status_code=303)


# ── 技術インテリジェンス ──────────────────────────────────────────────────
@router.get("/academic-intel")
def academic_intel(request: Request):
    return templates.TemplateResponse(request, "academic_intel.html", {})


# ── オープンクローズ戦略マトリクス ──────────────────────────────────────────
@router.get("/open-close")
def open_close_form(request: Request):
    return templates.TemplateResponse(request, "open_close.html", {"result": None})


@router.post("/open-close")
async def open_close_evaluate(request: Request):
    from app.services.open_close_matrix import OpenCloseInput, evaluate_open_close
    form = await request.form()
    inp = OpenCloseInput(
        title=form.get("title", ""),
        description=form.get("description", ""),
        eccn=form.get("eccn") or None,
        patent_risk_level=form.get("patent_risk_level", "NONE"),
        tech_maturity=form.get("tech_maturity", "middle"),
        competitive_position=form.get("competitive_position", "parity"),
        has_trade_secret_value=form.get("has_trade_secret_value", "true") == "true",
        export_destination=form.get("export_destination") or None,
    )
    result = evaluate_open_close(inp)
    return templates.TemplateResponse(request, "open_close.html", {
        "result": result,
        "title": inp.title,
        "description": inp.description,
    })