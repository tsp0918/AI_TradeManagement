"""
貿易統計自動取得 API。

エンドポイント:
  POST /products/{product_id}/country-profiles/{profile_id}/fetch-trade-stats
    → UN Comtrade v2 API で輸出入実績を取得し、trade_stats フィールドを更新。

  POST /products/{product_id}/country-profiles/fetch-trade-stats/all
    → 品目配下のすべての国別プロファイルを一括更新。

  GET  /products/{product_id}/country-profiles/{profile_id}/trade-stats
    → 保存済み貿易統計を取得（JSON フォーマット済み）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Product, ProductCountryProfile
from ..services.comtrade_fetch import fetch_trade_stats

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Pydantic 出力スキーマ ──────────────────────────────────────────────────────

class TradeFlowOut(BaseModel):
    value_usd: Optional[float]
    qty:       Optional[float]
    qty_unit:  Optional[str]
    partner:   Optional[str]


class TradeStatsOut(BaseModel):
    profile_id:   int
    country_code: str
    hs6:          Optional[str]
    year:         Optional[int]
    exports:      Optional[TradeFlowOut]
    imports:      Optional[TradeFlowOut]
    source:       Optional[str]
    fetched_at:   Optional[datetime]
    fetch_status: str          # "updated" | "no_data" | "no_hs" | "no_key" | "error"
    fetch_message: Optional[str]


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


def _hs6_from(profile: ProductCountryProfile, product: Product) -> Optional[str]:
    hs = profile.local_hs_code or product.hs_code or ""
    clean = hs.strip().replace(".", "").replace("-", "")
    return clean[:6] if len(clean) >= 6 else None


def _parse_trade_stats(prof: ProductCountryProfile) -> dict:
    if not prof.trade_stats:
        return {}
    if isinstance(prof.trade_stats, dict):
        return prof.trade_stats
    try:
        return json.loads(prof.trade_stats)
    except Exception:
        return {}


def _build_out(
    prof:    ProductCountryProfile,
    hs6:     Optional[str],
    status:  str,
    message: Optional[str],
) -> TradeStatsOut:
    stats = _parse_trade_stats(prof)
    exp   = stats.get("exports")
    imp   = stats.get("imports")
    return TradeStatsOut(
        profile_id    = prof.id,
        country_code  = prof.country_code,
        hs6           = hs6,
        year          = stats.get("year"),
        exports       = TradeFlowOut(**exp) if exp else None,
        imports       = TradeFlowOut(**imp) if imp else None,
        source        = stats.get("source") or prof.data_source,
        fetched_at    = prof.fetched_at,
        fetch_status  = status,
        fetch_message = message,
    )


# ── 単体取得 ───────────────────────────────────────────────────────────────────

@router.post(
    "/products/{product_id}/country-profiles/{profile_id}/fetch-trade-stats",
    response_model=TradeStatsOut,
)
async def fetch_trade_stats_for_profile(
    product_id:  int,
    profile_id:  int,
    force:       bool          = Query(False, description="キャッシュを無視して再取得"),
    year:        Optional[int] = Query(None, description="取得年（省略時: 2年前）"),
    db:          Session       = Depends(get_db),
):
    """指定プロファイルの貿易統計を UN Comtrade API で取得してプロファイルを更新する。"""
    product = _get_product_or_404(product_id, db)
    prof    = _get_profile_or_404(profile_id, product_id, db)

    hs6 = _hs6_from(prof, product)
    if not hs6:
        return _build_out(prof, None, "no_hs",
                          "HSコード未設定。local_hs_code または品目のhs_codeを設定してください。")

    result = await fetch_trade_stats(
        country_code=prof.country_code,
        hs6=hs6,
        year=year,
        force=force,
    )

    if result.has_data:
        stats_json = json.dumps(result.to_dict(), ensure_ascii=False)
        prof.trade_stats = stats_json
        prof.fetched_at  = datetime.now(timezone.utc)
        if result.source != "none":
            prof.data_source = result.source
        db.commit()
        db.refresh(prof)
        status  = "updated"
        message = result.message
    elif result.message and "COMTRADE_API_KEY" in result.message:
        status  = "no_key"
        message = result.message
    else:
        status  = "no_data"
        message = result.message

    return _build_out(prof, hs6, status, message)


# ── 一括取得 ───────────────────────────────────────────────────────────────────

@router.post(
    "/products/{product_id}/country-profiles/fetch-trade-stats/all",
    response_model=list[TradeStatsOut],
)
async def fetch_trade_stats_all(
    product_id: int,
    force:      bool          = Query(False),
    year:       Optional[int] = Query(None),
    db:         Session       = Depends(get_db),
):
    """品目配下の全国別プロファイルを一括で貿易統計更新する。"""
    product  = _get_product_or_404(product_id, db)
    profiles = (
        db.query(ProductCountryProfile)
        .filter_by(product_id=product_id)
        .all()
    )

    results: list[TradeStatsOut] = []
    for prof in profiles:
        hs6 = _hs6_from(prof, product)
        if not hs6:
            results.append(_build_out(prof, None, "no_hs", "HSコード未設定のためスキップ"))
            continue

        try:
            result = await fetch_trade_stats(
                country_code=prof.country_code,
                hs6=hs6,
                year=year,
                force=force,
            )
            if result.has_data:
                stats_json = json.dumps(result.to_dict(), ensure_ascii=False)
                prof.trade_stats = stats_json
                prof.fetched_at  = datetime.now(timezone.utc)
                if result.source != "none":
                    prof.data_source = result.source
                db.commit()
                db.refresh(prof)
                results.append(_build_out(prof, hs6, "updated", result.message))
            elif result.message and "COMTRADE_API_KEY" in result.message:
                results.append(_build_out(prof, hs6, "no_key", result.message))
            else:
                results.append(_build_out(prof, hs6, "no_data", result.message))

        except Exception as e:
            logger.exception("Bulk trade stats error for profile %d", prof.id)
            db.rollback()
            results.append(_build_out(prof, hs6, "error", str(e)))

    return results


# ── 保存済み統計の読み取り ────────────────────────────────────────────────────

@router.get(
    "/products/{product_id}/country-profiles/{profile_id}/trade-stats",
    response_model=TradeStatsOut,
)
def get_trade_stats(
    product_id: int,
    profile_id: int,
    db:         Session = Depends(get_db),
):
    """保存済み貿易統計を返す（API 呼び出しなし）。"""
    product = _get_product_or_404(product_id, db)
    prof    = _get_profile_or_404(profile_id, product_id, db)
    hs6     = _hs6_from(prof, product)
    stats   = _parse_trade_stats(prof)
    status  = "saved" if stats else "empty"
    return _build_out(prof, hs6, status, None)
