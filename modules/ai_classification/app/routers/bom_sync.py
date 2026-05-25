"""BOM sync: products.bom_json → plat_supply_chain_node / edge.

同期関数として実装。同期エンドポイント（BOM upload POST）から直接呼べる。
"""

from __future__ import annotations

import json
import uuid
import logging
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from platform_core.models.supply_chain import SupplyChainEdge, SupplyChainNode
from platform_core.models.tenant import Tenant

if TYPE_CHECKING:
    from ..models import Product

logger = logging.getLogger(__name__)


def _get_or_create_default_tenant(pg: Session) -> uuid.UUID:
    tenant = pg.query(Tenant).first()
    if tenant is None:
        tenant = Tenant(name="default", slug="default", plan="standard")
        pg.add(tenant)
        pg.flush()
    return tenant.id


def _upsert_node(
    pg: Session,
    product_code: str,
    name: str,
    node_type: str,
    country_of_origin: str | None,
    hs_code: str | None,
    eccn: str | None,
    unit_value_usd: float | None,
    tenant_id: uuid.UUID,
) -> SupplyChainNode:
    """product_code で既存ノードを検索し、なければ作成・あれば更新。"""
    node = pg.query(SupplyChainNode).filter(
        SupplyChainNode.product_code == product_code
    ).first()
    coo = (country_of_origin or "").upper()[:2] or None
    is_us = coo == "US"
    us_ctrl = unit_value_usd if (is_us and eccn and eccn.upper() not in ("", "EAR99")) else None

    if node is None:
        node = SupplyChainNode(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            product_code=product_code,
            name=name,
            part_number=product_code,
            node_type=node_type,
            country_of_origin=coo,
            is_us_origin=is_us,
            hs_code=hs_code,
            eccn=eccn,
            unit_value_usd=unit_value_usd,
            us_controlled_value_usd=us_ctrl,
        )
        pg.add(node)
        pg.flush()
    else:
        node.name = name
        node.part_number = product_code
        node.node_type = node_type
        node.country_of_origin = coo
        node.is_us_origin = is_us
        node.hs_code = hs_code
        node.eccn = eccn
        node.unit_value_usd = unit_value_usd
        node.us_controlled_value_usd = us_ctrl
        pg.flush()
    return node


def sync_bom_to_supply_chain(product: "Product", pg: Session) -> dict:
    """
    product.bom_json の内容を plat_supply_chain_node/edge に同期する。

    Returns:
        dict: { "parent_node_id": str, "synced_components": int, "skipped": int }
    """
    tenant_id = _get_or_create_default_tenant(pg)

    parent_node = _upsert_node(
        pg=pg,
        product_code=product.code,
        name=product.name,
        node_type="product",
        country_of_origin=product.country_of_origin,
        hs_code=product.hs_code,
        eccn=product.eccn,
        unit_value_usd=float(product.std_price) if product.std_price else None,
        tenant_id=tenant_id,
    )

    bom_items: list[dict] = []
    if product.bom_json:
        try:
            bom_items = json.loads(product.bom_json)
        except Exception:
            bom_items = []

    # 既存のエッジを削除（再同期）
    pg.query(SupplyChainEdge).filter(
        SupplyChainEdge.parent_node_id == parent_node.id
    ).delete(synchronize_session=False)
    pg.flush()

    synced = 0
    skipped = 0

    for item in bom_items:
        if item.get("kind") != "material":
            skipped += 1
            continue

        comp_code = item.get("component_code")
        if not comp_code:
            skipped += 1
            continue

        child_node = _upsert_node(
            pg=pg,
            product_code=comp_code,
            name=item.get("component_name") or comp_code,
            node_type="component",
            country_of_origin=item.get("coo"),
            hs_code=None,
            eccn=None,
            unit_value_usd=item.get("unit_value"),
            tenant_id=tenant_id,
        )

        ratio = float(item.get("ratio") or 1.0)
        edge = SupplyChainEdge(
            parent_node_id=parent_node.id,
            child_node_id=child_node.id,
            quantity=ratio,
            unit="ratio",
        )
        pg.add(edge)
        synced += 1

    pg.commit()
    logger.info(
        "BOM sync: product=%s parent_node=%s synced=%d skipped=%d",
        product.code, str(parent_node.id), synced, skipped,
    )
    return {
        "parent_node_id": str(parent_node.id),
        "synced_components": synced,
        "skipped": skipped,
    }
