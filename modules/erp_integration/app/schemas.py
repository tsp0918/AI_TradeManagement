"""ERP ↔ AI_TM アダプター用スキーマ（ERP 側の契約に合わせる）。"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


# ── HS Code Classification ────────────────────────────────────────────────────

class HSClassifyRequest(BaseModel):
    description: str
    material_code: Optional[str] = None
    country_of_origin: Optional[str] = None


class HSClassifyResponse(BaseModel):
    hs_code: str
    confidence: float = 0.0
    rationale: Optional[str] = None


# ── 該非判定 (Gaihi Judge) ────────────────────────────────────────────────────

class GaihiJudgeRequest(BaseModel):
    material_code: str
    description: str
    hs_code: Optional[str] = None
    chemical_composition: Optional[str] = None


class GaihiJudgeResponse(BaseModel):
    judgment: str               # APPLICABLE / NOT_APPLICABLE / PENDING
    eccn: Optional[str] = None
    item_number: Optional[str] = None
    rationale: Optional[str] = None
    requires_license: bool = False


# ── 制裁スクリーニング ─────────────────────────────────────────────────────────

class DeniedPartyRequest(BaseModel):
    name: str
    country: str
    address: Optional[str] = None


class DeniedPartyResponse(BaseModel):
    is_match: bool
    list_name: Optional[str] = None
    confidence: float = 0.0
    rationale: Optional[str] = None


# ── Export Pre-check ──────────────────────────────────────────────────────────

class ExportCheckItem(BaseModel):
    material_code: str
    quantity: Decimal
    eccn: Optional[str] = None
    hs_code: Optional[str] = None


class ExportCheckRequest(BaseModel):
    reference: str
    destination_country: str
    customer_code: str
    customer_name: str
    items: list[ExportCheckItem] = Field(..., min_length=1)


class ExportCheckResponse(BaseModel):
    decision: str                   # PASSED / BLOCKED / NEEDS_LICENSE
    check_id: str
    message: Optional[str] = None
    blocking_items: list[str] = []


# ── BOM Judgment ──────────────────────────────────────────────────────────────

class BomComponent(BaseModel):
    level: int = 1
    material_code: str
    description: Optional[str] = None
    quantity: Decimal
    unit: str = "PC"
    hs_code: Optional[str] = None
    eccn: Optional[str] = None
    country_of_origin: Optional[str] = None
    fefta_judgment: Optional[str] = None


class BomJudgeRequest(BaseModel):
    material_code: str
    plant_code: str
    production_version_code: Optional[str] = None
    product_hs_code: Optional[str] = None
    product_eccn: Optional[str] = None
    components: list[BomComponent] = []


class BomJudgeResponse(BaseModel):
    judgment: str                       # APPLICABLE / NOT_APPLICABLE / NEEDS_REVIEW
    aggregate_eccn: Optional[str] = None
    risk_factors: list[str] = []
    controlled_components: list[str] = []
    foreign_origin_share_percent: float = 0.0
    rationale: str = ""


# ── ヘルスチェック ─────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    upstream: dict[str, str] = {}
