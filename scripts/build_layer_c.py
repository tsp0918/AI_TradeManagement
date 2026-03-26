"""
build_layer_c.py
────────────────────────────────────────────────────────────────────────────
Layer C FAISS インデックス構築スクリプト。

ソース: data/staging/hs_all_merged.json  (5,476 件)
モデル: intfloat/multilingual-e5-large  (dim=1024, Layer A/B と同一)
出力:
  data/staging/layer_c.index
  data/staging/layer_c_meta.json

embed_text 方針 (P3-5 改訂):
  "passage: {description}"
  FEFTA 日本語ラベルは embed_text に含めない。
  旧方針では " / 外為法: {item_labels}" を末尾追加していたが、
  英語 description と日本語ラベルを混在させると heading 間で
  重複ヒット（false cluster）が多発するため廃止。
  FEFTA メタデータは meta JSON のフィールドとして保持し、
  integrations.py の _lookup_fefta() が post-hoc に付加する。

使い方:
  cd /Users/takehirosato/Desktop/AI_TradeManagement
  python scripts/build_layer_c.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
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
_ROOT = Path(__file__).resolve().parents[1]
_HS_JSON    = _ROOT / "data" / "staging" / "hs_all_merged.json"
_OUT_INDEX  = _ROOT / "data" / "staging" / "layer_c.index"
_OUT_META   = _ROOT / "data" / "staging" / "layer_c_meta.json"

_MODEL_NAME     = "intfloat/multilingual-e5-large"
_PASSAGE_PREFIX = "passage: "
_ENCODE_BATCH   = 16   # macOS SIGSEGV 対策: 小さめバッチで安定動作


# ── embed_text 生成 ───────────────────────────────────────────────────────────
def _make_embed_text(record: dict) -> str:
    """HS レコードから embedding 用テキストを生成する。

    P3-5 改訂: FEFTA 日本語ラベルを embed_text から除外。
    英語 description のみを埋め込むことで heading 間の false cluster を防ぐ。
    FEFTA 情報は meta JSON フィールドとして保持し、後段で付加する。
    """
    desc = (record.get("description") or "").strip()
    if not desc:
        return ""
    return _PASSAGE_PREFIX + desc


# ── メインビルド ──────────────────────────────────────────────────────────────
def build(dry_run: bool = False) -> None:
    # ── データ読み込み ─────────────────────────────────────────────────────
    logger.info("Loading %s", _HS_JSON)
    with open(_HS_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    records_raw = raw.get("records", raw) if isinstance(raw, dict) else raw
    logger.info("Loaded %d records", len(records_raw))

    # embed_text 生成・空行除外
    build_records = []
    for r in records_raw:
        et = _make_embed_text(r)
        if not et:
            continue
        build_records.append({
            "hs_code":          r.get("hs_code", ""),
            "hs_version":       r.get("hs_version", ""),
            "description":      r.get("description", ""),
            "chapter":          r.get("chapter", ""),
            "heading":          r.get("heading", ""),
            "subheading":       r.get("subheading", ""),
            "level":            r.get("level", ""),
            "fefta_items":      r.get("fefta_items", []),
            "has_fefta_mapping": bool(r.get("has_fefta_mapping", False)),
            "embed_text":       et,
        })

    logger.info("Records to index: %d  (skipped %d empty)",
                len(build_records), len(records_raw) - len(build_records))

    if dry_run:
        logger.info("[dry-run] sample embed_texts:")
        for br in build_records[:3]:
            logger.info("  hs_code=%s  fefta=%s",
                        br["hs_code"],
                        [f.get("item_no") for f in br["fefta_items"]])
            logger.info("  embed: %s", br["embed_text"][:120])
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
            num_workers=0,   # macOS fork 安全対策
        )
        all_emb.append(np.asarray(emb, dtype="float32"))
        done = min(start + _ENCODE_BATCH, total)
        # batch=16 では 500 の倍数にならないため、バッチ番号ベースで出力
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

    # ── ソース分析 ─────────────────────────────────────────────────────────
    with_fefta = sum(1 for r in build_records if r["has_fefta_mapping"])
    level_counts: dict[str, int] = {}
    for r in build_records:
        lv = r["level"]
        level_counts[lv] = level_counts.get(lv, 0) + 1

    # ── 保存 ──────────────────────────────────────────────────────────────
    _OUT_INDEX.parent.mkdir(parents=True, exist_ok=True)

    faiss.write_index(index, str(_OUT_INDEX))
    logger.info("Saved index: %s  (%s)", _OUT_INDEX, _fmt_size(_OUT_INDEX))

    meta = {
        "total":            index.ntotal,
        "dim":              dim,
        "model":            _MODEL_NAME,
        "built_at":         datetime.utcnow().isoformat(timespec="seconds"),
        "source_breakdown": {
            "with_fefta_mapping":    with_fefta,
            "without_fefta_mapping": index.ntotal - with_fefta,
            **{f"level_{k}": v for k, v in level_counts.items()},
        },
        "records": build_records,
    }
    _OUT_META.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Saved meta:  %s  (%s)", _OUT_META, _fmt_size(_OUT_META))
    logger.info("Done. ntotal=%d  with_fefta=%d", index.ntotal, with_fefta)


def _fmt_size(path: Path) -> str:
    size = path.stat().st_size
    if size > 1_000_000:
        return f"{size/1_000_000:.1f} MB"
    return f"{size/1_000:.0f} KB"


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Layer C FAISS index for HS codes")
    parser.add_argument("--dry-run", action="store_true",
                        help="embed_text サンプルを表示してモデルロードせずに終了")
    args = parser.parse_args()
    build(dry_run=args.dry_run)
