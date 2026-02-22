from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.core.logging import setup_logging
from app.api.v1.api import api_router
from app.ui.router import router as ui_router


def create_app() -> FastAPI:
    setup_logging()

    app = FastAPI(
        title="R&D Risk App (PoC)",
        version="0.1.0",
    )

    # API (JSON)
    app.include_router(api_router, prefix="/api/v1")

    # UI (Jinja2 GUI)
    app.include_router(ui_router)

    @app.get("/", include_in_schema=False)
    def _root():
        return RedirectResponse(url="/ui/")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
