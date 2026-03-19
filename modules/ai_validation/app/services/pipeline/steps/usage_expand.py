"""
step_usage_expand:
  patent_retrieve が保存した PatentRetrieval 上位N件を読み込み、
  各特許の patent_usecases（quality_score 閾値以上）を
  UsageRequirement(source='expanded') として登録する。

  これにより matrix_match が expanded_hit を生成し、
  2リストの intersection（core & expanded 両方でヒット）が機能する。

パラメータ:
  top_k_patents       : PatentRetrieval の上位何件を使うか (default: 5)
  min_quality_score   : patent_usecases の quality_score 最低値 (default: 0.75)
  min_patent_score    : PatentRetrieval の score 最低値 (default: 0.2)
  max_expanded_per_tx : 1トランザクションあたり expanded UsageReq 上限 (default: 20)
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Any, List

from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.db.models.ai_run import PatentRetrieval
from app.db.models.patent import Patent
from app.db.models.transaction import UsageRequirement, UsageSource


def step_usage_expand(
    db: Session,
    transaction_id: int,
    run_id: int,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    top_k_patents = int(params.get("top_k_patents", 5))
    min_quality = float(params.get("min_quality_score", 0.75))
    min_patent_score = float(params.get("min_patent_score", 0.2))
    max_expanded = int(params.get("max_expanded_per_tx", 20))

    # ---- 直近の patent_retrieve run の PatentRetrieval を取得 ----
    # patent_retrieve は直前のステップとして同じオーケストレーター内で実行される。
    # run_id はそのまま使えないので（usage_expand の run_id）、
    # transaction の最新 patent_retrieve run を拾う。
    from app.db.models.ai_run import AiRun, RunType
    latest_patent_run = (
        db.query(AiRun)
        .filter(
            AiRun.transaction_id == transaction_id,
            AiRun.run_type == RunType.patent_retrieve.value,
            AiRun.status == "success",
        )
        .order_by(desc(AiRun.id))
        .first()
    )

    if not latest_patent_run:
        return {
            "step": "usage_expand",
            "transaction_id": transaction_id,
            "run_id": run_id,
            "inserted": 0,
            "note": "patent_retrieve の実行履歴がないためスキップ",
        }

    # PatentRetrieval 上位 top_k_patents 件（score 閾値以上）
    retrievals: List[PatentRetrieval] = (
        db.query(PatentRetrieval)
        .filter(
            PatentRetrieval.ai_run_id == latest_patent_run.id,
            PatentRetrieval.score >= min_patent_score,
        )
        .order_by(desc(PatentRetrieval.score))
        .limit(top_k_patents)
        .all()
    )

    if not retrievals:
        return {
            "step": "usage_expand",
            "transaction_id": transaction_id,
            "run_id": run_id,
            "inserted": 0,
            "note": f"score >= {min_patent_score} の PatentRetrieval が0件",
        }

    # ---- 既存の expanded UsageRequirement を削除して再作成 ----
    db.query(UsageRequirement).filter(
        UsageRequirement.transaction_id == transaction_id,
        UsageRequirement.source == UsageSource.expanded.value,
    ).delete(synchronize_session=False)
    db.flush()

    from app.db.models.patent import PatentUsecase

    inserted = 0
    now = datetime.utcnow()
    seen_texts: set = set()
    path_used = "none"

    # ---- Layer B FAISSメタ（abstract）をキャッシュ ----
    _layer_b_abstract_cache: dict = {}
    try:
        from platform_core.services.faiss_e5_service import _layer_b_records
        _layer_b_abstract_cache = {
            int(r["faiss_id"]): r.get("abstract", "")
            for r in _layer_b_records
            if r.get("abstract")
        }
    except Exception:
        pass

    def _add_requirement(text: str, confidence: float) -> bool:
        nonlocal inserted
        if not text or inserted >= max_expanded:
            return False
        dedup_key = text[:80]
        if dedup_key in seen_texts:
            return False
        seen_texts.add(dedup_key)
        db.add(UsageRequirement(
            transaction_id=transaction_id,
            transaction_item_id=None,
            source=UsageSource.expanded.value,
            text=text,
            normalized_text=None,
            confidence=round(confidence, 4),
            risk_tags=[],
            created_by="ai",
            created_at=now,
            updated_at=now,
        ))
        inserted += 1
        return True

    # ---- Path A: patent_id あり → patent_usecases ----
    retrievals_with_id = [r for r in retrievals if r.patent_id is not None]
    if retrievals_with_id:
        path_used = "patent_usecases"
        patent_score_map = {r.patent_id: float(r.score) for r in retrievals_with_id}
        for ret in retrievals_with_id:
            if inserted >= max_expanded:
                break
            usecases: List[PatentUsecase] = (
                db.query(PatentUsecase)
                .filter(
                    PatentUsecase.patent_id == ret.patent_id,
                    PatentUsecase.quality_score >= min_quality,
                )
                .order_by(desc(PatentUsecase.quality_score))
                .all()
            )
            for uc in usecases:
                text = (getattr(uc, "usecase_text") or "").strip()
                confidence = float(getattr(uc, "quality_score") or 0.0) * patent_score_map[ret.patent_id]
                _add_requirement(text, confidence)
                if inserted >= max_expanded:
                    break

    # ---- Path B: patent_id なし (Layer B FAISS) → abstract をフォールバック ----
    if inserted == 0:
        path_used = "layer_b_abstract"
        retrievals_layer_b = [r for r in retrievals if r.patent_id is None and r.layer_b_faiss_id is not None]
        for ret in retrievals_layer_b:
            if inserted >= max_expanded:
                break
            abstract = _layer_b_abstract_cache.get(int(ret.layer_b_faiss_id), "")
            if abstract:
                _add_requirement(abstract.strip(), float(ret.score))

    db.commit()

    return {
        "step": "usage_expand",
        "transaction_id": transaction_id,
        "run_id": run_id,
        "patent_retrieve_run_id": latest_patent_run.id,
        "patents_used": len(retrievals),
        "min_patent_score": min_patent_score,
        "min_quality_score": min_quality,
        "top_k_patents": top_k_patents,
        "max_expanded": max_expanded,
        "inserted": inserted,
        "path_used": path_used,
        "note": (
            f"PatentRetrieval 上位{len(retrievals)}件 [{path_used}] から "
            f"expanded UsageRequirement を {inserted} 件登録"
        ),
    }
