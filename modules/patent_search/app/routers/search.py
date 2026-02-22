from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database import get_db
from app.services.search_service import SearchService

router = APIRouter()


@router.post("/search")
async def search_patents(
    keywords: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    inventor: Optional[str] = None,
    applicant: Optional[str] = None,
    country: Optional[str] = None,
    limit: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Search Google Patents using BigQuery.

    Query Parameters:
    - keywords: Search keywords (required)
    - date_from: Start date filter (YYYY-MM-DD)
    - date_to: End date filter (YYYY-MM-DD)
    - inventor: Inventor name filter
    - applicant: Applicant/assignee name filter
    - country: Country code filter (e.g., US, JP, EP)
    - limit: Maximum number of results
    """
    search_service = SearchService()

    try:
        results = await search_service.search(
            db=db,
            keywords=keywords,
            date_from=date_from,
            date_to=date_to,
            inventor=inventor,
            applicant=applicant,
            country=country,
            limit=limit
        )
        return results

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Search failed: {str(e)}"
        )


@router.get("/search/history")
async def get_search_history(
    limit: int = 20,
    db: AsyncSession = Depends(get_db)
):
    """Get recent search history."""
    search_service = SearchService()

    try:
        history = await search_service.get_search_history(db, limit=limit)
        return {
            "history": [
                {
                    "id": h.id,
                    "query": h.query,
                    "filters": h.filters,
                    "result_count": h.result_count,
                    "created_at": h.created_at.isoformat()
                }
                for h in history
            ]
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve history: {str(e)}"
        )


@router.delete("/search/history/{history_id}")
async def delete_history_entry(
    history_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Delete a specific search history entry."""
    search_service = SearchService()

    try:
        deleted = await search_service.delete_history_entry(db, history_id)

        if deleted:
            return {"message": "History entry deleted successfully"}
        else:
            raise HTTPException(
                status_code=404,
                detail="History entry not found"
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete history entry: {str(e)}"
        )


@router.delete("/search/history")
async def clear_all_history(db: AsyncSession = Depends(get_db)):
    """Clear all search history."""
    search_service = SearchService()

    try:
        count = await search_service.clear_all_history(db)
        return {
            "message": f"Deleted {count} history entries",
            "count": count
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear history: {str(e)}"
        )
