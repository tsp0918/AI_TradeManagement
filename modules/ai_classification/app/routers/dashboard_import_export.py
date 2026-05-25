"""輸出入統合ダッシュボード — ai_classification

GET  /api/dashboard/import-export   JSON 集計データ
GET  /dashboard/import-export       UI ページ
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Product
from ..pg_session import get_pg_db
from platform_core.models.import_profile import ImportProfile
from platform_core.models.supply_chain import SupplyChainEdge, SupplyChainNode

logger = logging.getLogger(__name__)
router = APIRouter(tags=["dashboard_import_export"])
templates = Jinja2Templates(directory="templates")

_IMPORT_TYPES = {"PURCHASED_PART", "RAW_MATERIAL", "INTERNAL_TRANSFER"}
_EXPORT_TYPES = {"FINISHED_GOODS", "BOM_COMPONENT", "SOFTWARE"}
_EAR_CONCERN_ECCN_PREFIXES = ("0A", "0B", "0C", "0D", "0E", "1C", "2B", "3A", "4A", "5A", "5D", "5E", "6A", "7A", "9A")


def _ear_risk(node: SupplyChainNode) -> str:
    if not node.is_us_origin:
        return "none"
    eccn = (node.eccn or "").upper()
    if eccn and eccn != "EAR99":
        return "high"
    return "medium"


@router.get("/api/dashboard/import-export")
async def dashboard_data(
    sqlite_db: Session = Depends(get_db),
    pg_db: AsyncSession = Depends(get_pg_db),
) -> dict[str, Any]:
    """輸出入統合ダッシュボード用集計 API。"""

    # ─── SQLite: Product 集計 ───────────────────────────────────────
    all_products = sqlite_db.query(Product).all()
    total_products = len(all_products)

    by_type: dict[str, int] = {}
    for p in all_products:
        t = p.item_type or "UNKNOWN"
        by_type[t] = by_type.get(t, 0) + 1

    import_products = [p for p in all_products if p.item_type in _IMPORT_TYPES]
    export_products = [p for p in all_products if p.item_type in _EXPORT_TYPES]

    # 未ECCN品目（輸出品）
    no_eccn_export = [p for p in export_products if not p.eccn]
    # 未確認品目
    unconfirmed = [p for p in all_products if p.is_unconfirmed]
    # US原産品目
    us_origin_products = [p for p in all_products if (p.country_of_origin or "").upper() == "US"]

    # ─── PostgreSQL: ImportProfile 集計 ─────────────────────────────
    ip_result = await pg_db.execute(select(ImportProfile))
    profiles = ip_result.scalars().all()

    total_profiles = len(profiles)
    ear_profiles = [p for p in profiles if p.us_reexport_applicable]
    eccn_requested = [p for p in profiles if p.eccn_validation_tx_id]
    eccn_claimed = [p for p in profiles if p.eccn_claimed]
    reexport_triggered = [p for p in profiles if p.reexport_license_id]
    fta_profiles = [p for p in profiles if p.fta_applicable]
    restrictions_checked = [p for p in profiles if p.import_restrictions_checked]

    by_exporter_country: dict[str, int] = {}
    for p in profiles:
        c = p.exporter_country or "不明"
        by_exporter_country[c] = by_exporter_country.get(c, 0) + 1

    # ─── PostgreSQL: SupplyChainNode 集計 ────────────────────────────
    sc_result = await pg_db.execute(select(SupplyChainNode))
    sc_nodes = sc_result.scalars().all()

    total_sc_nodes = len(sc_nodes)
    us_sc_nodes = [n for n in sc_nodes if n.is_us_origin]
    high_risk_sc = [n for n in sc_nodes if _ear_risk(n) == "high"]
    medium_risk_sc = [n for n in sc_nodes if _ear_risk(n) == "medium"]

    # ─── リスクサマリー ───────────────────────────────────────────────
    risk_items = []

    # US EAR 規制品 (ImportProfile)
    for p in ear_profiles:
        if not p.eccn_claimed:
            risk_items.append({
                "level": "high",
                "category": "EAR再輸出",
                "message": f"US原産輸入品 {p.product_code} の ECCN が未設定",
                "action": f"/import-profiles",
            })
        elif not p.reexport_license_id:
            risk_items.append({
                "level": "medium",
                "category": "EAR再輸出",
                "message": f"{p.product_code} (ECCN:{p.eccn_claimed}) の再輸出許可申請が未作成",
                "action": f"/import-profiles",
            })

    # ECCN なし輸出品
    for p in no_eccn_export[:5]:
        risk_items.append({
            "level": "medium",
            "category": "ECCN未設定",
            "message": f"輸出品 {p.code}（{p.name}）の ECCN が未設定",
            "action": f"/products/{p.id}/edit",
        })

    # 未確認品目
    for p in unconfirmed[:3]:
        risk_items.append({
            "level": "low",
            "category": "未確認品目",
            "message": f"{p.code}（{p.name}）が未確認状態",
            "action": f"/products/{p.id}/edit",
        })

    # BOM上の高リスク US EAR ノード
    for n in high_risk_sc[:3]:
        risk_items.append({
            "level": "high",
            "category": "BOM EARリスク",
            "message": f"BOMノード {n.product_code or n.name} が US EAR 規制品 (ECCN:{n.eccn})",
            "action": f"/api/supply-chain/impact/{n.product_code}" if n.product_code else "#",
        })

    return {
        "summary": {
            "total_products": total_products,
            "export_products": len(export_products),
            "import_products": len(import_products),
            "unconfirmed": len(unconfirmed),
            "us_origin_products": len(us_origin_products),
            "no_eccn_export": len(no_eccn_export),
        },
        "import_profiles": {
            "total": total_profiles,
            "ear_reexport_applicable": len(ear_profiles),
            "eccn_requested": len(eccn_requested),
            "eccn_claimed": len(eccn_claimed),
            "reexport_triggered": len(reexport_triggered),
            "fta_applicable": len(fta_profiles),
            "restrictions_checked": len(restrictions_checked),
            "by_exporter_country": dict(sorted(by_exporter_country.items(), key=lambda x: -x[1])),
        },
        "supply_chain": {
            "total_nodes": total_sc_nodes,
            "us_origin_nodes": len(us_sc_nodes),
            "high_risk_nodes": len(high_risk_sc),
            "medium_risk_nodes": len(medium_risk_sc),
        },
        "by_item_type": by_type,
        "risk_items": sorted(risk_items, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["level"]]),
    }


@router.get("/dashboard/import-export", response_class=HTMLResponse)
async def dashboard_ui(
    request: Request,
    sqlite_db: Session = Depends(get_db),
    pg_db: AsyncSession = Depends(get_pg_db),
):
    data = await dashboard_data(sqlite_db=sqlite_db, pg_db=pg_db)
    return templates.TemplateResponse(
        request,
        "dashboard_import_export.html",
        {"data": data},
    )
