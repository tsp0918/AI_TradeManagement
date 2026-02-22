from __future__ import annotations

from typing import Optional, Any, Dict, List
from pydantic import BaseModel, Field


class AppCreate(BaseModel):
    app_key: str
    name: str


class PageCreate(BaseModel):
    app_id: int
    page_key: str
    name: str = ""
    url_regex: str
    must_have: Dict[str, Any] = Field(default_factory=dict)


class TargetUpsert(BaseModel):
    page_id: int
    target_key: str
    description: str = ""
    anchors: List[Dict[str, Any]]


class RuleCreate(BaseModel):
    app_id: int
    rule_key: str
    name: str = ""
    rule_type: str
    params: Dict[str, Any] = Field(default_factory=dict)
    severity: str = "medium"


class InterventionCreate(BaseModel):
    page_id: int
    intervention_key: str
    name: str = ""
    trigger: Dict[str, Any]
    rule_key: Optional[str] = None
    block_action: int = 0
    coach: Dict[str, Any] = Field(default_factory=dict)
    actions: List[Dict[str, Any]] = Field(default_factory=list)


class ReleaseCreate(BaseModel):
    app_id: int
    env: str = "local"
    version: str = "0.1.0"


class PublishIn(BaseModel):
    app_id: int
    env: str = "local"
    version: str = "0.1.0"


class PublishAutoIn(BaseModel):
    app_id: int
    env: str = "local"


class InterventionUpdate(BaseModel):
    name: str = ""
    trigger: Dict[str, Any]
    rule_key: Optional[str] = None
    block_action: int = 0
    coach: Dict[str, Any] = Field(default_factory=dict)
    actions: List[Dict[str, Any]] = Field(default_factory=list)


class PageUpdate(BaseModel):
    name: str = ""
    url_regex: str
    must_have: Dict[str, Any] = Field(default_factory=dict)


class EventIn(BaseModel):
    ts: str
    app_key: str
    page_id: str
    user_pseudo_id: str = ""
    event_type: str
    event_name: str
    context: Dict[str, Any] = Field(default_factory=dict)
