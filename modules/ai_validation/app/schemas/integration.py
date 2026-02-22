# app/schemas/integration.py
from __future__ import annotations

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, HttpUrl


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


class ExportControlRequestAccepted(BaseModel):
    request_id: str
    status: str = Field(default="queued")


class ExportControlWebhookOut(BaseModel):
    product_id: int
    request_id: str
    status: str
    reason: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
