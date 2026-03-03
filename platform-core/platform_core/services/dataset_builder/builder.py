"""control_nodes.json ビルダー。

処理フロー:
  1. matrix.json → regulation nodes（パーサー）
  2. ai_validation DB → requirement_text・matrix_rule_db_id を enrich
  3. 外為法 XML → regulation nodes に 別表第一 公式テキストを付与
  4. patents.json → patent_index（パーサー）
  5. Explicit edges: regulation node の eccn_explicit から ECCN ノードを生成
  6. BIS CCL PDF → ECCN nodes を説明文で補強
  7. Wassenaar PDF → WA dual-use ノードを生成
  8. Semantic edges: キーワード + IPC ヒントで patent → regulation をリンク
  9. control_nodes.json + build_manifest.json を書き出す

生成ファイル:
  data/unified/control_nodes.json
  data/unified/build_manifest.json
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .parsers.parse_matrix import parse_matrix
from .parsers.parse_patents import parse_patents, _parse_ipc_codes, _ipc_domain_hints, _extract_title_keywords
from .ingest import load_ingested_patents

# ── パス ────────────────────────────────────────────────────────
_PROJECT_ROOT  = pathlib.Path(__file__).parent.parent.parent.parent.parent
_DATA_DIR      = _PROJECT_ROOT / "data"
_UNIFIED_DIR   = _DATA_DIR / "unified"
_MATRIX_PATH   = _DATA_DIR / "matrix.json"
_PATENTS_PATH  = _DATA_DIR / "patents.json"
_AI_VAL_DB     = _PROJECT_ROOT / "modules" / "ai_validation" / "app.db"

# ── ソースデータ パス ────────────────────────────────────────────
_SOURCE_ROOT      = _DATA_DIR / "source"
_FEFTA_RAW_DIR    = _SOURCE_ROOT / "fefta"    / "raw"
_ECCN_RAW_DIR     = _SOURCE_ROOT / "eccn"     / "raw"
_WASSENAAR_RAW_DIR = _SOURCE_ROOT / "wassenaar" / "raw"
_HS_JSON_PATH     = _DATA_DIR / "hs2022_6digit.json"

_SCHEMA_VERSION = "1.1.0"


def _find_file(
    directory: pathlib.Path,
    suffixes: tuple[str, ...],
) -> pathlib.Path | None:
    """ディレクトリ内で指定した拡張子の最初のファイルを返す。"""
    if not directory.is_dir():
        return None
    for f in sorted(directory.iterdir()):
        if f.is_file() and f.suffix.lower() in suffixes:
            return f
    return None


# ── regulation ↔ patent キーワードマッピング ─────────────────────
# regulation node のラベル・説明から検索キーワードを定義する
_REG_KEYWORD_MAP: list[tuple[re.Pattern, list[str], list[str]]] = [
    # (label_pattern, keywords, ipc_hints)
    (re.compile(r"レジスト"),
     ["レジスト", "resist", "photoresist", "EUV", "ArF", "193nm", "フォトレジスト", "露光"],
     ["G03F"]),
    (re.compile(r"集積回路|半導体"),
     ["集積回路", "IC", "semiconductor", "半導体", "CMOS", "FinFET", "GAA"],
     ["H01L"]),
    (re.compile(r"レーザ"),
     ["レーザ", "laser", "光源", "EUV"],
     ["H01S", "G03F"]),
    (re.compile(r"センサー|検出"),
     ["センサー", "sensor", "検出", "photodetector", "イメージセンサー"],
     ["H01L", "G01J"]),
    (re.compile(r"光学|反射"),
     ["光学", "optical", "レンズ", "反射", "optics"],
     ["G02B"]),
    (re.compile(r"通信|マイクロ波|ミリ波"),
     ["通信", "RF", "wireless", "マイクロ波", "5G"],
     ["H04B", "H01Q"]),
    (re.compile(r"暗号"),
     ["暗号", "crypto", "encryption", "セキュリティ"],
     ["H04L", "G06F"]),
    (re.compile(r"成膜|スパッタ|CVD"),
     ["成膜", "CVD", "ALD", "スパッタ", "deposition"],
     ["C23C"]),
    (re.compile(r"爆発|火薬"),
     ["爆発", "火薬", "explosive"],
     ["C06B", "C06C"]),
    (re.compile(r"ナノ|微細"),
     ["ナノ", "nano", "微細", "MEMS"],
     ["B82Y", "B81B"]),
]


def _regulation_keywords(node: dict) -> tuple[list[str], list[str]]:
    """regulation node からキーワードと IPC ヒントを返す。"""
    text = ((node.get("label") or "") + " " +
            (node.get("description") or "") + " " +
            (node.get("requirement_text") or ""))
    keywords: list[str] = []
    ipc_hints: list[str] = []
    for pattern, kws, ipcs in _REG_KEYWORD_MAP:
        if pattern.search(text):
            keywords.extend(kws)
            ipc_hints.extend(ipcs)
    return list(dict.fromkeys(keywords)), list(dict.fromkeys(ipc_hints))


def _patent_score(patent: dict, kws: list[str], ipc_hints: list[str]) -> float:
    """特許と regulation node の意味的スコアを計算する（キーワード + IPC）。"""
    score = 0.0
    text_blob = (
        patent.get("title", "") + " " +
        patent.get("abstract_short", "") + " " +
        " ".join(patent.get("usecase_texts", [])) + " " +
        patent.get("domain_tag", "")
    ).lower()

    # キーワードヒット（各0.1〜0.15）
    hit_count = 0
    for kw in kws:
        if kw.lower() in text_blob:
            score += 0.12
            hit_count += 1

    # IPC コードヒット（各0.2）
    for ipc in ipc_hints:
        if any(p.startswith(ipc) for p in patent.get("ipc_codes", [])):
            score += 0.20

    # ボーナス: domain_hints と keyword の重複
    dh = set(patent.get("domain_hints", []))
    kw_set = set(kws)
    overlap = len(dh & kw_set)
    score += overlap * 0.05

    return min(round(score, 4), 1.0)


def _build_semantic_edges(
    reg_node: dict,
    patent_index: list[dict],
    threshold: float = 0.30,
    top_k: int = 5,
) -> list[dict]:
    """regulation node に対して上位 top_k 件の patent を semantic edge として返す。"""
    kws, ipc_hints = _regulation_keywords(reg_node)
    if not kws and not ipc_hints:
        return []

    scored = []
    for patent in patent_index:
        s = _patent_score(patent, kws, ipc_hints)
        if s >= threshold:
            scored.append((s, patent))

    scored.sort(key=lambda x: x[0], reverse=True)

    edges: list[dict] = []
    for score, patent in scored[:top_k]:
        edges.append({
            "target_type":   "patent",
            "target_id":     patent["id"],
            "score":         score,
            "link_keywords": kws[:5],
            "link_ipc":      ipc_hints,
            "patent_title":  patent["title"][:60],
        })
    return edges


def _build_explicit_edges(reg_node: dict) -> list[dict]:
    """eccn_explicit から explicit edges を生成する。"""
    edges: list[dict] = []
    for eccn in reg_node.get("eccn_explicit", []):
        edges.append({
            "target_type": "eccn",
            "target_id":   eccn,
            "relation":    "fefta_eccn_ref",
            "label":       f"ECCN {eccn} 参照",
        })
    return edges


def _enrich_from_db(nodes: list[dict]) -> None:
    """ai_validation の matrix_rules DB から requirement_text 等を enrich する。"""
    if not _AI_VAL_DB.exists():
        return

    conn = sqlite3.connect(str(_AI_VAL_DB))
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT id, item_no, requirement_text, usage_criteria_text, tech_criteria_text "
            "FROM matrix_rules"
        )
        db_rules = {r[1]: r for r in cur.fetchall()}
    except Exception:
        conn.close()
        return
    conn.close()

    for node in nodes:
        item_no = node.get("item_no", "")
        if item_no in db_rules:
            r = db_rules[item_no]
            node["source_refs"]["matrix_rule_db_id"] = r[0]
            if r[2]:   # requirement_text
                node["requirement_text"] = r[2]
            if r[3]:   # usage_criteria_text
                node["usage_criteria_text"] = r[3]
            if r[4]:   # tech_criteria_text
                node["tech_criteria_text"] = r[4]


def build(
    matrix_path:  pathlib.Path | None = None,
    patents_path: pathlib.Path | None = None,
    output_dir:   pathlib.Path | None = None,
    sem_threshold: float = 0.30,
    sem_top_k:     int   = 5,
) -> dict:
    """control_nodes.json を構築して書き出す。

    Returns:
        build_manifest dict
    """
    matrix_path  = matrix_path  or _MATRIX_PATH
    patents_path = patents_path or _PATENTS_PATH
    output_dir   = output_dir   or _UNIFIED_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc)

    # ── 1. parse regulation nodes ─────────────────────────────
    reg_nodes = parse_matrix(matrix_path)

    # ── 2. enrich from DB ─────────────────────────────────────
    _enrich_from_db(reg_nodes)

    # ── 2b. enrich from 外為法 XML (別表第一) ─────────────────
    fefta_cat_count = 0
    fefta_xml_path = _find_file(_FEFTA_RAW_DIR, (".xml",))
    if fefta_xml_path:
        try:
            from .parsers.parse_fefta_xml import parse_fefta_xml, enrich_nodes_with_fefta_categories
            fefta_categories = parse_fefta_xml(fefta_xml_path)
            fefta_cat_count  = enrich_nodes_with_fefta_categories(reg_nodes, fefta_categories)
        except Exception:
            pass  # XML が壊れていてもビルドは続行

    # ── 3. parse patent index ─────────────────────────────────
    # ベース patents.json を読み込み
    patent_index = parse_patents(patents_path)

    # processed/ に追加特許があればマージ（pub_no で重複排除）
    ingested = load_ingested_patents()
    if ingested:
        existing_ids = {p["id"] for p in patent_index}
        added = 0
        for raw in ingested:
            pub_no = raw.get("publication_number", "")
            if not pub_no or pub_no in existing_ids:
                continue
            ipc_codes    = _parse_ipc_codes(raw.get("ipc_codes_raw", "") or "")
            domain_hints = _ipc_domain_hints(ipc_codes)
            title        = raw.get("title", "") or ""
            usecases     = raw.get("usecases", []) or []
            patent_index.append({
                "id":            pub_no,
                "type":          "patent",
                "title":         title,
                "assignee":      raw.get("assignee", "") or "",
                "abstract_short": (raw.get("abstract", "") or "")[:200],
                "ipc_codes":     ipc_codes,
                "domain_hints":  domain_hints,
                "domain_tag":    raw.get("_domain_tag", "") or "",
                "usecase_texts": [u.get("usecase_text", "") for u in usecases if u.get("usecase_text")][:3],
                "keywords":      list(dict.fromkeys(domain_hints + _extract_title_keywords(title))),
            })
            existing_ids.add(pub_no)
            added += 1

    # ── 4 & 5. build edges ───────────────────────────────────
    sem_edge_count = 0
    exp_edge_count = 0

    for node in reg_nodes:
        # explicit edges (ECCN)
        exp_edges = _build_explicit_edges(node)
        node["edges"]["explicit"] = exp_edges
        exp_edge_count += len(exp_edges)

        # semantic edges (patent links)
        sem_edges = _build_semantic_edges(
            node, patent_index,
            threshold=sem_threshold,
            top_k=sem_top_k,
        )
        node["edges"]["semantic"] = sem_edges
        sem_edge_count += len(sem_edges)

    # ── 6. ECCN stub nodes (explicit targets) ────────────────
    # eccn_explicit で参照された ECCN コードを stub node として追加
    eccn_ids: set[str] = set()
    for node in reg_nodes:
        for e in node["edges"]["explicit"]:
            eccn_ids.add(e["target_id"])

    eccn_nodes: list[dict] = [
        {
            "id":     eccn,
            "type":   "eccn",
            "regime": "ear",
            "label":  eccn,
            "item_no": eccn,
            "description": "",
            "requirement_text": None,
            "edges":  {"explicit": [], "derived": [], "semantic": []},
            "source_refs": {"matrix_json": None},
        }
        for eccn in sorted(eccn_ids)
    ]

    # ── 6b. ECCN nodes を BIS CCL PDF で補強 ─────────────────
    eccn_pdf_count = 0
    eccn_pdf_path  = _find_file(_ECCN_RAW_DIR, (".pdf",))
    if eccn_pdf_path:
        try:
            from .parsers.parse_eccn_pdf import parse_eccn_pdf, enrich_eccn_nodes
            eccn_entries  = parse_eccn_pdf(eccn_pdf_path, max_pages=500)
            eccn_pdf_count = enrich_eccn_nodes(eccn_nodes, eccn_entries)
        except Exception:
            pass

    # ── 6c. Wassenaar nodes 生成 ──────────────────────────────
    wa_nodes: list[dict] = []
    wa_node_count = 0
    wa_pdf_path   = _find_file(_WASSENAAR_RAW_DIR, (".pdf",))
    if wa_pdf_path:
        try:
            from .parsers.parse_wassenaar_pdf import parse_wassenaar_pdf, build_wa_nodes
            wa_entries  = parse_wassenaar_pdf(wa_pdf_path, max_pages=200)
            wa_nodes    = build_wa_nodes(wa_entries)
            wa_node_count = len(wa_nodes)
        except Exception:
            pass

    # ── 6d. HS nodes 生成（近似マッピング） ──────────────────
    hs_nodes: list[dict] = []
    hs_node_count = 0
    if _HS_JSON_PATH.exists():
        try:
            from .parsers.parse_hs_json import parse_hs_json
            hs_nodes      = parse_hs_json(_HS_JSON_PATH)
            hs_node_count = len(hs_nodes)
        except Exception:
            pass

    # ── 7. patent stub nodes ─────────────────────────────────
    # semantic edge で使用された patent を stub として含める
    linked_patent_ids: set[str] = set()
    for node in reg_nodes:
        for e in node["edges"]["semantic"]:
            linked_patent_ids.add(e["target_id"])

    patent_index_map = {p["id"]: p for p in patent_index}
    patent_nodes: list[dict] = []
    for pid in sorted(linked_patent_ids):
        p = patent_index_map.get(pid, {})
        patent_nodes.append({
            "id":         pid,
            "type":       "patent",
            "title":      p.get("title", ""),
            "assignee":   p.get("assignee", ""),
            "ipc_codes":  p.get("ipc_codes", []),
            "domain_tag": p.get("domain_tag", ""),
            "abstract_short": p.get("abstract_short", ""),
            "usecase_texts":  p.get("usecase_texts", []),
            "edges": {"explicit": [], "derived": [], "semantic": []},
            "source_refs": {"patents_json": pid},
        })

    # ── 8. assemble ──────────────────────────────────────────
    all_nodes = reg_nodes + eccn_nodes + patent_nodes + wa_nodes + hs_nodes
    finished_at = datetime.now(timezone.utc)
    duration_s  = (finished_at - started_at).total_seconds()

    # DB enrich stats
    enriched_count = sum(
        1 for n in reg_nodes
        if n.get("source_refs", {}).get("matrix_rule_db_id") is not None
    )
    with_sem = sum(1 for n in reg_nodes if n["edges"]["semantic"])

    manifest: dict[str, Any] = {
        "schema_version":   _SCHEMA_VERSION,
        "generated_at":     finished_at.isoformat(),
        "duration_seconds": round(duration_s, 2),
        "sources": {
            "matrix_json":   str(matrix_path),
            "patents_json":  str(patents_path),
            "ai_val_db":     str(_AI_VAL_DB) if _AI_VAL_DB.exists() else None,
            "fefta_xml":     str(fefta_xml_path) if fefta_xml_path else None,
            "eccn_pdf":      str(eccn_pdf_path)  if eccn_pdf_path  else None,
            "wassenaar_pdf": str(wa_pdf_path)    if wa_pdf_path    else None,
            "hs_json":       str(_HS_JSON_PATH)  if _HS_JSON_PATH.exists() else None,
        },
        "params": {
            "sem_threshold": sem_threshold,
            "sem_top_k":     sem_top_k,
        },
        "stats": {
            "regulation_nodes":     len(reg_nodes),
            "eccn_stub_nodes":      len(eccn_nodes),
            "patent_stub_nodes":    len(patent_nodes),
            "wassenaar_nodes":      wa_node_count,
            "hs_nodes":             hs_node_count,
            "total_nodes":          len(all_nodes),
            "db_enriched":          enriched_count,
            "fefta_xml_enriched":   fefta_cat_count,
            "eccn_pdf_enriched":    eccn_pdf_count,
            "explicit_edges":       exp_edge_count,
            "semantic_edges":       sem_edge_count,
            "nodes_with_sem_link":  with_sem,
            "patent_base":          len(patent_index) - (added if ingested else 0),
            "patent_ingested_added": added if ingested else 0,
            "patent_total":         len(patent_index),
        },
    }

    output: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "generated_at":   finished_at.isoformat(),
        "build_manifest": manifest,
        "nodes":          all_nodes,
        "patent_index":   [
            {"id": p["id"], "title": p["title"], "ipc_codes": p["ipc_codes"],
             "domain_tag": p["domain_tag"]}
            for p in patent_index
        ],
    }

    # 書き出し
    nodes_path    = output_dir / "control_nodes.json"
    manifest_path = output_dir / "build_manifest.json"

    with nodes_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return manifest
