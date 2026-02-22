# app/main.py
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routers.products import router as products_router
from .routers.sds import router as sds_router
from .routers.integrations import router as integrations_router  # ← こっちだけ

def create_app() -> FastAPI:
    app = FastAPI()

    if os.path.isdir("static"):
        app.mount("/static", StaticFiles(directory="static"), name="static")

    app.include_router(products_router)
    app.include_router(sds_router)
    app.include_router(integrations_router)

    return app

app = create_app()
