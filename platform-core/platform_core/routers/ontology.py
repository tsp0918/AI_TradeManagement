"""
ontology.py — 輸出管理オントロジー HTTP エンドポイント

オントロジーグラフを使って ECCN / HS / IPC / 外為法別表 の
相互変換・関係展開・説明チェーン生成を提供する内部 API。
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ontology", tags=["ontology"])

_ROOT    = Path(__file__).resolve().parents[3]
_ONT_DIR = _ROOT / "data" / "ontology"


# ──────────────────────────────────────────────────────────────────────────────
# グラフロード（起動時に一度だけ）
# ──────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_graph() -> dict:
    path = _ONT_DIR / "ontology_graph.json"
    if not path.exists():
        logger.warning("ontology_graph.json not found: %s", path)
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    logger.info(
        "Ontology graph loaded: ECCN=%d, IPC=%d, HS=%d",
        len(data.get("eccn_nodes", {})),
        len(data.get("ipc_to_eccn", {})),
        len(data.get("hs_to_eccn", {})),
    )
    return data


@lru_cache(maxsize=1)
def _load_hierarchy() -> dict:
    path = _ONT_DIR / "eccn_hierarchy.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _load_fefta_xref() -> dict:
    path = _ONT_DIR / "fefta_eccn_xref.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic モデル
# ──────────────────────────────────────────────────────────────────────────────

class ECCNNode(BaseModel):
    eccn: str
    label_en: str
    category: str
    category_label_ja: str
    product_group: str
    product_group_label_ja: str
    reason_for_control: list[str]
    fefta_item_nos: list[str]
    patent_count: int
    paper_count: int
    relations: dict[str, Any]


class OntologyLookupResponse(BaseModel):
    query: str
    query_type: str
    eccns: list[ECCNNode]
    explanation_chain: list[str]
    fefta_items: list[dict]


class StatsResponse(BaseModel):
    eccn_nodes: int
    ipc_mappings: int
    hs_eccn_mappings: int
    fefta_items: int
    eccns_with_patents: int
    eccns_with_papers: int
    eccns_with_fefta: int
    eccns_with_ipc: int
    eccns_with_hs: int


class ExpandResponse(BaseModel):
    eccn: str
    node: dict
    ipc_prefixes: list[dict]
    hs_codes: list[dict]
    fefta_items: list[dict]
    layer_b_patents: int
    layer_d_papers: int
    explanation_chain: list[str]


# ──────────────────────────────────────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────────────────────────────────────

_FEFTA_ITEM_LABELS: dict[str, str] = {
    "1": "武器・軍用品", "2": "核弾頭等", "3": "電子応用機器",
    "4": "計算機", "5": "通信機器", "5の2": "情報セキュリティ",
    "6": "センサー・レーザー", "7": "航法計器", "8": "航空機等",
    "9": "推進装置", "10": "工作機械", "11": "先進複合材",
    "12": "有機薬品", "13": "生物剤", "14": "原子炉等",
    "15": "火薬・爆発物", "16": "その他",
}


def _eccn_to_node(eccn: str, graph: dict) -> dict | None:
    return graph.get("eccn_nodes", {}).get(eccn)


def _build_explanation_chain(
    query: str,
    query_type: str,
    eccn: str,
    graph: dict,
    node: dict | None = None,
) -> list[str]:
    """query → ECCN → 外為法 → Layer B/D の説明チェーンを生成する。"""
    chain: list[str] = []
    if not node:
        node = _eccn_to_node(eccn, graph)
    if not node:
        return [f"{query} → ECCN {eccn} の詳細情報が見つかりません"]

    # ステップ1: 入力 → ECCN
    if query_type == "hs":
        conf_records = graph.get("hs_to_eccn", {}).get(query, [])
        conf = next((r["confidence"] for r in conf_records if r["eccn"] == eccn), "medium")
        conf_label = {"exact": "完全一致", "high": "高信頼", "medium": "中信頼",
                      "low": "低信頼", "inferred": "推定"}.get(conf, conf)
        chain.append(f"HS {query} → ECCN {eccn}（{conf_label}マッピング）")
    elif query_type == "ipc":
        chain.append(f"IPC {query} → ECCN {eccn}（IPC-ECCN マッピング）")
    elif query_type == "eccn":
        chain.append(f"ECCN {eccn}")
    else:
        chain.append(f"{query} → ECCN {eccn}")

    # ステップ2: ECCN → カテゴリ説明
    cat = node.get("category", "")
    pg  = node.get("product_group", "")
    label_en = node.get("label_en", "")
    cat_ja   = node.get("category_label_ja", "")
    pg_ja    = node.get("product_group_label_ja", "")
    chain.append(f"  カテゴリ {cat}（{cat_ja}）/ 製品グループ {pg}（{pg_ja}）: {label_en[:80]}")

    # ステップ3: 統制理由
    rfcs = node.get("reason_for_control", [])
    if rfcs:
        rfc_labels = {"NS": "国家安全保障", "MT": "ミサイル技術", "NP": "核不拡散",
                      "AT": "テロ対策", "CB": "化学・生物兵器", "RS": "地域安定", "EI": "暗号"}
        rfc_str = "・".join(rfc_labels.get(r, r) for r in rfcs)
        chain.append(f"  統制理由: {rfc_str}")

    # ステップ4: 外為法別表
    fefta_items = node.get("fefta_item_nos", [])
    if fefta_items:
        fefta_str = "、".join(
            f"第{i}項（{_FEFTA_ITEM_LABELS.get(i, i)}）" for i in fefta_items
        )
        chain.append(f"  外為法 輸出令別表第1: {fefta_str}")
        chain.append("  → 輸出許可申請が必要な場合があります")
    else:
        chain.append("  外為法別表: 対比なし（EAR-only または確認要）")

    # ステップ5: エビデンス
    patents = node.get("patent_count", 0)
    papers  = node.get("paper_count", 0)
    evidence_parts = []
    if patents:
        evidence_parts.append(f"関連特許 {patents}件（Layer B）")
    if papers:
        evidence_parts.append(f"学術論文 {papers}件（Layer D）")
    if evidence_parts:
        chain.append(f"  エビデンス: {' / '.join(evidence_parts)}")

    return chain


# ──────────────────────────────────────────────────────────────────────────────
# エンドポイント
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/stats", response_model=StatsResponse)
def get_stats() -> StatsResponse:
    """オントロジーグラフの統計情報を返す。"""
    graph = _load_graph()
    if not graph:
        raise HTTPException(503, "Ontology graph not loaded")
    return StatsResponse(**graph.get("stats", {}))


@router.get("/lookup", response_model=OntologyLookupResponse)
def lookup(
    q: str = Query(..., description="ECCN / HS コード / IPC コードを入力"),
    top_k: int = Query(5, ge=1, le=20),
) -> OntologyLookupResponse:
    """
    ECCN / HS / IPC を入力として、関連 ECCN ノードと説明チェーンを返す。

    - q=3A001      → ECCN 直接ルックアップ
    - q=854231     → HS コード → ECCN 変換
    - q=H01L       → IPC コード → ECCN 変換
    """
    graph = _load_graph()
    if not graph:
        raise HTTPException(503, "Ontology graph not loaded")

    q = q.strip().upper()
    eccn_nodes_raw = graph.get("eccn_nodes", {})
    query_type = "unknown"
    eccn_list: list[str] = []

    # 入力タイプ判定
    if q in eccn_nodes_raw:
        # ECCN 直接
        query_type = "eccn"
        eccn_list = [q]
    elif re.match(r"^\d{4,10}$", q.replace(".", "").replace(" ", "")):
        # HS コード（数字6-10桁）
        query_type = "hs"
        hs_clean = q.replace(".", "").replace(" ", "")
        hs_hits = graph.get("hs_to_eccn", {}).get(hs_clean, [])
        eccn_list = [h["eccn"] for h in sorted(hs_hits, key=lambda x: -x.get("confidence_score", 0))][:top_k]
    elif re.match(r"^[A-Z]\d{2}", q):
        # IPC コード（例: H01L, G01S）
        query_type = "ipc"
        ipc_hits = graph.get("ipc_to_eccn", {}).get(q[:4], {}).get("eccns", [])
        eccn_list = [h["eccn"] for h in ipc_hits][:top_k]
        if not eccn_list:
            # prefix match
            for ipc_key, ipc_val in graph.get("ipc_to_eccn", {}).items():
                if q.startswith(ipc_key) or ipc_key.startswith(q):
                    for h in ipc_val.get("eccns", []):
                        if h["eccn"] not in eccn_list:
                            eccn_list.append(h["eccn"])
                    if len(eccn_list) >= top_k:
                        break
    else:
        # 部分一致でECCN検索
        query_type = "partial"
        q_lower = q.lower()
        for eccn, node in eccn_nodes_raw.items():
            if (q_lower in eccn.lower() or
                    q_lower in node.get("label_en", "").lower()):
                eccn_list.append(eccn)
            if len(eccn_list) >= top_k:
                break

    # FEFTA 参照ロード
    fefta_xref = _load_fefta_xref()
    fefta_items_map = {r["fefta_item_no"]: r for r in fefta_xref.get("fefta_items", [])}

    nodes_out: list[ECCNNode] = []
    all_fefta: list[dict] = []
    all_chains: list[str] = []

    for eccn in eccn_list[:top_k]:
        raw = eccn_nodes_raw.get(eccn)
        if not raw:
            continue
        nodes_out.append(ECCNNode(
            eccn=eccn,
            label_en=raw.get("label_en", ""),
            category=raw.get("category", ""),
            category_label_ja=raw.get("category_label_ja", ""),
            product_group=raw.get("product_group", ""),
            product_group_label_ja=raw.get("product_group_label_ja", ""),
            reason_for_control=raw.get("reason_for_control", []),
            fefta_item_nos=raw.get("fefta_item_nos", []),
            patent_count=raw.get("patent_count", 0),
            paper_count=raw.get("paper_count", 0),
            relations=raw.get("relations", {}),
        ))
        chain = _build_explanation_chain(q, query_type, eccn, graph, raw)
        all_chains.extend(chain)
        for fi in raw.get("fefta_item_nos", []):
            if fi in fefta_items_map and fi not in [x.get("fefta_item_no") for x in all_fefta]:
                all_fefta.append({
                    "fefta_item_no": fi,
                    "fefta_label": fefta_items_map[fi].get("fefta_label", fi),
                    "description": fefta_items_map[fi].get("fefta_description", ""),
                    "catch_all_applicable": fefta_items_map[fi].get("catch_all_applicable", False),
                })

    return OntologyLookupResponse(
        query=q,
        query_type=query_type,
        eccns=nodes_out,
        explanation_chain=all_chains,
        fefta_items=all_fefta,
    )


@router.get("/expand/{eccn}", response_model=ExpandResponse)
def expand_eccn(eccn: str) -> ExpandResponse:
    """
    ECCN を起点にグラフを展開。
    IPC・HS コード・外為法別表・Layer B 特許数・Layer D 論文数を一括取得。
    """
    graph = _load_graph()
    if not graph:
        raise HTTPException(503, "Ontology graph not loaded")

    eccn = eccn.strip().upper()
    node = graph.get("eccn_nodes", {}).get(eccn)
    if not node:
        raise HTTPException(404, f"ECCN {eccn} not found in ontology")

    fefta_xref = _load_fefta_xref()
    fefta_items_map = {r["fefta_item_no"]: r for r in fefta_xref.get("fefta_items", [])}

    ipc_list = graph.get("eccn_to_ipc", {}).get(eccn, [])
    hs_list  = graph.get("eccn_to_hs", {}).get(eccn, [])
    fefta_nos = node.get("fefta_item_nos", [])
    fefta_items = [
        {
            "fefta_item_no": fi,
            "fefta_label": fefta_items_map.get(fi, {}).get("fefta_label", fi),
            "description": fefta_items_map.get(fi, {}).get("fefta_description", ""),
            "catch_all_applicable": fefta_items_map.get(fi, {}).get("catch_all_applicable", False),
            "ear99_possible": fefta_items_map.get(fi, {}).get("ear99_possible", False),
        }
        for fi in fefta_nos
    ]

    chain = _build_explanation_chain(eccn, "eccn", eccn, graph, node)

    return ExpandResponse(
        eccn=eccn,
        node=node,
        ipc_prefixes=ipc_list,
        hs_codes=hs_list,
        fefta_items=fefta_items,
        layer_b_patents=node.get("patent_count", 0),
        layer_d_papers=node.get("paper_count", 0),
        explanation_chain=chain,
    )


@router.get("/fefta/{item_no}")
def fefta_item(item_no: str) -> dict:
    """外為法別表 item_no に対応する ECCN リストと詳細を返す。"""
    fefta_xref = _load_fefta_xref()
    items = {r["fefta_item_no"]: r for r in fefta_xref.get("fefta_items", [])}
    if item_no not in items:
        raise HTTPException(404, f"外為法別表第1 第{item_no}項 not found")
    return items[item_no]


@router.get("/categories")
def list_categories() -> dict:
    """ECCN カテゴリ（0-9）と製品グループ（A-E）の一覧を返す。"""
    graph = _load_graph()
    return {
        "categories": graph.get("eccn_categories", {}),
        "product_groups": graph.get("product_groups", {}),
        "reason_for_control": graph.get("reason_for_control_labels", {}),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Country Control Matrix
# ──────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_country_matrix() -> dict:
    path = _ONT_DIR / "country_control_matrix.json"
    if not path.exists():
        logger.warning("country_control_matrix.json not found: %s", path)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class LicenseCheckResponse(BaseModel):
    eccn: str
    country: str
    country_name_ja: str
    country_group: str
    fefta_zone: str
    reasons_for_control: list[str]
    applicable_exceptions: list[str]
    verdict: str
    verdict_label: str
    rationale: list[str]
    recommended_actions: list[str]
    explanation_chain: list[str]


@router.get("/license-check", response_model=LicenseCheckResponse)
def license_check(
    eccn: str = Query(..., description="ECCN コード（例: 3A001）"),
    country: str = Query(..., description="仕向国 ISO 3166-1 alpha-2（例: CN）"),
    end_user_type: str = Query("commercial", description="エンドユーザー種別: commercial / government / military / unknown"),
) -> LicenseCheckResponse:
    """
    ECCN × 仕向国 → ライセンス要否判定。

    - verdict: NLR / EXCEPTION / LICENSE_REQUIRED / PROHIBITED
    - EAR Country Group + 外為法 FEFTA ゾーン + 説明チェーンを返す
    """
    import sys, pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[3] / "platform-core"))
    from platform_core.ontology.rules.ear_reason_engine import (
        evaluate_ear, EARContext,
    )

    matrix = _load_country_matrix()
    country_up = country.strip().upper()

    ctx = EARContext(
        transaction_id=0,
        destination_country=country_up,
        eccn=eccn.strip().upper(),
        end_user_type=end_user_type,
    )
    judgment = evaluate_ear(ctx)

    # 国情報
    country_info = matrix.get("countries", {}).get(country_up, {})
    country_name_ja = country_info.get("name_ja", country_up)
    fefta_zone = country_info.get("fefta_zone", "普通地域")

    # 説明チェーン
    graph = _load_graph()
    eccn_up = eccn.strip().upper()
    ont_chain: list[str] = []
    if graph:
        ont_chain = _build_explanation_chain(eccn_up, "eccn", eccn_up, graph)

    explanation_chain = [
        f"[EAR] 仕向国 {country_up}（{country_name_ja}）→ Country Group {judgment.country_group}",
        f"[FEFTA] 外為法 仕向地ゾーン: {fefta_zone}",
    ] + ont_chain + judgment.rationale

    return LicenseCheckResponse(
        eccn=judgment.eccn or eccn_up,
        country=country_up,
        country_name_ja=country_name_ja,
        country_group=judgment.country_group,
        fefta_zone=fefta_zone,
        reasons_for_control=judgment.reasons_for_control,
        applicable_exceptions=judgment.applicable_exceptions,
        verdict=judgment.verdict,
        verdict_label=judgment.verdict_label,
        rationale=judgment.rationale,
        recommended_actions=judgment.recommended_actions,
        explanation_chain=explanation_chain,
    )


@router.get("/countries")
def list_countries(
    group: str = Query(None, description="フィルタ: A / B / D:1 / E:1"),
    fefta_zone: str = Query(None, description="フィルタ: white / 普通地域 / 懸念地域 / 禁輸地域"),
) -> dict:
    """主要国のカントリーグループ・外為法ゾーン一覧を返す。"""
    matrix = _load_country_matrix()
    countries = matrix.get("countries", {})
    if group:
        countries = {k: v for k, v in countries.items() if v.get("country_group") == group}
    if fefta_zone:
        countries = {k: v for k, v in countries.items() if v.get("fefta_zone") == fefta_zone}
    return {
        "total": len(countries),
        "countries": countries,
        "country_groups": matrix.get("country_groups", {}),
    }


# ──────────────────────────────────────────────────────────────────────────────
# NetworkX 多ホップ推論
# ──────────────────────────────────────────────────────────────────────────────

import pickle  # noqa: E402
import re      # noqa: E402


@lru_cache(maxsize=1)
def _load_nx_graph():
    """NetworkX DiGraph をメモリにロードする（起動時1回のみ）。"""
    try:
        import networkx as nx  # noqa: F401 - 存在確認
    except ImportError:
        logger.warning("networkx not installed; graph endpoints disabled")
        return None

    path = _ONT_DIR / "nx_graph.gpickle"
    if not path.exists():
        logger.warning("nx_graph.gpickle not found: %s", path)
        return None
    with open(path, "rb") as f:
        G = pickle.load(f)
    logger.info("NX graph loaded: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())
    return G


def _normalize_node_id(raw: str) -> str:
    """
    ユーザー入力を正規ノードID に変換する。
    - "3A001" → "ECCN:3A001"
    - "HS:854231" → "HS:854231"（そのまま）
    - "854231" → "HS:854231"
    - "H01L" → "IPC:H01L"
    - "FEFTA:7" / "7" （外為法）は "FEFTA:7"
    - "FTERM:5F048" → "FTERM:5F048"
    """
    s = raw.strip().upper()
    for prefix in ("ECCN:", "HS:", "IPC:", "FEFTA:", "FTERM:", "CAT:", "PG:"):
        if s.startswith(prefix):
            return s
    # 数字のみ → HS
    if re.match(r"^\d{4,10}$", s.replace(".", "").replace(" ", "")):
        return f"HS:{s.replace('.','').replace(' ','')}"
    # ECCN パターン (1文字+1文字+3桁)
    if re.match(r"^[0-9][A-E]\d{3}", s):
        return f"ECCN:{s}"
    # IPC パターン (A-H + 2桁 + A-Z)
    if re.match(r"^[A-H]\d{2}[A-Z]", s):
        return f"IPC:{s[:4]}"
    # F-term パターン
    if re.match(r"^\d[A-Z]\d{3}$", s) or re.match(r"^[A-Z]\d{2}[A-Z]\d", s):
        return f"FTERM:{s}"
    return s


class PathStep(BaseModel):
    node_id: str
    node_type: str
    label: str
    label_ja: str = ""
    edge_to_next: str = ""
    edge_weight: float = 1.0


class PathResponse(BaseModel):
    from_node: str
    to_node: str
    path_length: int
    path: list[PathStep]
    undirected_fallback: bool = False


class NeighborNode(BaseModel):
    node_id: str
    node_type: str
    label: str
    label_ja: str = ""
    depth: int
    edge_type: str = ""
    edge_weight: float = 1.0


class NeighborsResponse(BaseModel):
    center: str
    depth: int
    total_neighbors: int
    neighbors: list[NeighborNode]


def _node_label_ja(G, node_id: str) -> str:
    data = G.nodes.get(node_id, {})
    return (data.get("label_ja") or data.get("description", "")[:40]
            or data.get("label_en", "")[:40] or "")


def _step_from_node(G, node_id: str, edge_type: str = "", weight: float = 1.0) -> PathStep:
    data = G.nodes.get(node_id, {})
    return PathStep(
        node_id=node_id,
        node_type=data.get("node_type", "unknown"),
        label=data.get("label", node_id.split(":")[-1]),
        label_ja=_node_label_ja(G, node_id),
        edge_to_next=edge_type,
        edge_weight=weight,
    )


@router.get("/path", response_model=PathResponse)
def graph_path(
    from_node: str = Query(..., description="起点ノード（例: HS:854231 / 3A001 / H01L）"),
    to_node: str   = Query(..., description="終点ノード（例: FEFTA:7 / ECCN:3A001）"),
) -> PathResponse:
    """
    2ノード間の最短パスを返す。

    例:
    - from=HS:854231&to=FEFTA:7  → HS:854231 → ECCN:3A001 → FEFTA:7
    - from=IPC:H01L&to=FEFTA:3  → IPC:H01L → ECCN:3A001 → FEFTA:3
    - from=3A001&to=FEFTA:7     → ECCN:3A001 → FEFTA:7
    """
    import networkx as nx

    G = _load_nx_graph()
    if G is None:
        raise HTTPException(503, "NetworkX graph not loaded")

    src = _normalize_node_id(from_node)
    dst = _normalize_node_id(to_node)

    if src not in G:
        raise HTTPException(404, f"ノード '{src}' がグラフに見つかりません")
    if dst not in G:
        raise HTTPException(404, f"ノード '{dst}' がグラフに見つかりません")

    undirected = False
    try:
        raw_path = nx.shortest_path(G, src, dst, weight=None)
    except nx.NetworkXNoPath:
        # 有向グラフで到達不能 → 無向グラフで再試行
        try:
            raw_path = nx.shortest_path(G.to_undirected(), src, dst, weight=None)
            undirected = True
        except nx.NetworkXNoPath:
            raise HTTPException(404, f"'{src}' から '{dst}' へのパスが見つかりません")

    steps: list[PathStep] = []
    for i, node_id in enumerate(raw_path):
        edge_type = ""
        weight = 1.0
        if i < len(raw_path) - 1:
            edge_data = G.get_edge_data(raw_path[i], raw_path[i + 1]) or {}
            if not edge_data and undirected:
                edge_data = G.get_edge_data(raw_path[i + 1], raw_path[i]) or {}
            edge_type = edge_data.get("edge_type", "")
            weight = edge_data.get("weight", 1.0)
        steps.append(_step_from_node(G, node_id, edge_type, weight))

    return PathResponse(
        from_node=src,
        to_node=dst,
        path_length=len(raw_path) - 1,
        path=steps,
        undirected_fallback=undirected,
    )


@router.get("/neighbors", response_model=NeighborsResponse)
def graph_neighbors(
    node: str  = Query(..., description="中心ノード（例: ECCN:3A001 / 3A001 / FEFTA:7）"),
    depth: int = Query(1, ge=1, le=4, description="探索深さ（1〜4）"),
    edge_types: str = Query(None, description="カンマ区切りでエッジ種別をフィルタ（例: hs_to_eccn,ipc_to_eccn）"),
) -> NeighborsResponse:
    """
    指定ノードの周辺ノードを深さ優先で返す。

    例:
    - node=ECCN:3A001&depth=1  → 直接繋がる HS/IPC/FEFTA/CAT/PG
    - node=ECCN:3A001&depth=2  → 2ホップ先まで
    - node=FEFTA:7&depth=1&edge_types=eccn_to_fefta  → FEFTA:7 に繋がる ECCN 一覧
    """
    import networkx as nx

    G = _load_nx_graph()
    if G is None:
        raise HTTPException(503, "NetworkX graph not loaded")

    center = _normalize_node_id(node)
    if center not in G:
        raise HTTPException(404, f"ノード '{center}' がグラフに見つかりません")

    allowed_edge_types = set(edge_types.split(",")) if edge_types else None

    # BFS（有向・逆方向両方をたどる）
    neighbors: list[NeighborNode] = []
    visited = {center}
    queue = [(center, 0)]

    ug = G.to_undirected(as_view=True)  # 双方向探索

    while queue:
        cur, cur_depth = queue.pop(0)
        if cur_depth >= depth:
            continue
        for nbr in ug.neighbors(cur):
            if nbr in visited:
                continue
            visited.add(nbr)
            # エッジデータ（有向/逆有向どちらか）
            edge_data = G.get_edge_data(cur, nbr) or G.get_edge_data(nbr, cur) or {}
            et = edge_data.get("edge_type", "")
            if allowed_edge_types and et not in allowed_edge_types:
                continue
            ndata = G.nodes.get(nbr, {})
            neighbors.append(NeighborNode(
                node_id=nbr,
                node_type=ndata.get("node_type", "unknown"),
                label=ndata.get("label", nbr.split(":")[-1]),
                label_ja=_node_label_ja(G, nbr),
                depth=cur_depth + 1,
                edge_type=et,
                edge_weight=edge_data.get("weight", 1.0),
            ))
            queue.append((nbr, cur_depth + 1))

    return NeighborsResponse(
        center=center,
        depth=depth,
        total_neighbors=len(neighbors),
        neighbors=neighbors,
    )


@router.get("/graph/stats")
def graph_stats() -> dict:
    """NetworkX グラフの統計情報を返す。"""
    path = _ONT_DIR / "nx_graph_stats.json"
    if not path.exists():
        raise HTTPException(503, "グラフ統計ファイルが見つかりません")
    return json.loads(path.read_text(encoding="utf-8"))
