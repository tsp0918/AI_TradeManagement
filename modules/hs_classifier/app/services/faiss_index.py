"""HS コード用 FAISS 2 段階インデックス。

Stage 1: Chapter レベル（~96 ベクトル）で大分類を絞り込む。
Stage 2: 絞り込まれた Chapter 内の Subheading（~5,600 件）で精緻化する。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from app.config import settings
from app.services import embedding as emb_svc

logger = logging.getLogger(__name__)

# ── グローバルキャッシュ ──────────────────────────────────────────────────
_hs_data:      list[dict] | None = None        # 全 HS エントリ (JSON 由来)
_sub_index:    faiss.Index | None = None       # Subheading 全件インデックス
_sub_meta:     list[dict] | None = None        # index position → HS エントリ
_chapter_index: faiss.Index | None = None      # Chapter 平均ベクトルインデックス
_chapter_ids:  list[str] | None = None         # index position → chapter code


def _is_built() -> bool:
    return _sub_index is not None and _sub_index.ntotal > 0


def ntotal() -> int:
    return _sub_index.ntotal if _sub_index else 0


def load_hs_data() -> list[dict]:
    """JSON ファイルから HS データを読み込む。"""
    path = Path(settings.hs_data_path)
    if not path.exists():
        raise FileNotFoundError(
            f"HS データファイルが見つかりません: {path}\n"
            f"data/ ディレクトリに hs2022_6digit.json を配置してください。"
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    logger.info("HS データロード完了: %d 件", len(data))
    return data


def _build_chapter_index(hs_data: list[dict]) -> tuple[faiss.Index, list[str]]:
    """Chapter ごとに説明文を平均ベクトル化してインデックス化する。"""
    chapter_texts: dict[str, list[str]] = {}
    for entry in hs_data:
        chap = entry.get("chapter", "")
        desc = entry.get("chapter_description", entry.get("description_en", ""))
        if chap and desc:
            chapter_texts.setdefault(chap, []).append(desc)

    chapter_ids: list[str] = sorted(chapter_texts.keys())
    # 各章の代表テキスト（最初の 1 件を使う）
    texts = [chapter_texts[c][0] for c in chapter_ids]
    embs = emb_svc.encode_passages(texts)

    index = faiss.IndexFlatIP(settings.embedding_dimension)
    index.add(embs)
    logger.info("Chapter インデックス構築: %d 件", index.ntotal)
    return index, chapter_ids


def _build_subheading_index(
    hs_data: list[dict],
    embs: np.ndarray,
) -> tuple[faiss.Index, list[dict]]:
    """全 Subheading のインデックスを構築する。"""
    index = faiss.IndexFlatIP(settings.embedding_dimension)
    index.add(embs)
    def _str_or_none(v) -> str | None:
        return v if isinstance(v, str) else None

    meta = [
        {
            "hs_code": e.get("hs_code", ""),
            "description_en": e.get("description_en", ""),
            "section": e.get("section", ""),
            "chapter": e.get("chapter", ""),
            "chapter_description": _str_or_none(e.get("chapter_description")),
            "heading": e.get("heading", ""),
            "heading_description": _str_or_none(e.get("heading_description")),
            "atlas_short_name": _str_or_none(e.get("atlas_short_name")),
            "hs2017_codes": e.get("hs2017_codes") or [],
        }
        for e in hs_data
    ]
    logger.info("Subheading インデックス構築: %d 件", index.ntotal)
    return index, meta


def _compute_embeddings(hs_data: list[dict]) -> np.ndarray:
    """全 HS コード説明文を Embedding 化する（バッチ処理）。"""
    texts = [e.get("description_en", "") for e in hs_data]
    BATCH = 256
    all_emb: list[np.ndarray] = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i: i + BATCH]
        all_emb.append(emb_svc.encode_passages(chunk))
        if i % 1000 == 0:
            logger.info("  Embedding 計算中: %d / %d", i, len(texts))
    return np.vstack(all_emb)


def build(force: bool = False) -> None:
    """インデックスを構築する。キャッシュがあれば読み込む。"""
    global _hs_data, _sub_index, _sub_meta, _chapter_index, _chapter_ids

    _hs_data = load_hs_data()
    cache_path = Path(settings.embedding_cache_path)

    if not force and cache_path.exists():
        logger.info("Embedding キャッシュをロード中: %s", cache_path)
        embs = np.load(str(cache_path))
        logger.info("キャッシュロード完了: shape=%s", embs.shape)
    else:
        logger.info("Embedding を新規計算します (%d 件)…", len(_hs_data))
        embs = _compute_embeddings(_hs_data)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(str(cache_path), embs)
        logger.info("Embedding キャッシュ保存: %s", cache_path)

    _sub_index, _sub_meta    = _build_subheading_index(_hs_data, embs)
    _chapter_index, _chapter_ids = _build_chapter_index(_hs_data)
    logger.info("✅ FAISS インデックス構築完了 (ntotal=%d)", _sub_index.ntotal)


def search(
    query: str,
    top_k: int = 5,
    stage1_top_chapters: int | None = None,
) -> list[dict]:
    """2 段階 FAISS 検索。

    Returns:
        list of dict with hs_code, description_en, ..., similarity_score
    """
    if not _is_built():
        return []

    n_chapters = stage1_top_chapters or settings.stage1_top_chapters
    q_emb = emb_svc.encode_query(query)

    # Stage 1: Chapter 絞り込み
    k1 = min(n_chapters, _chapter_index.ntotal)
    _, I1 = _chapter_index.search(q_emb, k1)
    top_chapters = {_chapter_ids[i] for i in I1[0] if i >= 0}

    # Stage 2: Subheading 全件検索 → Chapter フィルタ
    # 先にたくさん取ってフィルタリング後に top_k に絞る
    fetch_k = min(top_k * 10, _sub_index.ntotal)
    D2, I2 = _sub_index.search(q_emb, fetch_k)

    results: list[dict] = []
    for score, idx in zip(D2[0], I2[0]):
        if idx < 0:
            continue
        entry = _sub_meta[idx]
        if entry["chapter"] not in top_chapters:
            continue
        if float(score) < settings.min_similarity_threshold:
            continue
        results.append({**entry, "similarity_score": float(score)})
        if len(results) >= top_k:
            break

    # Chapter フィルタで top_k に足りない場合、フィルタなし候補で補完
    if len(results) < top_k:
        seen = {r["hs_code"] for r in results}
        for score, idx in zip(D2[0], I2[0]):
            if len(results) >= top_k:
                break
            if idx < 0:
                continue
            entry = _sub_meta[idx]
            if entry["hs_code"] in seen:
                continue
            if float(score) < settings.min_similarity_threshold:
                continue
            results.append({**entry, "similarity_score": float(score)})

    return results
