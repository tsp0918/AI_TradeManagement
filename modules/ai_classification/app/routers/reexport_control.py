"""
再輸出規制アセスメント API。

エンドポイント:
  POST /products/{product_id}/country-profiles/{profile_id}/reexport-assess
    → 品目の ECCN × プロファイルの国 で EAR Country Chart 判定を実行。
    → 判定結果を re_export_control フィールドに保存。

  GET  /products/{product_id}/country-profiles/{profile_id}/reexport-assess
    → 保存済み判定結果を返す（再計算なし）。

  POST /products/{product_id}/country-profiles/reexport-assess/all
    → 品目配下の全プロファイルを一括判定。

  GET  /api/reexport/eccn-info/{eccn}
    → ECCN の RFC コードと基本情報を返す（デバッグ / 参照用）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Product, ProductCountryProfile
from ..services.reexport_control import (
    assess_reexport,
    get_rfc_codes,
    _load_eccn_index,
    ReexportAssessment,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Pydantic スキーマ ──────────────────────────────────────────────────────────

class ControlItemOut(BaseModel):
    rfc:       str
    column:    str
    required:  bool
    high_risk: bool


class ReexportAssessOut(BaseModel):
    profile_id:     int
    eccn:           str
    destination:    str
    origin:         Optional[str]
    verdict:        str
    verdict_label:  str
    verdict_color:  str
    controls:       list[ControlItemOut]
    notes:          list[str]
    assessed_at:    Optional[datetime]


class BulkReexportOut(ReexportAssessOut):
    assess_status: str   # "assessed" | "no_eccn" | "skipped"


# ── ヘルパー ───────────────────────────────────────────────────────────────────

def _get_product_or_404(product_id: int, db: Session) -> Product:
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return p


def _get_profile_or_404(profile_id: int, product_id: int, db: Session) -> ProductCountryProfile:
    prof = db.query(ProductCountryProfile).filter_by(
        id=profile_id, product_id=product_id
    ).first()
    if not prof:
        raise HTTPException(status_code=404, detail="Country profile not found")
    return prof


def _assessment_to_out(
    prof:       ProductCountryProfile,
    assessment: ReexportAssessment,
    origin:     Optional[str],
) -> ReexportAssessOut:
    stored = _parse_stored(prof)
    return ReexportAssessOut(
        profile_id    = prof.id,
        eccn          = assessment.eccn,
        destination   = assessment.destination,
        origin        = origin,
        verdict       = assessment.verdict,
        verdict_label = assessment.verdict_label,
        verdict_color = assessment.verdict_color,
        controls      = [ControlItemOut(**c.__dict__) for c in assessment.controls],
        notes         = assessment.notes,
        assessed_at   = stored.get("assessed_at") and datetime.fromisoformat(stored["assessed_at"]),
    )


def _parse_stored(prof: ProductCountryProfile) -> dict:
    if not prof.re_export_control:
        return {}
    if isinstance(prof.re_export_control, dict):
        return prof.re_export_control
    try:
        return json.loads(prof.re_export_control)
    except Exception:
        return {}


def _save_assessment(
    prof:       ProductCountryProfile,
    assessment: ReexportAssessment,
    origin:     Optional[str],
    db:         Session,
) -> None:
    payload = assessment.to_dict()
    payload["origin"]      = origin
    payload["assessed_at"] = datetime.now(timezone.utc).isoformat()
    prof.re_export_control = json.dumps(payload, ensure_ascii=False)
    prof.fetched_at        = datetime.now(timezone.utc)
    db.commit()
    db.refresh(prof)


# ── 単体アセスメント ───────────────────────────────────────────────────────────

@router.post(
    "/products/{product_id}/country-profiles/{profile_id}/reexport-assess",
    response_model=ReexportAssessOut,
)
def run_reexport_assess(
    product_id: int,
    profile_id: int,
    db:         Session = Depends(get_db),
):
    """品目 ECCN × プロファイルの仕向国で EAR Country Chart 再輸出規制を判定する。"""
    product = _get_product_or_404(product_id, db)
    prof    = _get_profile_or_404(profile_id, product_id, db)

    eccn   = (product.eccn or "").strip() or "EAR99"
    origin = product.country_of_origin  # 仕出国

    assessment = assess_reexport(
        eccn        = eccn,
        destination = prof.country_code,
        origin      = origin,
    )
    _save_assessment(prof, assessment, origin, db)
    return _assessment_to_out(prof, assessment, origin)


@router.get(
    "/products/{product_id}/country-profiles/{profile_id}/reexport-assess",
    response_model=ReexportAssessOut,
)
def get_reexport_assess(
    product_id: int,
    profile_id: int,
    db:         Session = Depends(get_db),
):
    """保存済みの再輸出規制判定を返す（再計算なし）。"""
    product = _get_product_or_404(product_id, db)
    prof    = _get_profile_or_404(profile_id, product_id, db)

    stored = _parse_stored(prof)
    if not stored:
        # 未判定の場合はその場で実行
        eccn   = (product.eccn or "").strip() or "EAR99"
        origin = product.country_of_origin
        assessment = assess_reexport(eccn=eccn, destination=prof.country_code, origin=origin)
        _save_assessment(prof, assessment, origin, db)
        return _assessment_to_out(prof, assessment, origin)

    # 保存済みを復元
    controls = [
        ControlItemOut(**c) for c in stored.get("controls", [])
    ]
    return ReexportAssessOut(
        profile_id    = prof.id,
        eccn          = stored.get("eccn", ""),
        destination   = stored.get("destination", prof.country_code),
        origin        = stored.get("origin"),
        verdict       = stored.get("verdict", "unknown"),
        verdict_label = stored.get("verdict_label", ""),
        verdict_color = stored.get("verdict_color", "gray"),
        controls      = controls,
        notes         = stored.get("notes", []),
        assessed_at   = stored.get("assessed_at") and datetime.fromisoformat(stored["assessed_at"]),
    )


# ── 一括アセスメント ───────────────────────────────────────────────────────────

@router.post(
    "/products/{product_id}/country-profiles/reexport-assess/all",
    response_model=list[BulkReexportOut],
)
def run_reexport_assess_all(
    product_id: int,
    db:         Session = Depends(get_db),
):
    """品目配下の全プロファイルを一括で再輸出規制判定する。"""
    product  = _get_product_or_404(product_id, db)
    profiles = db.query(ProductCountryProfile).filter_by(product_id=product_id).all()

    eccn   = (product.eccn or "").strip() or "EAR99"
    origin = product.country_of_origin
    results: list[BulkReexportOut] = []

    for prof in profiles:
        assessment = assess_reexport(eccn=eccn, destination=prof.country_code, origin=origin)
        _save_assessment(prof, assessment, origin, db)
        out = _assessment_to_out(prof, assessment, origin)
        results.append(BulkReexportOut(
            **out.model_dump(),
            assess_status="assessed",
        ))

    return results


# ── ECCN 情報参照 ─────────────────────────────────────────────────────────────

@router.get("/api/reexport/eccn-info/{eccn}")
def eccn_info(eccn: str):
    """ECCN の RFC コードと基本情報を返す。"""
    eccn_clean = eccn.upper().strip()
    idx   = _load_eccn_index()
    entry = idx.get(eccn_clean)
    rfcs  = get_rfc_codes(eccn_clean)

    if not entry:
        return {
            "eccn":    eccn_clean,
            "found":   False,
            "rfcs":    [],
            "message": f"ECCN {eccn_clean} は CCL データに見つかりませんでした。"
        }

    return {
        "eccn":              eccn_clean,
        "found":             True,
        "rfcs":              rfcs,
        "category":          entry.get("category"),
        "product_group":     entry.get("product_group"),
        "heading":           (entry.get("heading") or "")[:200],
        "license_exceptions":entry.get("license_exceptions", ""),
    }
