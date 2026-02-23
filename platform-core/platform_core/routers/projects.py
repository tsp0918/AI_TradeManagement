"""案件 (Project) と特許リンク管理 API。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db.session import get_db
from platform_core.models.project import Project
from platform_core.models.project_patent_link import ProjectPatentLink

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    code: str | None = None
    title: str
    description: str | None = None
    status: str = "active"
    current_phase: str | None = None


class ProjectRead(BaseModel):
    id: uuid.UUID
    code: str | None
    title: str
    description: str | None
    status: str
    current_phase: str | None
    merged_into: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PatentLinkCreate(BaseModel):
    patent_publication_number: str
    note: str | None = None


class PatentLinkRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    patent_publication_number: str
    note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Projects ──────────────────────────────────────────────────────────

@router.get("/", response_model=list[ProjectRead])
async def search_projects(
    search: str | None = Query(None, description="タイトル・コードで絞り込み"),
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectRead]:
    """案件一覧を取得する。`search` を指定するとタイトル・コードで部分一致フィルタ。"""
    stmt = select(Project).order_by(Project.created_at.desc()).limit(limit)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            or_(Project.title.ilike(pattern), Project.code.ilike(pattern))
        )
    result = await db.execute(stmt)
    return [ProjectRead.model_validate(p) for p in result.scalars().all()]


@router.post("/", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db),
) -> ProjectRead:
    """案件を新規作成する。"""
    project = Project(**body.model_dump())
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return ProjectRead.model_validate(project)


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ProjectRead:
    """案件を1件取得する。"""
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectRead.model_validate(project)


# ── Patent links ──────────────────────────────────────────────────────

@router.post(
    "/{project_id}/patents",
    response_model=PatentLinkRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_patent_link(
    project_id: uuid.UUID,
    body: PatentLinkCreate,
    db: AsyncSession = Depends(get_db),
) -> PatentLinkRead:
    """案件に特許を紐付ける。同一案件への重複登録は 409 を返す。"""
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # アプリ側での重複チェック (DB 部分インデックスと二重防衛)
    existing = await db.execute(
        select(ProjectPatentLink).where(
            ProjectPatentLink.project_id == project_id,
            ProjectPatentLink.patent_publication_number == body.patent_publication_number,
            ProjectPatentLink.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Patent already linked to this project",
        )

    link = ProjectPatentLink(
        project_id=project_id,
        patent_publication_number=body.patent_publication_number,
        note=body.note,
    )
    db.add(link)
    await db.flush()
    await db.refresh(link)
    return PatentLinkRead.model_validate(link)


@router.get("/{project_id}/patents", response_model=list[PatentLinkRead])
async def list_patent_links(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[PatentLinkRead]:
    """案件に紐付けられた特許一覧を取得する（論理削除済みを除く）。"""
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    result = await db.execute(
        select(ProjectPatentLink)
        .where(
            ProjectPatentLink.project_id == project_id,
            ProjectPatentLink.deleted_at.is_(None),
        )
        .order_by(ProjectPatentLink.created_at)
    )
    return [PatentLinkRead.model_validate(link) for link in result.scalars().all()]


@router.delete(
    "/{project_id}/patents/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_patent_link(
    project_id: uuid.UUID,
    link_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """特許リンクを論理削除する（deleted_at を設定）。"""
    result = await db.execute(
        select(ProjectPatentLink).where(
            ProjectPatentLink.id == link_id,
            ProjectPatentLink.project_id == project_id,
            ProjectPatentLink.deleted_at.is_(None),
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="Patent link not found")
    link.deleted_at = datetime.now(timezone.utc)
    await db.flush()
