# app/routers/integration_export_control.py
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models.integration import ExternalEvalRequest
from app.services.integrations.export_control import process_external_request

router = APIRouter(prefix="/export-control", tags=["integration-export-control"])


class ExportControlRequestIn(BaseModel):
    product_id: int
    code: str
    name: str
    description: Optional[str] = None
    hs_code: Optional[str] = None
    eccn: Optional[str] = None
    item_class: Optional[str] = None
    bom_json: Optional[str] = None
    regulation_ai_raw: Optional[str] = None
    callback_webhook: HttpUrl


class ExportControlRequestOut(BaseModel):
    request_id: int
    status: str


@router.post("/requests", response_model=ExportControlRequestOut)
def create_export_control_request(
    body: ExportControlRequestIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ExportControlRequestOut:
    try:
        payload_dict = body.model_dump(mode="json")  # HttpUrl等をJSON化
        payload_in = json.dumps(payload_dict, ensure_ascii=False)

        req = ExternalEvalRequest(
            product_id=body.product_id,
            status="queued",
            callback_webhook=str(body.callback_webhook),
            payload_in=payload_in,  # ★ここが重要（request_payload ではなく payload_in）
        )
        db.add(req)
        db.commit()
        db.refresh(req)

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create request: {e}")

    # background: Route1 webhook
    background.add_task(process_external_request, req.id)

    return ExportControlRequestOut(request_id=req.id, status=req.status)
