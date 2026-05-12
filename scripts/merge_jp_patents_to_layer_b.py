"""
merge_jp_patents_to_layer_b.py
──────────────────────────────────────────────────────────────────────────────
特許 raw JSON（lens_patents_raw.json 等）を既存の layer_b_meta.json に
マージして Layer B を再ビルドするスクリプト。

使い方:
  python scripts/merge_jp_patents_to_layer_b.py [--dry-run] [--batch-size 16]
  python scripts/merge_jp_patents_to_layer_b.py --input lens_patents_us_ep_raw.json

前提:
  data/staging/lens_patents_raw.json（または指定ファイル）が存在すること
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

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ROOT          = Path(__file__).resolve().parents[1]
_STAGING       = _ROOT / "data" / "staging"
_LAYER_B_META  = _STAGING / "layer_b_meta.json"
_LAYER_B_INDEX = _STAGING / "layer_b.index"
# デフォルト raw ファイル（--input で上書き可能）
_JP_RAW_DEFAULT = (
    _STAGING / "lens_patents_raw.json"
    if (_STAGING / "lens_patents_raw.json").exists()
    else _STAGING / "jp_patents_raw.json"
)
_IPC_ECCN_MAP  = _STAGING / "ipc_eccn_mapping.json"
_MODEL_NAME    = "intfloat/multilingual-e5-large"
_PASSAGE_PREFIX = "passage: "
_ABSTRACT_MAX  = 600
_DEFAULT_BATCH = 16


def _load_ipc_eccn_map() -> dict[str, list[str]]:
    if not _IPC_ECCN_MAP.exists():
        return {}
    with open(_IPC_ECCN_MAP, encoding="utf-8") as f:
        data = json.load(f)
    ipc_to_eccns: dict[str, list[str]] = {}
    for entry in data.get("mappings", []):
        eccn = entry.get("eccn", "")
        for hint in entry.get("ipc_hints", []):
            ipc_to_eccns.setdefault(hint, [])
            if eccn not in ipc_to_eccns[hint]:
                ipc_to_eccns[hint].append(eccn)
    return ipc_to_eccns


def _resolve_eccn(ipc_codes_str: str, ipc_to_eccns: dict) -> list[str]:
    eccns: set[str] = set()
    for ipc_raw in ipc_codes_str.split(","):
        ipc = ipc_raw.strip()
        for hint in ipc_to_eccns:
            if ipc.startswith(hint):
                eccns.update(ipc_to_eccns[hint])
    return sorted(eccns)


def _parse_fefta_items(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = ast.literal_eval(raw.strip())
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except Exception:
            pass
    return []


def _build_embed_text(r: dict, eccn_tags: list[str]) -> str:
    title    = (r.get("title") or "").strip()
    abstract = (r.get("abstract") or "").strip()[:_ABSTRACT_MAX]
    ipc      = (r.get("ipc_codes") or "").strip()
    cpc      = (r.get("cpc_codes") or "").strip()
    body = title
    if abstract:
        body += " " + abstract
    if ipc:
        body += f" IPC: {ipc}"
    if cpc:
        # CPC は IPC より詳細なので先頭グループのみ embed（重複抑制）
        cpc_groups = " ".join(dict.fromkeys(
            c.strip()[:7] for c in cpc.split(",") if c.strip()
        ))
        body += f" CPC: {cpc_groups}"
    if eccn_tags:
        body += f" ECCN: {' '.join(eccn_tags)}"
    return (_PASSAGE_PREFIX + body)[:1400]


def build(
    dry_run: bool = False,
    batch_size: int = _DEFAULT_BATCH,
    input_path: Path | None = None,
) -> None:
    raw_path = input_path or _JP_RAW_DEFAULT

    # ── 既存 Layer B ロード ──────────────────────────────────────────────────
    if not _LAYER_B_META.exists():
        logger.error("layer_b_meta.json not found: %s", _LAYER_B_META)
        sys.exit(1)
    with open(_LAYER_B_META, encoding="utf-8") as f:
        existing_meta = json.load(f)
    existing_records = existing_meta.get("records", [])
    existing_pub_nos = {r.get("publication_number") for r in existing_records}
    logger.info("既存 Layer B: %d件", len(existing_records))

    # ── 特許ロード ────────────────────────────────────────────────────────
    if not raw_path.exists():
        logger.error("raw file not found: %s", raw_path)
        logger.error("まず fetch_patents_lens.py を実行してください。")
        sys.exit(1)
    with open(raw_path, encoding="utf-8") as f:
        jp_data = json.load(f)
    jp_patents = jp_data.get("patents", [])
    logger.info("特許（取得済み）: %d件 from %s", len(jp_patents), raw_path.name)

    # ── 重複チェックしながら新規分を追加 ────────────────────────────────────
    ipc_to_eccns = _load_ipc_eccn_map()
    new_count = 0
    new_records = []
    for p in jp_patents:
        pub_no = p.get("publication_number", "")
        if pub_no in existing_pub_nos:
            continue
        # IPC + CPC の両方から ECCN を解決（CPC は IPC と同じプレフィックス体系）
        ipc = str(p.get("ipc_codes") or "")
        cpc = str(p.get("cpc_codes") or "")
        combined_codes = ",".join(filter(None, [ipc, cpc]))
        eccn_tags = _resolve_eccn(combined_codes, ipc_to_eccns)
        fefta_items = _parse_fefta_items(p.get("fefta_items", []))
        embed_text = _build_embed_text(p, eccn_tags)
        new_records.append({
            **{k: v for k, v in p.items()
               if k not in {"embed_text", "fefta_items", "eccn_tags",
                             "faiss_id", "_ipc_prefix_searched"}},
            "fefta_items":     fefta_items,
            "eccn_tags":       eccn_tags,
            "has_fefta_mapping": bool(fefta_items),
            "embed_text":      embed_text,
        })
        new_count += 1

    logger.info("追加対象 JP 特許（重複除外）: %d件", new_count)
    if new_count == 0:
        logger.info("追加すべき新規特許がありません。終了します。")
        return

    all_records = existing_records + new_records
    logger.info("統合後合計: %d件 (US:%d + JP:%d)", len(all_records),
                len(existing_records), new_count)

    if dry_run:
        for r in new_records[:3]:
            logger.info("[dry-run] %s: %s", r["publication_number"], r["embed_text"][:80])
        return

    # ── エンコード（新規分のみ） ──────────────────────────────────────────────
    logger.info("Loading model: %s", _MODEL_NAME)
    model = SentenceTransformer(_MODEL_NAME)
    dim = model.get_sentence_embedding_dimension()

    # 既存インデックスをロードして新規分を追加
    existing_index = faiss.read_index(str(_LAYER_B_INDEX))
    logger.info("既存インデックスロード完了: ntotal=%d", existing_index.ntotal)

    new_texts = [r["embed_text"] for r in new_records]
    all_emb: list[np.ndarray] = []
    for start in range(0, len(new_texts), batch_size):
        chunk = new_texts[start: start + batch_size]
        emb = model.encode(chunk, normalize_embeddings=True,
                           show_progress_bar=False, batch_size=batch_size)
        all_emb.append(np.asarray(emb, dtype="float32"))
        done = min(start + batch_size, len(new_texts))
        logger.info("  encoded %d / %d", done, len(new_texts))

    new_emb_matrix = np.vstack(all_emb)
    existing_index.add(new_emb_matrix)
    logger.info("FAISS index: ntotal=%d (追加後)", existing_index.ntotal)

    # faiss_id を再割当て
    for i, r in enumerate(all_records):
        r["faiss_id"] = i

    # ── バックアップ & 保存 ───────────────────────────────────────────────────
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    if _LAYER_B_INDEX.exists():
        shutil.copy(_LAYER_B_INDEX, _STAGING / f"layer_b.index.bak_{ts}")
    if _LAYER_B_META.exists():
        shutil.copy(_LAYER_B_META, _STAGING / f"layer_b_meta.json.bak_{ts}")

    faiss.write_index(existing_index, str(_LAYER_B_INDEX))

    eccn_coverage: dict[str, int] = {}
    for r in all_records:
        for e in r.get("eccn_tags", []):
            eccn_coverage[e] = eccn_coverage.get(e, 0) + 1

    sb = existing_meta.get("source_breakdown", {}).copy()
    source_key = "lens_org" if "lens" in str(raw_path) else "jpo_ip_data"
    sb[source_key] = sb.get(source_key, 0) + new_count  # 累積（上書きバグ修正）

    new_meta = {
        "total":            existing_index.ntotal,
        "dim":              dim,
        "model":            _MODEL_NAME,
        "built_at":         datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_breakdown": sb,
        "eccn_coverage":    eccn_coverage,
        "records":          all_records,
    }
    _LAYER_B_META.write_text(
        json.dumps(new_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Done: %d件 (JP %d件追加), ECCN coverage: %d types",
                existing_index.ntotal, new_count, len(eccn_coverage))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="特許 raw JSON を Layer B にマージして再ビルド"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH)
    parser.add_argument(
        "--input", type=str, default="",
        help="入力 raw JSON ファイルパス（data/staging/ 配下のファイル名または絶対パス）"
    )
    args = parser.parse_args()

    input_path: Path | None = None
    if args.input:
        p = Path(args.input)
        input_path = p if p.is_absolute() else (_ROOT / "data" / "staging" / p)

    build(dry_run=args.dry_run, batch_size=args.batch_size, input_path=input_path)
