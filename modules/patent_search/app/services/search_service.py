from typing import Dict, Optional
import json
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.services.bigquery_service import get_bigquery_service
from app.models.search_history import SearchHistory


class SearchService:
    """Service for orchestrating patent searches and managing search history."""

    def __init__(self):
        self.bigquery_service = get_bigquery_service()  # シングルトン利用（リクエストごとに再初期化しない）

    async def search(
        self,
        db: AsyncSession,
        keywords: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        inventor: Optional[str] = None,
        applicant: Optional[str] = None,
        country: Optional[str] = None,
        limit: Optional[int] = None
    ) -> Dict:
        """
        Execute patent search and save to history.

        Args:
            db: Database session
            keywords: Search keywords
            date_from: Start date filter
            date_to: End date filter
            inventor: Inventor name filter
            applicant: Applicant name filter
            country: Country code filter
            limit: Maximum results

        Returns:
            Search results with patents and metadata
        """
        # Execute BigQuery search
        results = await self.bigquery_service.search_patents(
            keywords=keywords,
            date_from=date_from,
            date_to=date_to,
            inventor=inventor,
            applicant=applicant,
            country=country,
            limit=limit
        )

        # Save to search history
        filters = {
            "date_from": date_from,
            "date_to": date_to,
            "inventor": inventor,
            "applicant": applicant,
            "country": country,
            "limit": limit
        }

        # Remove None values from filters
        filters = {k: v for k, v in filters.items() if v is not None}

        history_entry = SearchHistory(
            query=keywords,
            filters=json.dumps(filters) if filters else None,
            result_count=results["count"],
            created_at=datetime.utcnow()
        )

        db.add(history_entry)
        await db.commit()

        return results

    async def get_search_history(
        self,
        db: AsyncSession,
        limit: int = 20
    ) -> list[SearchHistory]:
        """
        Get recent search history.

        Args:
            db: Database session
            limit: Maximum number of history entries

        Returns:
            List of search history entries
        """
        result = await db.execute(
            select(SearchHistory)
            .order_by(desc(SearchHistory.created_at))
            .limit(limit)
        )
        return result.scalars().all()

    async def delete_history_entry(
        self,
        db: AsyncSession,
        history_id: int
    ) -> bool:
        """Delete a specific search history entry."""
        result = await db.execute(
            select(SearchHistory).where(SearchHistory.id == history_id)
        )
        history_entry = result.scalar_one_or_none()

        if history_entry:
            await db.delete(history_entry)
            await db.commit()
            return True

        return False

    async def clear_all_history(self, db: AsyncSession) -> int:
        """Clear all search history and return count of deleted entries."""
        result = await db.execute(select(SearchHistory))
        entries = result.scalars().all()
        count = len(entries)

        for entry in entries:
            await db.delete(entry)

        await db.commit()
        return count
