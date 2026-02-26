# app/services/pipeline/steps/matrix_match.py
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from sqlalchemy.orm import Session
from sqlalchemy import inspect

from app.db.models.matrix import MatrixRule
from app.db.models.ai_run import MatrixMatch
from app.db.models.transaction import UsageRequirement


# ── Embedding model ───────────────────────────────────────────────────────────
_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_model: Optional[SentenceTransformer] = None


def _get_model() -> SentenceTransformer:
    """モデルをグローバルキャッシュ。リクエスト毎の再ロードを防ぐ。"""
    global _model
    if _model is None:
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


# ── Path helpers ──────────────────────────────────────────────────────────────
def _project_root() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


def _faiss_dir() -> str:
    return os.path.join(_project_root(), "data", "faiss")


def _matrix_index_path() -> str:
    return os.path.join(_faiss_dir(), "matrix_rules.index")


def _matrix_meta_path() -> str:
    return os.path.join(_faiss_dir(), "matrix_rules_meta.json")


# ── Matrix-rule FAISS cache ───────────────────────────────────────────────────
_matrix_index: Optional[faiss.Index] = None
_matrix_meta: Optional[List[Dict[str, Any]]] = None  # [{"rule_id": int}, ...]


def _rule_to_text(rule: MatrixRule) -> str:
    """ルールの全フィールドを結合してエンコード用テキストを作る。"""
    parts = [
        (rule.title or "").strip(),
        (rule.requirement_text or "").strip(),
        (rule.usage_criteria_text or "").strip(),
        (rule.tech_criteria_text or "").strip(),
        (rule.notes or "").strip(),
        (rule.item_no or "").strip(),
        (rule.list_name or "").strip(),
    ]
    return "\n".join(p for p in parts if p)


def _load_matrix_faiss() -> Optional[Tuple[faiss.Index, List[Dict[str, Any]]]]:
    ip, mp = _matrix_index_path(), _matrix_meta_path()
    if not os.path.exists(ip) or not os.path.exists(mp):
        return None
    try:
        index = faiss.read_index(ip)
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
        if isinstance(meta, list):
            return index, meta
    except Exception:
        pass
    return None


def _save_matrix_faiss(index: faiss.Index, meta: List[Dict[str, Any]]) -> None:
    os.makedirs(_faiss_dir(), exist_ok=True)
    faiss.write_index(index, _matrix_index_path())
    with open(_matrix_meta_path(), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


def _build_matrix_faiss(
    rules: List[MatrixRule],
) -> Tuple[faiss.Index, List[Dict[str, Any]]]:
    """ルール一覧を埋め込みエンコードして IndexFlatIP を構築する。"""
    model = _get_model()
    dim = model.get_sentence_embedding_dimension()
    index = faiss.IndexFlatIP(dim)

    if not rules:
        return index, []

    pairs = [(r, _rule_to_text(r)) for r in rules]
    keep = [(r, t) for r, t in pairs if t]
    if not keep:
        return index, []

    keep_rules, texts = zip(*keep)
    texts_list = list(texts)

    BATCH = 256
    all_emb: List[np.ndarray] = []
    for start in range(0, len(texts_list), BATCH):
        chunk = texts_list[start: start + BATCH]
        emb = model.encode(
            chunk, normalize_embeddings=True, show_progress_bar=False, batch_size=64
        )
        all_emb.append(np.asarray(emb, dtype="float32"))

    index.add(np.vstack(all_emb))
    meta = [{"rule_id": r.id} for r in keep_rules]
    return index, meta


def _get_or_build_matrix_faiss(
    rules: List[MatrixRule],
    force_rebuild: bool = False,
) -> Tuple[faiss.Index, List[Dict[str, Any]]]:
    """in-memory → disk → ビルド の順で取得。force_rebuild=True で強制再構築。"""
    global _matrix_index, _matrix_meta

    if not force_rebuild and _matrix_index is not None and _matrix_meta is not None:
        return _matrix_index, _matrix_meta

    if not force_rebuild:
        loaded = _load_matrix_faiss()
        if loaded:
            _matrix_index, _matrix_meta = loaded
            return _matrix_index, _matrix_meta

    _matrix_index, _matrix_meta = _build_matrix_faiss(rules)
    if _matrix_index.ntotal > 0:
        _save_matrix_faiss(_matrix_index, _matrix_meta)
    return _matrix_index, _matrix_meta


def _search_matrix_faiss(
    index: faiss.Index,
    meta: List[Dict[str, Any]],
    rules_map: Dict[int, MatrixRule],
    query: str,
    top_k: int,
) -> List[Tuple[MatrixRule, float]]:
    """クエリに対して FAISS コサイン類似度でルールを検索する。"""
    if not query.strip() or index.ntotal == 0:
        return []

    model = _get_model()
    qv = model.encode(
        [query.strip()], normalize_embeddings=True, show_progress_bar=False
    )
    qv = np.asarray(qv, dtype="float32")

    actual_k = min(top_k, index.ntotal)
    D, I = index.search(qv, actual_k)

    results: List[Tuple[MatrixRule, float]] = []
    for score, idx in zip(D[0].tolist(), I[0].tolist()):
        if idx < 0 or idx >= len(meta):
            continue
        rule = rules_map.get(meta[idx]["rule_id"])
        if rule is not None:
            results.append((rule, float(score)))
    return results


# ── misc helpers ──────────────────────────────────────────────────────────────
def _safe_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    try:
        return json.dumps(x, ensure_ascii=False)
    except Exception:
        return str(x)


def _table_has_column(db: Session, table_name: str, col_name: str) -> bool:
    try:
        cols = inspect(db.get_bind()).get_columns(table_name)
        return any(c["name"] == col_name for c in cols)
    except Exception:
        return False


# ── matrix.json ingest ────────────────────────────────────────────────────────
def _read_matrix_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _flatten_matrix_json_to_rules(doc: Dict[str, Any], regime: str) -> List[Dict[str, Any]]:
    """
    schema_version 0.2-normalized の export_items を DB用 MatrixRule rows に flatten
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

        intro_meti = item.get("intro_meti_order_ref") or {}
        intro_meti_norm = intro_meti.get("norm") or intro_meti.get("raw") or ""
        intro_meti_id = intro_meti.get("id") or ""
        intro_meti_text = item.get("intro_meti_order_text") or ""

        cargo_rules = item.get("cargo_rules") or []
        if cargo_rules:
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
    """JSON -> matrix_rules へ upsert (key: regime + item_no + version)"""
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
        if key_version is not None:
            q = q.filter(MatrixRule.version == key_version)

        obj = q.first()
        if obj:
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


# ── Step entry ────────────────────────────────────────────────────────────────
def step_matrix_match(
    db: Session,
    transaction_id: int,
    run_id: int,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    usage_requirements × matrix_rules を FAISS コサイン類似度でマッチング。

    - data/matrix.json を必要に応じて読み込み matrix_rules に upsert
    - ルール群を paraphrase-multilingual-MiniLM-L12-v2 でエンコードし FAISS インデックス構築
    - 各 usage_requirement をエンコードして上位 top_k_per_usage ルールを取得
    - score >= threshold → decision="hit"、それ以外 → "maybe"
    - FAISS インデックスはディスク & メモリにキャッシュ（matrix.json 更新時に再構築）
    """
    threshold = float(params.get("threshold", 0.75))
    regime = str(params.get("regime", "JP_FX"))
    top_k_per_usage = max(int(params.get("top_k_per_usage", 10)), 1)

    has_evidence_json = _table_has_column(db, "matrix_matches", "evidence_json")
    has_decision = _table_has_column(db, "matrix_matches", "decision")
    has_created_at = _table_has_column(db, "matrix_matches", "created_at")
    has_updated_at = _table_has_column(db, "matrix_matches", "updated_at")

    # --- matrix.json upsert ---
    matrix_json_path = str(params.get("matrix_json_path") or "").strip()
    if not matrix_json_path:
        matrix_json_path = os.path.join(_project_root(), "data", "matrix.json")

    ingest_result: Dict[str, int] = {"inserted": 0, "updated": 0, "total_in_json": 0}
    try:
        if os.path.exists(matrix_json_path):
            ingest_result = _upsert_matrix_rules_from_json(db, matrix_json_path, regime=regime)
    except Exception:
        pass

    # --- delete existing matches for this run ---
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

    rules: List[MatrixRule] = (
        db.query(MatrixRule).filter(MatrixRule.regime == regime).all()
    )
    rules_map: Dict[int, MatrixRule] = {r.id: r for r in rules}

    # --- FAISS インデックス取得（matrix.json に変更があれば強制再構築）---
    force_rebuild = (
        ingest_result.get("inserted", 0) > 0 or ingest_result.get("updated", 0) > 0
    )
    index, meta = _get_or_build_matrix_faiss(rules, force_rebuild=force_rebuild)

    inserted = 0
    now = datetime.utcnow()

    for u in usages:
        qt = (u.text or "").strip()
        if not qt:
            continue

        scored = _search_matrix_faiss(index, meta, rules_map, qt, top_k_per_usage)

        for rule, score in scored:
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

            if has_decision:
                setattr(mm, "decision", decision_val)
            if has_created_at:
                setattr(mm, "created_at", now)
            if has_updated_at:
                setattr(mm, "updated_at", now)
            if has_evidence_json:
                rule_text = _rule_to_text(rule)
                evidence = {
                    "usage_source": u.source,
                    "usage_text": qt[:500],
                    "rule_id": rule.id,
                    "rule_item_no": _safe_str(rule.item_no)[:300],
                    "rule_title": (rule.title or "")[:200],
                    "rule_snippet": rule_text[:900],
                    "scoring": {
                        "method": "faiss_cosine(paraphrase-multilingual-MiniLM-L12-v2)",
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
        "faiss_index_ntotal": index.ntotal,
        "matrix_json_path": matrix_json_path,
        "ingest": ingest_result,
        "note": "FAISS cosine(paraphrase-multilingual-MiniLM-L12-v2) でマトリクスルールとマッチング",
    }
