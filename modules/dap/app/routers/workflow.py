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

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DapWorkflowSession

router = APIRouter(tags=["workflow"])

# ── UC 定義 ─────────────────────────────────────────────────────────

_UC_DEFINITIONS: dict[str, dict[str, Any]] = {
    "UC1": {
        "title": "新規品目の輸出審査",
        "persona": "輸出管理担当者",
        "steps": [
            {"num": 1, "title": "品目登録", "detail": "品目名・型番・仕様を入力して品目を登録する",
             "navigate_to": "http://localhost:8000/proxy/ai_classification/products",
             "highlight": "新規登録"},
            {"num": 2, "title": "HSコード判定", "detail": "品目詳細の「HSコード判定」ボタンで上位5候補から選択して反映する",
             "navigate_to": None, "highlight": "HSコード判定"},
            {"num": 3, "title": "制裁照合", "detail": "取引先名を7ソース（OFAC/BIS-EL等）と照合する",
             "navigate_to": "http://localhost:8000/proxy/screening/ui/batch",
             "highlight": "一括照合"},
            {"num": 4, "title": "AI該非判定", "detail": "ECCN・仕向地・需要者・用途を入力して審査を開始する",
             "navigate_to": "http://localhost:8000/proxy/ai_validation/ui/transactions/new",
             "highlight": "新規審査"},
            {"num": 5, "title": "結果確認・証跡保存", "detail": "判定結果を確認し、REQUIRES_PERMITの場合はUC6へ進む",
             "navigate_to": None, "highlight": None},
        ],
    },
    "UC2": {
        "title": "R&D起案から品目登録まで",
        "persona": "R&Dチームリーダー / 輸出管理担当者",
        "steps": [
            {"num": 1, "title": "プロジェクト起案", "detail": "R&Dプロジェクトを新規作成し基本情報を入力する",
             "navigate_to": "http://localhost:8000/proxy/rnd_assessment/ui/cases/new",
             "highlight": "新規プロジェクト"},
            {"num": 2, "title": "AIリスク評価確認", "detail": "3テンプレート入力後にリスクスコアと開示戦略を確認する",
             "navigate_to": None, "highlight": "プロファイル入力"},
            {"num": 3, "title": "知財審査完了", "detail": "審査結果・エビデンスをアップロードして知財審査を完了する",
             "navigate_to": None, "highlight": "知財審査"},
            {"num": 4, "title": "品目管理へ登録", "detail": "「品目管理への登録」ボタンで品目管理に連携する",
             "navigate_to": None, "highlight": "品目管理への登録"},
            {"num": 5, "title": "AI該非判定へ", "detail": "品目管理でHSコード判定後、AI該非判定に進む",
             "navigate_to": "http://localhost:8000/proxy/ai_validation/ui/transactions/new",
             "highlight": None},
        ],
    },
    "UC4": {
        "title": "取引先デューデリジェンス",
        "persona": "輸出管理担当者",
        "steps": [
            {"num": 1, "title": "制裁リスト照合", "detail": "取引先名を7ソース制裁リストで一括照合する",
             "navigate_to": "http://localhost:8000/proxy/screening/ui/batch",
             "highlight": "一括照合"},
            {"num": 2, "title": "与信スコア確認", "detail": "財務スコアと制裁統合スコアを確認する",
             "navigate_to": "http://localhost:8000/ui/counterparty",
             "highlight": None},
            {"num": 3, "title": "コンプライアンス記録", "detail": "DD結果をコンプライアンス進捗パイプラインに記録する",
             "navigate_to": "http://localhost:8000/ui/compliance-lookup",
             "highlight": None},
        ],
    },
    "UC5": {
        "title": "みなし輸出審査",
        "persona": "輸出管理担当者 / 人事",
        "steps": [
            {"num": 1, "title": "人物スクリーニング", "detail": "外国籍研究者を人物管理に登録して制裁照合を実行する",
             "navigate_to": "http://localhost:8000/proxy/rnd_assessment/ui/personnel",
             "highlight": "人物管理"},
            {"num": 2, "title": "みなし輸出申請作成", "detail": "対象者・提供技術・アクセス期間を入力して申請を作成する",
             "navigate_to": "http://localhost:8000/proxy/ai_validation/ui/services",
             "highlight": "みなし輸出申請"},
            {"num": 3, "title": "経済安保法チェック", "detail": "特許非公開リスクとICP自己診断8要素を確認する",
             "navigate_to": "http://localhost:8000/proxy/rnd_assessment/ui/icp-diagnosis",
             "highlight": None},
            {"num": 4, "title": "台帳記録", "detail": "承認後にみなし輸出台帳に記録する（7年保存義務）",
             "navigate_to": "http://localhost:8000/proxy/ai_validation/ui/services",
             "highlight": "みなし輸出台帳"},
        ],
    },
    "UC6": {
        "title": "輸出許可申請ドラフト生成",
        "persona": "輸出管理担当者",
        "steps": [
            {"num": 1, "title": "ドラフト生成", "detail": "REQUIRES_PERMIT取引の詳細ページで「輸出許可申請ドラフト生成」をクリックする",
             "navigate_to": "http://localhost:8000/proxy/ai_validation/ui/transactions",
             "highlight": "輸出許可申請ドラフト生成"},
            {"num": 2, "title": "許可証登録", "detail": "許可証番号・有効期限・許可総額を登録する",
             "navigate_to": "http://localhost:8000/ui/export-license",
             "highlight": "新規申請"},
            {"num": 3, "title": "価値消費管理", "detail": "出荷のたびに「価値控除」フォームで残存許可額を更新する",
             "navigate_to": None, "highlight": "価値控除"},
        ],
    },
    "UC9": {
        "title": "技術インテリジェンス調査",
        "persona": "R&Dチームリーダー / 知財担当者",
        "steps": [
            {"num": 1, "title": "特許検索", "detail": "キーワード・CPC分類・出願人で特許を検索する",
             "navigate_to": "http://localhost:8000/proxy/patent_search/",
             "highlight": None},
            {"num": 2, "title": "制裁照合", "detail": "発明者・出願人を制裁リストで照合する",
             "navigate_to": None, "highlight": "制裁照合"},
            {"num": 3, "title": "学術論文確認", "detail": "技術インテリジェンスで関連論文の類似度を確認する",
             "navigate_to": "http://localhost:8000/proxy/rnd_assessment/ui/academic-intel",
             "highlight": None},
        ],
    },
}


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
    )


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/api/workflow/uc-list")
def list_ucs() -> list[dict[str, Any]]:
    """利用可能な UC 一覧を返す。"""
    return [
        {
            "uc_id": k,
            "title": v["title"],
            "persona": v["persona"],
            "total_steps": len(v["steps"]),
        }
        for k, v in _UC_DEFINITIONS.items()
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
