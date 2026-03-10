"""
transform/fefta_enricher.py
e-Gov 貨物等省令の条文データを control_nodes.json の FEFTA ノードにマッピングし、
欠損している requirement_text を補完するための中間 JSON を生成する。

出力: data/staging/fefta/fefta_supplement.json
形式: { "nodes": { "EL-1-1": "技術要件テキスト", ... } }

本ファイルへの書き込みは staging のみ。
本番 control_nodes.json への反映は builder.py + supplement ファイル経由で行う。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def build_fefta_supplement(
    control_nodes_path: Path,
    fefta_articles: list[dict[str, Any]],
    output_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, str]:
    """
    control_nodes.json の FEFTA ノード（requirement_text が null のもの）に対し、
    e-Gov 条文データから最適マッチを探してサプリメント辞書を生成する。

    マッチング戦略（優先度順）:
      1. item_id の直接一致 (EL-x-x 形式)
      2. label / description のキーワード重複スコア
      3. マッチなし → スキップ（Claude API 補完は 03_build_mappings で実施）

    Returns
    -------
    dict: { node_id: requirement_text }
    """
    # 現在の control_nodes.json をロード
    cn_data   = json.loads(control_nodes_path.read_text(encoding="utf-8"))
    all_nodes = cn_data.get("nodes", [])

    # 欠損ノード（FEFTA 制度 + requirement_text が null）のみ対象
    target_nodes = [
        n for n in all_nodes
        if n.get("regime") == "fefta" and not n.get("requirement_text")
    ]
    logger.info("FEFTA nodes with missing requirement_text: %d", len(target_nodes))

    # 条文データを item_id → article の dict に変換
    article_by_id = {a["item_id"]: a for a in fefta_articles}

    supplement: dict[str, str] = {}

    for node in target_nodes:
        node_id = node["id"]

        # Strategy 1: 直接一致
        if node_id in article_by_id:
            text = article_by_id[node_id]["requirement_text"]
            supplement[node_id] = text
            logger.debug("Direct match: %s", node_id)
            continue

        # Strategy 2: キーワードスコアマッチング
        best_article = _keyword_match(node, fefta_articles)
        if best_article:
            supplement[node_id] = best_article["requirement_text"]
            logger.debug(
                "Keyword match: %s → article_no=%s", node_id, best_article["article_no"]
            )

    logger.info(
        "Supplement generated: %d / %d nodes matched",
        len(supplement),
        len(target_nodes),
    )

    if output_path and not dry_run:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = {"_comment": "e-Gov 貨物等省令から自動生成 — builder.py リビルド後に反映", "nodes": supplement}
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Saved to %s", output_path)
    elif dry_run:
        logger.info("[DRY_RUN] Output suppressed. %d entries would be written.", len(supplement))

    return supplement


def _keyword_match(
    node: dict[str, Any],
    articles: list[dict[str, Any]],
    min_score: int = 2,
) -> dict[str, Any] | None:
    """node の label + description と article の raw_text とのキーワードスコアで最良候補を返す。"""
    node_text = " ".join(
        filter(None, [node.get("label", ""), node.get("description", "")])
    )
    node_keywords = set(_tokenize(node_text))
    if not node_keywords:
        return None

    best_score   = min_score - 1
    best_article = None

    for article in articles:
        art_keywords = set(_tokenize(article.get("raw_text", "")))
        score = len(node_keywords & art_keywords)
        if score > best_score:
            best_score   = score
            best_article = article

    return best_article


_SPLIT_RE = re.compile(r"[\s、。・,\n]+")


def _tokenize(text: str) -> list[str]:
    """簡易分かち書き（2文字以上のトークンのみ）。"""
    tokens = _SPLIT_RE.split(text)
    return [t for t in tokens if len(t) >= 2]
