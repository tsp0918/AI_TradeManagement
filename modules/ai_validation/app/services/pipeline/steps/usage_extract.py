from typing import Dict, Any
from sqlalchemy.orm import Session

def step_usage_extract(db: Session, transaction_id: int, run_id: int, params: Dict[str, Any]) -> Dict[str, Any]:
    # TODO: 実装に置換
    return {"step": "usage_extract", "transaction_id": transaction_id, "run_id": run_id}
