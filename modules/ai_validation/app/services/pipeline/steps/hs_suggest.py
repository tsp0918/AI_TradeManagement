# app/services/pipeline/steps/hs_suggest.py
"""
Layer A → Layer C クロスレイヤー HS コード提案ステップ。

Layer A matrix_match の結果から「hit」判定された item_no を抽出し、
その FEFTA 項番でフィルタリングして Layer C（HS コード）を検索する。

出力:
  result["hs_suggestions"] = {
      "7": [{"hs_code": "8486", "description": "...", "score": 0.84, ...}, ...],
      "3": [...],
  }
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.db.models.ai_run import AiRun, MatrixMatch, RunType
from app.db.models.transaction import UsageRequirement
from platform_core.services.faiss_e5_service import (
    LayerCHit,
    is_ready,
    layer_c_available,
    search_layer_c,
)

logger = logging.getLogger(__name__)


def step_hs_suggest(
    db: Session,
    transaction_id: int,
    run_id: int,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    matrix_match の hit item_no × usage テキスト → Layer C HS コード提案。

    Args:
        params:
          top_k_per_item  : 項番ごとの HS 候補数 (default=5)
          min_score       : スコア下限 (default=0.50)
          decision_filter : 対象 decision 値 (default=["hit"])
    """
    top_k: int = int(params.get("top_k_per_item", 5))
    min_score: float = float(params.get("min_score", 0.50))
    decision_filter: List[str] = params.get("decision_filter", ["hit"])

    if not is_ready() or not layer_c_available():
        return {
            "step": "hs_suggest",
            "transaction_id": transaction_id,
            "run_id": run_id,
            "hs_suggestions": {},
            "note": "Layer C not ready — HS suggestions skipped",
        }

    # --- 直近 matrix_match run から item_no + 代表 usage テキストを収集 ---
    latest_mm_run = (
        db.query(AiRun)
        .filter(
            AiRun.transaction_id == transaction_id,
            AiRun.run_type == RunType.matrix_match.value,
        )
        .order_by(AiRun.id.desc())
        .first()
    )
    if not latest_mm_run:
        return {
            "step": "hs_suggest",
            "transaction_id": transaction_id,
            "run_id": run_id,
            "hs_suggestions": {},
            "note": "matrix_match run not found",
        }

    # item_no → usage テキストリスト
    item_usages: Dict[str, List[str]] = defaultdict(list)
    matches = (
        db.query(MatrixMatch)
        .filter(
            MatrixMatch.ai_run_id == latest_mm_run.id,
            MatrixMatch.layer_a_item_no.isnot(None),
        )
        .all()
    )
    for mm in matches:
        if mm.decision not in decision_filter:
            continue
        item_no = (mm.layer_a_item_no or "").strip()
        if not item_no:
            continue
        # usage テキスト取得
        if mm.usage_requirement_id:
            ur = db.get(UsageRequirement, mm.usage_requirement_id)
            if ur and ur.text:
                item_usages[item_no].append(ur.text.strip())

    if not item_usages:
        return {
            "step": "hs_suggest",
            "transaction_id": transaction_id,
            "run_id": run_id,
            "hs_suggestions": {},
            "note": "No hit item_no found in matrix_match results",
        }

    # --- item_no ごとに Layer C 検索 ---
    hs_suggestions: Dict[str, List[Dict[str, Any]]] = {}

    for item_no, texts in item_usages.items():
        # 代表クエリ: 全 usage テキストを結合（重複排除）
        seen: set[str] = set()
        unique_texts: List[str] = []
        for t in texts:
            if t not in seen:
                seen.add(t)
                unique_texts.append(t)
        query = " / ".join(unique_texts[:3])  # 先頭3件を結合

        hits: List[LayerCHit] = search_layer_c(
            query,
            top_k=top_k,
            fefta_filter=[item_no],
        )

        candidates = []
        for h in hits:
            if h.score < min_score:
                continue
            candidates.append({
                "hs_code":           h.hs_code,
                "description":       h.description,
                "chapter":           h.chapter,
                "heading":           h.heading,
                "subheading":        h.subheading,
                "level":             h.level,
                "score":             round(h.score, 4),
                "fefta_items":       h.fefta_items,
                "has_fefta_mapping": h.has_fefta_mapping,
            })

        if candidates:
            hs_suggestions[item_no] = candidates
            logger.info(
                "HS suggest: item_no=%s → %d candidates (top: %s, score=%.4f)",
                item_no,
                len(candidates),
                candidates[0]["hs_code"],
                candidates[0]["score"],
            )

    return {
        "step": "hs_suggest",
        "transaction_id": transaction_id,
        "run_id": run_id,
        "item_count": len(item_usages),
        "hs_suggestions": hs_suggestions,
        "note": f"{len(hs_suggestions)} 項番に HS 候補を生成",
    }
