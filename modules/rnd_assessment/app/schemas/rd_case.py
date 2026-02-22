from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


class CaseCreate(BaseModel):
    tenant_id: str
    external_project_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    created_by_user_id: Optional[str] = None


class CaseRead(BaseModel):
    case_id: str
    tenant_id: str
    external_project_id: Optional[str]
    title: str
    description: Optional[str]
    status: str
    created_by_user_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProfileCreate(BaseModel):
    use_requirements_raw: Optional[str] = None
    end_user_requirements_raw: Optional[str] = None
    disclosure_requirements_raw: Optional[str] = None  # NEW (optional; usually rendered)

    intended_markets: Optional[List[str]] = None
    tech_domains: Optional[List[str]] = None
    project_country_risk: Optional[str] = None
    created_by_user_id: Optional[str] = None

    # template inputs (JSON)
    use_template: Optional[Dict[str, Any]] = None
    end_user_template: Optional[Dict[str, Any]] = None
    disclosure_template: Optional[Dict[str, Any]] = None  # NEW


class ProfileRead(BaseModel):
    profile_id: str
    case_id: str
    version_no: int

    use_requirements_raw: Optional[str]
    end_user_requirements_raw: Optional[str]
    disclosure_requirements_raw: Optional[str]  # NEW

    intended_markets: Optional[List[str]]
    tech_domains: Optional[List[str]]
    project_country_risk: Optional[str]

    input_fingerprint: str
    created_by_user_id: Optional[str]
    created_at: datetime

    # stored templates
    use_template_json: Optional[dict] = None
    end_user_template_json: Optional[dict] = None
    disclosure_template_json: Optional[dict] = None  # NEW

    model_config = {"from_attributes": True}


class CaseItemCreate(BaseModel):
    external_item_id: str
    internal_item_code: Optional[str] = None
    intended_use_in_project: Optional[str] = None


class CaseItemRead(BaseModel):
    case_item_id: str
    case_id: str
    external_item_id: str
    internal_item_code: Optional[str]
    intended_use_in_project: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AssessmentRunRequest(BaseModel):
    performed_by_user_id: str = Field(default="system")


class AssessmentRead(BaseModel):
    assessment_id: str
    profile_id: str
    performed_at: datetime
    performed_by_user_id: str

    overall_score: int
    risk_level: str
    recommendation: str

    tech_risk_score: int
    use_risk_score: int
    end_user_risk_score: int
    regulatory_risk_score: int

    rules_triggered: Optional[List[Dict[str, Any]]] = None
    top_factors: Optional[List[str]] = None

    input_snapshot: Optional[Dict[str, Any]] = None
    input_fingerprint: str

    ruleset_version: str
    model_version: str
    scoring_config_version: str

    external_screening_job: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}