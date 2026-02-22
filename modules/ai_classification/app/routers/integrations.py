# app/routers/integrations.py
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional, Union

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Product
from ..settings import settings
from ..services.external_app_client import ExternalAppClient

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _normalize_request_id(v: Any) -> Optional[Union[int, str]]:
    """
    AI側は request_id を int で返す例があるため、型揺れを吸収する。
    破壊的変換はしない（int化できればint、無理ならstr、空ならNone）。
    """
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        # "10" のような文字列なら int に寄せる
        if s.isdigit():
            try:
                return int(s)
            except Exception:
                return s
        return s
    # その他の型は文字列化
    return str(v)


def _wrap_payload(request_id: Any, payload_obj: Any) -> str:
    """
    UI側の external_eval_payload は常にこのwrapper形式に揃える。
    {
      "request_id": <int|str|null>,
      "payload": <dict|null>
    }
    """
    return _json_dumps(
        {
            "request_id": _normalize_request_id(request_id),
            "payload": payload_obj,
        }
    )


@router.post("/export-control/request/{product_id}")
async def request_export_control(product_id: int, db: Session = Depends(get_db)):
    """
    UI -> 外部AIへ判定依頼をPOSTする。
    外部AIが落ちていても UI が落ちないように queued を記録して返す。
    """
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # 外部AI仕様（AI側申し送りに合わせる）
    payload: Dict[str, Any] = {
        "product_id": product.id,
        "code": product.code,
        "name": product.name,
        # description が最重要。NoneだとAI側が困るので空文字に寄せる。
        "description": product.description or "",
        "hs_code": product.hs_code,
        "eccn": product.eccn,
        "item_class": product.item_class,
        "bom_json": product.bom_json,
        "regulation_ai_raw": product.regulation_ai_raw,
        "callback_webhook": settings.PUBLIC_WEBHOOK_URL,  # UI側Webhook URL
    }

    client = ExternalAppClient()

    try:
        resp = await client.request_export_control(payload)  # 202(JSON)を想定

        product.external_eval_requested_at = datetime.utcnow()
        product.external_eval_status = (resp.get("status") or "queued")
        # この時点でreasonは返らない想定なので、軽い説明のみ
        product.external_eval_reason = "External request accepted (queued)."

        # request_id をDB追加なしで保持する（payload wrapper で統一）
        product.external_eval_payload = _wrap_payload(
            resp.get("request_id"),
            {
                "status": resp.get("status", "queued"),
                "received_at": resp.get("received_at"),
            },
        )

        db.commit()
        return {"ok": True, "sent": True, "external_response": resp}

    except httpx.ConnectError:
        product.external_eval_requested_at = datetime.utcnow()
        product.external_eval_status = "queued"
        product.external_eval_reason = "External app is not reachable (connection failed)."
        # payloadも「wrapper形式」で統一（UI表示がブレない）
        product.external_eval_payload = _wrap_payload(None, None)
        db.commit()
        return {
            "ok": True,
            "sent": False,
            "status": "queued",
            "detail": "External app not reachable. Please start external app or fix EXTERNAL_APP_BASE_URL.",
        }

    except httpx.HTTPError as e:
        product.external_eval_requested_at = datetime.utcnow()
        product.external_eval_status = "error"
        product.external_eval_reason = f"External app HTTP error: {type(e).__name__}"
        product.external_eval_payload = _wrap_payload(None, None)
        db.commit()
        raise HTTPException(status_code=502, detail="External app returned an error")


@router.post("/export-control/webhook")
async def export_control_webhook(body: dict, db: Session = Depends(get_db)):
    """
    外部AI -> UI(Webhook) に判定結果をPOSTしてもらう。

    仕様（AI側確定版）:
    {
      "product_id": 123,
      "request_id": 10,
      "status": "needs_review",
      "reason": "...",
      "payload": {...}
    }

    冪等性:
    - 同じ request_id の webhook が複数回届く可能性あり
    - UI側は「最新の内容で上書き」する（Product行を更新）
    """
    product_id = body.get("product_id")
    if product_id is None:
        raise HTTPException(status_code=400, detail="product_id is required")

    try:
        pid = int(product_id)
    except Exception:
        raise HTTPException(status_code=400, detail="product_id must be an integer")

    product = db.get(Product, pid)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    status = body.get("status")
    reason = body.get("reason")
    payload_obj = body.get("payload")
    request_id = body.get("request_id")

    # 保存ルール（外部仕様に完全一致）
    if status is not None:
        product.external_eval_status = str(status)
    product.external_eval_reason = (str(reason) if reason is not None else None)

    # external_eval_payload は wrapper 形式で統一（request_id + payload）
    product.external_eval_payload = _wrap_payload(request_id, payload_obj)

    product.external_eval_received_at = datetime.utcnow()

    db.commit()
    return {"ok": True}


@router.get("/export-control/result/{product_id}")
def get_export_control_result(product_id: int, db: Session = Depends(get_db)):
    """
    UI側に保存されている外部AI判定結果をGET
    （画面でJSON確認する用途）
    """
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return {
        "product_id": product.id,
        "code": product.code,
        "external_eval_status": product.external_eval_status,
        "external_eval_reason": product.external_eval_reason,
        "external_eval_payload": product.external_eval_payload,
        "requested_at": product.external_eval_requested_at,
        "received_at": product.external_eval_received_at,
    }
