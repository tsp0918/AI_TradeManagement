import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.db.models.transaction import Transaction, TransactionItem, UsageRequirement, TransactionStatus
from app.services.two_list import compute_two_lists
from app.services.pipeline.orchestrator import run_until_matrix_match

import logging as _logging
import os as _os
_EXPORT_LICENSE_BASE = _os.environ.get("MODULE_EXPORT_LICENSE_URL", "http://localhost:8012")
_RND_BASE = _os.environ.get("MODULE_RND_ASSESSMENT_URL", "http://localhost:8003")
_ERP_WEBHOOK_URL = _os.environ.get("ERP_WEBHOOK_URL", "http://localhost:8888/gts/webhook/judgment-updated")
_ERP_WEBHOOK_BEARER = _os.environ.get("ERP_WEBHOOK_BEARER", "dev-erp-integration-key")
_logger_dec = _logging.getLogger(__name__)

router = APIRouter(prefix="/decision", tags=["decision"])


def _notify_erp_judgment(tx: Transaction, judgment_status: str) -> None:
    """
    判定完了時に ERP の /gts/webhook/judgment-updated へ結果を通知する。
    ERP_WEBHOOK_URL が未設定の場合はスキップ。fire-and-forget スレッドで実行。
    """
    if not _ERP_WEBHOOK_URL or not tx.erp_case_no:
        return

    _JUDGMENT_MAP = {
        "approved":        "APPROVED",
        "rejected":        "REJECTED",
        "requires_permit": "NEEDS_REVIEW",
        "requires_review": "NEEDS_REVIEW",
        "controlled":      "NEEDS_REVIEW",
        "clear":           "APPROVED",
    }
    normalized = _JUDGMENT_MAP.get((judgment_status or "").lower(), "NEEDS_REVIEW")

    payload = {
        "material_code": tx.linked_product_code or tx.erp_case_no or tx.case_no,
        "new_judgment":  normalized,
        "new_eccn":      tx.linked_product_eccn,
        "rationale":     judgment_status,
        "client_id":     "DEMO",
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_ERP_WEBHOOK_BEARER}",
    }

    def _post():
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(_ERP_WEBHOOK_URL, json=payload, headers=headers)
            _logger_dec.info("ERP webhook sent: tx=%d judgment=%s status=%d", tx.id, normalized, resp.status_code)
        except Exception as exc:
            _logger_dec.warning("ERP webhook failed: tx=%d %s", tx.id, exc)

    threading.Thread(target=_post, daemon=True).start()


def _auto_create_license_draft(tx_id: int, tx_case_no: str, product_code: str | None,
                                dest_country: str | None, end_use: str | None) -> None:
    """
    judgment=requires_permit 確定時に export_license モジュールへドラフトを自動作成する。
    作成成功時に linked_license_id / linked_license_status を Transaction へ書き戻す。
    fire-and-forget スレッドで実行（失敗しても審査フローは止めない）。
    """
    def _post():
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.post(
                    f"{_EXPORT_LICENSE_BASE}/api/export-licenses/draft-from-transaction",
                    json={
                        "transaction_id": tx_id,
                        "transaction_ids": [str(tx_id)],
                        "auto_created": True,
                        "reason": "requires_permit",
                    },
                )
            if r.status_code not in (200, 201):
                return
            data = r.json()
            license_id = data.get("id") or data.get("app_id")
            license_status = data.get("status", "draft")
            if not license_id:
                return
            from app.db.session import SessionLocal
            with SessionLocal() as db:
                tx = db.get(Transaction, tx_id)
                if tx and not tx.linked_license_id:
                    tx.linked_license_id = str(license_id)
                    tx.linked_license_status = license_status
                    db.commit()
        except Exception:
            pass
    threading.Thread(target=_post, daemon=True).start()


def _auto_register_deemed_export(
    tx_id: int,
    end_user_name: str | None,
    nationality: str | None,
    eccn: str | None,
    case_no: str | None,
) -> None:
    """
    みなし輸出対象人物を rnd_assessment に自動登録する。
    登録成功時に personnel_id を transaction.deemed_export_personnel_id へ書き戻す。
    fire-and-forget — 失敗しても審査フローは止めない。
    """
    if not nationality or nationality.upper() == "JP":
        return

    def _post() -> None:
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.post(
                    f"{_RND_BASE}/api/v1/personnel",
                    json={
                        "name":            end_user_name or f"EndUser-{case_no}",
                        "role":            "contractor",
                        "nationality":     nationality.upper(),
                        "residence_country": nationality.upper(),
                        "tech_access_eccn": eccn,
                        "note":            f"ai_validation 取引審査 {case_no} からの自動登録（みなし輸出判定）",
                    },
                )
            if r.status_code not in (200, 201):
                return
            personnel_id = r.json().get("personnel_id")
            if not personnel_id:
                return
            # personnel_id を transaction へ書き戻す
            from app.db.session import SessionLocal
            from app.db.models.transaction import Transaction as _Tx
            with SessionLocal() as db:
                tx = db.get(_Tx, tx_id)
                if tx and not tx.deemed_export_personnel_id:
                    tx.deemed_export_personnel_id = personnel_id
                    db.commit()
        except Exception:
            pass

    threading.Thread(target=_post, daemon=True).start()


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
    ③ ティア判定を実行し DB に保存（Tier 1 は自動承認）
    """
    from app.services.tier_determination import determine_tier

    try:
        run_until_matrix_match(db=db, transaction_id=transaction_id)
        result = compute_two_lists(db=db, transaction_id=transaction_id, run_id=None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # ── ティア判定 ───────────────────────────────────────────────────
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    eccn = tx.linked_product_eccn  # 品目管理から引き継いだECCN（なければ None）
    tier, req_steps, reason = determine_tier(
        two_list_result=result,
        eccn=eccn,
        screening_status=tx.screening_status,
        destination_country=tx.destination_country,
    )

    import json as _json
    tx.approval_tier     = tier
    tx.required_steps    = req_steps
    tx.tier_reason       = reason
    tx.tier_determined_at = datetime.utcnow()

    auto_approved = False
    if tier == 1 and tx.status != TransactionStatus.approved.value:
        # Tier 1: 自動承認
        submitted_at = datetime.utcnow()
        tx.status              = TransactionStatus.approved.value
        tx.formal_submitted_at = submitted_at
        if not tx.retention_until:
            from datetime import timedelta
            tx.retention_until = submitted_at + timedelta(days=365 * 7 + 2)
        if not tx.judgment_no and tx.case_no:
            tx.judgment_no = f"JDG-{tx.case_no}-{submitted_at.strftime('%Y%m%d')}"
        auto_approved = True

    db.commit()

    return {
        "ok": True,
        "transaction_id": transaction_id,
        "threshold": threshold,
        "two_lists": result,
        "tier": tier,
        "tier_label": {1: "自動承認", 2: "標準審査", 3: "輸出許可確認"}.get(tier, ""),
        "tier_reason": reason,
        "required_steps": req_steps,
        "auto_approved": auto_approved,
    }


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

    # ERP に判定結果を通知（erp_case_no がある場合のみ）
    _notify_erp_judgment(tx, body.overall_status)

    # requires_permit 確定時: export_license モジュールへドラフトを自動作成
    if body.overall_status == "requires_permit":
        _auto_create_license_draft(
            tx_id=transaction_id,
            tx_case_no=tx.case_no,
            product_code=tx.linked_product_code,
            dest_country=tx.destination_country,
            end_use=tx.end_use_description,
        )

    # 規制品 + 外国人エンドユーザー → みなし輸出人物登録
    _DEEMED_EXPORT_STATUSES = {"controlled", "requires_review", "requires_permit"}
    if body.overall_status in _DEEMED_EXPORT_STATUSES:
        nat = (tx.end_user_country or tx.destination_country or "").upper()
        if nat and nat != "JP":
            _auto_register_deemed_export(
                tx_id=transaction_id,
                end_user_name=tx.end_user_name or tx.counterparty_name,
                nationality=nat,
                eccn=tx.linked_product_eccn,
                case_no=tx.case_no,
            )

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

    submitted_at = datetime.utcnow()
    tx.status = TransactionStatus.approved.value
    tx.formal_submitted_at = submitted_at

    # 7年保存期限（外為法 第67条）を自動設定
    if not tx.retention_until:
        tx.retention_until = submitted_at + timedelta(days=365 * 7 + 2)  # うるう年考慮

    # 判定書番号が未設定の場合は案件番号ベースで自動採番
    if not tx.judgment_no and tx.case_no:
        tx.judgment_no = f"JDG-{tx.case_no}-{submitted_at.strftime('%Y%m%d')}"

    db.commit()
    return {
        "ok": True,
        "transaction_id": transaction_id,
        "status": tx.status,
        "formal_submitted_at": tx.formal_submitted_at.isoformat(),
        "judgment_no": tx.judgment_no,
        "retention_until": tx.retention_until.strftime("%Y-%m-%d") if tx.retention_until else None,
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

    # ティア対応の required_steps
    tier = tx.approval_tier
    required_steps = tx.required_steps or (["screening", "ai_run"] if tier is None else [])

    checklist_map = {
        "screening":        {"done": has_screening,      "label": "スクリーニング"},
        "ai_run":           {"done": has_ai_run,         "label": "AI判定実行"},
        "agent_judgment":   {"done": has_agent_judgment, "label": "エージェント判定"},
        "catchall":         {"done": has_catchall,       "label": "キャッチオール自己判定"},
        "formal_submitted": {"done": is_submitted,       "label": "正式審査提出"},
    }

    required_done = all(checklist_map.get(s, {}).get("done", False) for s in required_steps)

    return {
        "transaction_id": transaction_id,
        "status": tx.status,
        "tier": tier,
        "tier_label": {1: "自動承認", 2: "標準審査", 3: "輸出許可確認"}.get(tier, "") if tier else "",
        "tier_reason": tx.tier_reason,
        "required_steps": required_steps,
        "required_done": required_done,
        "checklist": checklist_map,
        "agent_judgment_status": tx.agent_judgment_status,
        "formal_submitted_at": tx.formal_submitted_at.isoformat() if tx.formal_submitted_at else None,
    }


# ── FDPR 判定 ────────────────────────────────────────────────────────────────────

class FDPRCheckRequest(BaseModel):
    """POST /decision/{transaction_id}/fdpr-check リクエストボディ"""
    destination_country: str
    eccn: Optional[str] = None
    us_content_pct: float = 0.0
    manufactured_using_us_tech: bool = False
    us_tech_eccns: List[str] = []
    end_user_type: str = "unknown"
    is_reexport: bool = False


@router.post("/{transaction_id}/fdpr-check")
def run_fdpr_check(
    transaction_id: int,
    body: FDPRCheckRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """FDPR（Foreign Direct Product Rule）判定を実行し、結果を保存する。"""
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    try:
        import json as _json
        from sqlalchemy import text
        from platform_core.ontology.rules.fdpr_engine import FDPRContext, evaluate_fdpr

        ctx = FDPRContext(
            transaction_id=transaction_id,
            destination_country=body.destination_country,
            eccn=body.eccn,
            us_content_pct=body.us_content_pct,
            manufactured_using_us_tech=body.manufactured_using_us_tech,
            us_tech_eccns=body.us_tech_eccns,
            end_user_type=body.end_user_type,
            is_reexport=body.is_reexport,
        )
        judgment = evaluate_fdpr(ctx)
        jdict = judgment.to_dict()

        db.execute(
            text("UPDATE transactions SET fdpr_judgment_json = :j WHERE id = :tid"),
            {"j": _json.dumps(jdict, ensure_ascii=False), "tid": transaction_id},
        )
        db.commit()
        return {"ok": True, "transaction_id": transaction_id, "fdpr_judgment": jdict}

    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{transaction_id}/fdpr-result")
def get_fdpr_result(transaction_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """最新の FDPR 判定結果を取得する。"""
    import json as _json
    from sqlalchemy import text

    row = db.execute(
        text("SELECT fdpr_judgment_json FROM transactions WHERE id = :tid"),
        {"tid": transaction_id},
    ).fetchone()

    if not row or not row[0]:
        return {"available": False, "transaction_id": transaction_id}

    try:
        jdict = _json.loads(row[0]) if isinstance(row[0], str) else row[0]
    except Exception:
        return {"available": False, "transaction_id": transaction_id}

    return {"available": True, "transaction_id": transaction_id, "fdpr_judgment": jdict}


# ── EAR 規制理由・ライセンス例外判定 ─────────────────────────────────────────

class EARCheckRequest(BaseModel):
    destination_country: str
    eccn: Optional[str] = None
    end_user_type: str = "unknown"
    is_reexport: bool = False


@router.post("/{transaction_id}/ear-check")
def run_ear_check(
    transaction_id: int,
    body: EARCheckRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """EAR Reason for Control + License Exception 判定を実行する。"""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).parents[4] / "platform-core"))
    from platform_core.ontology.rules.ear_reason_engine import EARContext, evaluate_ear

    ctx = EARContext(
        transaction_id=transaction_id,
        destination_country=body.destination_country,
        eccn=body.eccn,
        end_user_type=body.end_user_type,
        is_reexport=body.is_reexport,
    )
    judgment = evaluate_ear(ctx)
    return {"ok": True, "transaction_id": transaction_id, "ear_judgment": judgment.to_dict()}


# ── EU Dual-Use チェック ───────────────────────────────────────────────────

class EUDualUseCheckRequest(BaseModel):
    destination_country: str
    eccn: Optional[str] = None
    end_user_type: str = "unknown"
    is_intra_eu_transfer: bool = False


class LicenseStatusUpdateRequest(BaseModel):
    license_id: str
    status: str  # draft / submitted / approved / denied


@router.patch("/{transaction_id}/license-status")
def update_license_status(
    transaction_id: int,
    body: LicenseStatusUpdateRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """export_license モジュールからのコールバック: 許可証ステータスを Transaction へ反映する。"""
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    tx.linked_license_id = body.license_id
    tx.linked_license_status = body.status
    db.commit()
    return {"ok": True, "transaction_id": transaction_id, "license_status": body.status}


@router.post("/{transaction_id}/eu-dual-use-check")
def run_eu_dual_use_check(
    transaction_id: int,
    body: EUDualUseCheckRequest,
) -> Dict[str, Any]:
    """EU Dual-Use Regulation 2021/821 判定を実行する。"""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).parents[4] / "platform-core"))
    from platform_core.ontology.rules.eu_dual_use_checker import EUDualUseContext, evaluate_eu_dual_use

    ctx = EUDualUseContext(
        destination_country=body.destination_country,
        eccn=body.eccn,
        end_user_type=body.end_user_type,
        is_intra_eu_transfer=body.is_intra_eu_transfer,
    )
    judgment = evaluate_eu_dual_use(ctx)
    return {"ok": True, "transaction_id": transaction_id, "eu_judgment": judgment.to_dict()}
