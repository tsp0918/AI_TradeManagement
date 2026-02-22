# app/routers/ui.py
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.db.deps import get_db

from app.db.models.transaction import Transaction
from app.db.models.ai_run import AiRun, RunType
from app.services.pipeline.orchestrator import run_until_matrix_match
from app.services.two_list import compute_two_lists

router = APIRouter(tags=["ui"])


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    # 取引一覧へ
    return RedirectResponse(url="/ui/transactions", status_code=302)


@router.get("/ui/transactions", response_class=HTMLResponse)
def transactions_page(request: Request, db: Session = Depends(get_db)):
    txs = db.query(Transaction).order_by(desc(Transaction.id)).all()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "transactions.html",
        {"request": request, "txs": txs},
    )


@router.get("/ui/transactions/{transaction_id}", response_class=HTMLResponse)
def transaction_detail_page(
    request: Request,
    transaction_id: int,
    db: Session = Depends(get_db),
    run_id: Optional[int] = Query(default=None),
):
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="transaction not found")

    # 最新run（UI表示用）
    runs = (
        db.query(AiRun)
        .filter(AiRun.transaction_id == transaction_id)
        .order_by(desc(AiRun.id))
        .limit(50)
        .all()
    )

    # 直近の matrix_match run_id（あれば）
    latest_matrix_match = (
        db.query(AiRun)
        .filter(AiRun.transaction_id == transaction_id, AiRun.run_type == RunType.matrix_match.value)
        .order_by(desc(AiRun.id))
        .first()
    )

    # 2リスト結果（任意：run_id指定があれば先に見せる）
    two_lists: Optional[Dict[str, Any]] = None
    two_lists_error: Optional[str] = None
    if run_id is not None:
        try:
            two_lists = compute_two_lists(db=db, transaction_id=transaction_id, run_id=run_id)
        except Exception as e:
            two_lists_error = str(e)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "transaction_detail.html",
        {
            "request": request,
            "tx": tx,
            "runs": runs,
            "latest_matrix_match": latest_matrix_match,
            "two_lists": two_lists,
            "two_lists_error": two_lists_error,
        },
    )


@router.post("/ui/transactions/{transaction_id}/run", response_class=HTMLResponse)
def run_pipeline_and_show(
    request: Request,
    transaction_id: int,
    db: Session = Depends(get_db),
    threshold: float = Query(default=0.75, ge=0.0, le=1.0),
):
    """
    ①〜④ pipelineを回し、最後に two_lists を作って詳細画面へ戻す
    """
    # orchestrator側が threshold を受け取れるようにしておく（後述の修正も入れてください）
    run_until_matrix_match(db=db, transaction_id=transaction_id, threshold=threshold)

    # 最新 matrix_match run を引いて、その run_id を付けて詳細へ戻す
    latest = (
        db.query(AiRun)
        .filter(AiRun.transaction_id == transaction_id, AiRun.run_type == RunType.matrix_match.value)
        .order_by(desc(AiRun.id))
        .first()
    )

    url = f"/ui/transactions/{transaction_id}"
    if latest:
        url += f"?run_id={latest.id}"

    return RedirectResponse(url=url, status_code=303)
