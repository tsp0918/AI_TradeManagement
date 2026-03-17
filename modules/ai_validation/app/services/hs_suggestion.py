# app/services/hs_suggestion.py
"""
Layer A → Layer C クロスレイヤー HS コード提案の UI 向け計算。

two_list の item_no からまとめてHS候補を引き、テンプレートに渡せる形式に整形する。
DB には保存せず、リクエストのたびに faiss_e5_service から即時計算する（軽量）。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models.ai_run import AiRun, MatrixMatch, RunType

logger = logging.getLogger(__name__)


def compute_hs_suggestions(
    db: Session,
    transaction_id: int,
    run_id: Optional[int] = None,
    top_k: int = 5,
    min_score: float = 0.50,
    decision_filter: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    最新 matrix_match run の hit item_no ごとに Layer C HS 候補を返す。

    Returns:
        {
            "by_item": {
                "7": {
                    "item_label": "電子部品・集積回路・半導体",
                    "regime": "JP_FEFTA",
                    "candidates": [{"hs_code":..., "description":..., "score":..., ...}]
                },
                ...
            },
            "available": True/False,
            "note": "..."
        }
    """
    if decision_filter is None:
        decision_filter = ["hit"]

    # Layer C 利用可能チェック
    try:
        from platform_core.services.faiss_e5_service import (
            is_ready, layer_c_available, search_layer_c,
        )
        if not is_ready() or not layer_c_available():
            return {"by_item": {}, "available": False, "note": "Layer C not ready"}
    except ImportError:
        return {"by_item": {}, "available": False, "note": "faiss_e5_service not available"}

    # 直近 matrix_match run を特定
    from sqlalchemy import desc
    mm_run = (
        db.query(AiRun)
        .filter(
            AiRun.transaction_id == transaction_id,
            AiRun.run_type == RunType.matrix_match.value,
            *([AiRun.id == run_id] if run_id else []),
        )
        .order_by(desc(AiRun.id))
        .first()
    )
    if not mm_run:
        return {"by_item": {}, "available": False, "note": "matrix_match run not found"}

    # hit item_no → usage テキスト の収集
    from app.db.models.transaction import UsageRequirement
    item_usages: Dict[str, List[str]] = defaultdict(list)
    item_meta: Dict[str, Dict[str, str]] = {}  # item_no → {item_label, source_type}

    matches = (
        db.query(MatrixMatch)
        .filter(
            MatrixMatch.ai_run_id == mm_run.id,
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

        # Layer A メタ（item_label）を取得
        if item_no not in item_meta:
            try:
                from platform_core.services import faiss_e5_service as svc
                r = svc._layer_a_records[mm.layer_a_faiss_id] if mm.layer_a_faiss_id is not None else {}
                item_meta[item_no] = {
                    "item_label":  r.get("item_label", f"外為法 {item_no}号"),
                    "source_type": mm.layer_a_source_type or "",
                }
            except Exception:
                item_meta[item_no] = {
                    "item_label":  f"外為法 {item_no}号",
                    "source_type": mm.layer_a_source_type or "",
                }

        # usage テキスト収集
        if mm.usage_requirement_id:
            ur = db.get(UsageRequirement, mm.usage_requirement_id)
            if ur and ur.text:
                t = ur.text.strip()
                if t not in item_usages[item_no]:
                    item_usages[item_no].append(t)

    if not item_usages:
        return {"by_item": {}, "available": True, "note": "No hit item_no in latest run"}

    # item_no ごとに Layer C 検索
    _SOURCE_TYPE_TO_REGIME = {
        "law": "JP_FEFTA", "parameter": "JP_FEFTA",
        "tsutatsu": "JP_FEFTA", "eccn": "EAR_ECCN",
    }

    by_item: Dict[str, Dict[str, Any]] = {}

    for item_no, texts in item_usages.items():
        query = " / ".join(texts[:3])
        from platform_core.services.faiss_e5_service import LayerCHit, search_layer_c
        hits: List[LayerCHit] = search_layer_c(query, top_k=top_k, fefta_filter=[item_no])

        candidates = [
            {
                "hs_code":           h.hs_code,
                "description":       h.description,
                "chapter":           h.chapter,
                "heading":           h.heading,
                "subheading":        h.subheading,
                "level":             h.level,
                "score":             round(h.score, 4),
                "fefta_items":       h.fefta_items,
                "has_fefta_mapping": h.has_fefta_mapping,
            }
            for h in hits
            if h.score >= min_score
        ]

        meta = item_meta.get(item_no, {})
        by_item[item_no] = {
            "item_label": meta.get("item_label", f"外為法 {item_no}号"),
            "regime":     _SOURCE_TYPE_TO_REGIME.get(meta.get("source_type", ""), "JP_FEFTA"),
            "candidates": candidates,
        }

    return {
        "by_item":   by_item,
        "available": True,
        "note":      f"{len(by_item)} 項番 / {sum(len(v['candidates']) for v in by_item.values())} HS候補",
    }
