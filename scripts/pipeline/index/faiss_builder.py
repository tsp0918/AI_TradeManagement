"""
index/faiss_builder.py
FAISSインデックスを staging データから構築して data/staging/faiss/ に出力するモジュール。

本番インデックス（modules/*/data/faiss/）への書き込みは行わない。
本番反映は 05_export_to_fastapi.ipynb の明示的なプロモーション操作で実施する。

対応インデックス:
  - matrix_rules : Layer A (232 規制ルール → MiniLM-L12-v2)
  - patents       : Layer B (7,001+ 特許 → MiniLM-L12-v2)
  - entities      : 制裁スクリーニング (10,000+ エンティティ → MiniLM-L12-v2)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MODEL_NAME  = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
ENCODE_BATCH = 256


def build_sanctions_index(
    entities: list[dict[str, Any]],
    output_dir: Path,
    dry_run: bool = False,
) -> tuple[int, Path, Path] | None:
    """
    制裁エンティティリストから FAISS インデックスを構築する。

    Parameters
    ----------
    entities   : sanctions_cleaner.py が出力した SanctionEntity リスト
    output_dir : 保存先ディレクトリ (data/staging/faiss/)
    dry_run    : True の場合インデックスを保存しない

    Returns
    -------
    (ntotal, index_path, meta_path) または None (dry_run 時)
    """
    import faiss
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)
    dim   = model.get_sentence_embedding_dimension()
    index = faiss.IndexFlatIP(dim)

    texts = [_entity_to_text(e) for e in entities]
    keep  = [(e, t) for e, t in zip(entities, texts) if t]
    if not keep:
        logger.warning("No entities with text to index.")
        return None

    entities_k, texts_k = zip(*keep)
    texts_list = list(texts_k)

    all_emb: list[np.ndarray] = []
    for start in range(0, len(texts_list), ENCODE_BATCH):
        chunk = texts_list[start: start + ENCODE_BATCH]
        emb   = model.encode(chunk, normalize_embeddings=True, show_progress_bar=False, batch_size=64)
        all_emb.append(np.asarray(emb, dtype="float32"))
        logger.info("Encoded %d / %d", min(start + ENCODE_BATCH, len(texts_list)), len(texts_list))

    vectors = np.vstack(all_emb)
    index.add(vectors)
    meta = [{"id": str(i), "entity_name": e["entity_name"], "list_source": e["list_source"]}
            for i, e in enumerate(entities_k)]

    logger.info("FAISS built: ntotal=%d", index.ntotal)

    if dry_run:
        logger.info("[DRY_RUN] Index not saved.")
        return None

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "entities.index"
    meta_path  = output_dir / "entities_meta.json"

    faiss.write_index(index, str(index_path))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    logger.info("Saved: %s (%d bytes)", index_path, index_path.stat().st_size)
    return index.ntotal, index_path, meta_path


def build_matrix_rules_index(
    matrix_rules: list[dict[str, Any]],
    output_dir: Path,
    dry_run: bool = False,
) -> tuple[int, Path, Path] | None:
    """
    規制マトリクスルールから FAISS インデックスを構築する。

    matrix_rules の各エントリに含まれるフィールド:
      rule_id, title, requirement_text, usage_criteria_text, tech_criteria_text, notes, item_no, list_name
    """
    import faiss
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)
    dim   = model.get_sentence_embedding_dimension()
    index = faiss.IndexFlatIP(dim)

    texts = [_rule_to_text(r) for r in matrix_rules]
    keep  = [(r, t) for r, t in zip(matrix_rules, texts) if t]
    if not keep:
        logger.warning("No rules with text to index.")
        return None

    rules_k, texts_k = zip(*keep)
    all_emb = []
    for start in range(0, len(texts_k), ENCODE_BATCH):
        chunk = list(texts_k[start: start + ENCODE_BATCH])
        emb   = model.encode(chunk, normalize_embeddings=True, show_progress_bar=False)
        all_emb.append(np.asarray(emb, dtype="float32"))

    index.add(np.vstack(all_emb))
    meta  = [{"rule_id": r.get("rule_id", r.get("id", str(i)))} for i, r in enumerate(rules_k)]

    logger.info("matrix_rules FAISS built: ntotal=%d", index.ntotal)

    if dry_run:
        logger.info("[DRY_RUN] Index not saved.")
        return None

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "matrix_rules.index"
    meta_path  = output_dir / "matrix_rules_meta.json"

    import faiss as _faiss
    _faiss.write_index(index, str(index_path))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    logger.info("Saved: %s", index_path)
    return index.ntotal, index_path, meta_path


# ── ヘルパー ──────────────────────────────────────────────────────────────────

def _entity_to_text(e: dict[str, Any]) -> str:
    parts = [e.get("entity_name", "")]
    aliases = e.get("aliases", [])
    if isinstance(aliases, list):
        parts.extend(aliases)
    if e.get("address"):
        parts.append(e["address"])
    return "\n".join(p for p in parts if p)


def _rule_to_text(r: dict[str, Any]) -> str:
    fields = ["title", "requirement_text", "usage_criteria_text",
              "tech_criteria_text", "notes", "item_no", "list_name"]
    return "\n".join(str(r.get(f, "") or "") for f in fields if r.get(f))
