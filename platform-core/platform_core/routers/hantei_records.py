"""
hantei_records.py — モジュール横断 該非判定レコード CRUD API

モジュール間で共有する plat_hantei_records テーブルへの読み書きを提供する。
- R&D → 品目管理 → 取引審査 の判定データを引き継ぐために使用する
- POST /api/hantei/records         : 判定結果を保存（バルク対応）
- GET  /api/hantei/records/{code}  : 品目コードで判定履歴を取得（全モジュール分）
- DELETE /api/hantei/records/{id}  : レコード削除
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.hantei_record import HanteiRecord

router = APIRouter(prefix="/api/hantei", tags=["hantei"])


# ── Pydantic スキーマ ──────────────────────────────────────────────────────────

class HanteiRecordIn(BaseModel):
    product_code: str
    source_module: str           # rnd | classification | validation
    rnd_case_id: Optional[str] = None
    transaction_id: Optional[int] = None
    item_no: str = ""
    item_label: str = ""
    source_type: str = ""
    regulation_text: Optional[str] = None
    llm_verdict: str = ""        # APPLICABLE / REVIEW_NEEDED / NOT_APPLICABLE
    llm_confidence: str = ""     # HIGH / MEDIUM / LOW
    llm_reason: Optional[str] = None
    llm_key_question: Optional[str] = None
    decision: str = ""           # controlled / needs_review / non_controlled
    notes: Optional[str] = None


class HanteiRecordOut(HanteiRecordIn):
    id: int
    recorded_at: datetime

    class Config:
        from_attributes = True


class BulkSaveRequest(BaseModel):
    records: list[HanteiRecordIn]


class BulkSaveResponse(BaseModel):
    saved: int
    ids: list[int]


# ── エンドポイント ─────────────────────────────────────────────────────────────

@router.post("/records", response_model=BulkSaveResponse)
async def save_records(req: BulkSaveRequest, db: AsyncSession = Depends(get_db)):
    """
    判定結果をバルク保存する。
    既存レコードとの重複チェックは行わない（同一品目の再審査は追記で管理）。
    """
    saved_ids: list[int] = []
    for r in req.records:
        rec = HanteiRecord(**r.model_dump())
        db.add(rec)
        await db.flush()  # id を取得するために flush
        saved_ids.append(rec.id)
    await db.commit()
    return BulkSaveResponse(saved=len(saved_ids), ids=saved_ids)


@router.get("/records/{product_code}", response_model=list[HanteiRecordOut])
async def get_records(
    product_code: str,
    source_module: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """
    品目コードで判定履歴を取得する。
    source_module を指定すると特定モジュールの判定のみ返す。
    recorded_at の降順で返す（最新判定が先頭）。
    """
    stmt = (
        select(HanteiRecord)
        .where(HanteiRecord.product_code == product_code)
        .order_by(HanteiRecord.recorded_at.desc())
        .limit(limit)
    )
    if source_module:
        stmt = stmt.where(HanteiRecord.source_module == source_module)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.delete("/records/{record_id}", status_code=204)
async def delete_record(record_id: int, db: AsyncSession = Depends(get_db)):
    """指定 ID のレコードを削除する。"""
    stmt = delete(HanteiRecord).where(HanteiRecord.id == record_id)
    result = await db.execute(stmt)
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Record not found")
