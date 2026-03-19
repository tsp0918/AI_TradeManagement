from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.db.models.transaction import Transaction, TransactionItem, UsageRequirement
from app.services.two_list import compute_two_lists
from app.services.pipeline.orchestrator import run_until_matrix_match

router = APIRouter(prefix="/decision", tags=["decision"])


@router.get("/{transaction_id}/faiss-candidates")
def get_faiss_candidates(
    transaction_id: int,
    top_k: int = Query(default=20, description="Layer A の top_k 件数"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Layer A（外為法 + ECCN）FAISS 検索を実行し、
    HanteiAgent の initial_candidates として使える domain_id リストを返す。

    - tx の品目名 + 用途テキストをクエリとして使う
    - FAISS が未準備の場合は empty list を返す（エージェントが全候補でフォールバック）
    """
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # クエリ文字列を構築
    parts: List[str] = []
    items = db.query(TransactionItem).filter_by(transaction_id=transaction_id).all()
    for it in items:
        if it.item_name:
            parts.append(it.item_name)
        if it.item_model:
            parts.append(it.item_model)
        if it.spec_text:
            parts.append(it.spec_text[:200])
    usages = db.query(UsageRequirement).filter_by(transaction_id=transaction_id).all()
    for u in usages:
        if u.text:
            parts.append(u.text[:300])
    query = " ".join(parts) if parts else (tx.title or "")

    try:
        from platform_core.services.faiss_e5_service import is_ready, search_layer_a
        if not is_ready():
            return {"transaction_id": transaction_id, "query": query, "candidates": [], "faiss_ready": False}

        hits = search_layer_a(query, top_k=top_k)
        candidates: List[str] = []
        seen: set = set()
        for h in hits:
            source_type = getattr(h, "source_type", "")
            if source_type == "eccn":
                eccn = (getattr(h, "extra", {}) or {}).get("eccn", "")
                did = f"ECCN::{eccn}" if eccn else ""
            else:
                item_no = getattr(h, "item_no", "")
                article_no = (getattr(h, "extra", {}) or {}).get("article_no", "")
                did = f"FEFTA::{item_no}::{source_type}::{article_no}"
            if did and did not in seen:
                seen.add(did)
                candidates.append(did)

        return {"transaction_id": transaction_id, "query": query, "candidates": candidates, "faiss_ready": True}
    except Exception as e:
        return {"transaction_id": transaction_id, "query": query, "candidates": [], "faiss_ready": False, "error": str(e)}


@router.get("/{transaction_id}/two-lists")
def get_two_lists(
    transaction_id: int,
    run_id: Optional[int] = Query(default=None, description="指定したrun_idのmatrix_matchesを使う。省略時は最新のmatrix_match runを使う"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    try:
        return compute_two_lists(db=db, transaction_id=transaction_id, run_id=run_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{transaction_id}/run-and-two-lists")
def run_and_two_lists(
    transaction_id: int,
    threshold: float = Query(default=0.75, description="matrix_match の閾値（暫定）"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    ① pipeline を matrix_match まで実行
    ② 2リスト集計を返す（intersection / expanded_only）
    """
    try:
        # pipeline（thresholdだけ上書きしたいなら orchestrator を引数化するのが綺麗）
        run_until_matrix_match(db=db, transaction_id=transaction_id)

        # 省略時は最新runを拾う設計なので run_id は渡さない
        result = compute_two_lists(db=db, transaction_id=transaction_id, run_id=None)
        return {
            "ok": True,
            "transaction_id": transaction_id,
            "threshold": threshold,
            "two_lists": result,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
