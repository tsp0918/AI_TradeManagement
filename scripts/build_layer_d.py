"""
build_layer_d.py
────────────────────────────────────────────────────────────────────────────
Layer D FAISS インデックス構築スクリプト。

ソース: data/academic/academic_intel.db  (collect_academic_papers.py で収集)
モデル: intfloat/multilingual-e5-large  (dim=1024, Layer A/B/C と同一)
出力:
  data/staging/layer_d.index
  data/staging/layer_d_meta.json

embed_text 方針:
  "passage: {title}. {abstract[:400]}"
  タイトルと要旨を連結して意味空間にマッピングする。
  ECCN タグ・被引用数は meta JSON に保持し、後段での後処理フィルタリングに使用。

使い方:
  cd /Users/takehirosato/Desktop/AI_TradeManagement
  python scripts/build_layer_d.py
  python scripts/build_layer_d.py --dry-run
  python scripts/build_layer_d.py --min-citations 5
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# macOS での PyTorch / tokenizers セグフォルト対策
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── パス設定 ──────────────────────────────────────────────────────────────────
_ROOT      = Path(__file__).resolve().parents[1]
_DB_PATH   = _ROOT / "data" / "academic" / "academic_intel.db"
_OUT_INDEX = _ROOT / "data" / "staging" / "layer_d.index"
_OUT_META  = _ROOT / "data" / "staging" / "layer_d_meta.json"

_MODEL_NAME     = "intfloat/multilingual-e5-large"
_PASSAGE_PREFIX = "passage: "
_ENCODE_BATCH   = 16   # macOS SIGSEGV 対策: 小さめバッチ
_ABSTRACT_TRUNC = 400  # abstract 切り詰め文字数


def _fmt_size(p: Path) -> str:
    sz = p.stat().st_size
    if sz > 1_048_576:
        return f"{sz/1_048_576:.1f} MB"
    return f"{sz/1024:.1f} KB"


# ── データ読み込み ─────────────────────────────────────────────────────────────
def load_papers(min_citations: int = 0) -> list[dict]:
    """academic_intel.db から論文を取得し、ECCN タグを結合して返す。"""
    if not _DB_PATH.exists():
        logger.error("DB が見つかりません: %s", _DB_PATH)
        logger.error("  先に: python scripts/collect_academic_papers.py --all")
        sys.exit(1)

    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row

    # 論文 + ECCN タグを一括取得
    rows = conn.execute("""
        SELECT
            p.paper_id,
            p.title,
            p.abstract,
            p.year,
            p.citation_count,
            p.influential_citation_count,
            p.source,
            GROUP_CONCAT(DISTINCT t.eccn) AS eccn_tags,
            GROUP_CONCAT(DISTINCT t.keyword_matched) AS keywords_matched
        FROM papers p
        JOIN paper_eccn_tags t ON p.paper_id = t.paper_id
        WHERE p.abstract IS NOT NULL
          AND p.abstract != ''
          AND p.title IS NOT NULL
          AND p.citation_count >= ?
        GROUP BY p.paper_id
        ORDER BY p.citation_count DESC
    """, (min_citations,)).fetchall()

    conn.close()
    return [dict(r) for r in rows]


# ── embed_text 生成 ────────────────────────────────────────────────────────────
def _make_embed_text(paper: dict) -> str:
    title = (paper.get("title") or "").strip()
    abstract = (paper.get("abstract") or "").strip()
    abstract_snippet = abstract[:_ABSTRACT_TRUNC]
    if title and abstract_snippet:
        return f"{_PASSAGE_PREFIX}{title}. {abstract_snippet}"
    elif title:
        return f"{_PASSAGE_PREFIX}{title}"
    return ""


# ── メインビルド ──────────────────────────────────────────────────────────────
def build(dry_run: bool = False, min_citations: int = 0) -> None:
    logger.info("Loading papers from %s  (min_citations=%d)", _DB_PATH, min_citations)
    papers = load_papers(min_citations=min_citations)
    logger.info("Loaded %d papers with ECCN tags", len(papers))

    if not papers:
        logger.error("論文が 0 件です。先に collect_academic_papers.py を実行してください。")
        sys.exit(1)

    # embed_text 生成・空行除外
    build_records: list[dict] = []
    for p in papers:
        et = _make_embed_text(p)
        if not et:
            continue
        eccn_list = [e.strip() for e in (p.get("eccn_tags") or "").split(",") if e.strip()]
        build_records.append({
            "paper_id":                    p["paper_id"],
            "title":                       (p.get("title") or "")[:200],
            "abstract_snippet":            (p.get("abstract") or "")[:200],
            "year":                        p.get("year"),
            "citation_count":              p.get("citation_count", 0),
            "influential_citation_count":  p.get("influential_citation_count", 0),
            "source":                      p.get("source", "s2"),
            "eccn_tags":                   eccn_list,
            "keywords_matched":            (p.get("keywords_matched") or ""),
            "embed_text":                  et,
        })

    logger.info("Records to index: %d  (skipped %d)", len(build_records),
                len(papers) - len(build_records))

    if dry_run:
        logger.info("[dry-run] sample records:")
        for r in build_records[:3]:
            logger.info("  paper_id=%s  year=%s  citations=%d  eccn=%s",
                        r["paper_id"], r["year"], r["citation_count"], r["eccn_tags"])
            logger.info("  embed: %s", r["embed_text"][:120])
        logger.info("[dry-run] 終了 (インデックスは保存しません)")
        return

    # ── モデルロード ───────────────────────────────────────────────────────
    logger.info("Loading model: %s", _MODEL_NAME)
    model = SentenceTransformer(_MODEL_NAME)
    dim = model.get_sentence_embedding_dimension()
    logger.info("Model ready  dim=%d", dim)

    # ── エンコード ─────────────────────────────────────────────────────────
    texts = [r["embed_text"] for r in build_records]
    all_emb: list[np.ndarray] = []
    total = len(texts)
    for start in range(0, total, _ENCODE_BATCH):
        chunk = texts[start: start + _ENCODE_BATCH]
        emb = model.encode(
            chunk,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=_ENCODE_BATCH,
        )
        all_emb.append(np.asarray(emb, dtype="float32"))
        done = min(start + _ENCODE_BATCH, total)
        batch_no = start // _ENCODE_BATCH + 1
        if batch_no % 30 == 0 or done == total:
            logger.info("  encoded %d / %d (batch %d)", done, total, batch_no)

    emb_matrix = np.vstack(all_emb)
    logger.info("Embedding matrix: %s", emb_matrix.shape)

    # ── FAISS インデックス構築 ─────────────────────────────────────────────
    index = faiss.IndexFlatIP(dim)
    index.add(emb_matrix)
    logger.info("FAISS index built: ntotal=%d", index.ntotal)

    # faiss_id を付与
    for i, r in enumerate(build_records):
        r["faiss_id"] = i
        del r["embed_text"]  # meta には不要

    # ── ECCN 別集計 ───────────────────────────────────────────────────────
    eccn_counts: dict[str, int] = {}
    for r in build_records:
        for eccn in r["eccn_tags"]:
            eccn_counts[eccn] = eccn_counts.get(eccn, 0) + 1

    source_counts: dict[str, int] = {}
    for r in build_records:
        src = r.get("source", "s2")
        source_counts[src] = source_counts.get(src, 0) + 1

    # ── 保存 ──────────────────────────────────────────────────────────────
    _OUT_INDEX.parent.mkdir(parents=True, exist_ok=True)

    faiss.write_index(index, str(_OUT_INDEX))
    logger.info("Saved index: %s  (%s)", _OUT_INDEX, _fmt_size(_OUT_INDEX))

    meta = {
        "total":     index.ntotal,
        "dim":       dim,
        "model":     _MODEL_NAME,
        "built_at":  datetime.utcnow().isoformat(timespec="seconds"),
        "min_citations_filter": min_citations,
        "source_breakdown": source_counts,
        "eccn_coverage": eccn_counts,
        "records":   build_records,
    }
    _OUT_META.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Saved meta:  %s  (%s)", _OUT_META, _fmt_size(_OUT_META))

    logger.info("=" * 50)
    logger.info("Layer D ビルド完了")
    logger.info("  総ベクトル数: %d", index.ntotal)
    logger.info("  ECCN カバレッジ: %d 種", len(eccn_counts))
    for eccn, cnt in sorted(eccn_counts.items(), key=lambda x: -x[1]):
        logger.info("    %-10s %d 件", eccn, cnt)
    logger.info("  ソース内訳: %s", source_counts)
    logger.info("=" * 50)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="academic_intel.db → FAISS Layer D インデックス構築"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="エンコード・保存をスキップして確認のみ")
    parser.add_argument("--min-citations", type=int, default=0,
                        help="被引用数の下限フィルタ (default: 0 = 全件)")
    args = parser.parse_args()

    build(dry_run=args.dry_run, min_citations=args.min_citations)


if __name__ == "__main__":
    main()
