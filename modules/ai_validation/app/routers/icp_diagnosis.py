"""
icp_diagnosis.py
=================
社内コンプライアンスプログラム（ICP）自己診断ツール。

CISTEC が定義する 8要素のチェックリストに基づき、
企業の内部管理プログラムの成熟度を評価し優先改善事項を提示する。

参考: CISTEC「安全保障貿易に係る機微技術管理ガイダンス」
     経産省「輸出管理内部規程（ICP）ガイドライン」
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/ui/icp", tags=["ICP Diagnosis"])

# ── ICP 8要素 定義 ──────────────────────────────────────────────────────────
ICP_ELEMENTS = [
    {
        "id":    "e1",
        "title": "① 経営の関与（Management Commitment）",
        "desc":  "トップマネジメントが輸出管理の重要性を認識し、方針・資源を確保しているか",
        "questions": [
            {"id": "e1q1", "text": "輸出管理方針（Export Control Policy）が文書化され、役員会で承認されている"},
            {"id": "e1q2", "text": "輸出管理担当役員（ECCO: Export Control Compliance Officer）が任命されている"},
            {"id": "e1q3", "text": "輸出管理のための十分な人員・予算・システムが確保されている"},
            {"id": "e1q4", "text": "年次に経営層への輸出管理状況の報告が実施されている"},
        ],
    },
    {
        "id":    "e2",
        "title": "② 担当組織の整備（Organization）",
        "desc":  "輸出管理を担う組織・役割・権限が明確に定義されているか",
        "questions": [
            {"id": "e2q1", "text": "輸出管理専担部署または担当者が設置・任命されている"},
            {"id": "e2q2", "text": "営業・技術・物流部門との連携体制（エスカレーション手順）が定められている"},
            {"id": "e2q3", "text": "判定権限・承認フローが文書化されており、職務分掌が明確である"},
            {"id": "e2q4", "text": "海外子会社・グループ企業への輸出管理指導・管理体制がある"},
        ],
    },
    {
        "id":    "e3",
        "title": "③ 規制の把握と周知（Regulatory Awareness）",
        "desc":  "適用法令・規制の最新情報を把握し、社内に周知できているか",
        "questions": [
            {"id": "e3q1", "text": "外為法・EAR・EU Dual-Use 等の規制動向を定期的（少なくとも月1回）に確認している"},
            {"id": "e3q2", "text": "規制改正時に影響範囲を評価し、担当部署・経営層に速やかに周知する手続きがある"},
            {"id": "e3q3", "text": "外為法別表第1・ECCN・USML の対照表（該非判定一覧）が整備・更新されている"},
            {"id": "e3q4", "text": "制裁リスト（OFAC/BIS Entity List等）の更新情報を自動的に取得・確認している"},
        ],
    },
    {
        "id":    "e4",
        "title": "④ 取引審査（Transaction Screening & Review）",
        "desc":  "輸出・技術移転の前に適切な審査が行われているか",
        "questions": [
            {"id": "e4q1", "text": "全輸出取引（製品・技術・役務）について事前審査フローが運用されている"},
            {"id": "e4q2", "text": "取引先・エンドユーザーのスクリーニング（制裁リスト照合）が取引前に実施される"},
            {"id": "e4q3", "text": "該非判定書（CISTEC様式準拠）が作成・保存されている（外為法第67条 7年保存）"},
            {"id": "e4q4", "text": "キャッチオール規制（輸出令第4条）の自己判定が文書化されている"},
            {"id": "e4q5", "text": "みなし輸出（外為法第25条第1項ただし書き）の審査が国内の外国籍者に対しても実施されている"},
        ],
    },
    {
        "id":    "e5",
        "title": "⑤ 教育・訓練（Training）",
        "desc":  "関係者に対する定期的な輸出管理教育が実施されているか",
        "questions": [
            {"id": "e5q1", "text": "新入社員・中途採用者向けの輸出管理基礎教育が実施されている"},
            {"id": "e5q2", "text": "営業・技術・物流担当者向けの実務教育が年1回以上実施されている"},
            {"id": "e5q3", "text": "輸出管理担当者・審査担当者向けの専門研修（CISTEC研修等）への参加が促進されている"},
            {"id": "e5q4", "text": "教育実施記録が保存されており、受講率が管理されている"},
        ],
    },
    {
        "id":    "e6",
        "title": "⑥ 記録・保存管理（Record Keeping）",
        "desc":  "法定保存期間を遵守した記録管理体制が整備されているか",
        "questions": [
            {"id": "e6q1", "text": "輸出許可申請書・該非判定書が外為法第67条の7年間保存義務を遵守して管理されている"},
            {"id": "e6q2", "text": "EAR Part 762 に基づく米国関連輸出記録が5年間保存されている"},
            {"id": "e6q3", "text": "記録はアクセス管理された安全な場所（電子/紙）に保存されている"},
            {"id": "e6q4", "text": "保存記録の廃棄基準・手順が定められており、適切に運用されている"},
        ],
    },
    {
        "id":    "e7",
        "title": "⑦ 自己監査・是正措置（Self-Audit & Corrective Action）",
        "desc":  "内部監査により問題を早期発見し、改善を継続しているか",
        "questions": [
            {"id": "e7q1", "text": "輸出管理内部監査が少なくとも年1回実施されている"},
            {"id": "e7q2", "text": "監査発見事項（Non-Compliance）に対する是正措置（CAPA）が文書化・追跡されている"},
            {"id": "e7q3", "text": "違反が発覚した場合の報告・開示（自主開示: Voluntary Self-Disclosure）手順が定められている"},
            {"id": "e7q4", "text": "過去の違反事例・ヒヤリハット事例が社内共有され、再発防止策が実施されている"},
        ],
    },
    {
        "id":    "e8",
        "title": "⑧ 外部連携・通報体制（External Communication）",
        "desc":  "規制当局・外部専門家との連携および内部通報体制が整備されているか",
        "questions": [
            {"id": "e8q1", "text": "規制当局（経済産業省・METI・BIS）への問い合わせ・相談手続きが定められている"},
            {"id": "e8q2", "text": "外部弁護士・CISTEC 等の専門家への相談体制が確立されている"},
            {"id": "e8q3", "text": "取引先・サプライヤーへの輸出管理要求事項（契約条項・誓約書）が整備されている"},
            {"id": "e8q4", "text": "従業員が違反・疑義を匿名で報告できる内部通報窓口が設置されている"},
        ],
    },
]

TOTAL_QUESTIONS = sum(len(e["questions"]) for e in ICP_ELEMENTS)


def _compute_score(answers: dict[str, str]) -> dict:
    """回答を集計しスコア・レベルを算出する。"""
    element_scores: list[dict] = []
    total_score = 0
    total_max = 0

    for elem in ICP_ELEMENTS:
        elem_score = 0
        elem_max = len(elem["questions"]) * 2
        gaps: list[str] = []
        for q in elem["questions"]:
            v = answers.get(q["id"], "no")
            if v == "yes":
                elem_score += 2
            elif v == "partial":
                elem_score += 1
            else:
                gaps.append(q["text"])
        pct = round(elem_score / elem_max * 100) if elem_max else 0
        element_scores.append({
            "id":    elem["id"],
            "title": elem["title"],
            "score": elem_score,
            "max":   elem_max,
            "pct":   pct,
            "gaps":  gaps,
        })
        total_score += elem_score
        total_max += elem_max

    total_pct = round(total_score / total_max * 100) if total_max else 0

    if total_pct >= 85:
        level = 4
        level_label = "レベル4 — 優良（Best Practice 相当）"
        level_color = "#166534"
        level_desc = "CISTEC 推奨の内部管理プログラムが高度に整備されています。外部審査・認証取得を検討してください。"
    elif total_pct >= 65:
        level = 3
        level_label = "レベル3 — 良好（Standard Compliance 相当）"
        level_color = "#1d4ed8"
        level_desc = "基本的な管理体制は整備されています。スコアの低い要素を重点的に強化してください。"
    elif total_pct >= 40:
        level = 2
        level_label = "レベル2 — 基本的（Developing）"
        level_color = "#92400e"
        level_desc = "一部の管理体制が未整備です。違反リスクを低減するため、優先改善事項から速やかに着手してください。"
    else:
        level = 1
        level_label = "レベル1 — 要改善（Inadequate）"
        level_color = "#7f1d1d"
        level_desc = "管理体制が不十分で、法令違反のリスクが高い状態です。緊急的な体制整備が必要です。"

    # 優先改善事項（スコアが低い要素 上位3件）
    sorted_elements = sorted(element_scores, key=lambda x: x["pct"])
    priority_improvements = [
        {"element": e["title"], "pct": e["pct"], "gaps": e["gaps"][:2]}
        for e in sorted_elements[:3] if e["pct"] < 80
    ]

    return {
        "total_score": total_score,
        "total_max":   total_max,
        "total_pct":   total_pct,
        "level":       level,
        "level_label": level_label,
        "level_color": level_color,
        "level_desc":  level_desc,
        "element_scores": element_scores,
        "priority_improvements": priority_improvements,
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


@router.get("", response_class=HTMLResponse)
def icp_form(request: Request):
    """ICP 自己診断フォームを表示する。"""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "icp_diagnosis.html", {
        "elements": ICP_ELEMENTS,
        "total_questions": TOTAL_QUESTIONS,
    })


@router.post("", response_class=HTMLResponse)
async def icp_submit(request: Request):
    """ICP 自己診断フォームを受け取り、結果を表示する。"""
    templates = request.app.state.templates
    form = await request.form()
    answers = {k: v for k, v in form.items()}
    result = _compute_score(answers)
    company_name = form.get("company_name", "")
    return templates.TemplateResponse(request, "icp_result.html", {
        "result": result,
        "elements": ICP_ELEMENTS,
        "company_name": company_name,
        "answers": answers,
    })
