from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Optional

from app.database import get_db
from app.services.export_service import ExportService
from app.services.favorites_service import FavoritesService

router = APIRouter()


@router.get("/export/favorites/csv")
async def export_favorites_csv(
    db: AsyncSession = Depends(get_db)
):
    """Export all favorite patents to CSV."""
    favorites_service = FavoritesService()
    export_service = ExportService()

    try:
        # Get all favorites
        favorites = await favorites_service.get_all_favorites(db)

        if not favorites:
            raise HTTPException(
                status_code=404,
                detail="No favorites to export"
            )

        # Prepare data for export
        export_data = export_service.prepare_favorites_for_export(favorites)

        # Generate CSV
        csv_content = export_service.export_to_csv(export_data)

        # Return as downloadable file
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=favorite_patents.csv"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Export failed: {str(e)}"
        )


@router.get("/export/favorites/json")
async def export_favorites_json(
    db: AsyncSession = Depends(get_db)
):
    """Export all favorite patents to JSON."""
    favorites_service = FavoritesService()
    export_service = ExportService()

    try:
        # Get all favorites
        favorites = await favorites_service.get_all_favorites(db)

        if not favorites:
            raise HTTPException(
                status_code=404,
                detail="No favorites to export"
            )

        # Prepare data for export
        export_data = export_service.prepare_favorites_for_export(favorites)

        # Generate JSON
        json_content = export_service.export_to_json(export_data)

        # Return as downloadable file
        return Response(
            content=json_content,
            media_type="application/json",
            headers={
                "Content-Disposition": "attachment; filename=favorite_patents.json"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Export failed: {str(e)}"
        )


@router.post("/export/results/csv")
async def export_search_results_csv(
    results: List[Dict]
):
    """
    Export search results to CSV.

    Request body should contain a list of patent dictionaries.
    """
    export_service = ExportService()

    try:
        if not results:
            raise HTTPException(
                status_code=400,
                detail="No results to export"
            )

        # Generate CSV
        csv_content = export_service.export_to_csv(results)

        # Return as downloadable file
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=search_results.csv"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Export failed: {str(e)}"
        )


@router.post("/export/results/json")
async def export_search_results_json(
    results: List[Dict]
):
    """
    Export search results to JSON.

    Request body should contain a list of patent dictionaries.
    """
    export_service = ExportService()

    try:
        if not results:
            raise HTTPException(
                status_code=400,
                detail="No results to export"
            )

        # Generate JSON
        json_content = export_service.export_to_json(results)

        # Return as downloadable file
        return Response(
            content=json_content,
            media_type="application/json",
            headers={
                "Content-Disposition": "attachment; filename=search_results.json"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Export failed: {str(e)}"
        )

@router.get("/export/use-cases/{favorite_id}/csv")
async def export_use_cases_csv(
    favorite_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Export use cases for a specific patent to CSV."""
    favorites_service = FavoritesService()
    export_service = ExportService()

    try:
        # Get patent info
        favorite = await favorites_service.get_favorite_by_id(db, favorite_id)
        if not favorite:
            raise HTTPException(status_code=404, detail="Patent not found")

        # Get use cases
        use_cases = await favorites_service.get_use_cases(db, favorite_id)
        if not use_cases:
            raise HTTPException(
                status_code=404,
                detail="No use cases found for this patent"
            )

        # Prepare patent info
        patent_info = {
            "publication_number": favorite.publication_number,
            "title": favorite.title
        }

        # Prepare use cases data
        use_cases_data = [
            {
                "text": uc.use_case_text,
                "model": uc.extraction_model,
                "created_at": uc.created_at.isoformat()
            }
            for uc in use_cases
        ]

        # Generate CSV
        csv_content = export_service.export_use_cases_to_csv(patent_info, use_cases_data)

        # Return as downloadable file
        filename = f"use_cases_{favorite.publication_number}.csv"
        return Response(
            content=csv_content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Export failed: {str(e)}"
        )


@router.get("/export/use-cases/all/csv")
async def export_all_use_cases_csv(
    db: AsyncSession = Depends(get_db)
):
    """Export all use cases from all favorite patents to CSV."""
    favorites_service = FavoritesService()
    export_service = ExportService()

    try:
        # Get all favorites
        favorites = await favorites_service.get_all_favorites(db)
        if not favorites:
            raise HTTPException(status_code=404, detail="No favorites found")

        # Collect all patents with use cases
        patents_with_use_cases = []
        for favorite in favorites:
            use_cases = await favorites_service.get_use_cases(db, favorite.id)
            if use_cases:
                patent_data = {
                    "patent": {
                        "publication_number": favorite.publication_number,
                        "title": favorite.title
                    },
                    "use_cases": [
                        {
                            "text": uc.use_case_text,
                            "model": uc.extraction_model,
                            "created_at": uc.created_at.isoformat()
                        }
                        for uc in use_cases
                    ]
                }
                patents_with_use_cases.append(patent_data)

        if not patents_with_use_cases:
            raise HTTPException(
                status_code=404,
                detail="No use cases found"
            )

        # Generate CSV
        csv_content = export_service.export_multiple_patents_use_cases_to_csv(patents_with_use_cases)

        # Return as downloadable file
        return Response(
            content=csv_content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": "attachment; filename=all_use_cases.csv"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Export failed: {str(e)}"
        )
