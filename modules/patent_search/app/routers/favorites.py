from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from pydantic import BaseModel

from app.database import get_db
from app.services.favorites_service import FavoritesService

router = APIRouter()


class AddFavoriteRequest(BaseModel):
    """Request model for adding a favorite patent."""
    publication_number: str
    title: str
    abstract: Optional[str] = None
    description: Optional[str] = None
    claims: Optional[str] = None
    inventors: Optional[List[str]] = None
    assignees: Optional[List[str]] = None
    filing_date: Optional[str] = None
    publication_date: Optional[str] = None
    url: Optional[str] = None
    notes: Optional[str] = None


class UpdateNotesRequest(BaseModel):
    """Request model for updating notes."""
    notes: str


@router.get("/favorites")
async def get_all_favorites(
    limit: Optional[int] = None,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
):
    """Get all favorite patents."""
    favorites_service = FavoritesService()

    try:
        favorites = await favorites_service.get_all_favorites(db, limit=limit, offset=offset)

        return {
            "favorites": [
                favorites_service.parse_favorite_for_display(fav)
                for fav in favorites
            ],
            "count": len(favorites)
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve favorites: {str(e)}"
        )


@router.post("/favorites")
async def add_to_favorites(
    request: AddFavoriteRequest,
    db: AsyncSession = Depends(get_db)
):
    """Add a patent to favorites."""
    favorites_service = FavoritesService()

    try:
        favorite = await favorites_service.add_favorite(
            db=db,
            publication_number=request.publication_number,
            title=request.title,
            abstract=request.abstract,
            description=request.description,
            claims=request.claims,
            inventors=request.inventors,
            assignees=request.assignees,
            filing_date=request.filing_date,
            publication_date=request.publication_date,
            url=request.url,
            notes=request.notes
        )

        return {
            "message": "Patent added to favorites",
            "favorite": favorites_service.parse_favorite_for_display(favorite)
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to add favorite: {str(e)}"
        )


@router.get("/favorites/{favorite_id}")
async def get_favorite_by_id(
    favorite_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific favorite patent by ID."""
    favorites_service = FavoritesService()

    try:
        favorite = await favorites_service.get_favorite_by_id(db, favorite_id)

        if not favorite:
            raise HTTPException(status_code=404, detail="Favorite not found")

        return favorites_service.parse_favorite_for_display(favorite)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve favorite: {str(e)}"
        )


@router.put("/favorites/{favorite_id}")
async def update_favorite_notes(
    favorite_id: int,
    request: UpdateNotesRequest,
    db: AsyncSession = Depends(get_db)
):
    """Update notes for a favorite patent."""
    favorites_service = FavoritesService()

    try:
        favorite = await favorites_service.update_notes(
            db=db,
            favorite_id=favorite_id,
            notes=request.notes
        )

        if not favorite:
            raise HTTPException(status_code=404, detail="Favorite not found")

        return {
            "message": "Notes updated successfully",
            "favorite": favorites_service.parse_favorite_for_display(favorite)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update notes: {str(e)}"
        )


@router.delete("/favorites/{favorite_id}")
async def delete_favorite(
    favorite_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Delete a favorite patent."""
    favorites_service = FavoritesService()

    try:
        deleted = await favorites_service.delete_favorite(db, favorite_id)

        if deleted:
            return {"message": "Favorite deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="Favorite not found")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete favorite: {str(e)}"
        )
