import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from platform_core.module_sdk import AuditMiddleware, ModuleInfo, build_lifespan, health_router

from app.config import settings
from app.database import init_db, close_db
from app.routers import pages, search, favorites, use_cases, export, applicant_screening, academic_links, fterm_compliance

MODULE = ModuleInfo(
    key="patent_search",
    name="AI特許検索",
    base_url=os.environ.get("MODULE_PATENT_SEARCH_URL", "http://localhost:8004"),
    description="Google Patents BigQuery を用いた特許検索と LLM による用途要件抽出",
    capabilities=["patent_search", "use_case_extract", "favorite_manage"],
    data_contracts={
        "input":  ["SearchQuery"],
        "output": ["Patent", "UseCase"],
    },
    health_check_path="/health",
)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=build_lifespan(MODULE, on_startup=init_db, on_shutdown=close_db),
)

app.add_middleware(AuditMiddleware, module_key="patent_search")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(health_router)
app.include_router(pages.router, tags=["Pages"])
app.include_router(search.router, prefix="/api", tags=["Search"])
app.include_router(favorites.router, prefix="/api", tags=["Favorites"])
app.include_router(use_cases.router, prefix="/api", tags=["Use Cases"])
app.include_router(export.router, prefix="/api", tags=["Export"])
app.include_router(applicant_screening.router, prefix="/api", tags=["Applicant Screening"])
app.include_router(academic_links.router, prefix="/api", tags=["Academic Links"])
app.include_router(fterm_compliance.router, prefix="/api", tags=["Fterm Compliance"])


@app.get("/api/health")
async def health_check():
    from app.services.bigquery_service import get_bigquery_service
    from app.services.ollama_service import OllamaService
    bq = await get_bigquery_service().check_connection()
    ol = await OllamaService().check_connection()
    return {
        "status": "healthy",
        "services": {
            "bigquery": "connected" if bq else "disconnected",
            "ollama": "connected" if ol else "disconnected",
        },
    }


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/home")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.debug)
