"""
build_nx_graph.py
==================
オントロジーグラフを NetworkX に変換して保存する。

ノード種別:
  ECCN:{code}       例 ECCN:3A001
  HS:{code}         例 HS:854231
  IPC:{prefix}      例 IPC:H01L
  FEFTA:{item_no}   例 FEFTA:7
  CAT:{cat}         例 CAT:3  (0〜9)
  PG:{pg}           例 PG:A   (A〜E)

エッジ種別 (directed):
  hs_to_eccn     HS → ECCN     (confidence_score)
  ipc_to_eccn    IPC → ECCN    (confidence)
  eccn_to_fefta  ECCN → FEFTA  (weight=1.0)
  eccn_to_cat    ECCN → CAT
  eccn_to_pg     ECCN → PG
  cat_related    ECCN → ECCN   (同カテゴリ 技術/装備 間, weight=0.5)
  fterm_to_eccn  FTERM → ECCN  (confidence_score)

出力: data/ontology/nx_graph.gpickle（pickle形式）
      data/ontology/nx_graph_stats.json
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path
from collections import defaultdict

import networkx as nx

_ROOT   = Path(__file__).resolve().parents[1]
_ONT    = _ROOT / "data" / "ontology"
_STAGING = _ROOT / "data" / "staging"

OUT_GRAPH = _ONT / "nx_graph.gpickle"
OUT_STATS = _ONT / "nx_graph_stats.json"

CONFIDENCE_SCORE = {"exact": 1.0, "high": 0.85, "medium": 0.65, "low": 0.40, "inferred": 0.50}


def build_graph() -> nx.DiGraph:
    G = nx.DiGraph()

    graph = json.loads((_ONT / "ontology_graph.json").read_text(encoding="utf-8"))
    eccn_nodes = graph.get("eccn_nodes", {})

    # ── ECCN ノード ──────────────────────────────────────────────────────────
    for eccn, node in eccn_nodes.items():
        G.add_node(
            f"ECCN:{eccn}",
            node_type="ECCN",
            label=eccn,
            category=node.get("category", ""),
            product_group=node.get("product_group", ""),
            label_en=node.get("label_en", "")[:80],
            reason_for_control=node.get("reason_for_control", []),
            patent_count=node.get("patent_count", 0),
            paper_count=node.get("paper_count", 0),
        )

    # ── カテゴリ / 製品グループ ノード ─────────────────────────────────────
    categories = graph.get("eccn_categories", {})
    for cat, info in categories.items():
        G.add_node(f"CAT:{cat}", node_type="CATEGORY",
                   label=cat, label_ja=info.get("label_ja", ""), label_en=info.get("label_en", ""))

    product_groups = graph.get("product_groups", {})
    for pg, info in product_groups.items():
        G.add_node(f"PG:{pg}", node_type="PRODUCT_GROUP",
                   label=pg, label_ja=info.get("label_ja", ""))

    # ECCN → CAT / PG エッジ
    for eccn, node in eccn_nodes.items():
        cat = node.get("category")
        pg  = node.get("product_group")
        if cat and f"CAT:{cat}" in G:
            G.add_edge(f"ECCN:{eccn}", f"CAT:{cat}", edge_type="eccn_to_cat", weight=1.0)
        if pg and f"PG:{pg}" in G:
            G.add_edge(f"ECCN:{eccn}", f"PG:{pg}", edge_type="eccn_to_pg", weight=1.0)

    # ── FEFTA ノード + ECCN → FEFTA エッジ ──────────────────────────────────
    fefta_xref = json.loads((_ONT / "fefta_eccn_xref.json").read_text(encoding="utf-8"))
    fefta_labels = {
        "1": "武器・軍用品", "2": "核弾頭等", "3": "電子応用機器",
        "4": "計算機", "5": "通信機器", "5の2": "情報セキュリティ",
        "6": "センサー・レーザー", "7": "航法計器", "8": "航空機等",
        "9": "推進装置", "10": "工作機械", "11": "先進複合材",
        "12": "有機薬品", "13": "生物剤", "14": "原子炉等",
        "15": "火薬・爆発物", "16": "その他",
    }
    for item in fefta_xref.get("fefta_items", []):
        no = item["fefta_item_no"]
        G.add_node(f"FEFTA:{no}", node_type="FEFTA", label=no,
                   label_ja=fefta_labels.get(no, no),
                   description=item.get("fefta_description", "")[:120])

    eccn_to_fefta = graph.get("eccn_to_fefta", {})
    for eccn, fefta_list in eccn_to_fefta.items():
        for fi in fefta_list:
            src = f"ECCN:{eccn}"
            dst = f"FEFTA:{fi}"
            if src in G and dst in G:
                G.add_edge(src, dst, edge_type="eccn_to_fefta", weight=1.0)

    # ── HS ノード + HS → ECCN エッジ ─────────────────────────────────────────
    hs_to_eccn = graph.get("hs_to_eccn", {})
    for hs_code, mappings in hs_to_eccn.items():
        hs_node = f"HS:{hs_code}"
        if hs_node not in G:
            G.add_node(hs_node, node_type="HS", label=hs_code)
        for m in mappings:
            eccn = m.get("eccn")
            conf = m.get("confidence", "medium")
            score = m.get("confidence_score") or CONFIDENCE_SCORE.get(conf, 0.5)
            dst = f"ECCN:{eccn}"
            if dst in G:
                G.add_edge(hs_node, dst,
                           edge_type="hs_to_eccn",
                           confidence=conf,
                           weight=float(score))

    # ── IPC ノード + IPC → ECCN エッジ ─────────────────────────────────────
    ipc_to_eccn = graph.get("ipc_to_eccn", {})
    for ipc_prefix, info in ipc_to_eccn.items():
        ipc_node = f"IPC:{ipc_prefix}"
        if ipc_node not in G:
            G.add_node(ipc_node, node_type="IPC", label=ipc_prefix)
        for h in info.get("eccns", []):
            eccn = h.get("eccn")
            conf = h.get("confidence", "medium")
            score = CONFIDENCE_SCORE.get(conf, 0.5)
            dst = f"ECCN:{eccn}"
            if dst in G:
                G.add_edge(ipc_node, dst,
                           edge_type="ipc_to_eccn",
                           confidence=conf,
                           weight=float(score))

    # ── F-term ノード + FTERM → ECCN エッジ ─────────────────────────────────
    ft_path = _STAGING / "fterm_eccn_mapping.json"
    if ft_path.exists():
        fterm_data = json.loads(ft_path.read_text(encoding="utf-8"))
        for fterm, mappings in fterm_data.get("fterm_to_eccn_index", {}).items():
            ft_node = f"FTERM:{fterm}"
            if ft_node not in G:
                G.add_node(ft_node, node_type="FTERM", label=fterm)
            for m in mappings:
                eccn = m.get("eccn")
                conf = m.get("confidence", "medium")
                score = CONFIDENCE_SCORE.get(conf, 0.5)
                dst = f"ECCN:{eccn}"
                if dst in G:
                    G.add_edge(ft_node, dst,
                               edge_type="fterm_to_eccn",
                               confidence=conf,
                               rationale=m.get("rationale", ""),
                               weight=float(score))

    # ── 同カテゴリ A/E 間 関連エッジ（技術装備間の技術関係）─────────────────
    # 例: 3A001(装備) → 3E001(技術) は "技術規制" 関係
    cat_pg_groups: dict[str, list[str]] = defaultdict(list)
    for eccn in eccn_nodes:
        node = eccn_nodes[eccn]
        cat = node.get("category", "")
        if cat:
            cat_pg_groups[cat].append(eccn)

    for cat, eccns in cat_pg_groups.items():
        # A(機器) と E(技術) のペア
        a_nodes = [e for e in eccns if e[1] == "A"]
        e_nodes = [e for e in eccns if e[1] == "E"]
        for a in a_nodes[:20]:
            for e in e_nodes[:5]:  # 接続数を制限
                a_node = f"ECCN:{a}"
                e_node = f"ECCN:{e}"
                if a_node in G and e_node in G:
                    G.add_edge(a_node, e_node, edge_type="cat_related",
                               relation="technology_for", weight=0.5)

    return G


def save_stats(G: nx.DiGraph) -> dict:
    node_types: dict[str, int] = defaultdict(int)
    for _, data in G.nodes(data=True):
        node_types[data.get("node_type", "unknown")] += 1

    edge_types: dict[str, int] = defaultdict(int)
    for _, _, data in G.edges(data=True):
        edge_types[data.get("edge_type", "unknown")] += 1

    stats = {
        "_built_at": __import__("datetime").datetime.utcnow().isoformat() + "+00:00",
        "total_nodes": G.number_of_nodes(),
        "total_edges": G.number_of_edges(),
        "nodes_by_type": dict(node_types),
        "edges_by_type": dict(edge_types),
        "is_strongly_connected": nx.is_strongly_connected(G),
        "weakly_connected_components": nx.number_weakly_connected_components(G),
        "avg_out_degree": round(sum(d for _, d in G.out_degree()) / max(G.number_of_nodes(), 1), 2),
    }
    OUT_STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


if __name__ == "__main__":
    print("Building NetworkX graph from ontology data...")
    G = build_graph()

    # pickle 保存
    with open(OUT_GRAPH, "wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)

    stats = save_stats(G)
    print(f"Saved: {OUT_GRAPH}")
    print(f"  Nodes: {stats['total_nodes']} {dict(stats['nodes_by_type'])}")
    print(f"  Edges: {stats['total_edges']} {dict(stats['edges_by_type'])}")
    print(f"  Weakly connected components: {stats['weakly_connected_components']}")
