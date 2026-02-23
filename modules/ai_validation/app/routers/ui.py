# app/routers/ui.py
import csv
import io
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, Request, HTTPException, Query, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.db.deps import get_db

from app.db.models.transaction import Transaction, TransactionItem, UsageRequirement
from app.db.models.ai_run import AiRun, RunType
from app.db.models.integration import ExternalEvalRequest
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


def _make_case_no_manual() -> str:
    return f"MANUAL-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"


def _create_transaction_manual(
    db: Session,
    title: str,
    product_code: str,
    description: str,
    spec_text: str,
    case_no: Optional[str] = None,
) -> Transaction:
    tx = Transaction(
        case_no=case_no or _make_case_no_manual(),
        title=title.strip() or product_code.strip() or "新規審査",
        status="draft",
    )
    db.add(tx)
    db.flush()

    if product_code.strip() or spec_text.strip():
        item = TransactionItem(
            transaction_id=tx.id,
            item_name=title.strip() or product_code.strip(),
            item_model=product_code.strip(),
            spec_text=spec_text.strip() or None,
            attachments_meta={"files": []},
        )
        db.add(item)
        db.flush()
        item_id = item.id
    else:
        item_id = None

    usage_text = description.strip() or title.strip() or product_code.strip() or "N/A"
    u = UsageRequirement(
        transaction_id=tx.id,
        transaction_item_id=item_id,
        source="core",
        text=usage_text,
        risk_tags=[],
        created_by="user",
    )
    db.add(u)
    db.flush()

    return tx


@router.get("/ui/transactions/new", response_class=HTMLResponse)
def transaction_new_form(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "transaction_new.html",
        {"request": request, "error": None},
    )


@router.post("/ui/transactions/new", response_class=HTMLResponse)
def transaction_new_submit(
    request: Request,
    db: Session = Depends(get_db),
    title: str = Form(""),
    product_code: str = Form(""),
    description: str = Form(""),
    spec_text: str = Form(""),
    case_no: str = Form(""),
):
    """手動入力で新規審査を作成（AIパイプラインは実行しない）。"""
    templates = request.app.state.templates
    if not title.strip() and not product_code.strip() and not description.strip():
        return templates.TemplateResponse(
            "transaction_new.html",
            {"request": request, "error": "品名・品番・用途のいずれかを入力してください。"},
        )
    try:
        tx = _create_transaction_manual(
            db=db,
            title=title,
            product_code=product_code,
            description=description,
            spec_text=spec_text,
            case_no=case_no.strip() or None,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        return templates.TemplateResponse(
            "transaction_new.html",
            {"request": request, "error": f"登録エラー: {e}"},
        )
    return RedirectResponse(url=f"/ui/transactions/{tx.id}", status_code=303)


@router.post("/ui/transactions/csv-import", response_class=HTMLResponse)
async def transaction_csv_import(
    request: Request,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
):
    """CSVファイルから複数の審査を一括登録（AIパイプラインは実行しない）。"""
    templates = request.app.state.templates

    raw = await file.read()
    try:
        text_content = raw.decode("utf-8-sig")  # BOM付きUTF-8も対応
    except UnicodeDecodeError:
        text_content = raw.decode("shift_jis", errors="replace")

    reader = csv.DictReader(io.StringIO(text_content))
    created_ids = []
    errors = []

    for i, row in enumerate(reader, start=2):  # 2行目から（1行目はヘッダー）
        title = (row.get("title") or row.get("品名") or "").strip()
        product_code = (row.get("product_code") or row.get("品番") or "").strip()
        description = (row.get("description") or row.get("用途") or "").strip()
        spec_text = (row.get("spec_text") or row.get("仕様") or "").strip()
        case_no = (row.get("case_no") or "").strip()

        if not title and not product_code and not description:
            errors.append(f"行 {i}: 品名・品番・用途がすべて空です。スキップしました。")
            continue

        try:
            tx = _create_transaction_manual(
                db=db,
                title=title,
                product_code=product_code,
                description=description,
                spec_text=spec_text,
                case_no=case_no or None,
            )
            db.commit()
            created_ids.append(tx.id)
        except Exception as e:
            db.rollback()
            errors.append(f"行 {i}: 登録エラー: {e}")

    return templates.TemplateResponse(
        "transaction_new.html",
        {
            "request": request,
            "csv_result": {
                "created": len(created_ids),
                "ids": created_ids[:20],
                "errors": errors,
            },
            "error": None,
        },
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

    # 2リスト結果: run_id が指定されていない場合も latest_matrix_match があれば自動計算
    two_lists: Optional[Dict[str, Any]] = None
    two_lists_error: Optional[str] = None
    effective_run_id = run_id if run_id is not None else (
        latest_matrix_match.id if latest_matrix_match else None
    )
    if effective_run_id is not None:
        try:
            two_lists = compute_two_lists(db=db, transaction_id=transaction_id, run_id=effective_run_id)
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


@router.get("/ui/external-requests", response_class=HTMLResponse)
def external_requests_page(request: Request, db: Session = Depends(get_db)):
    reqs = (
        db.query(ExternalEvalRequest)
        .order_by(desc(ExternalEvalRequest.id))
        .limit(100)
        .all()
    )
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "external_requests.html",
        {"request": request, "reqs": reqs},
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
