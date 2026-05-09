"""サプライチェーン管理 API — ai_classification 統合版（SQLite/sync）。

Phase 6A-2: platform-core/routers/supply_chain.py から移管。
データは aicls_supply_chain_node / aicls_supply_chain_edge テーブルに格納。
"""

import json
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AiClsSupplyChainEdge, AiClsSupplyChainNode

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


def _is_us_controlled(node: AiClsSupplyChainNode) -> bool:
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


def _accumulate(node_id: str, qty_factor: float, acc: _Accumulator, db: Session) -> None:
    """BOM ツリーを深さ優先で再帰走査し価値を積算する。"""
    if node_id in acc.visited:
        return
    acc.visited.add(node_id)

    node = db.execute(
        select(AiClsSupplyChainNode).where(AiClsSupplyChainNode.id == node_id)
    ).scalar_one_or_none()
    if node is None:
        return

    edges = db.execute(
        select(AiClsSupplyChainEdge).where(AiClsSupplyChainEdge.parent_node_id == node_id)
    ).scalars().all()

    if not edges:
        if node.unit_value_usd is not None:
            contribution = node.unit_value_usd * qty_factor
            acc.total_value += contribution
            if _is_us_controlled(node):
                ctrl_val = (node.us_controlled_value_usd or node.unit_value_usd) * qty_factor
                acc.us_controlled_value += ctrl_val
                if _eccn_excluded(node.eccn):
                    acc.excluded_items.append({
                        "node_id": node.id,
                        "name": node.name,
                        "eccn": node.eccn,
                        "reason": "AT1管理品（De Minimis 免除不可）",
                    })
    else:
        for edge in edges:
            _accumulate(edge.child_node_id, qty_factor * edge.quantity, acc, db)


# ── Pydantic スキーマ ────────────────────────────────────────────

class NodeCreate(BaseModel):
    name: str
    part_number: str | None = None
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

def _serialize_node(n: AiClsSupplyChainNode, children: list[dict] | None = None) -> dict:
    d = {
        "id": n.id,
        "name": n.name,
        "part_number": n.part_number,
        "node_type": n.node_type,
        "country_of_origin": n.country_of_origin,
        "is_us_origin": n.is_us_origin,
        "hs_code": n.hs_code,
        "eccn": n.eccn,
        "unit_value_usd": n.unit_value_usd,
        "us_controlled_value_usd": n.us_controlled_value_usd,
        "description": n.description,
        "item_id": n.item_id,
        "created_at": n.created_at.isoformat(),
        "updated_at": n.updated_at.isoformat(),
    }
    if children is not None:
        d["children"] = children
    return d


# ── エンドポイント ────────────────────────────────────────────────

@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    nodes = db.execute(select(AiClsSupplyChainNode)).scalars().all()
    by_type: dict[str, int] = {}
    us_nodes = 0
    controlled_nodes = 0
    for n in nodes:
        by_type[n.node_type] = by_type.get(n.node_type, 0) + 1
        if n.is_us_origin:
            us_nodes += 1
        if _is_us_controlled(n):
            controlled_nodes += 1
    edge_count = len(db.execute(select(AiClsSupplyChainEdge)).scalars().all())
    return {
        "total_nodes": len(nodes),
        "total_edges": edge_count,
        "by_type": by_type,
        "us_origin_nodes": us_nodes,
        "us_controlled_nodes": controlled_nodes,
    }


@router.get("/nodes")
def list_nodes(
    q: str | None = Query(None),
    node_type: str | None = Query(None),
    is_us_origin: bool | None = Query(None),
    eccn: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    nodes = db.execute(
        select(AiClsSupplyChainNode).order_by(AiClsSupplyChainNode.updated_at.desc())
    ).scalars().all()
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
    page = filtered[offset: offset + limit]
    return {"total": total, "items": [_serialize_node(n) for n in page]}


@router.post("/nodes", status_code=201)
def create_node(body: NodeCreate, db: Session = Depends(get_db)):
    us_ctrl = body.us_controlled_value_usd
    if us_ctrl is None and body.is_us_origin and body.eccn and body.eccn.upper() != "EAR99":
        us_ctrl = body.unit_value_usd
    node = AiClsSupplyChainNode(
        id=str(uuid.uuid4()),
        tenant_id=body.tenant_id or "default",
        name=body.name,
        part_number=body.part_number,
        node_type=body.node_type,
        country_of_origin=body.country_of_origin,
        is_us_origin=body.is_us_origin,
        hs_code=body.hs_code,
        eccn=body.eccn,
        unit_value_usd=body.unit_value_usd,
        us_controlled_value_usd=us_ctrl,
        description=body.description,
        extra=json.dumps(body.extra) if body.extra else None,
        item_id=body.item_id,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return _serialize_node(node)


@router.get("/nodes/{node_id}")
def get_node(node_id: str, db: Session = Depends(get_db)):
    node = db.execute(
        select(AiClsSupplyChainNode).where(AiClsSupplyChainNode.id == node_id)
    ).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Not found")

    edges = db.execute(
        select(AiClsSupplyChainEdge).where(AiClsSupplyChainEdge.parent_node_id == node.id)
    ).scalars().all()
    children = []
    for edge in edges:
        child = db.execute(
            select(AiClsSupplyChainNode).where(AiClsSupplyChainNode.id == edge.child_node_id)
        ).scalar_one_or_none()
        if child:
            children.append({
                "edge_id": edge.id,
                "quantity": edge.quantity,
                "unit": edge.unit,
                **_serialize_node(child),
            })
    return _serialize_node(node, children=children)


@router.put("/nodes/{node_id}")
def update_node(node_id: str, body: NodeUpdate, db: Session = Depends(get_db)):
    node = db.execute(
        select(AiClsSupplyChainNode).where(AiClsSupplyChainNode.id == node_id)
    ).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Not found")
    for f in ("name", "part_number", "node_type", "country_of_origin", "is_us_origin",
              "hs_code", "eccn", "unit_value_usd", "us_controlled_value_usd", "description"):
        val = getattr(body, f)
        if val is not None:
            setattr(node, f, val)
    if body.extra is not None:
        node.extra = json.dumps(body.extra)
    if body.item_id is not None:
        node.item_id = body.item_id
    if (node.is_us_origin and node.eccn and node.eccn.upper() != "EAR99"
            and node.us_controlled_value_usd is None and node.unit_value_usd is not None):
        node.us_controlled_value_usd = node.unit_value_usd
    db.commit()
    db.refresh(node)
    return _serialize_node(node)


@router.delete("/nodes/{node_id}", status_code=204)
def delete_node(node_id: str, db: Session = Depends(get_db)):
    node = db.execute(
        select(AiClsSupplyChainNode).where(AiClsSupplyChainNode.id == node_id)
    ).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(node)
    db.commit()


@router.get("/nodes/{node_id}/tree")
def get_tree(node_id: str, db: Session = Depends(get_db)):
    """BOM ツリーを再帰展開して返す（最大深さ 10）。"""
    node = db.execute(
        select(AiClsSupplyChainNode).where(AiClsSupplyChainNode.id == node_id)
    ).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Not found")

    def _expand(nid: str, depth: int) -> dict:
        n = db.execute(
            select(AiClsSupplyChainNode).where(AiClsSupplyChainNode.id == nid)
        ).scalar_one_or_none()
        if n is None:
            return {}
        edges = db.execute(
            select(AiClsSupplyChainEdge).where(AiClsSupplyChainEdge.parent_node_id == nid)
        ).scalars().all()
        children = []
        if depth < 10:
            for edge in edges:
                children.append({
                    "edge_id": edge.id,
                    "quantity": edge.quantity,
                    "unit": edge.unit,
                    **_expand(edge.child_node_id, depth + 1),
                })
        return {**_serialize_node(n), "children": children}

    return _expand(node_id, 0)


@router.post("/nodes/{node_id}/de-minimis")
def calc_de_minimis(
    node_id: str,
    destination_country: str | None = Query(None, description="ISO 2-letter 仕向地国コード"),
    db: Session = Depends(get_db),
):
    """EAR §734.4 De Minimis ルール計算。BOM ツリーを再帰走査し US 原産管理品の割合を算出する。"""
    node = db.execute(
        select(AiClsSupplyChainNode).where(AiClsSupplyChainNode.id == node_id)
    ).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Not found")

    acc = _Accumulator()
    _accumulate(node_id, 1.0, acc, db)
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
def create_edge(body: EdgeCreate, db: Session = Depends(get_db)):
    if body.parent_node_id == body.child_node_id:
        raise HTTPException(status_code=400, detail="parent と child が同一ノードです")

    dup = db.execute(
        select(AiClsSupplyChainEdge).where(
            AiClsSupplyChainEdge.parent_node_id == body.parent_node_id,
            AiClsSupplyChainEdge.child_node_id == body.child_node_id,
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail="既に同じ BOM エッジが存在します")

    for nid in (body.parent_node_id, body.child_node_id):
        if db.execute(
            select(AiClsSupplyChainNode).where(AiClsSupplyChainNode.id == nid)
        ).scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail=f"ノード {nid} が存在しません")

    edge = AiClsSupplyChainEdge(
        parent_node_id=body.parent_node_id,
        child_node_id=body.child_node_id,
        quantity=body.quantity,
        unit=body.unit,
    )
    db.add(edge)
    db.commit()
    db.refresh(edge)
    return {
        "id": edge.id,
        "parent_node_id": edge.parent_node_id,
        "child_node_id": edge.child_node_id,
        "quantity": edge.quantity,
        "unit": edge.unit,
    }


@router.delete("/edges/{edge_id}", status_code=204)
def delete_edge(edge_id: int, db: Session = Depends(get_db)):
    edge = db.execute(
        select(AiClsSupplyChainEdge).where(AiClsSupplyChainEdge.id == edge_id)
    ).scalar_one_or_none()
    if edge is None:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(edge)
    db.commit()
