from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.search_service import SearchService
from app.services.favorites_service import FavoritesService

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/home", response_class=HTMLResponse)
async def home_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Home page with search form."""
    search_service = SearchService()

    # Get recent search history
    recent_searches = await search_service.get_search_history(db, limit=5)

    return templates.TemplateResponse(
        request, "index.html",
        {
            "recent_searches": recent_searches
        }
    )


@router.get("/search", response_class=HTMLResponse)
async def search_results_page(
    request: Request,
    keywords: str = "",
    date_from: str = None,
    date_to: str = None,
    inventor: str = None,
    applicant: str = None,
    country: str = None,
    db: AsyncSession = Depends(get_db)
):
    """Search results page."""
    search_service = SearchService()
    results = None
    error = None

    if keywords:
        try:
            results = await search_service.search(
                db=db,
                keywords=keywords,
                date_from=date_from if date_from else None,
                date_to=date_to if date_to else None,
                inventor=inventor if inventor else None,
                applicant=applicant if applicant else None,
                country=country if country else None
            )
        except Exception as e:
            error = str(e)

    return templates.TemplateResponse(
        request, "search_results.html",
        {
            "results": results,
            "error": error,
            "keywords": keywords,
            "filters": {
                "date_from": date_from or "",
                "date_to": date_to or "",
                "inventor": inventor or "",
                "applicant": applicant or "",
                "country": country or ""
            }
        }
    )


@router.get("/patent/{publication_number}", response_class=HTMLResponse)
async def patent_detail_page(
    request: Request,
    publication_number: str,
    db: AsyncSession = Depends(get_db)
):
    """Patent detail page."""
    favorites_service = FavoritesService()

    # Check if patent is in favorites
    favorite = await favorites_service.get_favorite_by_publication_number(
        db, publication_number
    )

    # Get use cases if available
    use_cases = []
    if favorite:
        use_cases = await favorites_service.get_use_cases(db, favorite.id)

    return templates.TemplateResponse(
        request, "patent_detail.html",
        {
            "publication_number": publication_number,
            "favorite": favorite,
            "use_cases": use_cases
        }
    )


@router.get("/favorites", response_class=HTMLResponse)
async def favorites_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Favorites page showing all saved patents."""
    favorites_service = FavoritesService()

    favorites = await favorites_service.get_all_favorites(db)

    # Parse favorites for display
    parsed_favorites = [
        favorites_service.parse_favorite_for_display(fav)
        for fav in favorites
    ]

    return templates.TemplateResponse(
        request, "favorites.html",
        {
            "favorites": parsed_favorites
        }
    )


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Search history page."""
    search_service = SearchService()

    history = await search_service.get_search_history(db, limit=50)

    return templates.TemplateResponse(
        request, "history.html",
        {
            "history": history
        }
    )
