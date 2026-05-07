"""POST /gaihi/judge, POST /gaihi/judge-bom — 該非判定・BOM 判定。"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..adapters import faiss_search
from ..adapters import platform_core as pc_adapter
from ..adapters import ai_classification as cls_adapter
from ..auth import verify_api_key
from ..schemas import (
    BomJudgeRequest,
    BomJudgeResponse,
    GaihiJudgeRequest,
    GaihiJudgeResponse,
)

router = APIRouter(prefix="/gaihi", tags=["Gaihi (Export Control)"])


@router.post("/judge", response_model=GaihiJudgeResponse)
async def judge_gaihi(
    body: GaihiJudgeRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
) -> GaihiJudgeResponse:
    """品目の外為法・EAR 該非判定を行う。FAISS Layer A セマンティック検索で照合。"""
    query_parts = [body.description]
    if body.hs_code:
        query_parts.append(body.hs_code)
    if body.chemical_composition:
        query_parts.append(body.chemical_composition)
    query = " ".join(query_parts)

    try:
        hits = await faiss_search.search_layer_a(query, top_k=5)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"FAISS search error: {exc}") from exc

    verdict = faiss_search.derive_judgment(hits, body.hs_code)

    # AI が ECCN を特定できなかった場合は ERP 提供の ECCN を fallback として使用
    effective_eccn = verdict["eccn"] or body.eccn

    response = GaihiJudgeResponse(
        judgment=verdict["judgment"],
        eccn=effective_eccn,
        item_number=verdict["item_number"],
        rationale=verdict["rationale"],
        requires_license=verdict["requires_license"],
    )

    # レスポンス送信後に品目を platform-core に同期（失敗してもレスポンスには影響しない）
    background_tasks.add_task(
        pc_adapter.upsert_item,
        material_code=body.material_code,
        name=body.description[:200],
        description=body.description,
        eccn=effective_eccn,
        hs_code=body.hs_code,
        judgment=verdict["judgment"],
    )
    # 判定結果を ERP にコールバック
    background_tasks.add_task(
        pc_adapter.push_judgment_to_erp,
        material_code=body.material_code,
        new_judgment=verdict["judgment"],
        new_eccn=effective_eccn,
        rationale=verdict["rationale"],
    )
    # ai_classification 品目DBに同期（UI に表示するため）
    background_tasks.add_task(
        cls_adapter.sync_to_classification,
        material_code=body.material_code,
        name=body.description[:200],
        description=body.description,
        hs_code=body.hs_code,
        eccn=effective_eccn,
        item_type="FINISHED_GOODS",
        judgment=verdict["judgment"],
        rationale=verdict["rationale"],
    )

    return response


@router.post("/judge-bom", response_model=BomJudgeResponse)
async def judge_bom(
    body: BomJudgeRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
) -> BomJudgeResponse:
    """BOM コンポーネントリストの規制リスクを評価する。"""
    controlled: list[str] = []
    risk_factors: list[str] = []
    aggregate_eccn: str | None = body.product_eccn

    foreign_count = 0
    total_components = len(body.components)

    for comp in body.components:
        # 既知 ECCN がある場合（EAR99 は統制なし）
        if comp.eccn:
            if comp.eccn.upper() != "EAR99":
                controlled.append(comp.material_code)
                if not aggregate_eccn:
                    aggregate_eccn = comp.eccn
                risk_factors.append(f"Controlled component: {comp.material_code} ({comp.eccn})")
            continue

        # 外国原産チェック
        if comp.country_of_origin and comp.country_of_origin.upper() not in ("JP", ""):
            foreign_count += 1

        # 説明文で FAISS 検索
        if comp.description:
            try:
                hits = await faiss_search.search_layer_a(comp.description, top_k=3)
                verdict = faiss_search.derive_judgment(hits)
                if verdict["judgment"] == "APPLICABLE":
                    controlled.append(comp.material_code)
                    if verdict["eccn"] and not aggregate_eccn:
                        aggregate_eccn = verdict["eccn"]
                    risk_factors.append(
                        f"Controlled component (AI): {comp.material_code} "
                        f"score={hits[0].get('score', 0):.3f}"
                    )
            except Exception:
                pass

    foreign_pct = (foreign_count / total_components * 100) if total_components else 0.0
    if foreign_pct >= 25.0:
        risk_factors.append(f"Foreign origin share {foreign_pct:.1f}% ≥ 25% threshold")

    # 全体判定
    if controlled:
        judgment = "APPLICABLE"
    elif risk_factors:
        judgment = "NEEDS_REVIEW"
    else:
        judgment = "NOT_APPLICABLE"

    rationale = (
        f"{len(controlled)}/{total_components} components controlled. "
        f"Foreign origin: {foreign_pct:.1f}%."
    )

    # ai_classification 品目DBに同期
    background_tasks.add_task(
        _sync_bom_to_classification, body, judgment, aggregate_eccn, rationale
    )

    return BomJudgeResponse(
        judgment=judgment,
        aggregate_eccn=aggregate_eccn,
        risk_factors=risk_factors,
        controlled_components=controlled,
        foreign_origin_share_percent=round(foreign_pct, 1),
        rationale=rationale,
    )


async def _sync_bom_to_classification(body: BomJudgeRequest, judgment: str, aggregate_eccn: str | None, rationale: str) -> None:
    """BOM 判定結果を ai_classification に非同期同期する。"""
    await cls_adapter.sync_to_classification(
        material_code=body.material_code,
        name=body.material_code,
        hs_code=body.product_hs_code,
        eccn=aggregate_eccn,
        item_type="FINISHED_GOODS",
        judgment=judgment,
        rationale=rationale,
    )
