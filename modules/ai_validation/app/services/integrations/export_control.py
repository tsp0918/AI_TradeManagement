# app/services/integrations/export_control.py
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.session import SessionLocal
from app.db.models.integration import ExternalEvalRequest
from app.db.models.transaction import Transaction, TransactionItem, UsageRequirement

from app.services.pipeline.orchestrator import run_until_matrix_match


DEFAULT_THRESHOLD = 0.75


# =============================================================================
# helpers
# =============================================================================
def _utc_ts() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _json_loads_safe(v: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return default
    return default


def _json_dumps_safe(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def _make_case_no(product_id: int) -> str:
    return f"UI-{product_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"


def _build_spec_text(payload: Dict[str, Any]) -> str:
    """
    UI payload を PoC向けに 1つの spec_text にまとめる。
    （後で「要約」「サイズ制限」「別テーブル管理」に差し替え可能）
    """
    parts: List[str] = []
    parts.append(f"code: {payload.get('code')}")
    parts.append(f"name: {payload.get('name')}")

    if payload.get("item_class"):
        parts.append(f"item_class: {payload.get('item_class')}")
    if payload.get("hs_code"):
        parts.append(f"hs_code: {payload.get('hs_code')}")
    if payload.get("eccn"):
        parts.append(f"eccn: {payload.get('eccn')}")

    desc = (payload.get("description") or "").strip()
    if desc:
        parts.append("description:\n" + desc)

    # 大きくなりがちなフィールド（PoCではそのまま）
    bom = (payload.get("bom_json") or "").strip()
    if bom:
        parts.append("bom_json:\n" + bom)

    raw = (payload.get("regulation_ai_raw") or "").strip()
    if raw:
        parts.append("regulation_ai_raw:\n" + raw)

    return "\n\n".join(parts).strip()


# =============================================================================
# Transaction builder (UI payload -> AI side transaction)
# =============================================================================
def create_transaction_from_payload(db: Session, payload: Dict[str, Any]) -> int:
    """
    UI(Product) payload → AI側 Transaction / Item / UsageRequirement を最小で作成
    """
    product_id = int(payload["product_id"])
    case_no = _make_case_no(product_id)
    title = f"External Request: {payload.get('code')} {payload.get('name')}"

    tx = Transaction(case_no=case_no, title=title, status="draft")
    db.add(tx)
    db.flush()

    item = TransactionItem(
        transaction_id=tx.id,
        item_name=str(payload.get("name") or "Item"),
        item_model=str(payload.get("code") or ""),
        spec_text=_build_spec_text(payload),
        attachments_meta={"files": []},
    )
    db.add(item)
    db.flush()

    usage_text = (payload.get("description") or "").strip()
    if not usage_text:
        usage_text = f"{payload.get('name')} / {payload.get('code')}"

    u = UsageRequirement(
        transaction_id=tx.id,
        transaction_item_id=item.id,
        source="core",
        text=usage_text,
        risk_tags=[],  # NOT NULL 対策
        created_by="ui",
    )
    db.add(u)
    db.flush()

    return tx.id


# =============================================================================
# DB fetch helpers (run_type別に最新を拾う / SQLAlchemy2 text()対応)
# =============================================================================
def _latest_ai_run_id(db: Session, transaction_id: int, run_type: str) -> Optional[int]:
    """
    ai_runs: transaction_id + run_type の最新 id を返す
    """
    try:
        row = db.execute(
            text(
                """
                select max(id) as id
                from ai_runs
                where transaction_id = :txid
                  and run_type = :rtype
                  and status = 'success'
                """
            ),
            {"txid": transaction_id, "rtype": run_type},
        ).fetchone()
        if row and row[0]:
            return int(row[0])
    except Exception:
        return None
    return None


def _fetch_patent_retrievals(db: Session, ai_run_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """
    patent_retrievals schema: usage_requirement_id, patent_id, score, why, ai_run_id ...
    """
    rows = db.execute(
        text(
            """
            select usage_requirement_id, patent_id, score, why
            from patent_retrievals
            where ai_run_id = :rid
            order by usage_requirement_id, score desc
            limit :lim
            """
        ),
        {"rid": ai_run_id, "lim": limit},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def _fetch_matrix_matches(db: Session, ai_run_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """
    matrix_matches schema（あなたの現状）:
      usage_requirement_id, matrix_rule_id, match_score, match_type, decision, evidence_json, ai_run_id ...
    """
    rows = db.execute(
        text(
            """
            select usage_requirement_id,
                   matrix_rule_id,
                   match_score,
                   match_type,
                   decision,
                   evidence_json
            from matrix_matches
            where ai_run_id = :rid
            order by match_score desc
            limit :lim
            """
        ),
        {"rid": ai_run_id, "lim": limit},
    ).fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r._mapping)
        ev_raw = d.get("evidence_json")
        ev = _json_loads_safe(ev_raw, default=None)
        # evidence_json が壊れていたり dict でない場合は None に寄せる
        d["evidence"] = ev if isinstance(ev, dict) else None
        # 互換のため evidence_json は削る（payloadが巨大化しがちなので）
        # 必要なら UI側で raw も見たいので残す場合はコメントアウト解除
        # d["evidence_json"] = ev_raw
        d.pop("evidence_json", None)
        out.append(d)
    return out


# =============================================================================
# summarizer + decision maker
# =============================================================================
def _summarize_result_payload(db: Session, transaction_id: int) -> Dict[str, Any]:
    """
    UIへ返す payload を「壊れない形」で構築する。
    - ai_runs を run_type 別に拾う（patent_retrieve / matrix_match）
    - SQLAlchemy2 の text() を使用
    """
    payload: Dict[str, Any] = {
        "transaction_id": transaction_id,
        "_debug": {"generated_at": _utc_ts()},
    }

    # transaction / items / usages
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if tx:
        payload["transaction"] = {
            "id": tx.id,
            "case_no": getattr(tx, "case_no", None),
            "title": getattr(tx, "title", None),
            "status": getattr(tx, "status", None),
        }

    items = db.query(TransactionItem).filter(TransactionItem.transaction_id == transaction_id).all()
    payload["items"] = [
        {"id": it.id, "item_name": getattr(it, "item_name", None), "item_model": getattr(it, "item_model", None)}
        for it in items
    ]

    usages = db.query(UsageRequirement).filter(UsageRequirement.transaction_id == transaction_id).all()
    payload["usages"] = [
        {
            "id": ur.id,
            "source": getattr(ur, "source", None),
            "text": getattr(ur, "text", None),
            "risk_tags": getattr(ur, "risk_tags", []),
            "confidence": getattr(ur, "confidence", None),
        }
        for ur in usages
    ]

    # latest ai_run ids
    rid_pat = _latest_ai_run_id(db, transaction_id, "patent_retrieve")
    rid_mat = _latest_ai_run_id(db, transaction_id, "matrix_match")
    payload["_debug"]["latest_patent_ai_run_id"] = rid_pat
    payload["_debug"]["latest_matrix_ai_run_id"] = rid_mat

    # results
    payload["patent_retrievals_top"] = _fetch_patent_retrievals(db, rid_pat, limit=50) if rid_pat else []
    payload["matrix_matches_top"] = _fetch_matrix_matches(db, rid_mat, limit=50) if rid_mat else []

    return payload


def _pick_followup_questions(top_evidence: Optional[Dict[str, Any]]) -> List[str]:
    """
    top rule の雰囲気に応じて質問を返す（最小実装）。
    後で matrix_rule_id -> 質問テンプレ辞書 に拡張すると強い。
    """
    if not top_evidence:
        return [
            "用途（何に・どの工程で使うか）を具体化してください（例：半導体製造/研究/教育）。",
            "製品の主成分・供給形態（液体/固体/混合物）と濃度・包装形態を教えてください。",
            "輸出先国・需要者（民生/研究/軍関連）情報はありますか？（キャッチオール観点）",
        ]

    title = (top_evidence.get("rule_title") or "").lower()
    item_no = (top_evidence.get("rule_item_no") or "").lower()
    snippet = (top_evidence.get("rule_snippet") or "").lower()

    # photolithography / resist 系
    if any(k in title + snippet + item_no for k in ["フォト", "リソ", "resist", "litho", "露光", "現像", "感光"]):
        return [
            "対象工程はどれですか？（塗布/露光/PEB/現像/洗浄/剥離）",
            "露光波長は確定していますか？（KrF 248nm / ArF 193nm / i-line 等）",
            "用途は半導体製造向けで確定ですか？（R&D/量産/教育用途なども含む）",
            "主成分（樹脂・PAG・溶媒）と濃度、供給形態（液/固体/混合物）を教えてください",
            "輸出先・需要者（民生/研究/軍関連）の情報はありますか？（キャッチオール観点）",
        ]

    # crypto / control device 系（例）
    if any(k in title + snippet + item_no for k in ["暗号", "crypto", "encryption"]):
        return [
            "暗号機能の有無と仕様（鍵長、アルゴリズム、実装形態）を教えてください。",
            "暗号機能はユーザーが有効化できますか？それとも固定ですか？",
            "最終用途（通信/産業制御/軍事転用可能性）とエンドユーザー情報はありますか？",
        ]

    # default
    return [
        "用途（何に・どの工程で使うか）を具体化してください。",
        "製品の主要仕様（性能・精度・濃度・サイズ等）を教えてください。",
        "輸出先国・需要者情報はありますか？（キャッチオール観点）",
    ]


def _decide_status_reason(payload_out: Dict[str, Any], threshold: float) -> Tuple[str, str, List[str]]:
    """
    UIに返す status/reason を、実務で説明しやすい形に安定化する。

    status（UI互換の1フィールド）:
      - controlled / non_controlled / needs_review / needs_more_info / error

    ルール:
      - matrix decision が controlled/non_controlled かつ match_score >= threshold → 確定
      - decision=maybe が上位 → needs_review (high/low を reason に明示)
      - matrix結果が薄い/空 → needs_more_info
    """
    mm: List[Dict[str, Any]] = payload_out.get("matrix_matches_top") or []
    if not mm:
        return "needs_more_info", "マトリクス突合結果が空のため追加情報が必要です", _pick_followup_questions(None)

    top = mm[0]
    top_score = float(top.get("match_score") or 0.0)
    top_decision = str(top.get("decision") or "").lower()
    top_ev = top.get("evidence") if isinstance(top.get("evidence"), dict) else None

    top_rule = (top_ev or {}).get("rule_item_no") or str(top.get("matrix_rule_id"))
    top_title = (top_ev or {}).get("rule_title") or "(no title)"

    # controlled/non_controlled が明示され、score も閾値を超えるなら確定
    if top_decision in {"controlled", "non_controlled"} and top_score >= threshold:
        return (
            top_decision,
            f"確定（{top_decision}）: top_rule={top_rule} / title={top_title} / score={top_score}",
            [],
        )

    # maybe の場合：review へ
    if top_decision == "maybe":
        level = "high" if top_score >= (threshold * 0.6) else "low"
        reason = (
            f"要レビュー（{level}/maybe）: 強い確定判断に不足 / "
            f"top_rule={top_rule} / title={top_title} / score={top_score}"
        )
        return "needs_review", reason, _pick_followup_questions(top_ev)

    # その他（未知の decision など）
    reason = f"要確認: decision={top_decision} / top_rule={top_rule} / title={top_title} / score={top_score}"
    return "needs_review", reason, _pick_followup_questions(top_ev)


def _post_webhook(callback_url: str, body: Dict[str, Any], *, retries: int = 3, timeout: float = 30.0) -> None:
    """
    webhook は失敗しやすいので最小のリトライを入れる（PoCでも効く）
    """
    last_err: Optional[Exception] = None
    for i in range(retries):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(callback_url, json=body)
                r.raise_for_status()
            return
        except Exception as e:
            last_err = e
            # 0.5s, 1s, 2s… くらい
            time.sleep(0.5 * (2**i))
    # 最後に例外を投げる（呼び元で error 記録される）
    raise RuntimeError(f"webhook post failed after {retries} retries: {last_err}")


# =============================================================================
# background entrypoint (Route 1)
# =============================================================================
def process_external_request(request_id: int, threshold: float = DEFAULT_THRESHOLD) -> None:
    """
    Background task entrypoint (Route 1: webhook返却)
      - request_id は ExternalEvalRequest.id (int) を想定
      - SQLite locked 回避のため SessionLocal を作り直す
    """
    db = SessionLocal()
    try:
        req = db.query(ExternalEvalRequest).filter(ExternalEvalRequest.id == request_id).first()
        if not req:
            return

        # payload_in は「文字列JSON」想定だが、揺れを吸収
        payload_in = _json_loads_safe(getattr(req, "payload_in", None), default={})
        if not payload_in:
            # 互換: request_payload 等があるケース
            payload_in = _json_loads_safe(getattr(req, "request_payload", None), default={})

        # 最低限必要
        if "product_id" not in payload_in:
            raise ValueError("payload_in missing product_id")

        callback = getattr(req, "callback_webhook", None)
        if not callback:
            # payload側に callback_webhook が入っている場合も吸収
            callback = payload_in.get("callback_webhook")
        if not callback:
            raise ValueError("callback_webhook is missing")

        # 状態更新
        req.status = "running"
        db.commit()

        # Transaction 生成
        tx_id = create_transaction_from_payload(db, payload_in)
        if hasattr(req, "transaction_id"):
            req.transaction_id = tx_id
        db.commit()

        # Pipeline 実行（matrix_matchまで）
        run_until_matrix_match(db=db, transaction_id=tx_id, threshold=threshold)

        # 結果集計
        payload_out = _summarize_result_payload(db, tx_id)
        status, reason, followups = _decide_status_reason(payload_out, threshold=threshold)
        if followups:
            payload_out["followup_questions"] = followups

        # DBへ保存（監査ログ）
        req.status = "completed"
        req.reason = reason
        if hasattr(req, "payload_out"):
            req.payload_out = _json_dumps_safe(payload_out)
        db.commit()

        # webhook 送信（UI互換の body）
        webhook_body = {
            "product_id": int(payload_in["product_id"]),
            "request_id": int(request_id),
            "status": status,   # controlled / non_controlled / needs_review / needs_more_info / error
            "reason": reason,
            "payload": payload_out,
        }
        _post_webhook(str(callback), webhook_body, retries=3, timeout=30.0)

    except Exception as e:
        # error をDBに記録し、可能なら error webhook も返す
        try:
            req2 = db.query(ExternalEvalRequest).filter(ExternalEvalRequest.id == request_id).first()
            if req2:
                req2.status = "error"
                req2.reason = str(e)
                db.commit()

                # error webhook（callback があれば）
                cb = getattr(req2, "callback_webhook", None)
                payload_in2 = _json_loads_safe(getattr(req2, "payload_in", None), default={})
                if not cb:
                    cb = payload_in2.get("callback_webhook")
                if cb and payload_in2.get("product_id") is not None:
                    try:
                        _post_webhook(
                            str(cb),
                            {
                                "product_id": int(payload_in2["product_id"]),
                                "request_id": int(request_id),
                                "status": "error",
                                "reason": str(e),
                                "payload": None,
                            },
                            retries=2,
                            timeout=20.0,
                        )
                    except Exception:
                        pass
        finally:
            # サーバが落ちるのを避けたい場合は raise しない選択肢もあるが、
            # PoCでは原因追跡しやすいように raise する
            raise
    finally:
        db.close()
