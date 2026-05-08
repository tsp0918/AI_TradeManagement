"""国別規制プロファイル CRUD API。

エンドポイント:
  GET    /products/{product_id}/country-profiles          一覧
  POST   /products/{product_id}/country-profiles          追加
  PUT    /products/{product_id}/country-profiles/{id}     更新
  DELETE /products/{product_id}/country-profiles/{id}     削除
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Product, ProductCountryProfile

router = APIRouter()

# ── 対応国マスター ─────────────────────────────────────────────────────────────
SUPPORTED_COUNTRIES: dict[str, str] = {
    "JP": "日本",
    "US": "米国",
    "CN": "中国",
    "EU": "EU（欧州連合）",
    "KR": "韓国",
}

ROLES = {
    "origin":      "仕出国",
    "destination": "仕向国",
    "both":        "仕出国 / 仕向国",
}


# ── Pydantic スキーマ ──────────────────────────────────────────────────────────

_LICENSE_LABELS: dict[str, str] = {
    "no_license":       "許可不要",
    "nLR":              "NLR (No License Required)",
    "license_required": "許可証必要",
    "prohibited":       "禁輸",
}


class CountryProfileIn(BaseModel):
    country_code:        str          = Field(..., description="JP / US / CN / EU / KR")
    role:                str          = Field(..., description="origin / destination / both")
    local_hs_code:       Optional[str]   = None
    local_hs_description:Optional[str]  = None
    local_eccn:          Optional[str]   = None
    license_required:    Optional[str]   = None   # no_license / nLR / license_required / prohibited
    tariff_rate:         Optional[float] = Field(None, ge=0.0, le=5.0,
                                                  description="小数（例: 0.05 = 5%）")
    tariff_type:         Optional[str]  = None   # MFN / FTA / GSP / 301 / ...
    tariff_notes:        Optional[str]  = None
    import_restrictions: Optional[dict] = None
    re_export_control:   Optional[dict] = None
    trade_stats:         Optional[dict] = None
    notes:               Optional[str]  = None


class CountryProfileOut(BaseModel):
    id:                  int
    product_id:          int
    country_code:        str
    country_name:        Optional[str]
    role:                str
    role_label:          str
    local_hs_code:       Optional[str]
    local_hs_description:Optional[str]
    local_eccn:          Optional[str]
    license_required:    Optional[str]
    license_required_label: Optional[str]
    tariff_rate:         Optional[float]
    tariff_rate_pct:     Optional[str]   # "5.0%" 表示用
    tariff_type:         Optional[str]
    tariff_notes:        Optional[str]
    import_restrictions: Optional[dict]
    re_export_control:   Optional[dict]
    trade_stats:         Optional[dict]
    data_source:         Optional[str]
    notes:               Optional[str]
    fetched_at:          Optional[datetime]
    created_at:          datetime
    updated_at:          datetime

    class Config:
        from_attributes = True


def _to_out(p: ProductCountryProfile) -> CountryProfileOut:
    def _j(v):
        if v is None:
            return None
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return None
        return v

    rate_pct = None
    if p.tariff_rate is not None:
        rate_pct = f"{p.tariff_rate * 100:.1f}%"

    lr = getattr(p, "license_required", None)
    return CountryProfileOut(
        id=p.id,
        product_id=p.product_id,
        country_code=p.country_code,
        country_name=p.country_name or SUPPORTED_COUNTRIES.get(p.country_code, p.country_code),
        role=p.role,
        role_label=ROLES.get(p.role, p.role),
        local_hs_code=p.local_hs_code,
        local_hs_description=p.local_hs_description,
        local_eccn=getattr(p, "local_eccn", None),
        license_required=lr,
        license_required_label=_LICENSE_LABELS.get(lr) if lr else None,
        tariff_rate=p.tariff_rate,
        tariff_rate_pct=rate_pct,
        tariff_type=p.tariff_type,
        tariff_notes=p.tariff_notes,
        import_restrictions=_j(p.import_restrictions),
        re_export_control=_j(p.re_export_control),
        trade_stats=_j(p.trade_stats),
        data_source=p.data_source,
        notes=p.notes,
        fetched_at=p.fetched_at,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


# ── ヘルパー ───────────────────────────────────────────────────────────────────

def _get_product_or_404(product_id: int, db: Session) -> Product:
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return p


def _get_profile_or_404(profile_id: int, product_id: int, db: Session) -> ProductCountryProfile:
    prof = db.query(ProductCountryProfile).filter_by(id=profile_id, product_id=product_id).first()
    if not prof:
        raise HTTPException(status_code=404, detail="Country profile not found")
    return prof


# ── エンドポイント ─────────────────────────────────────────────────────────────

@router.get("/products/{product_id}/country-profiles", response_model=list[CountryProfileOut])
def list_profiles(product_id: int, db: Session = Depends(get_db)):
    """品目の国別規制プロファイル一覧を返す。"""
    _get_product_or_404(product_id, db)
    profiles = (
        db.query(ProductCountryProfile)
        .filter_by(product_id=product_id)
        .order_by(ProductCountryProfile.country_code)
        .all()
    )
    return [_to_out(p) for p in profiles]


@router.get("/api/country-profiles/supported-countries")
def supported_countries():
    """対応国マスターを返す。"""
    return [
        {"code": code, "name": name, "roles": list(ROLES.items())}
        for code, name in SUPPORTED_COUNTRIES.items()
    ]


@router.post("/products/{product_id}/country-profiles", response_model=CountryProfileOut)
def create_profile(product_id: int, body: CountryProfileIn, db: Session = Depends(get_db)):
    """国別規制プロファイルを追加する。"""
    _get_product_or_404(product_id, db)

    if body.country_code not in SUPPORTED_COUNTRIES:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported country: {body.country_code}. "
                                   f"Supported: {list(SUPPORTED_COUNTRIES.keys())}")
    if body.role not in ROLES:
        raise HTTPException(status_code=400,
                            detail=f"Invalid role: {body.role}. "
                                   f"Valid: {list(ROLES.keys())}")

    def _dump(v):
        return json.dumps(v, ensure_ascii=False) if v is not None else None

    profile = ProductCountryProfile(
        product_id=product_id,
        country_code=body.country_code,
        country_name=SUPPORTED_COUNTRIES[body.country_code],
        role=body.role,
        local_hs_code=body.local_hs_code,
        local_hs_description=body.local_hs_description,
        local_eccn=body.local_eccn,
        license_required=body.license_required,
        tariff_rate=body.tariff_rate,
        tariff_type=body.tariff_type,
        tariff_notes=body.tariff_notes,
        import_restrictions=_dump(body.import_restrictions),
        re_export_control=_dump(body.re_export_control),
        trade_stats=_dump(body.trade_stats),
        notes=body.notes,
        data_source="manual",
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return _to_out(profile)


@router.put("/products/{product_id}/country-profiles/{profile_id}",
            response_model=CountryProfileOut)
def update_profile(
    product_id: int,
    profile_id: int,
    body: CountryProfileIn,
    db: Session = Depends(get_db),
):
    """国別規制プロファイルを更新する。"""
    prof = _get_profile_or_404(profile_id, product_id, db)

    def _dump(v):
        return json.dumps(v, ensure_ascii=False) if v is not None else None

    prof.country_code        = body.country_code
    prof.country_name        = SUPPORTED_COUNTRIES.get(body.country_code, body.country_code)
    prof.role                = body.role
    prof.local_hs_code       = body.local_hs_code
    prof.local_hs_description= body.local_hs_description
    prof.local_eccn          = body.local_eccn
    prof.license_required    = body.license_required
    prof.tariff_rate         = body.tariff_rate
    prof.tariff_type         = body.tariff_type
    prof.tariff_notes        = body.tariff_notes
    prof.import_restrictions = _dump(body.import_restrictions)
    prof.re_export_control   = _dump(body.re_export_control)
    prof.trade_stats         = _dump(body.trade_stats)
    prof.notes               = body.notes
    db.commit()
    db.refresh(prof)
    return _to_out(prof)


@router.delete("/products/{product_id}/country-profiles/{profile_id}")
def delete_profile(product_id: int, profile_id: int, db: Session = Depends(get_db)):
    """国別規制プロファイルを削除する。"""
    prof = _get_profile_or_404(profile_id, product_id, db)
    db.delete(prof)
    db.commit()
    return {"ok": True}
