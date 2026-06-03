# app/routers/ui.py
import csv
import io
import logging
from datetime import datetime
from typing import Optional, Dict, Any

import httpx
from fastapi import APIRouter, Depends, Request, HTTPException, Query, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.db.models.transaction import Transaction

logger = logging.getLogger(__name__)

import os as _os
SCREENING_BASE         = _os.environ.get("MODULE_SCREENING_URL",         "http://localhost:8005")
AI_CLASSIFICATION_BASE = _os.environ.get("MODULE_AI_CLASSIFICATION_URL", "http://localhost:8002")


def _auto_screen(db: Session, tx: Transaction) -> None:
    """取引先名が設定されていれば screening を自動実行して結果を保存する（非致命的）。"""
    company = (tx.counterparty_name or "").strip()
    if not company:
        return
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(f"{SCREENING_BASE}/api/screen",
                            json={"company_name": company, "threshold": 0.75})
        if r.status_code == 200:
            data = r.json()
            tx.screening_result_id = str(data["id"])
            tx.screening_status   = data["result_status"]
            db.flush()
    except Exception:
        pass


def _fetch_product_eccn(product_code: str) -> Optional[str]:
    """ai_classification から品目コードでECCNを取得する（非致命的）。"""
    if not product_code:
        return None
    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.get(f"{AI_CLASSIFICATION_BASE}/api/products/by-code/{product_code}")
        if r.status_code == 200:
            return r.json().get("eccn") or None
    except Exception:
        pass
    return None

from app.db.deps import get_db

from app.db.models.transaction import Transaction, TransactionItem, UsageRequirement
from app.db.models.ai_run import AiRun, RunType
from app.db.models.integration import ExternalEvalRequest
from app.db.models.catchall_assessment import CatchallAssessment
from app.services.pipeline.orchestrator import run_until_matrix_match
from app.services.two_list import compute_two_lists
from app.services.hs_suggestion import compute_hs_suggestions
from app.services.export_report import build_csv, build_pdf

router = APIRouter(tags=["ui"])


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    # 取引一覧へ
    return RedirectResponse(url="/ui/transactions", status_code=302)


@router.get("/ui/transactions", response_class=HTMLResponse)
def transactions_page(request: Request, db: Session = Depends(get_db)):
    txs = db.query(Transaction).order_by(desc(Transaction.id)).all()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "transactions.html", {"txs": txs},
    )


def _make_case_no_manual() -> str:
    return f"MANUAL-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"


def _build_chain(db: Session, tx: Transaction) -> list:
    """parent_transaction_id を辿って祖先チェーンを構築する（古い順）。"""
    source_labels = {
        "rnd_assessment": "R&D案件",
        "ai_classification": "品目管理",
        "manual": "手動起案",
    }

    def _risk_of(t: Transaction) -> str:
        """最新 matrix_match run の AiRun 存在から risk を簡易推定。"""
        latest = (
            db.query(AiRun)
            .filter(AiRun.transaction_id == t.id, AiRun.run_type == RunType.matrix_match.value)
            .order_by(desc(AiRun.id))
            .first()
        )
        if not latest:
            return "none"
        try:
            tl = compute_two_lists(db=db, transaction_id=t.id, run_id=latest.id)
            counts = tl.get("counts") or {}
            if (counts.get("intersection") or 0) > 0:
                return "danger"
            if (counts.get("core_only") or 0) > 0:
                return "warn"
            return "safe"
        except Exception:
            return "none"

    chain = []
    visited = set()
    cursor = tx
    while cursor and cursor.id not in visited:
        visited.add(cursor.id)
        risk = _risk_of(cursor)
        risk_label = {"danger": "要注意", "warn": "照合候補", "safe": "クリア", "none": "未実行"}.get(risk, "")
        chain.append({
            "id": cursor.id,
            "case_no": cursor.case_no or f"#{cursor.id}",
            "source_module": cursor.source_module or "manual",
            "source_label": source_labels.get(cursor.source_module or "manual", "審査"),
            "is_current": cursor.id == tx.id,
            "risk": risk,
            "risk_label": risk_label,
            "created_at": cursor.created_at.strftime("%Y-%m-%d") if cursor.created_at else None,
            "title": cursor.title or "",
        })
        if cursor.parent_transaction_id:
            cursor = db.query(Transaction).filter(Transaction.id == cursor.parent_transaction_id).first()
        else:
            break
    chain.reverse()  # 祖先が先（古い順）
    return chain


def _create_transaction_manual(
    db: Session,
    title: str,
    product_code: str,
    description: str,
    spec_text: str,
    case_no: Optional[str] = None,
    counterparty_name: Optional[str] = None,
    parent_transaction_id: Optional[int] = None,
    evaluator_name: Optional[str] = None,
    evaluator_title: Optional[str] = None,
    destination_country: Optional[str] = None,
    end_user_name: Optional[str] = None,
    end_use_description: Optional[str] = None,
) -> Transaction:
    resolved_case_no = case_no or _make_case_no_manual()
    tx = Transaction(
        case_no=resolved_case_no,
        title=title.strip() or product_code.strip() or "新規審査",
        status="draft",
        counterparty_name=counterparty_name.strip() if counterparty_name else None,
        source_module="manual",
        parent_transaction_id=parent_transaction_id,
        evaluator_name=evaluator_name.strip() if evaluator_name else None,
        evaluator_title=evaluator_title.strip() if evaluator_title else None,
        destination_country=destination_country.strip().upper() if destination_country else None,
        end_user_name=end_user_name.strip() if end_user_name else None,
        end_use_description=end_use_description.strip() if end_use_description else None,
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
    return templates.TemplateResponse(request, "transaction_new.html", {"error": None},
    )


@router.get("/api/products/search")
def products_search_proxy(q: str = Query(default="")):
    """ai_classification の品目検索を中継する（transaction_new.html 品目ピッカー用）。"""
    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.get(f"{AI_CLASSIFICATION_BASE}/api/products/json-search",
                           params={"q": q})
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


@router.get("/api/transactions/search")
def transactions_search(
    q: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """前段審査参照フィールドの検索用。q が空なら直近20件を返す。"""
    query = db.query(Transaction).order_by(desc(Transaction.id))
    if q.strip():
        keyword = f"%{q.strip()}%"
        from sqlalchemy import or_
        query = query.filter(
            or_(
                Transaction.case_no.ilike(keyword),
                Transaction.title.ilike(keyword),
            )
        )
    results = query.limit(20).all()

    def _infer_source(t: Transaction) -> str:
        """source_module が未設定の場合、case_no プレフィックスから推定する。"""
        if t.source_module:
            return t.source_module
        cn = (t.case_no or "").upper()
        if cn.startswith("RND-"):
            return "rnd_assessment"
        if cn.startswith("UI-"):
            return "ai_classification"
        return "manual"

    return [
        {
            "id": t.id,
            "case_no": t.case_no,
            "title": t.title,
            "source_module": _infer_source(t),
            "created_at": t.created_at.strftime("%Y-%m-%d") if t.created_at else None,
        }
        for t in results
    ]


@router.post("/ui/transactions/new", response_class=HTMLResponse)
def transaction_new_submit(
    request: Request,
    db: Session = Depends(get_db),
    title: str = Form(""),
    product_code: str = Form(""),
    description: str = Form(""),
    spec_text: str = Form(""),
    case_no: str = Form(""),
    counterparty_name: Optional[str] = Form(None),
    parent_transaction_id: Optional[int] = Form(None),
    evaluator_name: Optional[str] = Form(None),
    evaluator_title: Optional[str] = Form(None),
    destination_country: Optional[str] = Form(None),
    end_user_name: Optional[str] = Form(None),
    end_use_description: Optional[str] = Form(None),
    linked_product_code: Optional[str] = Form(None),
    is_new_product_entry: Optional[str] = Form(None),  # checkbox: "1" if checked
):
    """手動入力で新規審査を作成（AIパイプラインは実行しない）。"""
    templates = request.app.state.templates

    new_entry = bool(is_new_product_entry)
    effective_product_code = linked_product_code.strip() if linked_product_code else product_code.strip()

    # 品目未連携かつ同時登録チェックなし → エラー
    if not new_entry and not effective_product_code:
        return templates.TemplateResponse(
            request, "transaction_new.html",
            {"error": "品目管理から品目を選択するか、「品目同時登録」にチェックして品番を入力してください。"},
        )

    if not title.strip() and not effective_product_code and not description.strip():
        return templates.TemplateResponse(request, "transaction_new.html",
                                          {"error": "品名・品番・用途のいずれかを入力してください。"})

    try:
        tx = _create_transaction_manual(
            db=db,
            title=title,
            product_code=effective_product_code,
            description=description,
            spec_text=spec_text,
            case_no=case_no.strip() or None,
            counterparty_name=counterparty_name,
            parent_transaction_id=parent_transaction_id,
            evaluator_name=evaluator_name,
            evaluator_title=evaluator_title,
            destination_country=destination_country,
            end_user_name=end_user_name,
            end_use_description=end_use_description,
        )
        # 品目連携情報を保存
        if effective_product_code:
            tx.linked_product_code  = effective_product_code
            tx.is_new_product_entry = 1 if new_entry else 0
            if not new_entry:
                eccn = _fetch_product_eccn(effective_product_code)
                tx.linked_product_eccn = eccn
        _auto_screen(db, tx)
        db.commit()
    except Exception as e:
        db.rollback()
        return templates.TemplateResponse(request, "transaction_new.html", {"error": f"登録エラー: {e}"})
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
            counterparty = (row.get("counterparty_name") or row.get("取引先") or "").strip()
            tx = _create_transaction_manual(
                db=db,
                title=title,
                product_code=product_code,
                description=description,
                spec_text=spec_text,
                case_no=case_no or None,
                counterparty_name=counterparty or None,
            )
            _auto_screen(db, tx)
            db.commit()
            created_ids.append(tx.id)
        except Exception as e:
            db.rollback()
            errors.append(f"行 {i}: 登録エラー: {e}")

    return templates.TemplateResponse(
        request, "transaction_new.html",
        {
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

    # 品目・用途要件（取引情報表示用）
    items = (
        db.query(TransactionItem)
        .filter(TransactionItem.transaction_id == transaction_id)
        .order_by(TransactionItem.id)
        .all()
    )
    usages = (
        db.query(UsageRequirement)
        .filter(UsageRequirement.transaction_id == transaction_id)
        .order_by(UsageRequirement.id)
        .all()
    )

    # 2リスト結果: run_id が指定されていない場合も latest_matrix_match があれば自動計算
    two_lists: Optional[Dict[str, Any]] = None
    two_lists_error: Optional[str] = None
    hs_suggestions: Optional[Dict[str, Any]] = None
    effective_run_id = run_id if run_id is not None else (
        latest_matrix_match.id if latest_matrix_match else None
    )
    if effective_run_id is not None:
        try:
            two_lists = compute_two_lists(db=db, transaction_id=transaction_id, run_id=effective_run_id)
        except Exception as e:
            two_lists_error = str(e)
        try:
            hs_suggestions = compute_hs_suggestions(
                db=db, transaction_id=transaction_id, run_id=effective_run_id
            )
        except Exception:
            hs_suggestions = None

    # ハイライトテーブル用: 全マッチをカテゴリ付きでスコア降順ソート
    highlight_rows = []
    overall_risk = "none"
    overall_label = "未実行"
    if two_lists:
        for it in two_lists.get("intersection", []):
            highlight_rows.append({**it, "category": "intersection"})
        for it in two_lists.get("core_only", []):
            highlight_rows.append({**it, "category": "core_only"})
        for it in two_lists.get("expanded_only", []):
            highlight_rows.append({**it, "category": "expanded_only"})
        _cat_order = {"intersection": 0, "core_only": 1, "expanded_only": 2}
        highlight_rows.sort(
            key=lambda x: (_cat_order.get(x.get("category", ""), 99), -float(x.get("max_score") or 0))
        )

        c = two_lists.get("counts", {})
        if c.get("intersection", 0) > 0:
            overall_risk = "danger"
            overall_label = f"要注意: 両リスト合致 {c['intersection']} 件"
        elif c.get("core_only", 0) > 0:
            overall_risk = "warn"
            overall_label = f"照合候補あり: {c.get('core_only', 0)} 件"
        elif c.get("expanded_only", 0) > 0:
            overall_risk = "info"
            overall_label = f"参考情報: {c['expanded_only']} 件"
        else:
            overall_risk = "safe"
            overall_label = "ヒットなし"

    chain = _build_chain(db, tx)

    # 最新のキャッチオール自己判定結果
    latest_catchall = (
        db.query(CatchallAssessment)
        .filter(CatchallAssessment.transaction_id == transaction_id)
        .order_by(desc(CatchallAssessment.id))
        .first()
    )

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "transaction_detail.html",
        {
            "tx": tx,
            "items": items,
            "usages": usages,
            "runs": runs,
            "latest_matrix_match": latest_matrix_match,
            "two_lists": two_lists,
            "two_lists_error": two_lists_error,
            "highlight_rows": highlight_rows,
            "overall_risk": overall_risk,
            "overall_label": overall_label,
            "chain": chain,
            "faiss_ready": getattr(request.app.state, "faiss_ready", False),
            "hs_suggestions": hs_suggestions,
            "latest_catchall": latest_catchall,
            "platform_url": __import__("os").getenv("MODULE_PLATFORM_URL", "http://localhost:8000"),
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
    return templates.TemplateResponse(request, "external_requests.html", {"reqs": reqs},
    )


def _auto_catchall(db: Session, tx: Transaction, two_lists: dict) -> None:
    """AI run 後、キャッチオール推奨かつ未判定なら自動で初回評価を実行する。

    - 仕向国が設定されていれば destination_country を渡す
    - スクリーニング結果が match/possible_match なら rf7（懸念国経由）を True に
    - 既に CatchallAssessment がある場合はスキップ
    """
    if not two_lists.get("catchall_recommended"):
        return
    existing = db.query(CatchallAssessment).filter(
        CatchallAssessment.transaction_id == tx.id
    ).first()
    if existing:
        return

    try:
        from platform_core.ontology.models.catchall import (
            CatchallContext, RedFlagAnswers,
        )
        from platform_core.ontology.rules.catchall_engine import evaluate_catchall

        rf7 = tx.screening_status in ("match", "possible_match")
        ctx = CatchallContext(
            transaction_id=tx.id,
            destination_country=tx.destination_country or None,
            inform_notified=False,
            red_flags=RedFlagAnswers(rf7_concern_country_routing=rf7),
        )
        judgment = evaluate_catchall(ctx)

        record = CatchallAssessment(
            transaction_id=tx.id,
            verdict=judgment.verdict.value,
            catchall_type=judgment.catchall_type.value if judgment.catchall_type else None,
            destination_country=judgment.destination_country,
            destination_risk_level=judgment.country_risk_level.value if judgment.country_risk_level else None,
            red_flag_positive_count=judgment.red_flag_positive_count,
            judgment_json=judgment.to_dict(),
            evaluated_by="auto",
        )
        db.add(record)
        db.flush()
        logger.info(
            "auto-catchall tx=%d dest=%s verdict=%s rf7=%s",
            tx.id, tx.destination_country, judgment.verdict.value, rf7,
        )
    except Exception as exc:
        logger.warning("auto-catchall failed tx=%d: %s", tx.id, exc)


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
    # e5-large モデルのプリロードが完了していない場合は実行を拒否
    if not getattr(request.app.state, "faiss_ready", False):
        raise HTTPException(
            status_code=503,
            detail="AI モデルをロード中です。起動後しばらく待ってから再実行してください（通常 20〜30 秒）。",
        )
    run_until_matrix_match(db=db, transaction_id=transaction_id, threshold=threshold)

    # 最新 matrix_match run を引いて two_lists を計算
    latest = (
        db.query(AiRun)
        .filter(AiRun.transaction_id == transaction_id, AiRun.run_type == RunType.matrix_match.value)
        .order_by(desc(AiRun.id))
        .first()
    )

    # AI run 完了後にキャッチオール自動評価（LOW 判定 + 仕向国あり の場合）
    if latest:
        try:
            tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
            two_lists = compute_two_lists(db=db, transaction_id=transaction_id, run_id=latest.id)
            if tx:
                _auto_catchall(db=db, tx=tx, two_lists=two_lists)
                db.commit()
        except Exception as exc:
            logger.warning("post-run auto_catchall error tx=%d: %s", transaction_id, exc)

    url = f"/ui/transactions/{transaction_id}"
    if latest:
        url += f"?run_id={latest.id}"

    return RedirectResponse(url=url, status_code=303)


@router.get("/ui/transactions/{transaction_id}/export/csv")
def export_csv(
    transaction_id: int,
    db: Session = Depends(get_db),
    run_id: Optional[int] = Query(default=None),
):
    """該非判定結果を CSV でダウンロード。"""
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="transaction not found")

    latest = (
        db.query(AiRun)
        .filter(AiRun.transaction_id == transaction_id, AiRun.run_type == RunType.matrix_match.value)
        .order_by(desc(AiRun.id))
        .first()
    )
    effective_run_id = run_id or (latest.id if latest else None)
    if effective_run_id is None:
        raise HTTPException(status_code=400, detail="AI解析がまだ実行されていません。")

    two_lists = compute_two_lists(db=db, transaction_id=transaction_id, run_id=effective_run_id)
    run_at = latest.finished_at if latest else None

    csv_text = build_csv(tx=tx, two_lists=two_lists, run_at=run_at)

    filename = f"report_{tx.case_no or tx.id}_{datetime.now().strftime('%Y%m%d%H%M')}.csv"
    return StreamingResponse(
        iter([csv_text.encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/ui/transactions/{transaction_id}/export/pdf")
def export_pdf(
    request: Request,
    transaction_id: int,
    db: Session = Depends(get_db),
    run_id: Optional[int] = Query(default=None),
):
    """該非判定結果を PDF でダウンロード。"""
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="transaction not found")

    latest = (
        db.query(AiRun)
        .filter(AiRun.transaction_id == transaction_id, AiRun.run_type == RunType.matrix_match.value)
        .order_by(desc(AiRun.id))
        .first()
    )
    effective_run_id = run_id or (latest.id if latest else None)
    if effective_run_id is None:
        raise HTTPException(status_code=400, detail="AI解析がまだ実行されていません。")

    two_lists = compute_two_lists(db=db, transaction_id=transaction_id, run_id=effective_run_id)
    run_at = latest.finished_at if latest else None

    # 最新のキャッチオール自己判定をPDFに反映
    latest_ca = (
        db.query(CatchallAssessment)
        .filter(CatchallAssessment.transaction_id == transaction_id)
        .order_by(desc(CatchallAssessment.id))
        .first()
    )
    catchall_judgment = None
    if latest_ca and latest_ca.judgment_json:
        try:
            from platform_core.ontology.models.catchall import CatchallJudgment
            catchall_judgment = CatchallJudgment(**latest_ca.judgment_json)
        except Exception:
            pass

    templates = request.app.state.templates
    try:
        pdf_bytes = build_pdf(
            tx=tx, two_lists=two_lists, run_at=run_at,
            templates=templates, catchall_judgment=catchall_judgment,
        )
    except Exception as exc:
        logger.error("PDF generation failed for tx %d: %s", transaction_id, exc)
        raise HTTPException(status_code=500, detail=f"PDF生成エラー: {exc}")

    filename = f"report_{tx.case_no or tx.id}_{datetime.now().strftime('%Y%m%d%H%M')}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/ui/transactions/{transaction_id}/run-screening")
def run_screening(
    transaction_id: int,
    db: Session = Depends(get_db),
):
    """取引先企業名をスクリーニングモジュールに送信して結果を保存する。"""
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="transaction not found")
    if not tx.counterparty_name:
        raise HTTPException(status_code=400, detail="counterparty_name が未設定です")

    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(
                f"{SCREENING_BASE}/api/screen",
                json={"company_name": tx.counterparty_name, "threshold": 0.75},
            )
        if r.status_code == 200:
            data = r.json()
            tx.screening_result_id = str(data["id"])
            tx.screening_status = data["result_status"]
            db.commit()
        else:
            logger.warning("Screening API returned %d for tx %d", r.status_code, transaction_id)
    except Exception as exc:
        logger.warning("Screening call failed for tx %d: %s", transaction_id, exc)

    return RedirectResponse(url=f"/ui/transactions/{transaction_id}", status_code=303)
