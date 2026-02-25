"""HTML UI ルーター。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services import faiss_index
from app.config import settings

router = APIRouter(tags=["ui"])

_TMPL_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(_TMPL_DIR))


@router.get("/ui", response_class=HTMLResponse)
@router.get("/ui/", response_class=HTMLResponse)
def ui_index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "index_size": faiss_index.ntotal(),
            "is_built": faiss_index._is_built(),
            "model": settings.embedding_model_name,
        },
    )
