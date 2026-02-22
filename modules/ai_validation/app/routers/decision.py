from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.services.two_list import compute_two_lists
from app.services.pipeline.orchestrator import run_until_matrix_match

router = APIRouter(prefix="/decision", tags=["decision"])


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
