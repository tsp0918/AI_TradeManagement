from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.db.models.transaction import Transaction, TransactionItem, UsageRequirement, TransactionStatus
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


class RedFlagInput(BaseModel):
    rf1_unknown_end_user:        Optional[bool] = None
    rf1_note:                    Optional[str]  = None
    rf2_suspicious_end_use:      Optional[bool] = None
    rf2_note:                    Optional[str]  = None
    rf3_abnormal_price:          Optional[bool] = None
    rf3_note:                    Optional[str]  = None
    rf4_unusual_payment:         Optional[bool] = None
    rf4_note:                    Optional[str]  = None
    rf5_suspicious_routing:      Optional[bool] = None
    rf5_note:                    Optional[str]  = None
    rf6_no_technical_knowledge:  Optional[bool] = None
    rf6_note:                    Optional[str]  = None
    rf7_concern_country_routing: Optional[bool] = None
    rf7_note:                    Optional[str]  = None


class CatchallJudgmentRequest(BaseModel):
    """POST /decision/{transaction_id}/catchall-judgment のリクエストボディ"""
    session_id:          str
    destination_country: Optional[str]  = None   # ISO alpha-2
    end_user_name:       Optional[str]  = None
    end_user_country:    Optional[str]  = None   # ISO alpha-2
    consignee_name:      Optional[str]  = None
    inform_received:     Optional[bool] = None
    inform_note:         Optional[str]  = None
    end_use_description: Optional[str]  = None
    euc_obtained:        Optional[bool] = None
    red_flags:           RedFlagInput   = RedFlagInput()
    evaluator_note:      Optional[str]  = None


@router.post("/{transaction_id}/catchall-judgment")
def run_catchall_judgment(
    transaction_id: int,
    body: CatchallJudgmentRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    キャッチオール規制（輸出令第4条）自己判定を実行する。

    主な用途:
      - FAISS Two-List の結果が LOW（リスト規制候補なし）の取引に対して呼び出す
      - Red Flag 7項目 Y/N + 仕向地情報 + インフォーム通知有無を入力として受け取る
      - 輸出令第4条（大量破壊兵器等キャッチオール）および
        第4条の2（通常兵器キャッチオール）の該当性を Symbolic ルールで判定する
    """
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    try:
        from platform_core.ontology.models.catchall import CatchallContext, RedFlagAnswers
        from platform_core.ontology.rules.catchall_engine import evaluate_catchall
        from app.db.models.catchall_assessment import CatchallAssessment

        rf_data = body.red_flags.model_dump()
        ctx = CatchallContext(
            transaction_id=transaction_id,
            session_id=body.session_id,
            destination_country=body.destination_country,
            end_user_name=body.end_user_name,
            end_user_country=body.end_user_country,
            consignee_name=body.consignee_name,
            inform_received=body.inform_received,
            inform_note=body.inform_note,
            end_use_description=body.end_use_description,
            euc_obtained=body.euc_obtained,
            red_flags=RedFlagAnswers(**rf_data),
            evaluator_note=body.evaluator_note,
        )
        judgment = evaluate_catchall(ctx)

        # DB 保存
        record = CatchallAssessment(
            transaction_id=transaction_id,
            session_id=body.session_id,
            verdict=judgment.verdict.value,
            verdict_label=judgment.verdict_label,
            verdict_detail=judgment.verdict_detail,
            destination_country=judgment.destination_country,
            destination_risk_level=judgment.destination_risk_level.value if judgment.destination_risk_level else None,
            is_concern_country=int(judgment.is_concern_country),
            red_flag_positive_count=judgment.red_flag_positive_count,
            judgment_json=judgment.to_dict(),
            evaluated_at=judgment.evaluated_at,
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        return {
            "ok": True,
            "transaction_id": transaction_id,
            "assessment_id": record.id,
            "catchall_judgment": judgment.to_dict(),
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{transaction_id}/catchall-result")
def get_catchall_result(
    transaction_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """最新のキャッチオール判定結果を取得する（HanteiAgent / DAP チャット用）。"""
    from sqlalchemy import desc
    from app.db.models.catchall_assessment import CatchallAssessment

    record = (
        db.query(CatchallAssessment)
        .filter(CatchallAssessment.transaction_id == transaction_id)
        .order_by(desc(CatchallAssessment.id))
        .first()
    )
    if not record:
        return {"available": False, "transaction_id": transaction_id}

    return {
        "available": True,
        "transaction_id": transaction_id,
        "assessment_id": record.id,
        "verdict": record.verdict,
        "verdict_label": record.verdict_label,
        "verdict_detail": record.verdict_detail,
        "destination_country": record.destination_country,
        "destination_risk_level": record.destination_risk_level,
        "is_concern_country": bool(record.is_concern_country),
        "red_flag_positive_count": record.red_flag_positive_count,
        "evaluated_at": record.evaluated_at.isoformat() if record.evaluated_at else None,
        "judgment": record.judgment_json,
    }


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


# ── エージェント判定結果の保存 ───────────────────────────────────────────

class AgentJudgmentSaveRequest(BaseModel):
    overall_status: str          # controlled / not_controlled / requires_review / pending
    session_id: Optional[str] = None


@router.post("/{transaction_id}/save-agent-judgment")
def save_agent_judgment(
    transaction_id: int,
    body: AgentJudgmentSaveRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    HanteiAgent の最終判定結果を Transaction に保存する。
    判定完了後に status を in_review へ遷移させる。
    """
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    tx.agent_judgment_status = body.overall_status
    tx.agent_judged_at = datetime.utcnow()

    # draft の場合のみ in_review へ遷移（already in_review/approved はそのまま）
    if tx.status == TransactionStatus.draft.value:
        tx.status = TransactionStatus.in_review.value

    db.commit()
    return {
        "ok": True,
        "transaction_id": transaction_id,
        "agent_judgment_status": tx.agent_judgment_status,
        "status": tx.status,
    }


@router.post("/{transaction_id}/submit-formal-review")
def submit_formal_review(
    transaction_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    正式審査へ提出する。status を approved へ遷移させ、提出日時を記録する。
    """
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    if tx.status == TransactionStatus.approved.value:
        return {"ok": True, "transaction_id": transaction_id, "status": tx.status, "already_approved": True}

    tx.status = TransactionStatus.approved.value
    tx.formal_submitted_at = datetime.utcnow()
    db.commit()
    return {
        "ok": True,
        "transaction_id": transaction_id,
        "status": tx.status,
        "formal_submitted_at": tx.formal_submitted_at.isoformat(),
    }


@router.get("/{transaction_id}/review-checklist")
def get_review_checklist(
    transaction_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    審査チェックリストの現在状態を返す。
    """
    from app.db.models.ai_run import AiRun, RunType
    from app.db.models.catchall_assessment import CatchallAssessment

    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    has_screening = bool(tx.screening_status)
    has_ai_run = db.query(AiRun).filter(
        AiRun.transaction_id == transaction_id,
        AiRun.run_type == RunType.matrix_match.value,
    ).first() is not None
    has_agent_judgment = bool(tx.agent_judged_at)
    has_catchall = db.query(CatchallAssessment).filter(
        CatchallAssessment.transaction_id == transaction_id
    ).first() is not None
    is_submitted = tx.status == TransactionStatus.approved.value

    return {
        "transaction_id": transaction_id,
        "status": tx.status,
        "checklist": {
            "screening":        {"done": has_screening,      "label": "スクリーニング実施"},
            "ai_run":           {"done": has_ai_run,         "label": "AI判定（FAISS照合）実行"},
            "agent_judgment":   {"done": has_agent_judgment, "label": "エージェント対話・判定完了"},
            "catchall":         {"done": has_catchall,       "label": "キャッチオール自己判定"},
            "formal_submitted": {"done": is_submitted,       "label": "正式審査へ提出"},
        },
        "agent_judgment_status": tx.agent_judgment_status,
        "formal_submitted_at": tx.formal_submitted_at.isoformat() if tx.formal_submitted_at else None,
    }
