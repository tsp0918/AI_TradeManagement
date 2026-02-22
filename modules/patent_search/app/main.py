from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

from app.config import settings
from app.database import init_db, close_db

# Import routers
from app.routers import pages, search, favorites, use_cases, export


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    await init_db()
    print(f"✓ Database initialized")
    print(f"✓ {settings.app_name} v{settings.app_version} starting...")
    print(f"✓ Server running at http://{settings.host}:{settings.port}")

    yield

    # Shutdown
    await close_db()
    print("✓ Database connections closed")


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include routers
app.include_router(pages.router, tags=["Pages"])
app.include_router(search.router, prefix="/api", tags=["Search"])
app.include_router(favorites.router, prefix="/api", tags=["Favorites"])
app.include_router(use_cases.router, prefix="/api", tags=["Use Cases"])
app.include_router(export.router, prefix="/api", tags=["Export"])


# Health check endpoint
@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    from app.services.bigquery_service import BigQueryService
    from app.services.ollama_service import OllamaService

    bigquery_service = BigQueryService()
    ollama_service = OllamaService()

    bigquery_status = await bigquery_service.check_connection()
    ollama_status = await ollama_service.check_connection()

    return {
        "status": "healthy",
        "app_name": settings.app_name,
        "version": settings.app_version,
        "services": {
            "bigquery": "connected" if bigquery_status else "disconnected",
            "ollama": "connected" if ollama_status else "disconnected"
        }
    }


# Root endpoint redirect
@app.get("/", include_in_schema=False)
async def root():
    """Root endpoint - handled by pages router."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/home")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )
