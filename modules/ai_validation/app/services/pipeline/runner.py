from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.db.models.ai_run import AiRun, RunStatus, RunType


@contextmanager
def db_transaction(db: Session):
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


def create_run(
    db: Session,
    transaction_id: int,
    run_type: RunType,
    model_name: Optional[str] = None,
    prompt_version: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
) -> AiRun:
    run = AiRun(
        transaction_id=transaction_id,
        run_type=run_type.value,
        status=RunStatus.running.value,
        model_name=model_name,
        prompt_version=prompt_version,
        params=params or {},
        started_at=datetime.utcnow(),
    )
    db.add(run)
    db.flush()
    return run


def finalize_run_success(db: Session, run: AiRun):
    run.status = RunStatus.success.value
    run.finished_at = datetime.utcnow()
    db.add(run)


def finalize_run_failed(db: Session, run: AiRun, error: str):
    run.status = RunStatus.failed.value
    run.finished_at = datetime.utcnow()
    run.error = error[:8000]
    db.add(run)


def execute_step(
    db: Session,
    transaction_id: int,
    run_type: RunType,
    step_fn: Callable[[Session, int, int, Dict[str, Any]], Dict[str, Any]],
    *,
    model_name: Optional[str] = None,
    prompt_version: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    params = params or {}

    # run作成だけ先に確定
    with db_transaction(db):
        run = create_run(
            db=db,
            transaction_id=transaction_id,
            run_type=run_type,
            model_name=model_name,
            prompt_version=prompt_version,
            params=params,
        )

    try:
        with db_transaction(db):
            result = step_fn(db, transaction_id, run.id, params)
            finalize_run_success(db, run)
        return {"run_id": run.id, "result": result}
    except Exception as e:
        with db_transaction(db):
            finalize_run_failed(db, run, error=repr(e))
        raise
