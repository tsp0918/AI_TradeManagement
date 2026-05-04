"""POST /export/precheck — 輸出事前チェック（スクリーニング + 規制判定の合成）。"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..adapters import faiss_search, screening as screening_adapter
from ..adapters import platform_core as pc_adapter
from ..auth import verify_api_key
from ..schemas import ExportCheckItem, ExportCheckRequest, ExportCheckResponse

router = APIRouter(prefix="/export", tags=["Export Check"])

# 禁輸国（EAR Part 746 / 外為令）
_EMBARGOED = {"IR", "KP", "RU", "BY", "SY", "CU", "SD", "MM"}


async def _check_item(item: ExportCheckItem) -> dict | None:
    """
    1 品目を FAISS Layer A で判定。
    APPLICABLE かつ requires_license=True の場合は blocking item として返す。
    """
    if item.eccn:
        # EAR99 以外は要確認
        if item.eccn.upper() != "EAR99":
            return {"material_code": item.material_code, "eccn": item.eccn, "reason": "controlled_eccn"}
        return None

    if not item.hs_code and not item.eccn:
        return None  # 情報不足 → スキップ（審査側に委ねる）

    query = item.hs_code or ""
    try:
        hits = await faiss_search.search_layer_a(query, top_k=3)
        verdict = faiss_search.derive_judgment(hits)
        if verdict["judgment"] == "APPLICABLE":
            return {
                "material_code": item.material_code,
                "eccn": verdict["eccn"],
                "reason": verdict["rationale"],
            }
    except Exception:
        pass
    return None


@router.post("/precheck", response_model=ExportCheckResponse)
async def export_precheck(
    body: ExportCheckRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
) -> ExportCheckResponse:
    """
    販売注文の輸出前チェック。
    1. 宛先国が禁輸国か確認
    2. 顧客を制裁リストに照合
    3. 各品目を FAISS Layer A で該非判定
    """
    check_id = str(uuid.uuid4())
    dest = body.destination_country.upper()
    blocking_items: list[str] = []
    messages: list[str] = []

    # ── 1. 禁輸国チェック ────────────────────────────────────────────────────
    if dest in _EMBARGOED:
        return ExportCheckResponse(
            decision="BLOCKED",
            check_id=check_id,
            message=f"Destination country {dest} is subject to comprehensive trade restrictions.",
            blocking_items=[item.material_code for item in body.items],
        )

    # ── 2. 制裁スクリーニング ──────────────────────────────────────────────────
    try:
        screening_result = await screening_adapter.screen_entity(
            body.customer_name, dest, address=None
        )
        if screening_result["is_match"]:
            return ExportCheckResponse(
                decision="BLOCKED",
                check_id=check_id,
                message=f"Customer '{body.customer_name}' matched sanctions list: "
                        f"{screening_result.get('list_name', 'unknown')}",
                blocking_items=[item.material_code for item in body.items],
            )
    except Exception as exc:
        messages.append(f"Screening service unavailable ({exc}); proceeding with item check only.")

    # ── 3. 品目チェック（並列） ────────────────────────────────────────────────
    item_results = await asyncio.gather(
        *[_check_item(item) for item in body.items],
        return_exceptions=True,
    )
    needs_license = False
    for res in item_results:
        if isinstance(res, dict):
            blocking_items.append(res["material_code"])
            needs_license = True

    # ── 判定 ────────────────────────────────────────────────────────────────
    if blocking_items and needs_license:
        decision = "NEEDS_LICENSE"
        message = (
            f"{len(blocking_items)} item(s) require export license for {dest}. "
            + (" ".join(messages) if messages else "")
        ).strip()
    else:
        decision = "PASSED"
        message = "No export restrictions detected. " + (" ".join(messages) if messages else "")
        message = message.strip()

    # NEEDS_LICENSE / BLOCKED の場合は輸出許可申請ドラフトを platform-core に記録
    if decision in ("NEEDS_LICENSE", "BLOCKED"):
        item_desc = ", ".join(i.material_code for i in body.items[:5])
        eccn_hint = next(
            (r["eccn"] for r in item_results if isinstance(r, dict) and r.get("eccn")),
            None,
        )
        background_tasks.add_task(
            pc_adapter.create_export_application,
            reference=body.reference,
            destination_country=dest,
            consignee_name=body.customer_name,
            item_description=item_desc,
            eccn=eccn_hint,
            decision=decision,
        )

    return ExportCheckResponse(
        decision=decision,
        check_id=check_id,
        message=message or None,
        blocking_items=blocking_items,
    )
