"""
build_layer_e.py
────────────────────────────────────────────────────────────────────────────
Layer E FAISS インデックス構築スクリプト（政策・戦略文書 / 技術哲学）。

ソース:
  data/policy_docs/strategic_policy_docs.json  → 政策分析・技術哲学文書
  platform-core/platform_core/ontology/seed/emerging_tech_taxonomy.json → 技術戦略軸
  platform-core/platform_core/ontology/seed/economic_security.json      → 経済安保軸
  platform-core/platform_core/ontology/seed/geopolitical_sc.json        → 地政学SC軸

モデル: intfloat/multilingual-e5-large  (dim=1024, IndexFlatIP)
出力:
  data/staging/layer_e.index
  data/staging/layer_e_meta.json

使い方:
  cd /path/to/AI_TradeManagement
  python scripts/build_layer_e.py [--dry-run] [--batch-size 16]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ROOT       = Path(__file__).resolve().parents[1]
_STAGING    = _ROOT / "data" / "staging"
_POLICY_DIR = _ROOT / "data" / "policy_docs"
_SEED_DIR   = _ROOT / "platform-core" / "platform_core" / "ontology" / "seed"
_OUT_INDEX  = _STAGING / "layer_e.index"
_OUT_META   = _STAGING / "layer_e_meta.json"

_MODEL_NAME     = "intfloat/multilingual-e5-large"
_PASSAGE_PREFIX = "passage: "
_DEFAULT_BATCH  = 16


def _load_records() -> list[dict]:
    records: list[dict] = []

    # ── 政策・戦略文書 ────────────────────────────────────────────────────────
    policy_path = _POLICY_DIR / "strategic_policy_docs.json"
    if policy_path.exists():
        data = json.loads(policy_path.read_text(encoding="utf-8"))
        for doc in data.get("documents", []):
            text = doc.get("text", "").strip()
            if not text:
                continue
            title    = doc.get("title", "")
            subtitle = doc.get("subtitle", "")
            category = doc.get("category", "")
            full_text = f"{title}。{subtitle}。{text}"
            embed_text = _PASSAGE_PREFIX + f"[{category}] {title}。{subtitle}。{text[:1200]}"
            records.append({
                "source_type":   "policy_doc",
                "source_name":   "strategic_policy_docs",
                "doc_id":        doc.get("doc_id", ""),
                "category":      category,
                "title":         title,
                "subtitle":      subtitle,
                "keywords":      doc.get("keywords", []),
                "eccn_relevance": doc.get("eccn_relevance", []),
                "fefta_relevance": doc.get("fefta_relevance", []),
                "strategic_axis": doc.get("strategic_axis", []),
                "full_text":     full_text[:2000],
                "embed_text":    embed_text,
            })
        logger.info("policy_docs: %d 文書", len(records))
    else:
        logger.warning("policy_docs not found: %s", policy_path)

    start_count = len(records)

    # ── 技術戦略軸 (emerging_tech_taxonomy) ─────────────────────────────────
    et_path = _SEED_DIR / "emerging_tech_taxonomy.json"
    if et_path.exists():
        et = json.loads(et_path.read_text(encoding="utf-8"))
        for domain in et.get("domains", []):
            name_ja = domain.get("name_ja", "")
            name_en = domain.get("name_en", "")
            desc    = domain.get("description", "").strip()
            trl_range = domain.get("trl_range", "")
            dual_use  = domain.get("dual_use_potential", 0.0)
            subs = "; ".join(s["name"] for s in domain.get("subtechnologies", []))
            full_text = (
                f"{name_ja}（{name_en}）— {trl_range}。"
                f"デュアルユース潜在度: {dual_use:.0%}。{desc} サブ技術: {subs}"
            )
            embed_text = _PASSAGE_PREFIX + full_text[:1200]
            records.append({
                "source_type":   "emerging_tech",
                "source_name":   "emerging_tech_taxonomy",
                "doc_id":        domain["domain_id"],
                "category":      "新興技術ドメイン",
                "title":         name_ja,
                "subtitle":      name_en,
                "keywords":      [name_ja, name_en] + domain.get("eccn_connection", []),
                "eccn_relevance": domain.get("eccn_connection", []),
                "fefta_relevance": domain.get("fefta_connection", []),
                "strategic_axis": ["技術戦略"],
                "dual_use_potential": dual_use,
                "full_text":     full_text[:2000],
                "embed_text":    embed_text,
            })
        logger.info("emerging_tech domains: %d", len(records) - start_count)
    start_count = len(records)

    # ── 経済安保軸 (economic_security) ───────────────────────────────────────
    es_path = _SEED_DIR / "economic_security.json"
    if es_path.exists():
        es = json.loads(es_path.read_text(encoding="utf-8"))
        # フレームワーク全体テキスト
        fw = es.get("framework", {})
        if fw.get("purpose"):
            full_text = (
                f"経済安全保障推進法（{fw.get('law_name', '')}）— {fw.get('purpose', '')}。"
                f"4本柱: " + "、".join(p["name"] for p in fw.get("pillars", []))
            )
            records.append({
                "source_type":   "keizai_anpo",
                "source_name":   "economic_security_framework",
                "doc_id":        "KA-FW",
                "category":      "経済安全保障フレームワーク",
                "title":         "経済安全保障推進法の全体構造",
                "subtitle":      fw.get("purpose", "")[:100],
                "keywords":      ["経済安全保障", "特定重要物資", "基幹インフラ", "先端技術", "特許非公開"],
                "eccn_relevance": [],
                "fefta_relevance": [],
                "strategic_axis": ["経済安保"],
                "full_text":     full_text[:2000],
                "embed_text":    _PASSAGE_PREFIX + full_text[:1200],
            })
        # 重要物資エントリ
        for mat in es.get("critical_materials", []):
            name_ja  = mat.get("name_ja", "")
            risk     = mat.get("geopolitical_risk", "").strip()
            policy   = mat.get("key_policy", "").strip()
            conc     = mat.get("concentration_risk", "").strip()
            eccns    = "、".join(mat.get("eccn_parallel", []))
            full_text = (
                f"特定重要物資: {name_ja}（{mat.get('name_en', '')}）。"
                f"集中リスク: {conc}。地政学リスク: {risk}。"
                f"政策: {policy}。関連ECCN: {eccns}"
            )
            records.append({
                "source_type":   "keizai_anpo",
                "source_name":   "economic_security_materials",
                "doc_id":        mat["material_id"],
                "category":      "特定重要物資",
                "title":         f"特定重要物資: {name_ja}",
                "subtitle":      mat.get("name_en", ""),
                "keywords":      [name_ja, mat.get("name_en", "")] + mat.get("eccn_parallel", []),
                "eccn_relevance": mat.get("eccn_parallel", []),
                "fefta_relevance": mat.get("fefta_parallel", []),
                "strategic_axis": ["経済安保"],
                "risk_level":    mat.get("risk_level"),
                "full_text":     full_text[:2000],
                "embed_text":    _PASSAGE_PREFIX + full_text[:1200],
            })
        logger.info("keizai_anpo entries: %d", len(records) - start_count)
    start_count = len(records)

    # ── サプライチェーンDD (supply_chain_dd) ─────────────────────────────────
    scdd_path = _SEED_DIR / "supply_chain_dd.json"
    if scdd_path.exists():
        scdd = json.loads(scdd_path.read_text(encoding="utf-8"))
        for fw in scdd.get("sc_dd_frameworks", []):
            fw_id    = fw.get("framework_id", "")
            name_ja  = fw.get("framework_name_ja", "")
            name_en  = fw.get("framework_name", "")
            basis    = fw.get("legal_basis", "")
            desc     = fw.get("description", "").strip()
            axes     = "・".join(fw.get("strategic_axis", []))
            reqs     = "; ".join(fw.get("key_requirements", fw.get("feoc_rules", fw.get("benchmarks_2030", fw.get("controlled_items", fw.get("key_commitments", [])))))[:4])
            impact   = fw.get("impact_on_japan", "")
            full_text = (
                f"サプライチェーンDD枠組み: {name_ja}（{name_en}）— {basis}。"
                f"{desc} 主要要件: {reqs}。日本への影響: {impact}"
            )
            embed_text = _PASSAGE_PREFIX + full_text[:1200]
            records.append({
                "source_type":    "sc_dd",
                "source_name":    "supply_chain_dd_frameworks",
                "doc_id":         fw_id,
                "category":       "サプライチェーンDD",
                "title":          name_ja,
                "subtitle":       name_en,
                "keywords":       [name_ja, name_en, basis],
                "eccn_relevance": fw.get("eccn_relevance", []),
                "fefta_relevance": fw.get("fefta_relevance", []),
                "strategic_axis": fw.get("strategic_axis", []),
                "full_text":      full_text[:2000],
                "embed_text":     embed_text,
            })
        logger.info("sc_dd entries: %d", len(records) - start_count)
    start_count = len(records)

    # ── 国際レジーム (international_regimes) ────────────────────────────────
    ir_path = _SEED_DIR / "international_regimes.json"
    if ir_path.exists():
        ir = json.loads(ir_path.read_text(encoding="utf-8"))
        for fw in ir.get("multilateral_frameworks", []):
            fw_id    = fw.get("framework_id", "")
            name_ja  = fw.get("framework_name_ja", "")
            name_en  = fw.get("framework_name", "")
            basis    = fw.get("legal_basis", "")
            desc     = fw.get("description", "").strip()
            members  = "・".join(fw.get("member_countries", [])[:8])
            provs    = "; ".join(fw.get("key_provisions", fw.get("key_commitments", []))[:4])
            full_text = (
                f"国際レジーム: {name_ja}（{name_en}）— {basis}。"
                f"{desc} 主要条項: {provs}。参加国: {members}"
            )
            embed_text = _PASSAGE_PREFIX + full_text[:1200]
            records.append({
                "source_type":    "intl_regime",
                "source_name":    "international_regimes",
                "doc_id":         fw_id,
                "category":       "国際レジーム",
                "title":          name_ja,
                "subtitle":       name_en,
                "keywords":       [name_ja, name_en, basis],
                "eccn_relevance": fw.get("eccn_relevance", []),
                "fefta_relevance": fw.get("fefta_relevance", []),
                "strategic_axis": fw.get("strategic_axis", []),
                "full_text":      full_text[:2000],
                "embed_text":     embed_text,
            })
        logger.info("intl_regime entries: %d", len(records) - start_count)
    start_count = len(records)

    # ── 重要鉱物 (critical_minerals) ─────────────────────────────────────────
    crm_path = _SEED_DIR / "critical_minerals.json"
    if crm_path.exists():
        crm = json.loads(crm_path.read_text(encoding="utf-8"))
        for fw in crm.get("critical_mineral_frameworks", []):
            fw_id    = fw.get("framework_id", "")
            name_ja  = fw.get("framework_name_ja", "")
            name_en  = fw.get("framework_name", "")
            basis    = fw.get("legal_basis", "")
            desc     = fw.get("description", "").strip()
            items    = fw.get("strategic_raw_materials", fw.get("controlled_items", fw.get("priority_minerals", fw.get("key_commitments", []))))
            items_str = "; ".join(str(i) for i in items[:5])
            full_text = (
                f"重要鉱物枠組み: {name_ja}（{name_en}）— {basis}。"
                f"{desc} 主要鉱物・措置: {items_str}"
            )
            embed_text = _PASSAGE_PREFIX + full_text[:1200]
            records.append({
                "source_type":    "critical_mineral",
                "source_name":    "critical_minerals_frameworks",
                "doc_id":         fw_id,
                "category":       "重要鉱物",
                "title":          name_ja,
                "subtitle":       name_en,
                "keywords":       [name_ja, name_en, basis],
                "eccn_relevance": fw.get("eccn_relevance", []),
                "fefta_relevance": fw.get("fefta_relevance", []),
                "strategic_axis": fw.get("strategic_axis", []),
                "full_text":      full_text[:2000],
                "embed_text":     embed_text,
            })
        logger.info("critical_mineral entries: %d", len(records) - start_count)
    start_count = len(records)

    # ── 地政学SC軸 (geopolitical_sc) ─────────────────────────────────────────
    geo_path = _SEED_DIR / "geopolitical_sc.json"
    if geo_path.exists():
        geo = json.loads(geo_path.read_text(encoding="utf-8"))
        # チョークポイント
        for cp in geo.get("chokepoints", []):
            name    = cp.get("name", "")
            conc    = cp.get("concentration", "")
            desc    = cp.get("description", "").strip()
            mitig   = "、".join(cp.get("mitigation_options", []))
            eccns   = "、".join(cp.get("affected_eccn", []))
            full_text = (
                f"技術チョークポイント: {name}。"
                f"集中度: {conc}。{desc} 対策: {mitig}。関連ECCN: {eccns}"
            )
            records.append({
                "source_type":   "geopolitical_sc",
                "source_name":   "geopolitical_chokepoints",
                "doc_id":        cp["chokepoint_id"],
                "category":      "地政学チョークポイント",
                "title":         f"チョークポイント: {name}",
                "subtitle":      cp.get("concentration", ""),
                "keywords":      [name] + cp.get("affected_eccn", []),
                "eccn_relevance": cp.get("affected_eccn", []),
                "fefta_relevance": [],
                "strategic_axis": ["地政学"],
                "criticality":   cp.get("criticality"),
                "full_text":     full_text[:2000],
                "embed_text":    _PASSAGE_PREFIX + full_text[:1200],
            })
        # 同盟エコシステム
        for al in geo.get("alliance_ecosystems", []):
            members = "・".join(al.get("members", []))
            focus   = "、".join(al.get("tech_focus", []))
            jp_rel  = al.get("jp_relevance", "")
            ec_impl = al.get("export_control_implication", "")
            full_text = (
                f"{al.get('name', '')} — メンバー: {members}。"
                f"技術フォーカス: {focus}。日本との関係: {jp_rel}。"
                f"輸出管理含意: {ec_impl}"
            )
            records.append({
                "source_type":   "geopolitical_sc",
                "source_name":   "alliance_ecosystems",
                "doc_id":        al["alliance_id"],
                "category":      "技術同盟",
                "title":         al.get("name", ""),
                "subtitle":      f"加盟国: {members}",
                "keywords":      [al.get("alliance_id", "")] + al.get("members", []) + al.get("eccn_relevant", []),
                "eccn_relevance": al.get("eccn_relevant", []),
                "fefta_relevance": [],
                "strategic_axis": ["地政学"],
                "full_text":     full_text[:2000],
                "embed_text":    _PASSAGE_PREFIX + full_text[:1200],
            })
        # 技術覇権マップ（主要国）
        power_map = geo.get("technology_power_map", {})
        for country, info in power_map.items():
            if country.startswith("_"):
                continue
            domains = "、".join(info.get("dominant_domains", [])[:4])
            leverage = info.get("key_leverage", "")
            full_text = (
                f"{country} の技術覇権ポジション。"
                f"支配的ドメイン: {domains}。核心レバレッジ: {leverage}"
            )
            records.append({
                "source_type":   "geopolitical_sc",
                "source_name":   "tech_power_map",
                "doc_id":        f"TPM-{country}",
                "category":      "技術覇権マップ",
                "title":         f"{country} 技術覇権ポジション",
                "subtitle":      leverage[:100],
                "keywords":      [country, leverage[:50]] + info.get("eccn_strongholds", []),
                "eccn_relevance": info.get("eccn_strongholds", []),
                "fefta_relevance": [],
                "strategic_axis": ["地政学"],
                "full_text":     full_text[:2000],
                "embed_text":    _PASSAGE_PREFIX + full_text[:1200],
            })
        logger.info("geopolitical_sc entries: %d", len(records) - start_count)

    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH)
    args = parser.parse_args()

    records = _load_records()
    logger.info("Layer E 総レコード数: %d", len(records))

    if args.dry_run:
        logger.info("dry-run 完了 — embed は実行しません")
        for src_type in {r["source_type"] for r in records}:
            count = sum(1 for r in records if r["source_type"] == src_type)
            logger.info("  %s: %d", src_type, count)
        return

    logger.info("モデル読み込み中: %s", _MODEL_NAME)
    model = SentenceTransformer(_MODEL_NAME)

    embed_texts = [r["embed_text"] for r in records]
    logger.info("Embedding %d テキスト (batch=%d)...", len(embed_texts), args.batch_size)
    vecs = model.encode(
        embed_texts,
        batch_size=args.batch_size,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    vecs = np.array(vecs, dtype=np.float32)
    logger.info("vecs.shape=%s", vecs.shape)

    dim = vecs.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(vecs)
    logger.info("FAISS ntotal=%d dim=%d", index.ntotal, dim)

    faiss.write_index(index, str(_OUT_INDEX))
    logger.info("index → %s", _OUT_INDEX)

    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {
        "built_at":   built_at,
        "ntotal":     index.ntotal,
        "dim":        dim,
        "model":      _MODEL_NAME,
        "source_types": list({r["source_type"] for r in records}),
        "records":    records,
    }
    _OUT_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("meta  → %s (%d records)", _OUT_META, len(records))
    logger.info("=== Layer E ビルド完了 ===")


if __name__ == "__main__":
    main()
