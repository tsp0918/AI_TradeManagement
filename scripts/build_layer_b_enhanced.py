"""
build_layer_b_enhanced.py
──────────────────────────────────────────────────────────────────────────────
Layer B（特許）FAISS インデックス強化スクリプト。

既存の layer_b_meta.json (1,595 US 特許) を読み込み、以下を改善して再ビルドする:
  1. IPC→ECCN マッピングを使用して各特許に ECCN タグを付与
  2. embed_text に ECCN タグ・IPC コードを追加（検索精度向上）
  3. fefta_items の文字列→リスト変換
  4. アブストラクト長を 600 文字まで拡張（元は 300 文字切り捨て）

出力:
  data/staging/layer_b.index
  data/staging/layer_b_meta.json
  (元ファイルは layer_b.index.bak / layer_b_meta.json.bak にバックアップ)

使い方:
  cd /path/to/AI_TradeManagement
  python scripts/build_layer_b_enhanced.py [--dry-run] [--batch-size 16]
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ROOT          = Path(__file__).resolve().parents[1]
_STAGING       = _ROOT / "data" / "staging"
_LAYER_B_META  = _STAGING / "layer_b_meta.json"
_LAYER_B_INDEX = _STAGING / "layer_b.index"
_IPC_ECCN_MAP  = _STAGING / "ipc_eccn_mapping.json"
_MODEL_NAME    = "intfloat/multilingual-e5-large"
_PASSAGE_PREFIX = "passage: "
_DEFAULT_BATCH = 16
_ABSTRACT_MAX  = 600


def _load_ipc_eccn_map() -> dict[str, list[str]]:
    """IPC コード → ECCN コードリスト のマッピングを読み込む。

    ipc_eccn_mapping.json の mappings リストから、
    ipc_hints（前方一致）→ eccn の逆引き辞書を構築する。
    """
    if not _IPC_ECCN_MAP.exists():
        logger.warning("IPC→ECCN mapping not found: %s", _IPC_ECCN_MAP)
        return {}

    with open(_IPC_ECCN_MAP, encoding="utf-8") as f:
        data = json.load(f)

    mappings = data.get("mappings", [])
    ipc_to_eccns: dict[str, list[str]] = {}
    for entry in mappings:
        eccn = entry.get("eccn", "")
        ipc_hints = entry.get("ipc_hints", [])
        for hint in ipc_hints:
            if hint not in ipc_to_eccns:
                ipc_to_eccns[hint] = []
            if eccn not in ipc_to_eccns[hint]:
                ipc_to_eccns[hint].append(eccn)

    logger.info("IPC→ECCN mapping loaded: %d IPC prefixes", len(ipc_to_eccns))
    return ipc_to_eccns


def _resolve_eccn_from_ipc(ipc_codes_str: str, ipc_to_eccns: dict) -> list[str]:
    """特許の IPC コード文字列から ECCN タグリストを解決する。"""
    if not ipc_codes_str:
        return []

    eccns: set[str] = set()
    for ipc_raw in ipc_codes_str.split(","):
        ipc = ipc_raw.strip()
        # 前方一致: "H01L33" は "H01L" で検索
        for hint in ipc_to_eccns:
            if ipc.startswith(hint):
                eccns.update(ipc_to_eccns[hint])
    return sorted(eccns)


def _parse_fefta_items(raw) -> list[str]:
    """fefta_items を文字列またはリストから正規化する。"""
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        try:
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except Exception:
            pass
        return [raw]
    return []


def _build_embed_text(r: dict, eccn_tags: list[str]) -> str:
    """特許レコードから ECCN タグ付き embed_text を構築する。"""
    title    = (r.get("title") or "").strip()
    abstract = (r.get("abstract") or "").strip()[:_ABSTRACT_MAX]
    ipc      = (r.get("ipc_codes") or "").strip()

    body = title
    if abstract:
        body += " " + abstract
    if ipc:
        body += f" IPC: {ipc}"
    if eccn_tags:
        body += f" ECCN: {' '.join(eccn_tags)}"

    return (_PASSAGE_PREFIX + body)[:1400]


def build(dry_run: bool = False, batch_size: int = _DEFAULT_BATCH) -> None:
    if not _LAYER_B_META.exists():
        logger.error("layer_b_meta.json not found: %s", _LAYER_B_META)
        sys.exit(1)

    with open(_LAYER_B_META, encoding="utf-8") as f:
        meta = json.load(f)

    records_raw = meta.get("records", [])
    logger.info("Loaded %d patent records from layer_b_meta.json", len(records_raw))

    ipc_to_eccns = _load_ipc_eccn_map()

    # ── レコード強化 ──────────────────────────────────────────────────────────
    records: list[dict] = []
    eccn_coverage: dict[str, int] = {}

    for r in records_raw:
        ipc_codes   = str(r.get("ipc_codes") or "")
        eccn_tags   = _resolve_eccn_from_ipc(ipc_codes, ipc_to_eccns)
        fefta_items = _parse_fefta_items(r.get("fefta_items"))

        # ECCN カバレッジカウント
        for e in eccn_tags:
            eccn_coverage[e] = eccn_coverage.get(e, 0) + 1

        embed_text = _build_embed_text(r, eccn_tags)

        records.append({
            **{k: v for k, v in r.items()
               if k not in {"embed_text", "fefta_items", "eccn_tags", "faiss_id"}},
            "fefta_items":     fefta_items,
            "eccn_tags":       eccn_tags,
            "has_fefta_mapping": bool(fefta_items),
            "embed_text":      embed_text,
        })

    tagged = sum(1 for r in records if r["eccn_tags"])
    logger.info("ECCN-tagged patents: %d / %d (%.1f%%)",
                tagged, len(records), 100 * tagged / len(records) if records else 0)
    logger.info("ECCN coverage (%d types): %s",
                len(eccn_coverage),
                sorted(eccn_coverage.items(), key=lambda x: -x[1])[:15])

    if dry_run:
        for r in records[:3]:
            logger.info("[dry-run] embed_text[:120]: %s", r["embed_text"][:120])
            logger.info("          eccn_tags: %s", r["eccn_tags"])
        return

    # ── モデルロード & エンコード ─────────────────────────────────────────────
    logger.info("Loading model: %s  (batch_size=%d)", _MODEL_NAME, batch_size)
    model = SentenceTransformer(_MODEL_NAME)
    dim = model.get_sentence_embedding_dimension()
    logger.info("Model ready  dim=%d", dim)

    texts = [r["embed_text"] for r in records]
    all_emb: list[np.ndarray] = []
    total = len(texts)
    for start in range(0, total, batch_size):
        chunk = texts[start: start + batch_size]
        emb = model.encode(chunk, normalize_embeddings=True,
                           show_progress_bar=False, batch_size=batch_size)
        all_emb.append(np.asarray(emb, dtype="float32"))
        done = min(start + batch_size, total)
        if start // batch_size % 20 == 0 or done == total:
            logger.info("  encoded %d / %d", done, total)

    emb_matrix = np.vstack(all_emb)

    # ── FAISS ビルド ──────────────────────────────────────────────────────────
    index = faiss.IndexFlatIP(dim)
    index.add(emb_matrix)
    logger.info("FAISS index built: ntotal=%d", index.ntotal)

    for i, r in enumerate(records):
        r["faiss_id"] = i

    # ── バックアップ & 保存 ───────────────────────────────────────────────────
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    if _LAYER_B_INDEX.exists():
        shutil.copy(_LAYER_B_INDEX, _STAGING / f"layer_b.index.bak_{ts}")
    if _LAYER_B_META.exists():
        shutil.copy(_LAYER_B_META, _STAGING / f"layer_b_meta.json.bak_{ts}")

    faiss.write_index(index, str(_LAYER_B_INDEX))

    new_meta = {
        "total":            index.ntotal,
        "dim":              dim,
        "model":            _MODEL_NAME,
        "built_at":         datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_breakdown": meta.get("source_breakdown", {}),
        "eccn_coverage":    eccn_coverage,
        "records":          records,
    }
    _LAYER_B_META.write_text(
        json.dumps(new_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Saved index: %s  (ntotal=%d)", _LAYER_B_INDEX, index.ntotal)
    logger.info("Done. ECCN coverage: %d types, %d tagged / %d total",
                len(eccn_coverage), tagged, len(records))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enhance Layer B FAISS index with ECCN tags")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH)
    args = parser.parse_args()
    build(dry_run=args.dry_run, batch_size=args.batch_size)
