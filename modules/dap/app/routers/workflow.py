"""DAP ワークフロー伴走 API。

ユーザーが UC を選択するとセッションが作成され、
各ステップを完了するたびに次のナビゲーション指示を返す。

エンドポイント:
    POST /api/workflow/start        → UC を選択してセッション開始
    GET  /api/workflow/status       → 現在のセッション状態
    POST /api/workflow/complete_step → ステップ完了を記録して次の指示を取得
    POST /api/workflow/abandon      → セッション中断
    GET  /api/workflow/uc-list      → 利用可能な UC 一覧
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DapWorkflowSession

router = APIRouter(tags=["workflow"])

_PLATFORM_URL = os.environ.get("MODULE_PLATFORM_URL", "http://localhost:8000")

# ── UC 定義をJSONファイルから読み込み ─────────────────────────────────

def _load_uc_definitions() -> dict[str, dict[str, Any]]:
    json_path = Path(__file__).resolve().parent.parent / "uc_definitions.json"
    with open(json_path, encoding="utf-8") as f:
        raw: list[dict] = json.load(f)

    result: dict[str, dict[str, Any]] = {}
    for uc in raw:
        uc_id = uc["id"]
        # {PLATFORM_URL} プレースホルダーを実際のURLに置換
        uc_str = json.dumps(uc, ensure_ascii=False).replace("{PLATFORM_URL}", _PLATFORM_URL)
        uc_resolved = json.loads(uc_str)
        result[uc_id] = uc_resolved
    return result


_UC_DEFINITIONS: dict[str, dict[str, Any]] = _load_uc_definitions()


# ── Schemas ──────────────────────────────────────────────────────────

class WorkflowStartRequest(BaseModel):
    session_id: str
    uc_id: str


class WorkflowStepCompleteRequest(BaseModel):
    session_id: str
    step_num: int
    context_update: dict[str, Any] = {}


class StepGuidance(BaseModel):
    step_num: int
    total_steps: int
    title: str
    detail: str
    navigate_to: str | None
    highlight: str | None
    is_last: bool
    guidance_steps: list[dict[str, Any]] = []


class WorkflowStatusResponse(BaseModel):
    session_id: str
    uc_id: str
    uc_title: str
    current_step: int
    total_steps: int
    completed_steps: list[int]
    status: str
    next_guidance: StepGuidance | None
    context: dict[str, Any]


# ── Helpers ──────────────────────────────────────────────────────────

def _get_guidance(uc_id: str, step_num: int) -> StepGuidance | None:
    uc = _UC_DEFINITIONS.get(uc_id)
    if not uc:
        return None
    steps = uc["steps"]
    step = next((s for s in steps if s["num"] == step_num), None)
    if not step:
        return None
    return StepGuidance(
        step_num=step["num"],
        total_steps=len(steps),
        title=step["title"],
        detail=step["detail"],
        navigate_to=step.get("navigate_to"),
        highlight=step.get("highlight"),
        is_last=(step["num"] == len(steps)),
        guidance_steps=step.get("guidance_steps", []),
    )


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/api/workflow/uc-list")
def list_ucs() -> list[dict[str, Any]]:
    """利用可能な UC 一覧を返す。"""
    return [
        {
            "uc_id": uc_id,
            "title": v["title"],
            "persona": v["persona"],
            "description": v.get("description", ""),
            "total_steps": len(v["steps"]),
        }
        for uc_id, v in _UC_DEFINITIONS.items()
    ]


@router.post("/api/workflow/start")
def start_workflow(
    body: WorkflowStartRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """UC を選択してワークフローセッションを開始する。"""
    uc = _UC_DEFINITIONS.get(body.uc_id)
    if not uc:
        raise HTTPException(status_code=400, detail=f"UC '{body.uc_id}' は存在しません")

    # 既存のアクティブセッションがあれば再利用
    existing = db.query(DapWorkflowSession).filter(
        DapWorkflowSession.session_id == body.session_id,
        DapWorkflowSession.uc_id == body.uc_id,
        DapWorkflowSession.status == "active",
    ).first()
    if existing:
        session = existing
    else:
        session = DapWorkflowSession(
            session_id=body.session_id,
            uc_id=body.uc_id,
            uc_title=uc["title"],
            current_step=1,
            total_steps=len(uc["steps"]),
            completed_steps=[],
            context={},
            status="active",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

    guidance = _get_guidance(body.uc_id, session.current_step)
    return {
        "session_id": body.session_id,
        "uc_id": body.uc_id,
        "uc_title": uc["title"],
        "current_step": session.current_step,
        "total_steps": session.total_steps,
        "status": session.status,
        "next_guidance": guidance.model_dump() if guidance else None,
        "message": f"UC{body.uc_id[2:]}「{uc['title']}」を開始します。{guidance.detail if guidance else ''}",
    }


@router.get("/api/workflow/status")
def workflow_status(
    session_id: str,
    db: Session = Depends(get_db),
) -> WorkflowStatusResponse:
    """現在のワークフローセッション状態を返す。"""
    session = db.query(DapWorkflowSession).filter(
        DapWorkflowSession.session_id == session_id,
        DapWorkflowSession.status == "active",
    ).order_by(DapWorkflowSession.started_at.desc()).first()

    if not session:
        raise HTTPException(status_code=404, detail="アクティブなワークフローセッションがありません")

    guidance = _get_guidance(session.uc_id, session.current_step)
    return WorkflowStatusResponse(
        session_id=session_id,
        uc_id=session.uc_id,
        uc_title=session.uc_title,
        current_step=session.current_step,
        total_steps=session.total_steps,
        completed_steps=session.completed_steps or [],
        status=session.status,
        next_guidance=guidance,
        context=session.context or {},
    )


@router.post("/api/workflow/complete_step")
def complete_step(
    body: WorkflowStepCompleteRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """ステップ完了を記録して次のナビゲーション指示を返す。"""
    session = db.query(DapWorkflowSession).filter(
        DapWorkflowSession.session_id == body.session_id,
        DapWorkflowSession.status == "active",
    ).order_by(DapWorkflowSession.started_at.desc()).first()

    if not session:
        raise HTTPException(status_code=404, detail="アクティブなセッションが見つかりません")

    # 完了ステップを記録
    completed = list(session.completed_steps or [])
    if body.step_num not in completed:
        completed.append(body.step_num)
    session.completed_steps = sorted(completed)

    # コンテキスト更新
    ctx = dict(session.context or {})
    ctx.update(body.context_update)
    session.context = ctx

    # 次のステップへ
    next_step = body.step_num + 1
    uc = _UC_DEFINITIONS.get(session.uc_id, {})
    total = len(uc.get("steps", []))

    if next_step > total:
        session.status = "completed"
        db.commit()
        return {
            "status": "completed",
            "message": f"✓ {session.uc_title} が完了しました。お疲れ様でした。",
            "next_guidance": None,
        }

    session.current_step = next_step
    session.last_active_at = datetime.utcnow()
    db.commit()

    guidance = _get_guidance(session.uc_id, next_step)
    return {
        "status": "active",
        "current_step": next_step,
        "total_steps": total,
        "completed_steps": session.completed_steps,
        "next_guidance": guidance.model_dump() if guidance else None,
        "message": guidance.detail if guidance else "",
    }


@router.post("/api/workflow/abandon")
def abandon_workflow(
    session_id: str,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """ワークフローセッションを中断する。"""
    session = db.query(DapWorkflowSession).filter(
        DapWorkflowSession.session_id == session_id,
        DapWorkflowSession.status == "active",
    ).first()
    if session:
        session.status = "abandoned"
        db.commit()
    return {"status": "abandoned"}
