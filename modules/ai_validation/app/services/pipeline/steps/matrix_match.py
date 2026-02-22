# app/services/pipeline/steps/matrix_match.py
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

from sqlalchemy.orm import Session
from sqlalchemy import inspect

from app.db.models.matrix import MatrixRule
from app.db.models.ai_run import MatrixMatch
from app.db.models.transaction import UsageRequirement


# -----------------------------
# File / path helpers
# -----------------------------
def _project_root() -> str:
    # this file: app/services/pipeline/steps/matrix_match.py
    here = os.path.abspath(os.path.dirname(__file__))
    # steps -> pipeline -> services -> app -> PROJECT_ROOT
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


def _read_matrix_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _safe_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    # dict/list etc -> JSON string
    try:
        return json.dumps(x, ensure_ascii=False)
    except Exception:
        return str(x)


def _flatten_matrix_json_to_rules(doc: Dict[str, Any], regime: str) -> List[Dict[str, Any]]:
    """
    schema_version 0.2-normalized の export_items を DB用 MatrixRule rows に flatten
    - item_no: export_order_ref + meti_order_ref を “見える文字列” として連結
    - requirement_text: 基本は meti_order_text / export_order_item / term / notes 等を寄せる
    """
    out: List[Dict[str, Any]] = []

    export_items = doc.get("export_items") or []
    for item in export_items:
        export_ref = item.get("export_order_ref") or {}
        export_id = export_ref.get("id") or ""
        export_norm = export_ref.get("norm") or export_ref.get("raw") or ""
        export_item_text = item.get("export_order_item") or ""

        list_name = ""
        src = doc.get("source") or {}
        sheet = src.get("sheet") or ""
        if sheet:
            list_name = sheet

        # intro meti part (sometimes exists)
        intro_meti = item.get("intro_meti_order_ref") or {}
        intro_meti_norm = intro_meti.get("norm") or intro_meti.get("raw") or ""
        intro_meti_id = intro_meti.get("id") or ""
        intro_meti_text = item.get("intro_meti_order_text") or ""

        cargo_rules = item.get("cargo_rules") or []
        if cargo_rules:
            # 号ごとに 1 レコード
            for cr in cargo_rules:
                meti_ref = cr.get("meti_order_ref") or {}
                meti_norm = meti_ref.get("norm") or meti_ref.get("raw") or ""
                meti_id = meti_ref.get("id") or ""
                meti_text = cr.get("meti_order_text") or ""

                term = cr.get("term") or ""
                term_meaning = cr.get("term_meaning") or ""
                notes = cr.get("notes_or_exclusions") or ""
                eccn = cr.get("eccn") or ""
                substances = cr.get("substances") or []

                # substances text (join)
                subs_texts = []
                for s in substances:
                    subs_texts.append((s.get("text") or s.get("raw") or "").strip())
                subs_join = "\n".join([x for x in subs_texts if x])

                item_no = f"{export_norm} ({export_id}) / {meti_norm} ({meti_id})"
                title = export_item_text or ""

                requirement_parts = [
                    export_item_text,
                    intro_meti_text,
                    meti_text,
                    term,
                    term_meaning,
                    notes,
                    f"ECCN:{eccn}" if eccn else "",
                    subs_join,
                    intro_meti_norm,
                    meti_norm,
                ]
                requirement_text = "\n".join([p for p in requirement_parts if p])

                out.append(
                    dict(
                        regime=regime,
                        list_name=list_name,
                        item_no=item_no,
                        title=title,
                        requirement_text=requirement_text,
                        usage_criteria_text=None,
                        tech_criteria_text=None,
                        notes=None,
                        version=(doc.get("schema_version") or None),
                        effective_date=None,
                    )
                )
        else:
            # cargo_rules が無い場合も 1 レコードにしておく（0件回避）
            item_no = f"{export_norm} ({export_id})"
            title = export_item_text or ""
            requirement_parts = [
                export_item_text,
                intro_meti_text,
                intro_meti_norm,
                intro_meti_id,
            ]
            requirement_text = "\n".join([p for p in requirement_parts if p])

            out.append(
                dict(
                    regime=regime,
                    list_name=list_name,
                    item_no=item_no,
                    title=title,
                    requirement_text=requirement_text,
                    usage_criteria_text=None,
                    tech_criteria_text=None,
                    notes=None,
                    version=(doc.get("schema_version") or None),
                    effective_date=None,
                )
            )

    return out


def _upsert_matrix_rules_from_json(db: Session, json_path: str, regime: str) -> Dict[str, int]:
    """
    JSON -> matrix_rules へ upsert
    key: (regime, item_no, version)
    """
    doc = _read_matrix_json(json_path)
    rows = _flatten_matrix_json_to_rules(doc, regime=regime)

    inserted = 0
    updated = 0

    for r in rows:
        key_regime = r["regime"]
        key_item_no = r["item_no"]
        key_version = r.get("version")

        q = (
            db.query(MatrixRule)
            .filter(MatrixRule.regime == key_regime)
            .filter(MatrixRule.item_no == key_item_no)
        )
        # version がある運用なら version もキーへ
        if key_version is not None:
            q = q.filter(MatrixRule.version == key_version)

        obj = q.first()
        if obj:
            # update
            obj.list_name = r.get("list_name")
            obj.title = r.get("title")
            obj.requirement_text = r.get("requirement_text") or obj.requirement_text
            obj.usage_criteria_text = r.get("usage_criteria_text")
            obj.tech_criteria_text = r.get("tech_criteria_text")
            obj.notes = r.get("notes")
            obj.version = r.get("version")
            obj.effective_date = r.get("effective_date")
            obj.updated_at = datetime.utcnow()
            updated += 1
        else:
            # insert
            obj = MatrixRule(
                regime=r["regime"],
                list_name=r.get("list_name"),
                item_no=r["item_no"],
                title=r.get("title"),
                requirement_text=r["requirement_text"] or "",
                usage_criteria_text=r.get("usage_criteria_text"),
                tech_criteria_text=r.get("tech_criteria_text"),
                notes=r.get("notes"),
                version=r.get("version"),
                effective_date=r.get("effective_date"),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(obj)
            inserted += 1

    db.commit()
    return {"inserted": inserted, "updated": updated, "total_in_json": len(rows)}


# -----------------------------
# Tokenize (Japanese-friendly)
# -----------------------------
_LATIN_RE = re.compile(r"[A-Za-z0-9]+")
_JP_BLOCK_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]+")


def _normalize_text(s: str) -> str:
    if not s:
        return ""
    t = str(s).lower()
    t = t.replace("（", "(").replace("）", ")")
    t = t.replace("，", ",").replace("．", ".").replace("・", " ")
    t = t.replace("－", "-").replace("―", "-").replace("−", "-")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _ngrams(s: str, n: int) -> List[str]:
    if not s or len(s) < n:
        return []
    return [s[i : i + n] for i in range(0, len(s) - n + 1)]


def _tokenize(text: str) -> List[str]:
    t = _normalize_text(text)
    if not t:
        return []

    tokens: List[str] = []
    tokens.extend(_LATIN_RE.findall(t))

    for m in _JP_BLOCK_RE.finditer(t):
        block = m.group(0)
        tokens.extend(_ngrams(block, 2))
        tokens.extend(_ngrams(block, 3))

    seen = set()
    out: List[str] = []
    for x in tokens:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _binary_cosine(a_tokens: List[str], b_tokens: List[str]) -> Tuple[float, List[str]]:
    A = set(a_tokens)
    B = set(b_tokens)
    if not A or not B:
        return 0.0, []

    inter = A.intersection(B)
    score = len(inter) / ((len(A) * len(B)) ** 0.5)

    matched = sorted(list(inter), key=lambda x: (-len(x), x))[:40]
    return float(score), matched


def _table_has_column(db: Session, table_name: str, col_name: str) -> bool:
    try:
        cols = inspect(db.get_bind()).get_columns(table_name)
        return any(c["name"] == col_name for c in cols)
    except Exception:
        return False


def step_matrix_match(
    db: Session,
    transaction_id: int,
    run_id: int,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    usage_requirements × matrix_rules

    - data/matrix.json を必要に応じて読み込み、matrix_rules に upsert してから照合する
      * params["matrix_json_path"] でパス上書き可能
    - threshold 以上は decision="hit"
    - threshold 未満でも上位 top_k_per_usage は decision="maybe" として保存（0件回避）
    - evidence_json / updated_at 等は「実DBにカラムがある時だけ」埋める
    """
    threshold = float(params.get("threshold", 0.75))
    regime = str(params.get("regime", "JP_FX"))
    top_k_per_usage = int(params.get("top_k_per_usage", 10))
    top_k_per_usage = max(top_k_per_usage, 1)

    # --- detect real DB columns ---
    has_evidence_json = _table_has_column(db, "matrix_matches", "evidence_json")
    has_decision = _table_has_column(db, "matrix_matches", "decision")
    has_created_at = _table_has_column(db, "matrix_matches", "created_at")
    has_updated_at = _table_has_column(db, "matrix_matches", "updated_at")

    # --- ensure matrix_rules are loaded from data/matrix.json (optional ingest) ---
    matrix_json_path = str(params.get("matrix_json_path") or "").strip()
    if not matrix_json_path:
        matrix_json_path = os.path.join(_project_root(), "data", "matrix.json")

    ingest_result: Dict[str, int] = {"inserted": 0, "updated": 0, "total_in_json": 0}
    try:
        if os.path.exists(matrix_json_path):
            ingest_result = _upsert_matrix_rules_from_json(db, matrix_json_path, regime=regime)
    except Exception:
        # ingest 失敗でも match 自体は継続（既存DBで動かす）
        ingest_result = {"inserted": 0, "updated": 0, "total_in_json": 0}

    # --- delete existing matches for this run_id ---
    db.query(MatrixMatch).filter(MatrixMatch.ai_run_id == run_id).delete(synchronize_session=False)
    db.flush()

    usages: List[UsageRequirement] = (
        db.query(UsageRequirement)
        .filter(UsageRequirement.transaction_id == transaction_id)
        .all()
    )
    if not usages:
        db.commit()
        return {
            "step": "matrix_match",
            "transaction_id": transaction_id,
            "run_id": run_id,
            "threshold": threshold,
            "regime": regime,
            "inserted": 0,
            "note": "usage_requirements が0件のため照合なし",
            "matrix_json_path": matrix_json_path,
            "ingest": ingest_result,
        }

    current_rule_count = db.query(MatrixRule).filter(MatrixRule.regime == regime).count()
    if current_rule_count == 0:
        db.commit()
        return {
            "step": "matrix_match",
            "transaction_id": transaction_id,
            "run_id": run_id,
            "threshold": threshold,
            "regime": regime,
            "inserted": 0,
            "note": f"matrix_rules が0件（regime={regime}）のため照合なし",
            "matrix_json_path": matrix_json_path,
            "ingest": ingest_result,
            "matrix_rules_count": 0,
        }

    # --- load rules ---
    rules: List[MatrixRule] = (
        db.query(MatrixRule)
        .filter(MatrixRule.regime == regime)
        .all()
    )

    # --- build rule token packs ---
    rule_pack: List[Tuple[MatrixRule, str, List[str]]] = []
    for rule in rules:
        parts = [
            (rule.title or "").strip(),
            (rule.requirement_text or "").strip(),
            (rule.usage_criteria_text or "").strip(),
            (rule.tech_criteria_text or "").strip(),
            (rule.notes or "").strip(),
            (rule.item_no or "").strip(),
            (rule.list_name or "").strip(),
        ]
        rule_text = "\n".join([p for p in parts if p])
        rtoks = _tokenize(rule_text)
        if rtoks:
            rule_pack.append((rule, rule_text, rtoks))

    inserted = 0
    now = datetime.utcnow()

    for u in usages:
        ut = (u.text or "").strip()
        utoks = _tokenize(ut)
        if not utoks:
            continue

        scored: List[Tuple[float, MatrixRule, str, List[str]]] = []
        for rule, rule_text, rtoks in rule_pack:
            score, matched = _binary_cosine(utoks, rtoks)
            scored.append((score, rule, rule_text, matched))

        scored.sort(key=lambda x: x[0], reverse=True)
        keep = scored[:top_k_per_usage]

        for score, rule, rule_text, matched in keep:
            # 完全0は保存しない（ノイズ増えすぎ防止）
            if score <= 0.0:
                continue

            match_type = "core_hit" if (u.source or "").lower() == "core" else "expanded_hit"
            decision_val = "hit" if score >= threshold else "maybe"

            mm = MatrixMatch(
                ai_run_id=run_id,
                matrix_rule_id=rule.id,
                usage_requirement_id=u.id,
                match_type=match_type,
                match_score=float(score),
            )

            # --- set required columns if they exist in REAL DB ---
            if has_decision:
                setattr(mm, "decision", decision_val)

            if has_created_at:
                setattr(mm, "created_at", now)

            if has_updated_at:
                setattr(mm, "updated_at", now)

            if has_evidence_json:
                evidence = {
                    "matched_tokens": matched,
                    "usage_source": u.source,
                    "usage_text": ut[:500],
                    "rule_id": rule.id,
                    "rule_item_no": _safe_str(rule.item_no)[:300],
                    "rule_title": (rule.title or "")[:200],
                    "rule_snippet": rule_text[:900],
                    "scoring": {
                        "method": "binary_cosine(jp_2gram_3gram + latin_words)",
                        "threshold": threshold,
                        "kept_top_k_per_usage": top_k_per_usage,
                    },
                    "decision": decision_val,
                }
                setattr(mm, "evidence_json", json.dumps(evidence, ensure_ascii=False))

            db.add(mm)
            inserted += 1

    db.commit()

    return {
        "step": "matrix_match",
        "transaction_id": transaction_id,
        "run_id": run_id,
        "threshold": threshold,
        "regime": regime,
        "top_k_per_usage": top_k_per_usage,
        "inserted": inserted,
        "usage_count": len(usages),
        "matrix_rules_count": current_rule_count,
        "matrix_json_path": matrix_json_path,
        "ingest": ingest_result,
        "note": "data/matrix.json ingest(upsert) -> match. decision/updated_at NOT NULL対応 + 日本語ngramでマッチ改善",
    }
