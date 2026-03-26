"""
関税率自動取得 API。

エンドポイント:
  POST /products/{product_id}/country-profiles/{profile_id}/fetch-tariff
    → WTO / WITS API で MFN 関税率を取得し、プロファイルを更新する。

  GET  /products/{product_id}/country-profiles/fetch-tariff/all
    → 品目配下のすべての国別プロファイルを一括更新（hs_code が設定済みのもの）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Product, ProductCountryProfile
from ..services.tariff_fetch import fetch_tariff
from .country_profiles import _to_out, CountryProfileOut

router = APIRouter()
logger = logging.getLogger(__name__)


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


def _hs6_from(profile: ProductCountryProfile, product: Product) -> Optional[str]:
    """プロファイルのローカルHSコード先頭6桁、なければ品目のグローバルHSコードを返す。"""
    hs = profile.local_hs_code or product.hs_code or ""
    clean = hs.strip().replace(".", "").replace("-", "")
    return clean[:6] if len(clean) >= 6 else None


# ── 単体プロファイル更新 ───────────────────────────────────────────────────────

@router.post(
    "/products/{product_id}/country-profiles/{profile_id}/fetch-tariff",
    response_model=CountryProfileOut,
)
async def fetch_tariff_for_profile(
    product_id:  int,
    profile_id:  int,
    force:       bool    = Query(False, description="キャッシュを無視して再取得"),
    year:        Optional[int] = Query(None, description="取得年（省略時: 前年）"),
    db:          Session = Depends(get_db),
):
    """指定プロファイルの国 × HS6桁で WTO MFN 関税率を取得してプロファイルを更新する。"""
    product = _get_product_or_404(product_id, db)
    prof    = _get_profile_or_404(profile_id, product_id, db)

    hs6 = _hs6_from(prof, product)
    if not hs6:
        raise HTTPException(
            status_code=422,
            detail="HS code (6 digits) is required. Set local_hs_code or product hs_code first.",
        )

    logger.info("Fetching tariff: country=%s hs6=%s year=%s force=%s",
                prof.country_code, hs6, year, force)

    result = await fetch_tariff(
        country_code=prof.country_code,
        hs6=hs6,
        year=year,
        force=force,
    )

    # プロファイル更新
    if result.tariff_rate is not None:
        prof.tariff_rate  = result.tariff_rate
        prof.tariff_type  = result.tariff_type or prof.tariff_type
        prof.tariff_notes = result.tariff_notes
    else:
        # 税率が取れなくてもソース情報だけ更新
        prof.tariff_notes = result.tariff_notes

    prof.data_source = result.source
    prof.fetched_at  = datetime.now(timezone.utc)
    db.commit()
    db.refresh(prof)

    return _to_out(prof)


# ── 品目配下の全プロファイル一括更新 ──────────────────────────────────────────

class BulkFetchResult(CountryProfileOut):
    fetch_status:  str            # "updated" | "no_data" | "no_hs" | "error"
    fetch_message: Optional[str]

    class Config:
        from_attributes = True


@router.post(
    "/products/{product_id}/country-profiles/fetch-tariff/all",
    response_model=list[BulkFetchResult],
)
async def fetch_tariff_all(
    product_id: int,
    force:      bool = Query(False),
    year:       Optional[int] = Query(None),
    db:         Session = Depends(get_db),
):
    """品目配下の全国別プロファイルを一括で関税率更新する。"""
    product  = _get_product_or_404(product_id, db)
    profiles = (
        db.query(ProductCountryProfile)
        .filter_by(product_id=product_id)
        .all()
    )

    results: list[dict] = []
    for prof in profiles:
        hs6 = _hs6_from(prof, product)
        if not hs6:
            results.append({
                **_to_out(prof).model_dump(),
                "fetch_status":  "no_hs",
                "fetch_message": "HSコード未設定のためスキップ",
            })
            continue

        try:
            result = await fetch_tariff(
                country_code=prof.country_code,
                hs6=hs6,
                year=year,
                force=force,
            )
            if result.tariff_rate is not None:
                prof.tariff_rate  = result.tariff_rate
                prof.tariff_type  = result.tariff_type or prof.tariff_type
                prof.tariff_notes = result.tariff_notes
                status, msg = "updated", f"{result.tariff_rate*100:.1f}% ({result.source})"
            else:
                prof.tariff_notes = result.tariff_notes
                status, msg = "no_data", result.tariff_notes

            prof.data_source = result.source
            prof.fetched_at  = datetime.now(timezone.utc)
            db.commit()
            db.refresh(prof)

        except Exception as e:
            logger.exception("Bulk fetch error for profile %d", prof.id)
            db.rollback()
            status, msg = "error", str(e)

        results.append({
            **_to_out(prof).model_dump(),
            "fetch_status":  status,
            "fetch_message": msg,
        })

    return results
