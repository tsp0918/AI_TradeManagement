"""組織コンテキスト解決ユーティリティ。

優先順位:
  1. X-Organization-Id リクエストヘッダー（フロントエンドのスイッチャーが設定）
  2. データベースの最初のアクティブテナント（デフォルト）

使い方:
    from platform_core.auth.organization import get_current_org_id

    @router.get("/something")
    async def handler(
        org_id: uuid.UUID = Depends(get_current_org_id),
        db: AsyncSession = Depends(get_db),
    ):
        ...
"""
from __future__ import annotations

import uuid

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db


async def get_current_org_id(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID:
    """現在の拠点（テナント）ID を返す FastAPI 依存関数。"""
    header_val = request.headers.get("X-Organization-Id", "").strip()
    if header_val:
        try:
            return uuid.UUID(header_val)
        except ValueError:
            pass
    return await _resolve_default_org(db)


async def _resolve_default_org(db: AsyncSession) -> uuid.UUID:
    """デフォルト拠点（最初のアクティブテナント）を返す。存在しない場合は自動作成。"""
    from platform_core.models.tenant import Tenant

    result = await db.execute(
        select(Tenant).where(Tenant.is_active == True).order_by(Tenant.created_at).limit(1)
    )
    tenant = result.scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(
            name="本社（日本）",
            slug="default",
            plan="standard",
            country_code="JP",
            export_regime="FEFTA",
            org_type="HQ",
            timezone="Asia/Tokyo",
            flag_emoji="🇯🇵",
        )
        db.add(tenant)
        await db.flush()
    return tenant.id
