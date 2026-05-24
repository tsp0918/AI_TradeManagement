"""サプライチェーン管理 API — ai_classification 統合版。

Phase 6A-2: platform-core から移管。データは plat_supply_chain_* テーブル（共有 PostgreSQL）。
platform_core.models をそのまま利用し、screening と同じ共有 DB 接続パターンを採用。
"""

import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..pg_session import get_pg_db
from ..pg_session_sync import get_pg_db_sync
from ..database import SessionLocal
from ..models import Product as _Product
from .bom_sync import sync_bom_to_supply_chain as _sync_bom
from platform_core.models.supply_chain import SupplyChainEdge, SupplyChainNode

router = APIRouter(prefix="/api/supply-chain", tags=["supply_chain"])

# ── De Minimis 定数 (EAR §734.4) ────────────────────────────────

_E1_COUNTRIES = {"CU", "IR", "KP", "SY", "SD"}
_DE_MINIMIS_EXCLUDED_PREFIXES = ("0A", "0B", "0C", "0D", "0E", "2B352")
_THRESHOLD_GENERAL = 25.0
_THRESHOLD_E1 = 10.0


def _de_minimis_threshold(destination_country: str | None) -> float:
    if destination_country and destination_country.upper() in _E1_COUNTRIES:
        return _THRESHOLD_E1
    return _THRESHOLD_GENERAL


def _eccn_excluded(eccn: str | None) -> bool:
    if not eccn or eccn.upper() == "EAR99":
        return False
    return any(eccn.upper().startswith(p) for p in _DE_MINIMIS_EXCLUDED_PREFIXES)


def _is_us_controlled(node: SupplyChainNode) -> bool:
    if not node.is_us_origin:
        return False
    if not node.eccn or node.eccn.upper() == "EAR99":
        return False
    return True


# ── De Minimis BOM ツリー再帰計算 ───────────────────────────────

@dataclass
class _Accumulator:
    total_value: float = 0.0
    us_controlled_value: float = 0.0
    excluded_items: list[dict] = field(default_factory=list)
    visited: set = field(default_factory=set)


async def _accumulate(
    node_id: uuid.UUID,
    qty_factor: float,
    acc: _Accumulator,
    db: AsyncSession,
) -> None:
    if node_id in acc.visited:
        return
    acc.visited.add(node_id)

    result = await db.execute(
        select(SupplyChainNode).where(SupplyChainNode.id == node_id)
    )
    node = result.scalar_one_or_none()
    if node is None:
        return

    edges_result = await db.execute(
        select(SupplyChainEdge).where(SupplyChainEdge.parent_node_id == node_id)
    )
    edges = edges_result.scalars().all()

    if not edges:
        if node.unit_value_usd is not None:
            contribution = node.unit_value_usd * qty_factor
            acc.total_value += contribution
            if _is_us_controlled(node):
                ctrl_val = (node.us_controlled_value_usd or node.unit_value_usd) * qty_factor
                acc.us_controlled_value += ctrl_val
                if _eccn_excluded(node.eccn):
                    acc.excluded_items.append({
                        "node_id": str(node.id),
                        "name": node.name,
                        "eccn": node.eccn,
                        "reason": "AT1管理品（De Minimis 免除不可）",
                    })
    else:
        for edge in edges:
            await _accumulate(edge.child_node_id, qty_factor * edge.quantity, acc, db)


# ── Pydantic スキーマ ────────────────────────────────────────────

class NodeCreate(BaseModel):
    name: str
    part_number: str | None = None
    product_code: str | None = None
    node_type: str = "component"
    country_of_origin: str | None = None
    is_us_origin: bool = False
    hs_code: str | None = None
    eccn: str | None = None
    unit_value_usd: float | None = None
    us_controlled_value_usd: float | None = None
    description: str | None = None
    extra: dict | None = None
    tenant_id: str | None = None
    item_id: str | None = None


class NodeUpdate(BaseModel):
    name: str | None = None
    part_number: str | None = None
    product_code: str | None = None
    node_type: str | None = None
    country_of_origin: str | None = None
    is_us_origin: bool | None = None
    hs_code: str | None = None
    eccn: str | None = None
    unit_value_usd: float | None = None
    us_controlled_value_usd: float | None = None
    description: str | None = None
    extra: dict | None = None
    item_id: str | None = None


class EdgeCreate(BaseModel):
    parent_node_id: str
    child_node_id: str
    quantity: float = 1.0
    unit: str = "each"


# ── シリアライザ ────────────────────────────────────────────────

def _serialize_node(n: SupplyChainNode, children: list[dict] | None = None) -> dict:
    d = {
        "id": str(n.id),
        "name": n.name,
        "part_number": n.part_number,
        "product_code": n.product_code,
        "node_type": n.node_type,
        "country_of_origin": n.country_of_origin,
        "is_us_origin": n.is_us_origin,
        "hs_code": n.hs_code,
        "eccn": n.eccn,
        "unit_value_usd": n.unit_value_usd,
        "us_controlled_value_usd": n.us_controlled_value_usd,
        "description": n.description,
        "item_id": str(n.item_id) if n.item_id else None,
        "created_at": n.created_at.isoformat(),
        "updated_at": n.updated_at.isoformat(),
    }
    if children is not None:
        d["children"] = children
    return d


async def _default_tenant_id(db: AsyncSession) -> uuid.UUID:
    from platform_core.models.tenant import Tenant
    result = await db.execute(select(Tenant).limit(1))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(name="default", slug="default", plan="standard")
        db.add(tenant)
        await db.flush()
    return tenant.id


# ── エンドポイント ────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_pg_db)):
    result = await db.execute(select(SupplyChainNode))
    nodes = result.scalars().all()
    by_type: dict[str, int] = {}
    us_nodes = 0
    controlled_nodes = 0
    for n in nodes:
        by_type[n.node_type] = by_type.get(n.node_type, 0) + 1
        if n.is_us_origin:
            us_nodes += 1
        if _is_us_controlled(n):
            controlled_nodes += 1
    edge_result = await db.execute(select(SupplyChainEdge))
    edge_count = len(edge_result.scalars().all())
    return {
        "total_nodes": len(nodes),
        "total_edges": edge_count,
        "by_type": by_type,
        "us_origin_nodes": us_nodes,
        "us_controlled_nodes": controlled_nodes,
    }


@router.get("/nodes")
async def list_nodes(
    q: str | None = Query(None),
    node_type: str | None = Query(None),
    is_us_origin: bool | None = Query(None),
    eccn: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_pg_db),
):
    result = await db.execute(
        select(SupplyChainNode).order_by(SupplyChainNode.updated_at.desc())
    )
    nodes = result.scalars().all()
    filtered = []
    for n in nodes:
        if q and q.lower() not in n.name.lower() and q.lower() not in (n.part_number or "").lower():
            continue
        if node_type and n.node_type != node_type:
            continue
        if is_us_origin is not None and n.is_us_origin != is_us_origin:
            continue
        if eccn and (n.eccn or "").upper() != eccn.upper():
            continue
        filtered.append(n)
    total = len(filtered)
    return {"total": total, "items": [_serialize_node(n) for n in filtered[offset: offset + limit]]}


@router.post("/nodes", status_code=201)
async def create_node(body: NodeCreate, db: AsyncSession = Depends(get_pg_db)):
    tid = uuid.UUID(body.tenant_id) if body.tenant_id else await _default_tenant_id(db)
    us_ctrl = body.us_controlled_value_usd
    if us_ctrl is None and body.is_us_origin and body.eccn and body.eccn.upper() != "EAR99":
        us_ctrl = body.unit_value_usd
    node = SupplyChainNode(
        tenant_id=tid,
        name=body.name,
        part_number=body.part_number,
        product_code=body.product_code,
        node_type=body.node_type,
        country_of_origin=body.country_of_origin,
        is_us_origin=body.is_us_origin,
        hs_code=body.hs_code,
        eccn=body.eccn,
        unit_value_usd=body.unit_value_usd,
        us_controlled_value_usd=us_ctrl,
        description=body.description,
        extra=body.extra,
        item_id=uuid.UUID(body.item_id) if body.item_id else None,
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)
    return _serialize_node(node)


@router.get("/nodes/{node_id}")
async def get_node(node_id: str, db: AsyncSession = Depends(get_pg_db)):
    result = await db.execute(
        select(SupplyChainNode).where(SupplyChainNode.id == uuid.UUID(node_id))
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Not found")

    edges_result = await db.execute(
        select(SupplyChainEdge).where(SupplyChainEdge.parent_node_id == node.id)
    )
    children = []
    for edge in edges_result.scalars().all():
        c_result = await db.execute(
            select(SupplyChainNode).where(SupplyChainNode.id == edge.child_node_id)
        )
        child = c_result.scalar_one_or_none()
        if child:
            children.append({
                "edge_id": edge.id,
                "quantity": edge.quantity,
                "unit": edge.unit,
                **_serialize_node(child),
            })
    return _serialize_node(node, children=children)


@router.put("/nodes/{node_id}")
async def update_node(node_id: str, body: NodeUpdate, db: AsyncSession = Depends(get_pg_db)):
    result = await db.execute(
        select(SupplyChainNode).where(SupplyChainNode.id == uuid.UUID(node_id))
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Not found")
    for f in ("name", "part_number", "product_code", "node_type", "country_of_origin", "is_us_origin",
              "hs_code", "eccn", "unit_value_usd", "us_controlled_value_usd",
              "description", "extra"):
        val = getattr(body, f)
        if val is not None:
            setattr(node, f, val)
    if body.item_id is not None:
        node.item_id = uuid.UUID(body.item_id) if body.item_id else None
    if (node.is_us_origin and node.eccn and node.eccn.upper() != "EAR99"
            and node.us_controlled_value_usd is None and node.unit_value_usd is not None):
        node.us_controlled_value_usd = node.unit_value_usd
    await db.commit()
    await db.refresh(node)
    return _serialize_node(node)


@router.delete("/nodes/{node_id}", status_code=204)
async def delete_node(node_id: str, db: AsyncSession = Depends(get_pg_db)):
    result = await db.execute(
        select(SupplyChainNode).where(SupplyChainNode.id == uuid.UUID(node_id))
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(node)
    await db.commit()


@router.get("/nodes/{node_id}/tree")
async def get_tree(node_id: str, db: AsyncSession = Depends(get_pg_db)):
    """BOM ツリーを再帰展開して返す（最大深さ 10）。"""
    result = await db.execute(
        select(SupplyChainNode).where(SupplyChainNode.id == uuid.UUID(node_id))
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Not found")

    async def _expand(nid: uuid.UUID, depth: int) -> dict:
        r = await db.execute(select(SupplyChainNode).where(SupplyChainNode.id == nid))
        n = r.scalar_one_or_none()
        if n is None:
            return {}
        er = await db.execute(
            select(SupplyChainEdge).where(SupplyChainEdge.parent_node_id == nid)
        )
        edges = er.scalars().all()
        children = []
        if depth < 10:
            for edge in edges:
                child_tree = await _expand(edge.child_node_id, depth + 1)
                children.append({
                    "edge_id": edge.id,
                    "quantity": edge.quantity,
                    "unit": edge.unit,
                    **child_tree,
                })
        return {**_serialize_node(n), "children": children}

    return await _expand(uuid.UUID(node_id), 0)


@router.post("/nodes/{node_id}/de-minimis")
async def calc_de_minimis(
    node_id: str,
    destination_country: str | None = Query(None, description="ISO 2-letter 仕向地国コード"),
    db: AsyncSession = Depends(get_pg_db),
):
    """EAR §734.4 De Minimis ルール計算。"""
    result = await db.execute(
        select(SupplyChainNode).where(SupplyChainNode.id == uuid.UUID(node_id))
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Not found")

    acc = _Accumulator()
    await _accumulate(uuid.UUID(node_id), 1.0, acc, db)
    threshold = _de_minimis_threshold(destination_country)

    if acc.total_value == 0:
        us_pct = 0.0
        eligible = True
        note = "価値情報が未入力のため計算不可。各ノードに unit_value_usd を設定してください。"
    else:
        us_pct = round(acc.us_controlled_value / acc.total_value * 100, 2)
        eligible = us_pct < threshold and len(acc.excluded_items) == 0
        if acc.excluded_items:
            note = "AT1管理品を含むため De Minimis 免除は適用不可（EAR §734.4(b)）。"
        elif not eligible:
            note = f"US 原産管理品比率 {us_pct}% が閾値 {threshold}% を超過。許可申請が必要です。"
        else:
            note = f"US 原産管理品比率 {us_pct}% < 閾値 {threshold}%。De Minimis 適用可能。"

    return {
        "node_id": node_id,
        "node_name": node.name,
        "destination_country": destination_country,
        "threshold_pct": threshold,
        "total_value_usd": round(acc.total_value, 4),
        "us_controlled_value_usd": round(acc.us_controlled_value, 4),
        "us_controlled_pct": us_pct,
        "de_minimis_eligible": eligible,
        "excluded_items": acc.excluded_items,
        "note": note,
    }


@router.post("/edges", status_code=201)
async def create_edge(body: EdgeCreate, db: AsyncSession = Depends(get_pg_db)):
    parent_id = uuid.UUID(body.parent_node_id)
    child_id = uuid.UUID(body.child_node_id)

    if parent_id == child_id:
        raise HTTPException(status_code=400, detail="parent と child が同一ノードです")

    dup = await db.execute(
        select(SupplyChainEdge).where(
            SupplyChainEdge.parent_node_id == parent_id,
            SupplyChainEdge.child_node_id == child_id,
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="既に同じ BOM エッジが存在します")

    for nid in (parent_id, child_id):
        r = await db.execute(select(SupplyChainNode).where(SupplyChainNode.id == nid))
        if r.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail=f"ノード {nid} が存在しません")

    edge = SupplyChainEdge(
        parent_node_id=parent_id,
        child_node_id=child_id,
        quantity=body.quantity,
        unit=body.unit,
    )
    db.add(edge)
    await db.commit()
    await db.refresh(edge)
    return {
        "id": edge.id,
        "parent_node_id": str(edge.parent_node_id),
        "child_node_id": str(edge.child_node_id),
        "quantity": edge.quantity,
        "unit": edge.unit,
    }


@router.delete("/edges/{edge_id}", status_code=204)
async def delete_edge(edge_id: int, db: AsyncSession = Depends(get_pg_db)):
    result = await db.execute(
        select(SupplyChainEdge).where(SupplyChainEdge.id == edge_id)
    )
    edge = result.scalar_one_or_none()
    if edge is None:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(edge)
    await db.commit()


# ── BOM 統合エンドポイント ──────────────────────────────────────────

@router.get("/by-product-code/{product_code}")
async def get_by_product_code(product_code: str, db: AsyncSession = Depends(get_pg_db)):
    """品目コードに紐づく SupplyChainNode ツリーを返す（Phase IV-1 可視化基盤）。"""
    result = await db.execute(
        select(SupplyChainNode).where(SupplyChainNode.product_code == product_code)
    )
    parent = result.scalar_one_or_none()
    if parent is None:
        return {"product_code": product_code, "node": None, "children": []}

    edges_result = await db.execute(
        select(SupplyChainEdge).where(SupplyChainEdge.parent_node_id == parent.id)
    )
    edges = edges_result.scalars().all()

    children = []
    for edge in edges:
        child_result = await db.execute(
            select(SupplyChainNode).where(SupplyChainNode.id == edge.child_node_id)
        )
        child = child_result.scalar_one_or_none()
        if child:
            children.append({
                **_serialize_node(child),
                "quantity": edge.quantity,
                "unit": edge.unit,
            })

    return {
        "product_code": product_code,
        "node": _serialize_node(parent),
        "children": children,
    }


@router.post("/sync-from-bom/{product_id}", status_code=200)
def sync_from_bom(product_id: int):
    """
    品目IDを指定して bom_json → plat_supply_chain_node/edge を手動同期する。
    BOM upload 後は自動同期されるが、既存データの移行やデバッグ目的に使用。
    """
    sqlite_db = SessionLocal()
    try:
        product = sqlite_db.get(_Product, product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        pg = get_pg_db_sync()
        try:
            result = _sync_bom(product, pg)
        finally:
            pg.close()
        return {"product_id": product_id, "product_code": product.code, **result}
    finally:
        sqlite_db.close()


# ── Phase III-2: 輸入品影響分析 ───────────────────────────────────

@router.get("/impact/{component_code}")
async def get_impact_analysis(
    component_code: str,
    us_origin_only: bool = Query(False, description="US原産品のみ対象"),
    db: AsyncSession = Depends(get_pg_db),
):
    """
    指定した部品コード（輸入品・購入品）を BOM に含む完成品一覧を返す。
    「この US 原産購入品を使う完成品はどれか」を逆引き可能。

    Phase III-3（US EAR 再輸出チェック）のベースにもなる。
    """
    # 対象コンポーネントノードを取得
    comp_result = await db.execute(
        select(SupplyChainNode).where(SupplyChainNode.product_code == component_code)
    )
    comp_node = comp_result.scalar_one_or_none()

    if comp_node is None:
        return {
            "component_code": component_code,
            "component_node": None,
            "affected_products": [],
            "note": "このコードの SupplyChainNode が見つかりません。BOM同期を実行してください。",
        }

    if us_origin_only and not comp_node.is_us_origin:
        return {
            "component_code": component_code,
            "component_node": _serialize_node(comp_node),
            "affected_products": [],
            "note": "US原産フラグが立っていません。",
        }

    # このコンポーネントを子に持つエッジを取得（= このコンポーネントを使う親製品）
    edges_result = await db.execute(
        select(SupplyChainEdge).where(SupplyChainEdge.child_node_id == comp_node.id)
    )
    edges = edges_result.scalars().all()

    affected = []
    for edge in edges:
        parent_result = await db.execute(
            select(SupplyChainNode).where(SupplyChainNode.id == edge.parent_node_id)
        )
        parent = parent_result.scalar_one_or_none()
        if parent is None:
            continue

        # EAR再輸出リスク評価
        is_us_ctrl = comp_node.is_us_origin and comp_node.eccn and comp_node.eccn.upper() not in ("", "EAR99")
        ear_reexport_risk = "high" if is_us_ctrl else ("medium" if comp_node.is_us_origin else "low")

        affected.append({
            **_serialize_node(parent),
            "bom_quantity": edge.quantity,
            "bom_unit": edge.unit,
            "ear_reexport_risk": ear_reexport_risk,
        })

    return {
        "component_code": component_code,
        "component_node": _serialize_node(comp_node),
        "affected_products": affected,
        "total_affected": len(affected),
    }
