"""サプライチェーン管理 API ルーター。

エンドポイント:
- GET  /api/supply-chain/nodes            ノード一覧
- POST /api/supply-chain/nodes            ノード作成
- GET  /api/supply-chain/nodes/{id}       ノード詳細（直接の子リスト付き）
- PUT  /api/supply-chain/nodes/{id}       ノード更新
- DELETE /api/supply-chain/nodes/{id}     ノード削除
- GET  /api/supply-chain/nodes/{id}/tree  BOM ツリー全展開
- POST /api/supply-chain/nodes/{id}/de-minimis  De Minimis 計算（EAR §734.4）
- POST /api/supply-chain/edges            BOM エッジ追加
- DELETE /api/supply-chain/edges/{id}     BOM エッジ削除
- GET  /api/supply-chain/stats            概況サマリー
"""

import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.supply_chain import SupplyChainEdge, SupplyChainNode

router = APIRouter(prefix="/api/supply-chain", tags=["supply_chain"])

# ── De Minimis 定数 (EAR §734.4) ────────────────────────────────

# E:1 国（制裁対象・強化閾値 10%）
_E1_COUNTRIES = {"CU", "IR", "KP", "SY", "SD"}
# De Minimis が完全に使えない AT1 管理 ECCN プレフィックス（カテゴリ 0 系・WMD関連）
_DE_MINIMIS_EXCLUDED_PREFIXES = ("0A", "0B", "0C", "0D", "0E", "2B352")

_THRESHOLD_GENERAL = 25.0   # %
_THRESHOLD_E1      = 10.0   # %


def _de_minimis_threshold(destination_country: str | None) -> float:
    if destination_country and destination_country.upper() in _E1_COUNTRIES:
        return _THRESHOLD_E1
    return _THRESHOLD_GENERAL


def _eccn_excluded(eccn: str | None) -> bool:
    """De Minimis 免除が適用できない ECCN かどうかを判定する。"""
    if not eccn or eccn.upper() == "EAR99":
        return False
    return any(eccn.upper().startswith(p) for p in _DE_MINIMIS_EXCLUDED_PREFIXES)


def _is_us_controlled(node: SupplyChainNode) -> bool:
    """EAR 規制対象の US 原産品かどうかを判定する。"""
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
    visited: set = field(default_factory=set)  # 循環参照防止


async def _accumulate(
    node_id: uuid.UUID,
    qty_factor: float,
    acc: _Accumulator,
    db: AsyncSession,
) -> None:
    """BOM ツリーを深さ優先で再帰的に走査し価値を積算する。"""
    if node_id in acc.visited:
        return
    acc.visited.add(node_id)

    result = await db.execute(
        select(SupplyChainNode).where(SupplyChainNode.id == node_id)
    )
    node = result.scalar_one_or_none()
    if node is None:
        return

    # 子ノードがある場合は再帰、葉ノードの場合は価値を積算
    edges_result = await db.execute(
        select(SupplyChainEdge).where(SupplyChainEdge.parent_node_id == node_id)
    )
    edges = edges_result.scalars().all()

    if not edges:
        # 葉ノード: 価値を積算
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
            await _accumulate(
                edge.child_node_id,
                qty_factor * edge.quantity,
                acc,
                db,
            )
        # 親ノード自体の価値は子ノードの積算値で表現されるためスキップ

    # 中間ノードでも直接価値が設定されている場合は加算
    if edges and node.unit_value_usd is not None:
        pass  # 中間ノードの価値は子の合計で代表させる設計


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
    item_id: str | None = None  # plat_item.id との正式紐付け


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
    item_id: str | None = None  # plat_item.id との正式紐付け


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


# ── ヘルパ: デフォルトテナント ────────────────────────────────────

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
async def get_stats(db: AsyncSession = Depends(get_db)):
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
    db: AsyncSession = Depends(get_db),
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
    page = filtered[offset: offset + limit]
    return {"total": total, "items": [_serialize_node(n) for n in page]}


@router.post("/nodes", status_code=201)
async def create_node(body: NodeCreate, db: AsyncSession = Depends(get_db)):
    tid = uuid.UUID(body.tenant_id) if body.tenant_id else await _default_tenant_id(db)
    # is_us_origin=True かつ eccn が EAR99 以外なら us_controlled_value_usd を自動設定
    us_ctrl = body.us_controlled_value_usd
    if us_ctrl is None and body.is_us_origin and body.eccn and body.eccn.upper() != "EAR99":
        us_ctrl = body.unit_value_usd
    node = SupplyChainNode(
        tenant_id=tid,
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
        extra=body.extra,
        item_id=uuid.UUID(body.item_id) if body.item_id else None,
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)
    return _serialize_node(node)


@router.get("/nodes/{node_id}")
async def get_node(node_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SupplyChainNode).where(SupplyChainNode.id == uuid.UUID(node_id))
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Not found")

    # 直接の子ノードを取得
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
async def update_node(node_id: str, body: NodeUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SupplyChainNode).where(SupplyChainNode.id == uuid.UUID(node_id))
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Not found")
    for f in ("name", "part_number", "node_type", "country_of_origin", "is_us_origin",
              "hs_code", "eccn", "unit_value_usd", "us_controlled_value_usd",
              "description", "extra"):
        val = getattr(body, f)
        if val is not None:
            setattr(node, f, val)
    if body.item_id is not None:
        node.item_id = uuid.UUID(body.item_id) if body.item_id else None
    # us_controlled_value_usd 自動補完
    if node.is_us_origin and node.eccn and node.eccn.upper() != "EAR99" \
            and node.us_controlled_value_usd is None and node.unit_value_usd is not None:
        node.us_controlled_value_usd = node.unit_value_usd
    await db.commit()
    await db.refresh(node)
    return _serialize_node(node)


@router.delete("/nodes/{node_id}", status_code=204)
async def delete_node(node_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SupplyChainNode).where(SupplyChainNode.id == uuid.UUID(node_id))
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(node)
    await db.commit()


@router.get("/nodes/{node_id}/tree")
async def get_tree(node_id: str, db: AsyncSession = Depends(get_db), _depth: int = 0):
    """BOM ツリーを再帰展開して返す（最大深さ 10）。"""
    if _depth > 10:
        return {"error": "max depth exceeded"}
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
    db: AsyncSession = Depends(get_db),
):
    """EAR §734.4 De Minimis ルール計算。

    BOM ツリーを再帰走査し、US 原産管理品の割合を算出する。
    割合が閾値（一般 25%、E:1 国 10%）未満であれば De Minimis 適用可能。
    """
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
        note = ""
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
async def create_edge(body: EdgeCreate, db: AsyncSession = Depends(get_db)):
    parent_id = uuid.UUID(body.parent_node_id)
    child_id = uuid.UUID(body.child_node_id)

    # 自己参照チェック
    if parent_id == child_id:
        raise HTTPException(status_code=400, detail="parent と child が同一ノードです")

    # 重複チェック
    dup = await db.execute(
        select(SupplyChainEdge).where(
            SupplyChainEdge.parent_node_id == parent_id,
            SupplyChainEdge.child_node_id == child_id,
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="既に同じ BOM エッジが存在します")

    # 両ノードの存在確認
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
async def delete_edge(edge_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SupplyChainEdge).where(SupplyChainEdge.id == edge_id)
    )
    edge = result.scalar_one_or_none()
    if edge is None:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(edge)
    await db.commit()
