"""AI Trade Management → ERP プル型レビュー・スクリーニング。

AI TM 側からリクエストを発行して ERP の最新マスターデータを取得し、
その場で輸出審査またはスクリーニングを実施して結果を返す。

エンドポイント:
  POST /pull/item-review              品目1件を ERP から取得して AI 該非判定
  POST /pull/items-batch-review       ERP 全対象品目を一括取得して AI 該非判定
  POST /pull/counterparty-screen      取引先1件を ERP から取得して制裁スクリーニング
  POST /pull/counterparties-batch-screen  ERP 全取引先を一括スクリーニング
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ..adapters import erp_pull, faiss_search as faiss
from ..adapters import screening as screen_adapter
from ..adapters import platform_core as pc_adapter
from ..adapters import ai_classification as cls_adapter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pull", tags=["Pull Review"])


# ── リクエスト/レスポンス スキーマ ────────────────────────────────────────────

class ItemReviewRequest(BaseModel):
    erp_item_code: str
    reason: str = "periodic_review"


class ItemReviewResult(BaseModel):
    erp_item_code: str
    judgment: str           # APPLICABLE / NOT_APPLICABLE / PENDING
    eccn: str | None = None
    rationale: str | None = None
    item_data: dict | None = None
    synced: bool = False
    erp_reachable: bool = True


class ItemsBatchReviewRequest(BaseModel):
    limit: int = 100
    reason: str = "scheduled_review"


class ItemsBatchReviewResponse(BaseModel):
    reviewed: int
    applicable: int
    not_applicable: int
    pending: int
    erp_reachable: bool
    results: list[ItemReviewResult]


class CounterpartyScreenRequest(BaseModel):
    erp_party_code: str
    reason: str = "periodic_screening"


class CounterpartyScreenResult(BaseModel):
    erp_party_code: str
    screen_result: str          # HIT / CLEAR / UNKNOWN
    is_match: bool
    confidence: float
    list_name: str | None = None
    rationale: str | None = None
    counterparty_data: dict | None = None
    erp_reachable: bool = True


class CounterpartiesBatchScreenRequest(BaseModel):
    limit: int = 200
    reason: str = "scheduled_screening"


class CounterpartiesBatchScreenResponse(BaseModel):
    screened: int
    hits: int
    clear: int
    unknown: int
    erp_reachable: bool
    results: list[CounterpartyScreenResult]


# ── 内部ヘルパー ──────────────────────────────────────────────────────────────

def _normalize_item(raw: dict) -> dict:
    """ERP 品目レスポンスを内部標準形式に正規化する。"""
    return {
        "erp_item_code": raw.get("material_code") or raw.get("code") or raw.get("item_code") or "",
        "name":          raw.get("description") or raw.get("name") or "",
        "description":   raw.get("long_description") or raw.get("description") or "",
        "hs_code":       raw.get("hs_code") or raw.get("customs_tariff") or None,
        "eccn":          raw.get("eccn") or raw.get("export_control_code") or None,
        "country_of_origin": raw.get("country_of_origin") or raw.get("origin_country") or None,
    }


def _normalize_party(raw: dict) -> dict:
    """ERP 取引先レスポンスを内部標準形式に正規化する。"""
    return {
        "erp_party_code": raw.get("vendor_code") or raw.get("code") or raw.get("party_code") or "",
        "name":           raw.get("name") or raw.get("company_name") or "",
        "country":        raw.get("country") or raw.get("country_code") or "",
        "address":        raw.get("address") or raw.get("street") or None,
    }


async def _judge_item(item: dict) -> ItemReviewResult:
    """1品目を FAISS Layer A で判定し、結果を各モジュールに同期する。"""
    erp_code = item["erp_item_code"]
    query = " ".join(filter(None, [item["description"] or item["name"], item["hs_code"]]))

    try:
        hits = await faiss.search_layer_a(query, top_k=5)
        verdict = faiss.derive_judgment(hits, item["hs_code"])
    except Exception as exc:
        logger.warning("FAISS judgment failed for %s: %s", erp_code, exc)
        verdict = {"judgment": "PENDING", "eccn": item["eccn"], "rationale": "FAISS unavailable"}

    effective_eccn = verdict.get("eccn") or item["eccn"]
    judgment = verdict["judgment"]
    rationale = verdict.get("rationale", "")

    # plat_item (PostgreSQL) に同期
    synced = False
    try:
        await pc_adapter.upsert_item(
            material_code=erp_code,
            name=item["name"],
            description=item["description"],
            eccn=effective_eccn,
            hs_code=item["hs_code"],
            judgment=judgment,
        )
        synced = True
    except Exception as exc:
        logger.warning("upsert_item sync failed for %s: %s", erp_code, exc)

    # ai_classification SQLite にも同期（UI 表示用）
    try:
        await cls_adapter.sync_to_classification(
            material_code=erp_code,
            name=item["name"],
            description=item["description"],
            hs_code=item["hs_code"],
            eccn=effective_eccn,
            country_of_origin=item.get("country_of_origin"),
            item_type="FINISHED_GOODS",
            judgment=judgment,
            rationale=rationale,
        )
    except Exception as exc:
        logger.warning("cls sync failed for %s: %s", erp_code, exc)

    return ItemReviewResult(
        erp_item_code=erp_code,
        judgment=judgment,
        eccn=effective_eccn,
        rationale=rationale,
        item_data=item,
        synced=synced,
        erp_reachable=True,
    )


async def _screen_party(party: dict) -> CounterpartyScreenResult:
    """1取引先を制裁スクリーニングする。"""
    erp_code = party["erp_party_code"]
    try:
        result = await screen_adapter.screen_entity(
            name=party["name"],
            country=party["country"] or None,
            address=party["address"],
        )
        screen_result = "HIT" if result["is_match"] else "CLEAR"
        return CounterpartyScreenResult(
            erp_party_code=erp_code,
            screen_result=screen_result,
            is_match=result["is_match"],
            confidence=result["confidence"],
            list_name=result.get("list_name"),
            rationale=result.get("rationale"),
            counterparty_data=party,
            erp_reachable=True,
        )
    except Exception as exc:
        logger.warning("screening failed for %s: %s", erp_code, exc)
        return CounterpartyScreenResult(
            erp_party_code=erp_code,
            screen_result="UNKNOWN",
            is_match=False,
            confidence=0.0,
            rationale=f"Screening error: {exc}",
            counterparty_data=party,
            erp_reachable=True,
        )


# ── エンドポイント ─────────────────────────────────────────────────────────────

@router.post("/item-review", response_model=ItemReviewResult)
async def pull_item_review(body: ItemReviewRequest) -> ItemReviewResult:
    """
    ERP から品目コードで品目マスターを取得し、AI 該非判定を実施して返す。

    - ERP が応答しない場合は erp_reachable=False を返す（エラーにしない）
    - 判定結果は plat_item (PostgreSQL) および ai_classification (SQLite) に同期する
    """
    raw = await erp_pull.pull_item(body.erp_item_code)
    if raw is None:
        return ItemReviewResult(
            erp_item_code=body.erp_item_code,
            judgment="PENDING",
            rationale="ERP item not found or ERP unreachable.",
            erp_reachable=False,
        )

    item = _normalize_item(raw)
    item["erp_item_code"] = item["erp_item_code"] or body.erp_item_code
    return await _judge_item(item)


@router.post("/items-batch-review", response_model=ItemsBatchReviewResponse)
async def pull_items_batch_review(body: ItemsBatchReviewRequest) -> ItemsBatchReviewResponse:
    """
    ERP から輸出管理レビュー対象品目を一括取得して AI 該非判定を実施する。

    ERP 側は GET /api/materials?export_review=true&limit=N を提供していること。
    """
    raws = await erp_pull.pull_items_for_review(limit=body.limit)
    erp_reachable = True

    if not raws:
        logger.info("pull_items_batch_review: ERP returned 0 items (unreachable or empty)")
        erp_reachable = False

    items = [_normalize_item(r) for r in raws]
    results = await asyncio.gather(*[_judge_item(i) for i in items])

    counts: dict[str, int] = {"APPLICABLE": 0, "NOT_APPLICABLE": 0, "PENDING": 0}
    for r in results:
        counts[r.judgment] = counts.get(r.judgment, 0) + 1

    logger.info(
        "pull_items_batch_review complete: %d items reviewed (%s, reason=%s)",
        len(results), counts, body.reason,
    )
    return ItemsBatchReviewResponse(
        reviewed=len(results),
        applicable=counts.get("APPLICABLE", 0),
        not_applicable=counts.get("NOT_APPLICABLE", 0),
        pending=counts.get("PENDING", 0),
        erp_reachable=erp_reachable,
        results=list(results),
    )


@router.post("/counterparty-screen", response_model=CounterpartyScreenResult)
async def pull_counterparty_screen(body: CounterpartyScreenRequest) -> CounterpartyScreenResult:
    """
    ERP から取引先コードで取引先マスターを取得し、制裁スクリーニングを実施して返す。
    """
    raw = await erp_pull.pull_counterparty(body.erp_party_code)
    if raw is None:
        return CounterpartyScreenResult(
            erp_party_code=body.erp_party_code,
            screen_result="UNKNOWN",
            is_match=False,
            confidence=0.0,
            rationale="ERP counterparty not found or ERP unreachable.",
            erp_reachable=False,
        )

    party = _normalize_party(raw)
    party["erp_party_code"] = party["erp_party_code"] or body.erp_party_code
    return await _screen_party(party)


@router.post("/counterparties-batch-screen", response_model=CounterpartiesBatchScreenResponse)
async def pull_counterparties_batch_screen(
    body: CounterpartiesBatchScreenRequest,
) -> CounterpartiesBatchScreenResponse:
    """
    ERP から取引先リストを一括取得して制裁スクリーニングを実施する。

    ERP 側は GET /api/vendors?limit=N を提供していること。
    """
    raws = await erp_pull.pull_counterparties_for_review(limit=body.limit)
    erp_reachable = True

    if not raws:
        logger.info("pull_counterparties_batch_screen: ERP returned 0 parties (unreachable or empty)")
        erp_reachable = False

    parties = [_normalize_party(r) for r in raws]
    results = await asyncio.gather(*[_screen_party(p) for p in parties])

    hits = sum(1 for r in results if r.is_match)
    clear = sum(1 for r in results if not r.is_match and r.screen_result == "CLEAR")
    unknown = sum(1 for r in results if r.screen_result == "UNKNOWN")

    logger.info(
        "pull_counterparties_batch_screen complete: %d screened, %d hits (reason=%s)",
        len(results), hits, body.reason,
    )
    return CounterpartiesBatchScreenResponse(
        screened=len(results),
        hits=hits,
        clear=clear,
        unknown=unknown,
        erp_reachable=erp_reachable,
        results=list(results),
    )
