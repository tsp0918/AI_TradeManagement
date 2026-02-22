# app/services/two_list.py
from __future__ import annotations

import json
import re
from typing import Optional, Dict, Any, List, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.db.models.ai_run import AiRun, RunType, MatrixMatch
from app.db.models.matrix import MatrixRule
from app.db.models.transaction import UsageRequirement, UsageSource


# -----------------------------
# Run pick / load helpers
# -----------------------------
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
            "matrix_match の実行履歴（ai_runs）が見つかりません。先に pipeline の matrix_match を実行してください。"
        )
    return run.id


def _get_item_key(rule: MatrixRule) -> str:
    v = getattr(rule, "version", None) or ""
    # item_no がJSON風文字列でも、そのままキーにする（同一ルール集約が目的）
    return f"{rule.regime}::{rule.item_no}::{v}"


def _safe_json_loads(s: Optional[str]) -> Optional[Dict[str, Any]]:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _load_matches(db: Session, run_id: int) -> List[Tuple[MatrixMatch, MatrixRule]]:
    return (
        db.query(MatrixMatch, MatrixRule)
        .join(MatrixRule, MatrixRule.id == MatrixMatch.matrix_rule_id)
        .filter(MatrixMatch.ai_run_id == run_id)
        .all()
    )


def _load_usage_map(db: Session, transaction_id: int) -> Dict[int, UsageRequirement]:
    urs = (
        db.query(UsageRequirement)
        .filter(UsageRequirement.transaction_id == transaction_id)
        .all()
    )
    return {u.id: u for u in urs}


# -----------------------------
# Display helpers (compact UI)
# -----------------------------
_ID_RE_1 = re.compile(r"'id'\s*:\s*'([^']+)'")
_ID_RE_2 = re.compile(r'"id"\s*:\s*"([^"]+)"')


def _extract_item_ids(item_no: Optional[str]) -> List[str]:
    """
    item_no には現在、
      "{'raw': '...', 'norm': '...', 'id': 'EL-3-1'} / {'raw': ... 'id': 'METI-2-1-3'}"
    のような「python dict文字列」が入っていることがある。

    ここから id だけ抜いて UI表示を簡潔にする。
    """
    if not item_no:
        return []
    ids = _ID_RE_1.findall(item_no)
    if not ids:
        ids = _ID_RE_2.findall(item_no)
    # 去重しつつ順序保持
    seen = set()
    out: List[str] = []
    for x in ids:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _compact_item_label(rule: MatrixRule) -> str:
    ids = _extract_item_ids(getattr(rule, "item_no", None))
    if ids:
        return " / ".join(ids)
    # fallback: 長いので頭だけ
    s = (getattr(rule, "item_no", "") or "").strip()
    return s[:80] + ("…" if len(s) > 80 else "")


# 軽いストップワード（2-3gramベースの matched_tokens に混ざりやすいもの）
_STOP = {
    "する", "して", "した", "として", "ため", "用途", "用い", "用い", "用の", "用い",
    "に用", "に用い", "用いる", "いる", "に用", "に用い", "に用いる",
    "工程", "使用", "用", "に", "の", "は", "を", "と",
}


def _compact_matched_tokens(evidence: Optional[Dict[str, Any]], limit: int = 8) -> List[str]:
    """
    evidence_json に matched_tokens がある場合、UI用に見やすく整形。
    """
    if not evidence:
        return []
    toks = evidence.get("matched_tokens") or []
    if not isinstance(toks, list):
        return []
    cleaned: List[str] = []
    seen = set()
    for t in toks:
        if not isinstance(t, str):
            continue
        tt = t.strip()
        if not tt:
            continue
        # 1文字はノイズになりがち（2gram/3gram前提なので基本2以上だが念のため）
        if len(tt) <= 1:
            continue
        if tt in _STOP:
            continue
        if tt in seen:
            continue
        seen.add(tt)
        cleaned.append(tt)
        if len(cleaned) >= limit:
            break
    return cleaned


def compute_two_lists(db: Session, transaction_id: int, run_id: Optional[int] = None) -> Dict[str, Any]:
    """
    2リスト集計（UI向け compact 表示フィールド付き）:
      - core_hit と expanded_hit を item_no 単位で集約
      - A: 両方に出る item（intersection）
      - B: expanded のみに出る item（expanded_only）
    """
    rid = run_id or _pick_latest_matrix_match_run_id(db, transaction_id)

    rows = _load_matches(db, rid)
    usage_map = _load_usage_map(db, transaction_id)

    # 0件なら例外を投げずに空で返す
    if not rows:
        return {
            "transaction_id": transaction_id,
            "run_id": rid,
            "counts": {
                "intersection": 0,
                "expanded_only": 0,
                "total_unique_items": 0,
            },
            "intersection": [],
            "expanded_only": [],
            "note": "このrun_idでは matrix_matches が0件でした（用途要件とマトリクスの語彙が一致しない等）。",
        }

    grouped: Dict[str, Dict[str, Any]] = {}

    for mm, rule in rows:
        key = _get_item_key(rule)
        g = grouped.setdefault(
            key,
            {
                "key": key,
                "regime": rule.regime,
                "rule_id": rule.id,
                "version": getattr(rule, "version", None),

                # --- compact UI fields ---
                "item_ids": _extract_item_ids(getattr(rule, "item_no", None)),
                "item_label": _compact_item_label(rule),
                "rule_summary": (getattr(rule, "requirement_text", "") or "").strip()[:160],

                "title": getattr(rule, "title", None),
                "hits": {"core": [], "expanded": []},
                "max_score": None,
                "best_decision": None,   # hit/maybe/...
            },
        )

        ur = usage_map.get(mm.usage_requirement_id) if getattr(mm, "usage_requirement_id", None) else None
        ur_source = ur.source if ur else None
        ur_text = ur.text if ur else None

        evidence = _safe_json_loads(getattr(mm, "evidence_json", None))
        matched_compact = _compact_matched_tokens(evidence, limit=8)

        decision = getattr(mm, "decision", None)  # NOT NULLの想定

        hit_record = {
            "matrix_match_id": mm.id,
            "usage_requirement_id": getattr(mm, "usage_requirement_id", None),
            "usage_source": ur_source,
            "usage_text": (ur_text or "").strip(),
            "match_score": float(getattr(mm, "match_score", 0.0) or 0.0),
            "match_type": getattr(mm, "match_type", None),
            "decision": decision,

            # compact reason
            "matched_compact": matched_compact,
            "threshold": (evidence or {}).get("scoring", {}).get("threshold"),
            "evidence": evidence,
        }

        score = hit_record["match_score"]
        if g["max_score"] is None or score > g["max_score"]:
            g["max_score"] = score

        # best_decision: hit を最優先（なければ maybe など）
        if decision:
            if g["best_decision"] is None:
                g["best_decision"] = decision
            else:
                # 優先順位: hit > maybe > その他
                pr = {"hit": 2, "maybe": 1}
                if pr.get(decision, 0) > pr.get(g["best_decision"], 0):
                    g["best_decision"] = decision

        mt = (hit_record["match_type"] or "").lower()
        if mt == "core_hit" or (ur and ur.source == UsageSource.core.value):
            g["hits"]["core"].append(hit_record)
        elif mt == "expanded_hit" or (ur and ur.source in (UsageSource.expanded.value, UsageSource.analyst_added.value)):
            g["hits"]["expanded"].append(hit_record)
        else:
            # 不明なら expanded 側へ
            g["hits"]["expanded"].append(hit_record)

    intersection: List[Dict[str, Any]] = []
    expanded_only: List[Dict[str, Any]] = []

    for item in grouped.values():
        has_core = len(item["hits"]["core"]) > 0
        has_exp = len(item["hits"]["expanded"]) > 0
        if has_core and has_exp:
            intersection.append(item)
        elif (not has_core) and has_exp:
            expanded_only.append(item)

    def sort_key(x: Dict[str, Any]):
        score = x["max_score"]
        score_sort = -score if score is not None else 10**9
        # item_label の方が安定して見やすい
        return (score_sort, str(x.get("item_label") or ""))

    intersection.sort(key=sort_key)
    expanded_only.sort(key=sort_key)

    return {
        "transaction_id": transaction_id,
        "run_id": rid,
        "counts": {
            "intersection": len(intersection),
            "expanded_only": len(expanded_only),
            "total_unique_items": len(grouped),
        },
        "intersection": intersection,
        "expanded_only": expanded_only,
    }
