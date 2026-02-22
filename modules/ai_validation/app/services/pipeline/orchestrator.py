# app/services/pipeline/orchestrator.py
from typing import Dict, Any
from sqlalchemy.orm import Session

from app.db.models.ai_run import RunType
from app.services.pipeline.runner import execute_step

from app.services.pipeline.steps.usage_extract import step_usage_extract
from app.services.pipeline.steps.patent_retrieve import step_patent_retrieve
from app.services.pipeline.steps.usage_expand import step_usage_expand
from app.services.pipeline.steps.matrix_match import step_matrix_match


def run_until_matrix_match(db: Session, transaction_id: int, threshold: float = 0.75) -> Dict[str, Any]:
    r1 = execute_step(
        db=db,
        transaction_id=transaction_id,
        run_type=RunType.usage_extract,
        step_fn=step_usage_extract,
        params={"max_requirements": 10},
        model_name="local",
        prompt_version="usage_extract_v1",
    )

    r2 = execute_step(
        db=db,
        transaction_id=transaction_id,
        run_type=RunType.patent_retrieve,
        step_fn=step_patent_retrieve,
        params={"top_k": 10},
        model_name="local",
        prompt_version="patent_retrieve_v1",
    )

    r3 = execute_step(
        db=db,
        transaction_id=transaction_id,
        run_type=RunType.usage_expand,
        step_fn=step_usage_expand,
        params={},
        model_name="local",
        prompt_version="usage_expand_v1",
    )

    r4 = execute_step(
        db=db,
        transaction_id=transaction_id,
        run_type=RunType.matrix_match,
        step_fn=step_matrix_match,
        params={"threshold": threshold, "regime": "JP_FX", "top_k_per_usage": 10},
        model_name="local",
        prompt_version="matrix_match_v2_fx",
    )

    return {
        "usage_extract": r1,
        "patent_retrieve": r2,
        "usage_expand": r3,
        "matrix_match": r4,
    }
