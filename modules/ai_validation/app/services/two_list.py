# app/services/two_list.py
from __future__ import annotations

import json
import re
from typing import Optional, Dict, Any, List, Tuple

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.models.ai_run import AiRun, MatrixMatch, RunType
from app.db.models.matrix import MatrixRule
from app.db.models.transaction import UsageRequirement, UsageSource
from app.services.control_node_lookup import enrich_two_list_item
from app.services.judgment_reason import make_judgment_reason


# ── ソース情報マッピング ──────────────────────────────────────────────────────
_SOURCE_TYPE_TO_REGIME = {
    "law":        "JP_FEFTA",
    "parameter":  "JP_FEFTA",
    "tsutatsu":   "JP_FEFTA",
    "eccn":       "EAR_ECCN",
}

_SOURCE_TYPE_TO_LIST = {
    "law":        "外為法（法令）",
    "parameter":  "外為法（運用通達・省令）",
    "tsutatsu":   "外為法（通達）",
    "eccn":       "EAR/ECCN",
}


# ── Run pick / load helpers ──────────────────────────────────────────────────
def _pick_latest_matrix_match_run_id(db: Session, transaction_id: int) -> int:
    run = (
        db.query(AiRun)
        .filter(
            AiRun.transaction_id == transaction_id,
            AiRun.run_type == RunType.matrix_match.value,
        )
        .order_by(desc(AiRun.id))
        .first()
    )
    if not run:
        raise ValueError(
            "matrix_match の実行履歴（ai_runs）が見つかりません。"
            "先に pipeline の matrix_match を実行してください。"
        )
    return run.id


def _safe_json_loads(s: Optional[str]) -> Optional[Dict[str, Any]]:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _load_matches_with_rule(
    db: Session, run_id: int
) -> List[Tuple[MatrixMatch, MatrixRule]]:
    """旧スキーマ: matrix_rule_id が設定されているレコードを JOIN で取得。"""
    return (
        db.query(MatrixMatch, MatrixRule)
        .join(MatrixRule, MatrixRule.id == MatrixMatch.matrix_rule_id)
        .filter(MatrixMatch.ai_run_id == run_id)
        .all()
    )


def _load_matches_layer_a(db: Session, run_id: int) -> List[MatrixMatch]:
    """Layer A スキーマ: matrix_rule_id=NULL の Layer A マッチを取得。"""
    return (
        db.query(MatrixMatch)
        .filter(
            MatrixMatch.ai_run_id == run_id,
            MatrixMatch.matrix_rule_id.is_(None),
            MatrixMatch.layer_a_faiss_id.isnot(None),
        )
        .all()
    )


def _load_usage_map(db: Session, transaction_id: int) -> Dict[int, UsageRequirement]:
    urs = (
        db.query(UsageRequirement)
        .filter(UsageRequirement.transaction_id == transaction_id)
        .all()
    )
    return {u.id: u for u in urs}


# ── Display helpers ──────────────────────────────────────────────────────────
_ID_RE_1 = re.compile(r"'id'\s*:\s*'([^']+)'")
_ID_RE_2 = re.compile(r'"id"\s*:\s*"([^"]+)"')
_ID_RE_PLAIN = re.compile(r"\b([A-Z]{2,6}-\d+(?:-\d+)*)\b")

_STOP = {
    "する", "して", "した", "として", "ため", "用途", "用い", "用の",
    "工程", "使用", "用", "に", "の", "は", "を", "と",
}


def _extract_item_ids(item_no: Optional[str]) -> List[str]:
    if not item_no:
        return []
    ids = _ID_RE_1.findall(item_no)
    if not ids:
        ids = _ID_RE_2.findall(item_no)
    if not ids:
        ids = _ID_RE_PLAIN.findall(item_no)
    seen: set[str] = set()
    out: List[str] = []
    for x in ids:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _compact_matched_tokens(evidence: Optional[Dict[str, Any]], limit: int = 8) -> List[str]:
    if not evidence:
        return []
    toks = evidence.get("matched_tokens") or []
    if not isinstance(toks, list):
        return []
    cleaned: List[str] = []
    seen: set[str] = set()
    for t in toks:
        if not isinstance(t, str):
            continue
        tt = t.strip()
        if not tt or len(tt) <= 1 or tt in _STOP or tt in seen:
            continue
        seen.add(tt)
        cleaned.append(tt)
        if len(cleaned) >= limit:
            break
    return cleaned


def _hit_record_from_mm(
    mm: MatrixMatch,
    usage_map: Dict[int, UsageRequirement],
    evidence: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    ur = usage_map.get(mm.usage_requirement_id) if getattr(mm, "usage_requirement_id", None) else None
    return {
        "matrix_match_id":     mm.id,
        "usage_requirement_id": getattr(mm, "usage_requirement_id", None),
        "usage_source":        ur.source if ur else None,
        "usage_text":          (ur.text or "").strip() if ur else "",
        "match_score":         float(getattr(mm, "match_score", 0.0) or 0.0),
        "match_type":          getattr(mm, "match_type", None),
        "decision":            getattr(mm, "decision", None),
        "matched_compact":     _compact_matched_tokens(evidence, limit=8),
        "threshold":           (evidence or {}).get("scoring", {}).get("threshold"),
        "evidence":            evidence,
    }


def _resolve_bucket(
    hit: Dict[str, Any],
    ur: Optional[UsageRequirement],
) -> str:
    mt = (hit["match_type"] or "").lower()
    if mt == "core_hit" or (ur and ur.source == UsageSource.core.value):
        return "core"
    return "expanded"


# ── Layer A grouped item builder ─────────────────────────────────────────────
def _layer_a_meta(faiss_id: Optional[int], item_no: Optional[str], source_type: Optional[str]) -> Dict[str, Any]:
    """Layer A メタデータを faiss_e5_service のレコードから引く。"""
    try:
        from platform_core.services import faiss_e5_service as svc
        records = svc._layer_a_records
        if faiss_id is not None and faiss_id < len(records):
            r = records[faiss_id]
            return {
                "item_label":  r.get("item_label", ""),
                "source_name": r.get("source_name", ""),
                "article_no":  r.get("article_no", ""),
                "full_text":   r.get("full_text", ""),
                "eccn":        r.get("eccn", ""),
            }
    except Exception:
        pass
    return {"item_label": "", "source_name": "", "article_no": "", "full_text": "", "eccn": ""}


def _formal_item_label(item_no: str, article_no: str, source_type: str, item_label: str, eccn: str = "") -> str:
    """正式な項番表記を生成する。

    item_no の形式:
      "7"     → 輸出令別表第1の第7の項
      "外為令N" → 外国為替令第N条（輸出令とは別の法令）

    例:
      item_no="7",  source_type="law",  article_no="6"  → 輸出令第7項 貨物等省令第6条（電子部品・集積回路・半導体）
      item_no="7",  source_type="parameter"             → 輸出令第7項（運用通達・省令）（電子部品・集積回路・半導体）
      item_no="外為令6", source_type="law", article_no="18" → 外為令第6条 貨物等省令第18条
    """
    if source_type == "eccn":
        eccn_code = eccn or item_no
        if eccn_code:
            return f"ECCN {eccn_code}"
        return item_label or "ECCN"

    if not item_no:
        return item_label or "規制品目"

    # item_no が「外為令N」形式 → 外国為替令（外為令）
    if item_no.startswith("外為令"):
        n = item_no[3:]  # "外為令6" → "6"
        if source_type == "law" and article_no:
            base = f"外為令第{n}条 貨物等省令第{article_no}条"
        elif source_type == "parameter":
            base = f"外為令第{n}条（運用通達・省令）"
        else:
            base = f"外為令第{n}条"
    else:
        # 純数字: 輸出令別表第1の項番
        if source_type == "law" and article_no:
            base = f"輸出令第{item_no}項 貨物等省令第{article_no}条"
        elif source_type == "parameter":
            base = f"輸出令第{item_no}項（運用通達・省令）"
        elif source_type == "tsutatsu":
            base = f"輸出令第{item_no}項（通達）"
        else:
            base = f"輸出令第{item_no}項"

    if item_label:
        return f"{base}（{item_label}）"
    return base


def _build_grouped_layer_a(
    matches: List[MatrixMatch],
    usage_map: Dict[int, UsageRequirement],
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for mm in matches:
        item_no     = (mm.layer_a_item_no or "").strip()
        source_type = (mm.layer_a_source_type or "").strip()
        faiss_id    = mm.layer_a_faiss_id
        meta        = _layer_a_meta(faiss_id, item_no, source_type)
        article_no  = meta.get("article_no", "")
        eccn_val    = meta.get("eccn", "")
        key         = f"LayerA::{item_no}::{source_type}::{article_no}::{eccn_val}"

        if key not in grouped:
            formal_label = _formal_item_label(item_no, article_no, source_type, meta["item_label"], eccn_val)
            list_name    = _SOURCE_TYPE_TO_LIST.get(source_type, source_type)
            regime       = _SOURCE_TYPE_TO_REGIME.get(source_type, "JP_FEFTA")
            # ECCN の場合は eccn 値を item_ids に格納する
            if source_type == "eccn" and eccn_val:
                display_ids = [eccn_val]
            else:
                display_ids = [item_no] if item_no else []
            grouped[key] = {
                "key":              key,
                "regime":           regime,
                "rule_id":          None,
                "version":          None,
                "item_ids":         display_ids,
                "item_label":       formal_label,
                "item_label_short": eccn_val or meta["item_label"] or item_no,
                "article_no":       article_no,
                "list_name":        list_name,
                "rule_summary":     (meta["full_text"] or "")[:160],
                "title":            formal_label,
                # Layer A 固有
                "layer_a_item_no":       item_no,
                "layer_a_source_type":   source_type,
                "layer_a_article_no":    article_no,
                "hits": {"core": [], "expanded": []},
                "max_score":        None,
                "best_decision":    None,
            }

        g = grouped[key]
        evidence = _safe_json_loads(getattr(mm, "evidence_json", None))
        hit = _hit_record_from_mm(mm, usage_map, evidence)

        score = hit["match_score"]
        if g["max_score"] is None or score > g["max_score"]:
            g["max_score"] = score

        decision = hit["decision"]
        if decision:
            pr = {"hit": 2, "maybe": 1}
            if g["best_decision"] is None or pr.get(decision, 0) > pr.get(g["best_decision"], 0):
                g["best_decision"] = decision

        ur = usage_map.get(mm.usage_requirement_id) if getattr(mm, "usage_requirement_id", None) else None
        g["hits"][_resolve_bucket(hit, ur)].append(hit)

    return grouped


# ── 旧スキーマ grouped item builder（後方互換） ──────────────────────────────
def _get_item_key(rule: MatrixRule) -> str:
    v = getattr(rule, "version", None) or ""
    return f"{rule.regime}::{rule.item_no}::{v}"


def _compact_item_label(rule: MatrixRule) -> str:
    ids = _extract_item_ids(getattr(rule, "item_no", None))
    if ids:
        return " / ".join(ids)
    s = (getattr(rule, "item_no", "") or "").strip()
    return s[:80] + ("…" if len(s) > 80 else "")


def _build_grouped_legacy(
    rows: List[Tuple[MatrixMatch, MatrixRule]],
    usage_map: Dict[int, UsageRequirement],
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for mm, rule in rows:
        key = _get_item_key(rule)
        if key not in grouped:
            grouped[key] = {
                "key":           key,
                "regime":        rule.regime,
                "rule_id":       rule.id,
                "version":       getattr(rule, "version", None),
                "item_ids":      _extract_item_ids(getattr(rule, "item_no", None)),
                "item_label":    _compact_item_label(rule),
                "list_name":     getattr(rule, "list_name", None),
                "rule_summary":  (getattr(rule, "requirement_text", "") or "").strip()[:160],
                "title":         getattr(rule, "title", None),
                "hits": {"core": [], "expanded": []},
                "max_score":     None,
                "best_decision": None,
            }
        g = grouped[key]
        evidence = _safe_json_loads(getattr(mm, "evidence_json", None))
        hit = _hit_record_from_mm(mm, usage_map, evidence)

        score = hit["match_score"]
        if g["max_score"] is None or score > g["max_score"]:
            g["max_score"] = score

        decision = hit["decision"]
        if decision:
            pr = {"hit": 2, "maybe": 1}
            if g["best_decision"] is None or pr.get(decision, 0) > pr.get(g["best_decision"], 0):
                g["best_decision"] = decision

        ur = usage_map.get(mm.usage_requirement_id) if getattr(mm, "usage_requirement_id", None) else None
        g["hits"][_resolve_bucket(hit, ur)].append(hit)
    return grouped


# ── メイン公開関数 ────────────────────────────────────────────────────────────
def compute_two_lists(
    db: Session, transaction_id: int, run_id: Optional[int] = None
) -> Dict[str, Any]:
    """2リスト集計（Layer A 静的インデックス対応版）。

    - matrix_rule_id が NULL → Layer A パス（e5-large 静的 FAISS）
    - matrix_rule_id が非NULL → 旧スキーマ パス（MatrixRule JOIN）
    - 両者をマージして同一出力フォーマットで返す
    """
    rid = run_id or _pick_latest_matrix_match_run_id(db, transaction_id)
    usage_map = _load_usage_map(db, transaction_id)

    # Layer A パス（新スキーマ）
    layer_a_matches = _load_matches_layer_a(db, rid)
    grouped_a = _build_grouped_layer_a(layer_a_matches, usage_map)

    # 旧スキーマパス（後方互換）
    legacy_rows = _load_matches_with_rule(db, rid)
    grouped_legacy = _build_grouped_legacy(legacy_rows, usage_map)

    # マージ（新スキーマが優先）
    grouped = {**grouped_legacy, **grouped_a}

    if not grouped:
        return {
            "transaction_id": transaction_id,
            "run_id": rid,
            "counts": {
                "intersection": 0,
                "expanded_only": 0,
                "core_only": 0,
                "total_unique_items": 0,
            },
            "intersection": [],
            "expanded_only": [],
            "core_only": [],
            "catchall_recommended": True,
            "note": "matrix_matches が0件でした（用途要件と規制テキストが一致しない等）。",
        }

    intersection: List[Dict[str, Any]] = []
    expanded_only: List[Dict[str, Any]] = []
    core_only: List[Dict[str, Any]] = []

    for item in grouped.values():
        has_core = len(item["hits"]["core"]) > 0
        has_exp  = len(item["hits"]["expanded"]) > 0
        if has_core and has_exp:
            intersection.append(item)
        elif (not has_core) and has_exp:
            expanded_only.append(item)
        elif has_core and (not has_exp):
            core_only.append(item)

    def sort_key(x: Dict[str, Any]):
        score = x["max_score"]
        return (-(score or 0.0), str(x.get("item_label") or ""))

    intersection.sort(key=sort_key)
    expanded_only.sort(key=sort_key)
    core_only.sort(key=sort_key)

    # enrich + judgment reason（Layer A には enrich 不要、スキップ）
    for list_type, items in [
        ("intersection", intersection),
        ("core_only", core_only),
        ("expanded_only", expanded_only),
    ]:
        for item in items:
            if item.get("rule_id") is not None:
                # 旧スキーマのみ enrich
                try:
                    enrich_two_list_item(item)
                except Exception:
                    pass
            try:
                item["reason_text"] = make_judgment_reason(item, list_type)
            except Exception:
                item.setdefault("reason_text", "")

    is_low = len(intersection) == 0 and len(core_only) == 0

    return {
        "transaction_id": transaction_id,
        "run_id": rid,
        "counts": {
            "intersection":      len(intersection),
            "expanded_only":     len(expanded_only),
            "core_only":         len(core_only),
            "total_unique_items": len(grouped),
        },
        "intersection":  intersection,
        "expanded_only": expanded_only,
        "core_only":     core_only,
        "catchall_recommended": is_low,
    }
