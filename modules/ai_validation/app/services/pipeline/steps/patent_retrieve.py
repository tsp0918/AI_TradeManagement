# app/services/pipeline/steps/patent_retrieve.py
"""
Layer B 静的 FAISS インデックス（US 特許チャンク）による patent retrieve ステップ。

変更点（MiniLM 動的ビルド → e5-large 静的インデックス）:
- モデル: intfloat/multilingual-e5-large (faiss_e5_service 経由、共有シングルトン)
- インデックス: data/staging/layer_b.index (事前構築済み静的ファイル)
- DB patents テーブルへの動的 FAISS ビルドは廃止
- PatentRetrieval 書き込み: patent_id=None、publication_number / layer_b_faiss_id を記録
- IPC コード・FEFTA 対応情報も evidence_json に保存
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.db.models.ai_run import PatentRetrieval
from app.db.models.transaction import UsageRequirement
from app.services.faiss_e5_service import LayerBHit, is_ready, search_layer_b, ntotal_layer_b


# ── Step entry ────────────────────────────────────────────────────────────────
def step_patent_retrieve(
    db: Session,
    transaction_id: int,
    run_id: int,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    usage_requirements × Layer B (US 特許) を e5-large FAISS で検索。

    - data/staging/layer_b.index を参照（静的、起動時プリロード済み）
    - 各 usage_requirement をエンコードして上位 top_k 件を取得
    - PatentRetrieval レコード: patent_id=None、publication_number / layer_b_faiss_id で識別
    """
    top_k: int = max(int(params.get("top_k_patents_per_usage", 5)), 1)

    # FAISS 未ロード時はスキップ（起動直後のウォームアップ中）
    if not is_ready():
        db.commit()
        return {
            "step": "patent_retrieve",
            "transaction_id": transaction_id,
            "run_id": run_id,
            "inserted": 0,
            "note": "FAISS e5-large model not ready yet (warming up)",
        }

    # --- delete existing retrievals for this run ---
    db.query(PatentRetrieval).filter(PatentRetrieval.ai_run_id == run_id).delete(synchronize_session=False)
    db.flush()

    usages: List[UsageRequirement] = (
        db.query(UsageRequirement)
        .filter(UsageRequirement.transaction_id == transaction_id)
        .all()
    )
    if not usages:
        db.commit()
        return {
            "step": "patent_retrieve",
            "transaction_id": transaction_id,
            "run_id": run_id,
            "inserted": 0,
            "note": "usage_requirements が0件のため検索なし",
        }

    inserted = 0
    now = datetime.utcnow()

    for u in usages:
        qt = (u.text or "").strip()
        if not qt:
            continue

        hits: List[LayerBHit] = search_layer_b(qt, top_k=top_k)

        for hit in hits:
            evidence = {
                "usage_source": u.source,
                "usage_text": qt[:500],
                "layer_b_faiss_id": hit.faiss_id,
                "publication_number": hit.publication_number,
                "country_code": hit.country_code,
                "ipc_codes": hit.ipc_codes,
                "title": hit.title,
                "abstract_snippet": hit.abstract[:500],
                "fefta_items": hit.fefta_items,
                "has_fefta_mapping": hit.has_fefta_mapping,
                "scoring": {
                    "method": "faiss_cosine(intfloat/multilingual-e5-large)",
                    "top_k": top_k,
                },
            }

            pr = PatentRetrieval(
                ai_run_id=run_id,
                usage_requirement_id=u.id,
                patent_id=None,
                publication_number=hit.publication_number[:64] if hit.publication_number else None,
                layer_b_faiss_id=hit.faiss_id,
                score=float(hit.score),
                why=json.dumps(evidence, ensure_ascii=False),
                created_at=now,
                updated_at=now,
            )
            db.add(pr)
            inserted += 1

    db.commit()

    return {
        "step": "patent_retrieve",
        "transaction_id": transaction_id,
        "run_id": run_id,
        "top_k": top_k,
        "inserted": inserted,
        "usage_count": len(usages),
        "layer_b_ntotal": ntotal_layer_b(),
        "note": "FAISS cosine(intfloat/multilingual-e5-large, Layer B) で US 特許を検索",
    }
