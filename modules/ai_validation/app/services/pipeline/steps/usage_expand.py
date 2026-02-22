from typing import Dict, Any
from sqlalchemy.orm import Session

def step_usage_expand(db: Session, transaction_id: int, run_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
    # TODO: 実装に置換
    return {"step": "usage_expand", "transaction_id": transaction_id, "run_id": run_id}
