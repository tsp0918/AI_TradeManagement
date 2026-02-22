# app/services/pipeline/steps/patent_retrieve.py
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

from sqlalchemy.orm import Session
from sqlalchemy import inspect

from app.db.models.patent import Patent
from app.db.models.transaction import UsageRequirement
from app.db.models.ai_run import PatentRetrieval


# =========================
# Config
# =========================
_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def _project_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


def _faiss_dir() -> str:
    return os.path.join(_project_root(), "data", "faiss")


def _faiss_index_path() -> str:
    return os.path.join(_faiss_dir(), "patents.index")


def _faiss_meta_path() -> str:
    return os.path.join(_faiss_dir(), "patents_meta.json")


# =========================
# Ingest helpers (patents.json -> DB)
# =========================
def _read_patents_json(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict) and isinstance(doc.get("items"), list):
        return doc["items"]
    raise ValueError("patents.json must be a list or {items:[...]} JSON")


def _to_ipc_raw(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return ";".join([str(x).strip() for x in v if str(x).strip()])
    return str(v).strip()


def _upsert_patents_from_json(db: Session, json_path: str) -> Dict[str, int]:
    items = _read_patents_json(json_path)

    inserted = 0
    updated = 0
    now = datetime.utcnow()

    for it in items:
        pub = (it.get("publication_number") or it.get("pub_number") or "").strip()
        if not pub:
            continue

        title = (it.get("title") or "").strip()
        applicant = (it.get("applicant") or it.get("assignee") or "").strip()

        # ★ ここが “usage_details” ずれ吸収ポイント
        usage_detail = (
            it.get("usage_detail")
            or it.get("usage_details")
            or it.get("abstract")
            or it.get("description")
            or ""
        ).strip()

        ipc_raw = _to_ipc_raw(it.get("ipc_codes") or it.get("ipc") or it.get("ipc_codes_raw"))

        obj = db.query(Patent).filter(Patent.publication_number == pub).first()
        if obj:
            if hasattr(obj, "title"):
                obj.title = title
            if hasattr(obj, "applicant"):
                obj.applicant = applicant
            if hasattr(obj, "assignee"):
                obj.assignee = applicant
            if hasattr(obj, "usage_detail"):
                obj.usage_detail = usage_detail
            if hasattr(obj, "abstract"):
                obj.abstract = usage_detail
            if hasattr(obj, "description"):
                obj.description = usage_detail
            if hasattr(obj, "ipc_codes_raw"):
                obj.ipc_codes_raw = ipc_raw
            if hasattr(obj, "updated_at"):
                obj.updated_at = now
            updated += 1
        else:
            obj = Patent(publication_number=pub)
            if hasattr(obj, "title"):
                obj.title = title
            if hasattr(obj, "applicant"):
                obj.applicant = applicant
            if hasattr(obj, "assignee"):
                obj.assignee = applicant
            if hasattr(obj, "usage_detail"):
                obj.usage_detail = usage_detail
            if hasattr(obj, "abstract"):
                obj.abstract = usage_detail
            if hasattr(obj, "description"):
                obj.description = usage_detail
            if hasattr(obj, "ipc_codes_raw"):
                obj.ipc_codes_raw = ipc_raw
            if hasattr(obj, "created_at"):
                obj.created_at = now
            if hasattr(obj, "updated_at"):
                obj.updated_at = now
            db.add(obj)
            inserted += 1

    return {"inserted": inserted, "updated": updated, "total_in_json": len(items)}


# =========================
# FAISS Index Builder / Loader
# =========================
def _patent_to_text(p: Patent) -> str:
    parts: List[str] = []
    for key in ["title", "usage_detail", "abstract", "description", "ipc_codes_raw"]:
        if hasattr(p, key):
            v = getattr(p, key)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
    # publication_number は識別子として軽く混ぜる程度
    if hasattr(p, "publication_number") and getattr(p, "publication_number"):
        parts.append(str(getattr(p, "publication_number")))
    return "\n".join(parts).strip()


def _ensure_faiss_dir() -> None:
    d = _faiss_dir()
    os.makedirs(d, exist_ok=True)


def _load_faiss_if_exists() -> Optional[Tuple[faiss.Index, List[Dict[str, Any]]]]:
    ip = _faiss_index_path()
    mp = _faiss_meta_path()
    if os.path.exists(ip) and os.path.exists(mp):
        try:
            index = faiss.read_index(ip)
            with open(mp, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if isinstance(meta, list):
                return index, meta
        except Exception:
            return None
    return None


def _build_faiss_from_db(db: Session) -> Tuple[faiss.Index, List[Dict[str, Any]]]:
    model = SentenceTransformer(_MODEL_NAME)

    patents: List[Patent] = db.query(Patent).all()
    texts = [_patent_to_text(p) for p in patents]

    # 空を弾く（念のため）
    keep: List[Tuple[Patent, str]] = [(p, t) for p, t in zip(patents, texts) if t]
    patents = [p for p, _ in keep]
    texts = [t for _, t in keep]

    if not patents:
        # 空の index を返す
        dim = model.get_sentence_embedding_dimension()
        index = faiss.IndexFlatIP(dim)
        return index, []

    emb = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    emb = np.asarray(emb, dtype="float32")

    dim = emb.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(emb)

    meta = [{"patent_id": p.id} for p in patents]
    return index, meta


def _save_faiss(index: faiss.Index, meta: List[Dict[str, Any]]) -> None:
    _ensure_faiss_dir()
    faiss.write_index(index, _faiss_index_path())
    with open(_faiss_meta_path(), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _get_or_build_faiss(db: Session, force_rebuild: bool = False) -> Tuple[faiss.Index, List[Dict[str, Any]]]:
    if not force_rebuild:
        loaded = _load_faiss_if_exists()
        if loaded:
            return loaded

    index, meta = _build_faiss_from_db(db)
    _save_faiss(index, meta)
    return index, meta


def _search_patents_faiss(
    db: Session,
    index: faiss.Index,
    meta: List[Dict[str, Any]],
    query: str,
    top_k: int,
) -> List[Tuple[Patent, float]]:
    query = (query or "").strip()
    if not query or index.ntotal == 0:
        return []

    model = SentenceTransformer(_MODEL_NAME)
    qv = model.encode([query], normalize_embeddings=True, show_progress_bar=False)
    qv = np.asarray(qv, dtype="float32")

    D, I = index.search(qv, top_k)
    scores = D[0].tolist()
    ids = I[0].tolist()

    patent_ids: List[int] = []
    scored: List[Tuple[int, float]] = []
    for idx, score in zip(ids, scores):
        if idx < 0 or idx >= len(meta):
            continue
        pid = int(meta[idx]["patent_id"])
        patent_ids.append(pid)
        scored.append((pid, float(score)))

    if not patent_ids:
        return []

    # DBからまとめて引いて順番保持
    rows = db.query(Patent).filter(Patent.id.in_(patent_ids)).all()
    row_map = {r.id: r for r in rows}

    out: List[Tuple[Patent, float]] = []
    for pid, score in scored:
        p = row_map.get(pid)
        if p:
            out.append((p, score))
    return out


# =========================
# Step entry
# =========================
def step_patent_retrieve(
    db: Session,
    transaction_id: int,
    run_id: int,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    - patents テーブルを参照して retrieval を作る
    - patents が空なら data/patents.json を upsert して埋める
    - FAISSで類似検索して上位Kを PatentRetrieval に格納
    """

    # --- ensure patents are loaded ---
    patents_json_path = str(params.get("patents_json_path") or "").strip()
    if not patents_json_path:
        patents_json_path = os.path.join(_project_root(), "data", "patents.json")

    ingest_result = None
    patent_count = db.query(Patent).count()

    if patent_count == 0 and os.path.exists(patents_json_path):
        ingest_result = _upsert_patents_from_json(db, patents_json_path)
        db.commit()
        patent_count = db.query(Patent).count()

    # --- cleanup old rows for this run ---
    db.query(PatentRetrieval).filter(PatentRetrieval.ai_run_id == run_id).delete(synchronize_session=False)
    db.flush()

    usages = (
        db.query(UsageRequirement)
        .filter(UsageRequirement.transaction_id == transaction_id)
        .all()
    )
    if not usages:
        db.commit()
        return {"step": "patent_retrieve", "inserted": 0, "note": "usage_requirements が0件"}

    top_k = int(params.get("top_k_patents_per_usage", 5))

    # FAISSをロード（なければDBから作って保存）
    force_rebuild = bool(params.get("force_rebuild_faiss", False))
    index, meta = _get_or_build_faiss(db, force_rebuild=force_rebuild)

    inserted = 0
    for u in usages:
        q = (u.text or "").strip()
        if not q:
            continue

        results = _search_patents_faiss(db, index, meta, q, top_k=top_k)

        # fallback（FAISSが空等のとき）
        if not results:
            candidates = db.query(Patent).limit(top_k).all()
            results = [(p, 0.0) for p in candidates]

        for p, score in results:
            pr = PatentRetrieval(
                ai_run_id=run_id,
                usage_requirement_id=u.id,
                patent_id=p.id,
                score=float(score),
                why="faiss_embedding_search",
            )
            db.add(pr)
            inserted += 1

    db.commit()

    return {
        "step": "patent_retrieve",
        "transaction_id": transaction_id,
        "run_id": run_id,
        "patent_count": patent_count,
        "ingest": ingest_result,
        "inserted": inserted,
        "patents_json_path": patents_json_path,
        "faiss_index_path": _faiss_index_path(),
        "faiss_meta_path": _faiss_meta_path(),
        "note": "patents(DB) -> FAISS index -> retrieve topK by embedding similarity",
    }
