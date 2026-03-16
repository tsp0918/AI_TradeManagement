# app/services/pipeline/steps/matrix_match.py
"""
Layer A 静的 FAISS インデックス（外為法 + ECCN）による matrix match ステップ。

変更点（MiniLM 動的ビルド → e5-large 静的インデックス）:
- モデル: intfloat/multilingual-e5-large (faiss_e5_service 経由、共有シングルトン)
- インデックス: data/staging/layer_a.index (事前構築済み静的ファイル)
- DB matrix_rules テーブルへの動的 FAISS ビルドは廃止
- MatrixMatch 書き込み: matrix_rule_id=None、layer_a_faiss_id / layer_a_item_no / layer_a_source_type を記録
- デフォルト threshold: 0.82 (Layer A self_recall 95%、second_score_mean 0.9518 を考慮)
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.db.models.ai_run import MatrixMatch
from app.db.models.transaction import UsageRequirement
from app.services.faiss_e5_service import LayerAHit, is_ready, search_layer_a, ntotal_layer_a


# ── Step entry ────────────────────────────────────────────────────────────────
def step_matrix_match(
    db: Session,
    transaction_id: int,
    run_id: int,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    usage_requirements × Layer A (外為法+ECCN) を e5-large FAISS でマッチング。

    - data/staging/layer_a.index を参照（静的、起動時プリロード済み）
    - 各 usage_requirement をエンコードして上位 top_k_per_usage ヒットを取得
    - score >= threshold → decision="hit"、それ以外 → "maybe"
    - MatrixMatch レコード: matrix_rule_id=None、layer_a_* フィールドで識別
    """
    threshold: float = float(params.get("threshold", 0.82))
    top_k_per_usage: int = max(int(params.get("top_k_per_usage", 10)), 1)

    # FAISS 未ロード時はスキップ（起動直後のウォームアップ中）
    if not is_ready():
        db.commit()
        return {
            "step": "matrix_match",
            "transaction_id": transaction_id,
            "run_id": run_id,
            "inserted": 0,
            "note": "FAISS e5-large model not ready yet (warming up)",
        }

    # --- delete existing matches for this run ---
    db.query(MatrixMatch).filter(MatrixMatch.ai_run_id == run_id).delete(synchronize_session=False)
    db.flush()

    usages: List[UsageRequirement] = (
        db.query(UsageRequirement)
        .filter(UsageRequirement.transaction_id == transaction_id)
        .all()
    )
    if not usages:
        db.commit()
        return {
            "step": "matrix_match",
            "transaction_id": transaction_id,
            "run_id": run_id,
            "threshold": threshold,
            "inserted": 0,
            "note": "usage_requirements が0件のため照合なし",
        }

    inserted = 0
    now = datetime.utcnow()

    for u in usages:
        qt = (u.text or "").strip()
        if not qt:
            continue

        hits: List[LayerAHit] = search_layer_a(qt, top_k=top_k_per_usage)

        for hit in hits:
            if hit.score <= 0.0:
                continue

            match_type = "core_hit" if (u.source or "").lower() == "core" else "expanded_hit"
            decision_val = "hit" if hit.score >= threshold else "maybe"

            evidence = {
                "usage_source": u.source,
                "usage_text": qt[:500],
                "layer_a_faiss_id": hit.faiss_id,
                "item_no": hit.item_no,
                "item_label": hit.item_label,
                "source_type": hit.source_type,
                "source_name": hit.source_name,
                "text_snippet": hit.full_text[:900],
                "scoring": {
                    "method": "faiss_cosine(intfloat/multilingual-e5-large)",
                    "threshold": threshold,
                    "kept_top_k_per_usage": top_k_per_usage,
                },
                "decision": decision_val,
            }

            mm = MatrixMatch(
                ai_run_id=run_id,
                matrix_rule_id=None,
                usage_requirement_id=u.id,
                layer_a_faiss_id=hit.faiss_id,
                layer_a_item_no=hit.item_no[:64] if hit.item_no else None,
                layer_a_source_type=hit.source_type[:32] if hit.source_type else None,
                match_type=match_type,
                match_score=float(hit.score),
                decision=decision_val,
                evidence_json=json.dumps(evidence, ensure_ascii=False),
                created_at=now,
                updated_at=now,
            )
            db.add(mm)
            inserted += 1

    db.commit()

    return {
        "step": "matrix_match",
        "transaction_id": transaction_id,
        "run_id": run_id,
        "threshold": threshold,
        "top_k_per_usage": top_k_per_usage,
        "inserted": inserted,
        "usage_count": len(usages),
        "layer_a_ntotal": ntotal_layer_a(),
        "note": "FAISS cosine(intfloat/multilingual-e5-large, Layer A) で外為法+ECCN とマッチング",
    }
