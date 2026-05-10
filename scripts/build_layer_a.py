"""
build_layer_a.py
────────────────────────────────────────────────────────────────────────────
Layer A FAISS インデックス構築スクリプト（ローカル実行版）。

ソース:
  data/staging/fefta_law_v5.json          → 外為法省令 (law / tsutatsu / parameter)
                                             ※ entity_list は除外（スクリーニング用途と分離）
  data/staging/ccl_eccn_entries_v8.json   → US EAR CCL エントリ (eccn)
  data/staging/usml_itar_entries.json     → ITAR/USML (usml_itar)
  data/staging/eu_dual_use_entries.json   → EU デュアルユース (eu_dual_use)
  data/staging/wassenaar_ml_entries.json  → ワッセナー軍需品リスト (wassenaar_ml)
  data/academic/eccn_tech_terms.json      → ECCN技術用語辞書 (eccn_tech)

モデル: intfloat/multilingual-e5-large  (dim=1024)
出力:
  data/staging/layer_a.index
  data/staging/layer_a_meta.json

使い方:
  cd /path/to/AI_TradeManagement
  python scripts/build_layer_a.py [--dry-run] [--batch-size 32]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# macOS / tokenizer セグフォルト対策
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ROOT           = Path(__file__).resolve().parents[1]
_STAGING        = _ROOT / "data" / "staging"
_ACADEMIC       = _ROOT / "data" / "academic"
_FEFTA_JSON     = _STAGING / "fefta_law_v5.json"
_ECCN_JSON      = _STAGING / "ccl_eccn_entries_v8.json"
_USML_JSON      = _STAGING / "usml_itar_entries.json"
_EU_DU_JSON     = _STAGING / "eu_dual_use_entries.json"
_WA_ML_JSON     = _STAGING / "wassenaar_ml_entries.json"
_ECCN_TERMS_JSON = _ACADEMIC / "eccn_tech_terms.json"
_OUT_INDEX      = _STAGING / "layer_a.index"
_OUT_META       = _STAGING / "layer_a_meta.json"

# entity_list は制裁スクリーニング用途であり、品目規制判定には不要なため除外
_SKIP_SOURCE_TYPES = frozenset({"entity_list"})

_MODEL_NAME     = "intfloat/multilingual-e5-large"
_PASSAGE_PREFIX = "passage: "
_DEFAULT_BATCH  = 16   # CPU 安全バッチサイズ（GPU では --batch-size 64 推奨）


# ── embed_text 生成 ────────────────────────────────────────────────────────────

def _embed_text_fefta(rec: dict) -> str:
    """外為法省令レコード → embed_text。"""
    text = (rec.get("full_text") or rec.get("definition_text") or "").strip()
    if not text:
        return ""
    base = _PASSAGE_PREFIX + text

    # parameter エントリには数値パラメータを付加
    if rec.get("source_type") == "parameter":
        val  = rec.get("value_mm") or rec.get("value_numeric") or ""
        unit = rec.get("value_unit") or ""
        if val:
            base += f" [{val}{unit}]"
    return base


def _embed_text_eccn(rec: dict) -> str:
    """US EAR CCL エントリ → embed_text。
    CCL JSON の実フィールド: heading / items_controlled / technical_notes / raw_text
    """
    eccn    = (rec.get("eccn") or rec.get("entry_number") or "").strip()
    heading = (rec.get("heading") or "").strip()
    items   = (rec.get("items_controlled") or "").strip()
    tech    = (rec.get("technical_notes") or "").strip()
    raw     = (rec.get("raw_text") or "").strip()

    # heading は "ECCN 説明文" 形式なので ECCN コードの重複を除去
    # 最大 1200 文字に制限（e5-large の 512 token 上限を考慮）
    body = heading
    if items and items not in body:
        body += " " + items
    if tech and tech not in body:
        body += " Technical Notes: " + tech
    if not body and raw:
        body = raw

    body = body[:1200].strip()
    if not body:
        return _PASSAGE_PREFIX + eccn  # 最低限 ECCN コードだけ
    return _PASSAGE_PREFIX + body


# ── データ読み込み ──────────────────────────────────────────────────────────────

def _load_records() -> list[dict]:
    records: list[dict] = []

    # ── 外為法省令 ─────────────────────────────────────────────────────────
    if not _FEFTA_JSON.exists():
        logger.warning("FEFTA JSON not found: %s", _FEFTA_JSON)
    else:
        with open(_FEFTA_JSON, encoding="utf-8") as f:
            fefta_data = json.load(f)
        fefta_recs = fefta_data.get("records", [])
        logger.info("FEFTA records: %d (before entity_list filter)", len(fefta_recs))
        for r in fefta_recs:
            src_type = r.get("source_type", "law")
            if src_type in _SKIP_SOURCE_TYPES:
                continue
            et = _embed_text_fefta(r)
            if not et:
                continue
            records.append({
                "source_type": src_type,
                "source_name": r.get("source_name", "fefta_shorei"),
                "article_no":  r.get("article_no", ""),
                "title":       r.get("title", ""),
                "item_no":     r.get("item_no", ""),
                "item_label":  r.get("item_label", ""),
                "chunk_level": r.get("chunk_level", ""),
                "value_mm":    r.get("value_mm"),
                "value_unit":  r.get("value_unit"),
                "full_text":   (r.get("full_text") or r.get("definition_text") or "").strip(),
                "embed_text":  et,
            })

    # ── US EAR CCL (ECCN) ─────────────────────────────────────────────────
    if not _ECCN_JSON.exists():
        logger.warning("ECCN JSON not found: %s", _ECCN_JSON)
    else:
        with open(_ECCN_JSON, encoding="utf-8") as f:
            eccn_data = json.load(f)
        eccn_entries = eccn_data.get("entries", [])
        logger.info("ECCN entries: %d", len(eccn_entries))
        for r in eccn_entries:
            et = _embed_text_eccn(r)
            if not et:
                continue
            records.append({
                "source_type": "eccn",
                "source_name": "ccl_ear",
                "article_no":  r.get("eccn") or r.get("entry_number") or "",
                "title":       (r.get("title") or "").strip(),
                "item_no":     r.get("eccn") or r.get("entry_number") or "",
                "item_label":  (r.get("category") or "").strip(),
                "chunk_level": "entry",
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   (r.get("description") or r.get("full_text") or "").strip(),
                "embed_text":  et,
            })

    # ── ITAR/USML (22 CFR Part 121) ───────────────────────────────────────
    if not _USML_JSON.exists():
        logger.warning("USML/ITAR JSON not found: %s", _USML_JSON)
    else:
        with open(_USML_JSON, encoding="utf-8") as f:
            usml_data = json.load(f)
        usml_entries = usml_data.get("entries", [])
        logger.info("ITAR/USML categories: %d", len(usml_entries))
        for r in usml_entries:
            cat   = r.get("usml_category", "")
            title = r.get("title", "")
            desc  = r.get("description", "")
            items = "; ".join(r.get("key_items", []))
            full  = f"USML Category {cat}: {title}. {desc} Key items: {items}"
            et    = _PASSAGE_PREFIX + full
            records.append({
                "source_type": "usml_itar",
                "source_name": "itar_22cfr121",
                "article_no":  f"USML-{cat}",
                "title":       title,
                "item_no":     f"USML-{cat}",
                "item_label":  cat,
                "chunk_level": "category",
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   full,
                "embed_text":  et,
            })

    # ── EU Dual-Use Regulation Annex I ────────────────────────────────────
    if not _EU_DU_JSON.exists():
        logger.warning("EU Dual-Use JSON not found: %s", _EU_DU_JSON)
    else:
        with open(_EU_DU_JSON, encoding="utf-8") as f:
            eu_data = json.load(f)
        eu_entries = eu_data.get("entries", [])
        logger.info("EU Dual-Use categories: %d", len(eu_entries))
        for r in eu_entries:
            cat   = r.get("eu_category", "")
            title = r.get("title", "")
            desc  = r.get("description", "")
            items = "; ".join(r.get("key_controlled_items", []))
            full  = f"EU Dual-Use Category {cat}: {title}. {desc} Key items: {items}"
            et    = _PASSAGE_PREFIX + full
            records.append({
                "source_type": "eu_dual_use",
                "source_name": "eu_regulation_2021_821",
                "article_no":  f"EU-{cat}",
                "title":       title,
                "item_no":     f"EU-{cat}",
                "item_label":  cat,
                "chunk_level": "category",
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   full,
                "embed_text":  et,
            })

    # ── Wassenaar Arrangement Munitions List ─────────────────────────────────
    if not _WA_ML_JSON.exists():
        logger.warning("Wassenaar ML JSON not found: %s", _WA_ML_JSON)
    else:
        with open(_WA_ML_JSON, encoding="utf-8") as f:
            wa_data = json.load(f)
        wa_entries = wa_data.get("entries", [])
        logger.info("Wassenaar ML categories: %d", len(wa_entries))
        for r in wa_entries:
            cat   = r.get("ml_category", "")
            title = r.get("title", "")
            desc  = r.get("description", "")
            items = "; ".join(r.get("key_items", []))
            full  = f"Wassenaar Munitions List {cat}: {title}. {desc} Key items: {items}"
            et    = _PASSAGE_PREFIX + full
            records.append({
                "source_type": "wassenaar_ml",
                "source_name": "wassenaar_arrangement_2023",
                "article_no":  cat,
                "title":       title,
                "item_no":     cat,
                "item_label":  cat,
                "chunk_level": "category",
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   full,
                "embed_text":  et,
            })

    # ── ECCN 技術用語辞書 (eccn_tech_terms.json) ──────────────────────────────
    if not _ECCN_TERMS_JSON.exists():
        logger.warning("ECCN tech terms JSON not found: %s", _ECCN_TERMS_JSON)
    else:
        with open(_ECCN_TERMS_JSON, encoding="utf-8") as f:
            terms_data = json.load(f)
        eccn_entries = {k: v for k, v in terms_data.items() if not k.startswith("_")}
        logger.info("ECCN tech terms entries: %d", len(eccn_entries))
        for eccn_code, entry in eccn_entries.items():
            label    = entry.get("label", "")
            keywords = entry.get("keywords", [])
            ipc_list = entry.get("ipc_codes", [])
            if not keywords:
                continue
            kw_text = "; ".join(keywords)
            ipc_text = ", ".join(ipc_list) if ipc_list else ""
            full = f"ECCN {eccn_code} {label}: {kw_text}"
            if ipc_text:
                full += f" (IPC: {ipc_text})"
            et = _PASSAGE_PREFIX + full[:1200]
            records.append({
                "source_type": "eccn_tech",
                "source_name": "eccn_tech_terms",
                "article_no":  eccn_code,
                "title":       label,
                "item_no":     eccn_code,
                "item_label":  label,
                "chunk_level": "keywords",
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   full,
                "embed_text":  et,
            })

    logger.info("Total build records: %d", len(records))
    return records


# ── ビルド ─────────────────────────────────────────────────────────────────────

def build(dry_run: bool = False, batch_size: int = _DEFAULT_BATCH) -> None:
    records = _load_records()
    if not records:
        logger.error("No records to build. Exiting.")
        sys.exit(1)

    # source_type 別カウント
    type_counts: dict[str, int] = {}
    for r in records:
        t = r["source_type"]
        type_counts[t] = type_counts.get(t, 0) + 1
    logger.info("source_type breakdown: %s", type_counts)

    if dry_run:
        logger.info("[dry-run] First 5 embed_texts:")
        for r in records[:5]:
            logger.info("  [%s] %s", r["source_type"], r["embed_text"][:120])
        return

    # ── モデルロード ───────────────────────────────────────────────────────
    logger.info("Loading model: %s  (batch_size=%d)", _MODEL_NAME, batch_size)
    model = SentenceTransformer(_MODEL_NAME)
    dim = model.get_sentence_embedding_dimension()
    logger.info("Model ready  dim=%d", dim)

    # ── エンコード ─────────────────────────────────────────────────────────
    texts = [r["embed_text"] for r in records]
    all_emb: list[np.ndarray] = []
    total = len(texts)
    for start in range(0, total, batch_size):
        chunk = texts[start: start + batch_size]
        emb = model.encode(
            chunk,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=batch_size,
        )
        all_emb.append(np.asarray(emb, dtype="float32"))
        done = min(start + batch_size, total)
        batch_no = start // batch_size + 1
        if batch_no % 20 == 0 or done == total:
            logger.info("  encoded %d / %d (batch %d)", done, total, batch_no)

    emb_matrix = np.vstack(all_emb)
    logger.info("Embedding matrix: %s", emb_matrix.shape)

    # ── FAISS インデックス構築 ─────────────────────────────────────────────
    index = faiss.IndexFlatIP(dim)
    index.add(emb_matrix)
    logger.info("FAISS index built: ntotal=%d", index.ntotal)

    for i, r in enumerate(records):
        r["faiss_id"] = i

    # ── 保存 ──────────────────────────────────────────────────────────────
    _OUT_INDEX.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(_OUT_INDEX))
    logger.info("Saved index: %s  (%s)", _OUT_INDEX, _fmt_size(_OUT_INDEX))

    meta = {
        "total":            index.ntotal,
        "dim":              dim,
        "model":            _MODEL_NAME,
        "built_at":         datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_breakdown": type_counts,
        "records":          records,
    }
    _OUT_META.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Saved meta:  %s  (%s)", _OUT_META, _fmt_size(_OUT_META))
    logger.info("Done. ntotal=%d  breakdown=%s", index.ntotal, type_counts)


def _fmt_size(path: Path) -> str:
    size = path.stat().st_size
    if size > 1_000_000:
        return f"{size/1_000_000:.1f} MB"
    return f"{size/1_000:.0f} KB"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Layer A FAISS index (外為法 + ECCN)")
    parser.add_argument("--dry-run", action="store_true",
                        help="embed_text サンプルを表示してモデルロードせずに終了")
    parser.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH,
                        help=f"エンコードバッチサイズ (default: {_DEFAULT_BATCH}, GPU: 64 推奨)")
    args = parser.parse_args()
    build(dry_run=args.dry_run, batch_size=args.batch_size)
