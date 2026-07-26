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
_EU_DU_EXP_JSON = _STAGING / "eu_dual_use_expanded_entries.json"   # Phase 14-A: EU DU 拡充
_WA_ML_JSON     = _STAGING / "wassenaar_ml_entries.json"
_UK_ML_JSON     = _STAGING / "uk_ml_entries.json"                  # Phase 14-A: UK ML
_DSGL_JSON      = _STAGING / "australia_dsgl_entries.json"         # Phase 14-A: Australia DSGL
_KEIZAI_ANPO_JSON = _STAGING / "jp_keizai_anpo_entries.json"       # Phase 14-A: 経済安保推進法
_CHINA_ECL_JSON = _STAGING / "china_ecl_entries.json"              # Phase 14-A: China ECL
_CISTEC_JSON    = _STAGING / "cistec_guideline_entries.json"        # Phase 14-A: CISTEC
_JP_FX_2025_JSON = _STAGING / "jp_fx_2025_entries.json"            # Phase 16: 2025年改正追加品目
_ECCN_TERMS_JSON = _ACADEMIC / "eccn_tech_terms.json"
# Phase 17: 国際レジーム
_NSG_JSON        = _STAGING / "nsg_entries.json"                    # Phase 17: NSG原子力供給国グループ
_AG_JSON         = _STAGING / "ag_entries.json"                     # Phase 17: Australia Group CW/BW
_MTCR_JSON       = _STAGING / "mtcr_entries.json"                   # Phase 17: ミサイル技術管制レジーム
_ZANGGER_JSON    = _STAGING / "zangger_entries.json"                # Phase 17: Zangger委員会トリガーリスト
# Phase 18: アジア太平洋各国輸出管理法令
_KECO_JSON       = _STAGING / "keco_entries.json"                   # Phase 18: 韓国戦略物資
_SCOMET_JSON     = _STAGING / "scomet_entries.json"                 # Phase 18: インドSCOMET
_SHTC_JSON       = _STAGING / "shtc_entries.json"                   # Phase 18: 台湾SHTC
_SGCA_JSON       = _STAGING / "sgca_entries.json"                   # Phase 18: シンガポールSGCA
_CANADA_ECL_JSON = _STAGING / "canada_ecl_entries.json"             # Phase 18: カナダECL
# Phase 19: 半導体・FDPR・CHIPS・BIS
_EAR_744_JSON    = _STAGING / "ear_744_entries.json"                # Phase 19: EAR Part 744エンドユーザー規制
_FDPR_JSON       = _STAGING / "fdpr_entries.json"                   # Phase 19: 外国直接製品規則
_CHIPS_ACT_JSON  = _STAGING / "chips_act_entries.json"              # Phase 19: CHIPS法ガードレール
_BIS_SEMICON_JSON = _STAGING / "bis_semicon_entries.json"           # Phase 19: BIS半導体規制
_OUT_INDEX      = _STAGING / "layer_a.index"
_OUT_META       = _STAGING / "layer_a_meta.json"
# ai_validation SQLite DB (matrix_rules 2025年改正分)
_VALIDATION_DB  = _ROOT / "modules" / "ai_validation" / "app.db"

# entity_list は制裁スクリーニング用途であり、品目規制判定には不要なため除外
_SKIP_SOURCE_TYPES = frozenset({"entity_list"})

# 削除条文・単純参照のみ（法的ノイズ）を除去する最低文字数しきい値
# "passage: " prefix (9 chars) 込みで 40 文字未満、または "削除" を含む embed_text はスキップ
_MIN_EMBED_LEN  = 40
_NOISE_PATTERNS = ("削除", "Deleted", "Reserved")

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
            # 削除条文・単純参照ノイズを除去
            if len(et) < _MIN_EMBED_LEN or any(p in et for p in _NOISE_PATTERNS):
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

    # ── matrix_rules 2025年改正（ai_validation SQLite から自動取込） ────────
    if not _VALIDATION_DB.exists():
        logger.warning("ai_validation DB not found: %s", _VALIDATION_DB)
    else:
        import sqlite3
        conn = sqlite3.connect(str(_VALIDATION_DB))
        conn.row_factory = sqlite3.Row
        # effective_date が 2025-04-01 以降の改正追加分のみ取込
        rows = conn.execute(
            "SELECT item_no, list_name, title, requirement_text, usage_criteria_text, "
            "tech_criteria_text, effective_date FROM matrix_rules "
            "WHERE regime='JP_FX' AND effective_date >= '2025-04-01' "
            "ORDER BY item_no, id"
        ).fetchall()
        conn.close()
        # item_no 単位で最新1件のみ（同 item_no 重複回避）
        seen: set[str] = set()
        for row in rows:
            item_no = row["item_no"]
            if item_no in seen:
                continue
            seen.add(item_no)
            parts = [
                f"外為法輸出令別表第一 {item_no}（{row['list_name']}）: {row['title']}",
                row["requirement_text"] or "",
                row["usage_criteria_text"] or "",
                row["tech_criteria_text"] or "",
            ]
            full = " ".join(p for p in parts if p).strip()
            et = _PASSAGE_PREFIX + full[:1200]
            records.append({
                "source_type": "jp_fx_2025",
                "source_name": "jp_fx_amendment_2025",
                "article_no":  item_no,
                "title":       row["title"] or "",
                "item_no":     item_no,
                "item_label":  row["list_name"] or "",
                "chunk_level": "amendment",
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   full,
                "embed_text":  et,
            })
        logger.info("JP_FX 2025 amendments from DB: %d", len(seen))

    # ── EU Dual-Use 拡充（サブカテゴリレベル詳細版） ───────────────────────
    if not _EU_DU_EXP_JSON.exists():
        logger.warning("EU DU expanded JSON not found: %s", _EU_DU_EXP_JSON)
    else:
        with open(_EU_DU_EXP_JSON, encoding="utf-8") as f:
            eu_exp_data = json.load(f)
        eu_exp_entries = eu_exp_data.get("entries", [])
        logger.info("EU Dual-Use expanded entries: %d", len(eu_exp_entries))
        for r in eu_exp_entries:
            cat   = r.get("eu_category", "")
            title = r.get("title", "")
            desc  = r.get("description", "")
            items = "; ".join(r.get("key_controlled_items", []))
            full  = f"EU Dual-Use {cat}: {title}. {desc} Key items: {items}"
            et    = _PASSAGE_PREFIX + full[:1200]
            records.append({
                "source_type": "eu_dual_use_expanded",
                "source_name": "eu_regulation_2021_821_expanded",
                "article_no":  f"EU-{cat}",
                "title":       title,
                "item_no":     f"EU-{cat}",
                "item_label":  cat,
                "chunk_level": "subcategory",
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   full,
                "embed_text":  et,
            })

    # ── UK Military List ──────────────────────────────────────────────────────
    if not _UK_ML_JSON.exists():
        logger.warning("UK ML JSON not found: %s", _UK_ML_JSON)
    else:
        with open(_UK_ML_JSON, encoding="utf-8") as f:
            uk_data = json.load(f)
        uk_entries = uk_data.get("entries", [])
        logger.info("UK ML entries: %d", len(uk_entries))
        for r in uk_entries:
            cat   = r.get("ml_category", "")
            title = r.get("title", "")
            desc  = r.get("description", "")
            items = "; ".join(r.get("key_items", []))
            full  = f"UK Military List {cat}: {title}. {desc} Key items: {items}"
            et    = _PASSAGE_PREFIX + full[:1200]
            records.append({
                "source_type": "uk_ml",
                "source_name": "uk_strategic_export_controls_2024",
                "article_no":  f"UK-{cat}",
                "title":       title,
                "item_no":     f"UK-{cat}",
                "item_label":  cat,
                "chunk_level": "category",
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   full,
                "embed_text":  et,
            })

    # ── Australia DSGL ────────────────────────────────────────────────────────
    if not _DSGL_JSON.exists():
        logger.warning("Australia DSGL JSON not found: %s", _DSGL_JSON)
    else:
        with open(_DSGL_JSON, encoding="utf-8") as f:
            dsgl_data = json.load(f)
        dsgl_entries = dsgl_data.get("entries", [])
        logger.info("Australia DSGL entries: %d", len(dsgl_entries))
        for r in dsgl_entries:
            cat    = r.get("dsgl_category", "")
            regime = r.get("regime", "")
            title  = r.get("title", "")
            desc   = r.get("description", "")
            items  = "; ".join(r.get("key_items", []))
            full   = f"Australia DSGL {cat} ({regime}): {title}. {desc} Key items: {items}"
            et     = _PASSAGE_PREFIX + full[:1200]
            records.append({
                "source_type": "australia_dsgl",
                "source_name": "australia_dsgl_2024",
                "article_no":  f"DSGL-{cat}",
                "title":       title,
                "item_no":     f"DSGL-{cat}",
                "item_label":  cat,
                "chunk_level": "category",
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   full,
                "embed_text":  et,
            })

    # ── 経済安全保障推進法 ─────────────────────────────────────────────────────
    if not _KEIZAI_ANPO_JSON.exists():
        logger.warning("経済安保推進法 JSON not found: %s", _KEIZAI_ANPO_JSON)
    else:
        with open(_KEIZAI_ANPO_JSON, encoding="utf-8") as f:
            ka_data = json.load(f)
        ka_entries = ka_data.get("entries", [])
        logger.info("経済安保推進法 entries: %d", len(ka_entries))
        for r in ka_entries:
            anpo_id = r.get("anpo_id", "")
            pillar  = r.get("pillar", "")
            cat     = r.get("category", "")
            title   = r.get("title", "")
            desc    = r.get("description", "")
            items   = "; ".join(r.get("key_items", []))
            full    = (
                f"経済安全保障推進法 {anpo_id}（{pillar}・{cat}）: {title}. "
                f"{desc} 主要対象品目・事項: {items}"
            )
            et = _PASSAGE_PREFIX + full[:1200]
            records.append({
                "source_type": "jp_keizai_anpo",
                "source_name": "jp_economic_security_act_2022",
                "article_no":  anpo_id,
                "title":       title,
                "item_no":     anpo_id,
                "item_label":  f"{pillar}/{cat}",
                "chunk_level": "entry",
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   full,
                "embed_text":  et,
            })

    # ── 中国輸出管理法（ECL 2020）────────────────────────────────────────────
    if not _CHINA_ECL_JSON.exists():
        logger.warning("China ECL JSON not found: %s", _CHINA_ECL_JSON)
    else:
        with open(_CHINA_ECL_JSON, encoding="utf-8") as f:
            ecl_data = json.load(f)
        ecl_entries = ecl_data.get("entries", [])
        logger.info("China ECL entries: %d", len(ecl_entries))
        for r in ecl_entries:
            ecl_id = r.get("ecl_id", "")
            cat    = r.get("category", "")
            title  = r.get("title", "")
            desc   = r.get("description", "")
            items  = "; ".join(r.get("key_items", []))
            full   = (
                f"中国輸出管理法（ECL 2020）{ecl_id}（{cat}）: {title}. "
                f"{desc} 対象品目: {items}"
            )
            et = _PASSAGE_PREFIX + full[:1200]
            records.append({
                "source_type": "china_ecl",
                "source_name": "china_ecl_2020",
                "article_no":  ecl_id,
                "title":       title,
                "item_no":     ecl_id,
                "item_label":  cat,
                "chunk_level": "entry",
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   full,
                "embed_text":  et,
            })

    # ── CISTEC 安全保障輸出管理ガイドライン ──────────────────────────────────
    if not _CISTEC_JSON.exists():
        logger.warning("CISTEC guideline JSON not found: %s", _CISTEC_JSON)
    else:
        with open(_CISTEC_JSON, encoding="utf-8") as f:
            cistec_data = json.load(f)
        cistec_entries = cistec_data.get("entries", [])
        logger.info("CISTEC guideline entries: %d", len(cistec_entries))
        for r in cistec_entries:
            gc_id  = r.get("gc_id", "")
            cat    = r.get("category", "")
            title  = r.get("title", "")
            desc   = r.get("description", "")
            items  = "; ".join(r.get("key_items", []))
            full   = (
                f"CISTEC安全保障輸出管理 {gc_id}（{cat}）: {title}. "
                f"{desc} 実務ポイント: {items}"
            )
            et = _PASSAGE_PREFIX + full[:1200]
            records.append({
                "source_type": "cistec_guideline",
                "source_name": "cistec_compliance_guideline_2024",
                "article_no":  gc_id,
                "title":       title,
                "item_no":     gc_id,
                "item_label":  cat,
                "chunk_level": "entry",
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   full,
                "embed_text":  et,
            })

    # ── 外為法輸出令別表第一 2025年改正追加品目（JP_FX 2025 JSON） ──────────
    # validation DBから取込済の EL-7-24〜26 以外の追加品目を独立JSONファイルで管理
    if not _JP_FX_2025_JSON.exists():
        logger.warning("JP FX 2025 entries JSON not found: %s", _JP_FX_2025_JSON)
    else:
        with open(_JP_FX_2025_JSON, encoding="utf-8") as f:
            fx25_data = json.load(f)
        fx25_entries = fx25_data.get("entries", [])
        logger.info("JP FX 2025 additional entries: %d", len(fx25_entries))
        # validation DB から取込済の item_no と重複しないようにチェック
        existing_jp25 = {r["item_no"] for r in records if r.get("source_type") == "jp_fx_2025"}
        for r in fx25_entries:
            item_no = r.get("item_no", "")
            if item_no in existing_jp25:
                continue
            full = r.get("full_text") or r.get("requirement_text") or ""
            et = _PASSAGE_PREFIX + full[:1200]
            if len(et) < _MIN_EMBED_LEN:
                continue
            records.append({
                "source_type": "jp_fx_2025",
                "source_name": "jp_fx_amendment_2025",
                "article_no":  item_no,
                "title":       r.get("title", ""),
                "item_no":     item_no,
                "item_label":  "外為法輸出令別表第一",
                "chunk_level": r.get("chunk_level", "amendment"),
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   full,
                "embed_text":  et,
            })
            existing_jp25.add(item_no)

    # ── 汎用ローダー（Phase 17-19 新ソース）────────────────────────────────
    # JSON 形式: {"entries": [{"control_id":"...", "regime":"...", "regime_part":"...",
    #                          "title":"...", "title_en":"...", "description":"...",
    #                          "key_items":["..."], ...}]}
    def _load_generic(path: "Path", source_type: str, source_name: str) -> int:
        if not path.exists():
            logger.warning("%s JSON not found: %s", source_type, path)
            return 0
        with open(path, encoding="utf-8") as _f:
            _data = json.load(_f)
        _entries = _data.get("entries", [])
        _count = 0
        for _r in _entries:
            _control_id = _r.get("control_id", "")
            _regime     = _r.get("regime", source_type.upper())
            _part       = _r.get("regime_part", "")
            _title      = _r.get("title", "")
            _title_en   = _r.get("title_en", "")
            _desc       = _r.get("description", "")
            _items      = "; ".join(_r.get("key_items", []))
            _full = (
                f"{_regime} {_control_id}（{_part}）: {_title}（{_title_en}）. "
                f"{_desc} 主要品目・事項: {_items}"
            )
            _et = _PASSAGE_PREFIX + _full[:1200]
            if len(_et) < _MIN_EMBED_LEN or any(p in _et for p in _NOISE_PATTERNS):
                continue
            records.append({
                "source_type": source_type,
                "source_name": source_name,
                "article_no":  _control_id,
                "title":       _title,
                "item_no":     _control_id,
                "item_label":  _part,
                "chunk_level": "item",
                "value_mm":    None,
                "value_unit":  None,
                "full_text":   _full,
                "embed_text":  _et,
            })
            _count += 1
        logger.info("%s entries loaded: %d", source_type, _count)
        return _count

    # Phase 17: 国際レジーム
    _load_generic(_NSG_JSON,        "nsg",         "nsg_guidelines_2023")
    _load_generic(_AG_JSON,         "ag",          "australia_group_2023")
    _load_generic(_MTCR_JSON,       "mtcr",        "mtcr_annex_2023")
    _load_generic(_ZANGGER_JSON,    "zangger",     "zangger_trigger_list")
    # Phase 18: アジア太平洋各国
    _load_generic(_KECO_JSON,       "keco",        "korea_strategic_list_2023")
    _load_generic(_SCOMET_JSON,     "scomet",      "india_scomet_2023")
    _load_generic(_SHTC_JSON,       "shtc",        "taiwan_shtc_2023")
    _load_generic(_SGCA_JSON,       "sgca",        "singapore_sgca_2023")
    _load_generic(_CANADA_ECL_JSON, "canada_ecl",  "canada_ecl_2023")
    # Phase 19: 半導体・FDPR・CHIPS
    _load_generic(_EAR_744_JSON,    "ear_744",     "ear_part744_2024")
    _load_generic(_FDPR_JSON,       "fdpr",        "ear_fdpr_734_9_2024")
    _load_generic(_CHIPS_ACT_JSON,  "chips_act",   "us_chips_act_2023")
    _load_generic(_BIS_SEMICON_JSON,"bis_semicon",  "bis_semicon_rules_2024")

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
