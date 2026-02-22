from typing import List, Optional, Dict
import json
from datetime import datetime, date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.models.favorite_patent import FavoritePatent
from app.models.use_case import PatentUseCase


class FavoritesService:
    """Service for managing favorite patents."""

    async def add_favorite(
        self,
        db: AsyncSession,
        publication_number: str,
        title: str,
        abstract: Optional[str] = None,
        description: Optional[str] = None,
        claims: Optional[str] = None,
        inventors: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
        filing_date: Optional[str] = None,
        publication_date: Optional[str] = None,
        url: Optional[str] = None,
        notes: Optional[str] = None
    ) -> FavoritePatent:
        """
        Add a patent to favorites.

        Args:
            db: Database session
            publication_number: Patent publication number (unique identifier)
            title: Patent title
            abstract: Patent abstract
            description: Patent description
            claims: Patent claims
            inventors: List of inventor names
            assignees: List of assignee/applicant names
            filing_date: Filing date (YYYY-MM-DD)
            publication_date: Publication date (YYYY-MM-DD)
            url: URL to patent
            notes: User notes

        Returns:
            Created FavoritePatent instance
        """
        # Check if already exists
        result = await db.execute(
            select(FavoritePatent).where(
                FavoritePatent.publication_number == publication_number
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            raise ValueError(f"Patent {publication_number} is already in favorites")

        # Convert date strings to date objects
        filing_date_obj = None
        if filing_date:
            try:
                filing_date_obj = datetime.fromisoformat(filing_date).date()
            except (ValueError, TypeError):
                pass

        publication_date_obj = None
        if publication_date:
            try:
                publication_date_obj = datetime.fromisoformat(publication_date).date()
            except (ValueError, TypeError):
                pass

        # Create favorite
        favorite = FavoritePatent(
            publication_number=publication_number,
            title=title,
            abstract=abstract,
            description=description,
            claims=claims,
            inventors=json.dumps(inventors) if inventors else None,
            applicants=json.dumps(assignees) if assignees else None,
            filing_date=filing_date_obj,
            publication_date=publication_date_obj,
            url=url,
            notes=notes,
            added_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )

        db.add(favorite)
        await db.commit()
        await db.refresh(favorite)

        return favorite

    async def get_all_favorites(
        self,
        db: AsyncSession,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[FavoritePatent]:
        """Get all favorite patents, ordered by most recently added."""
        query = select(FavoritePatent).order_by(desc(FavoritePatent.added_at))

        if limit:
            query = query.limit(limit).offset(offset)

        result = await db.execute(query)
        return result.scalars().all()

    async def get_favorite_by_id(
        self,
        db: AsyncSession,
        favorite_id: int
    ) -> Optional[FavoritePatent]:
        """Get a specific favorite patent by ID."""
        result = await db.execute(
            select(FavoritePatent).where(FavoritePatent.id == favorite_id)
        )
        return result.scalar_one_or_none()

    async def get_favorite_by_publication_number(
        self,
        db: AsyncSession,
        publication_number: str
    ) -> Optional[FavoritePatent]:
        """Get a favorite patent by publication number."""
        result = await db.execute(
            select(FavoritePatent).where(
                FavoritePatent.publication_number == publication_number
            )
        )
        return result.scalar_one_or_none()

    async def update_notes(
        self,
        db: AsyncSession,
        favorite_id: int,
        notes: str
    ) -> Optional[FavoritePatent]:
        """Update notes for a favorite patent."""
        result = await db.execute(
            select(FavoritePatent).where(FavoritePatent.id == favorite_id)
        )
        favorite = result.scalar_one_or_none()

        if favorite:
            favorite.notes = notes
            favorite.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(favorite)
            return favorite

        return None

    async def delete_favorite(
        self,
        db: AsyncSession,
        favorite_id: int
    ) -> bool:
        """Delete a favorite patent."""
        result = await db.execute(
            select(FavoritePatent).where(FavoritePatent.id == favorite_id)
        )
        favorite = result.scalar_one_or_none()

        if favorite:
            await db.delete(favorite)
            await db.commit()
            return True

        return False

    async def add_use_case(
        self,
        db: AsyncSession,
        patent_id: int,
        use_case_text: str,
        extraction_model: Optional[str] = None,
        confidence_score: Optional[float] = None
    ) -> PatentUseCase:
        """Add a use case to a favorite patent."""
        use_case = PatentUseCase(
            patent_id=patent_id,
            use_case_text=use_case_text,
            extraction_model=extraction_model,
            confidence_score=confidence_score,
            created_at=datetime.utcnow()
        )

        db.add(use_case)
        await db.commit()
        await db.refresh(use_case)

        return use_case

    async def get_use_cases(
        self,
        db: AsyncSession,
        patent_id: int
    ) -> List[PatentUseCase]:
        """Get all use cases for a patent."""
        result = await db.execute(
            select(PatentUseCase).where(PatentUseCase.patent_id == patent_id)
        )
        return result.scalars().all()

    async def delete_use_case(
        self,
        db: AsyncSession,
        use_case_id: int
    ) -> bool:
        """Delete a specific use case."""
        result = await db.execute(
            select(PatentUseCase).where(PatentUseCase.id == use_case_id)
        )
        use_case = result.scalar_one_or_none()

        if use_case:
            await db.delete(use_case)
            await db.commit()
            return True

        return False

    def parse_favorite_for_display(self, favorite: FavoritePatent) -> Dict:
        """Parse favorite patent for display (deserialize JSON fields)."""
        return {
            "id": favorite.id,
            "publication_number": favorite.publication_number,
            "title": favorite.title,
            "abstract": favorite.abstract,
            "description": favorite.description,
            "claims": favorite.claims,
            "inventors": json.loads(favorite.inventors) if favorite.inventors else [],
            "assignees": json.loads(favorite.applicants) if favorite.applicants else [],
            "filing_date": favorite.filing_date.isoformat() if favorite.filing_date else None,
            "publication_date": favorite.publication_date.isoformat() if favorite.publication_date else None,
            "url": favorite.url,
            "notes": favorite.notes,
            "added_at": favorite.added_at.isoformat(),
            "updated_at": favorite.updated_at.isoformat()
        }
