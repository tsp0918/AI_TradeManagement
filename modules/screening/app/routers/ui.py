"""スクリーニング モジュール UI ルーター。"""

import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/ui", tags=["ui"])

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("", response_class=HTMLResponse, include_in_schema=False)
async def ui_root(request: Request):
    return RedirectResponse(url="/ui/screen")


@router.get("/screen", response_class=HTMLResponse, include_in_schema=False)
async def screen_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/results", response_class=HTMLResponse, include_in_schema=False)
async def results_page(request: Request):
    return templates.TemplateResponse("results.html", {"request": request})


@router.get("/watchlist", response_class=HTMLResponse, include_in_schema=False)
async def watchlist_page(request: Request):
    return templates.TemplateResponse("watchlist.html", {"request": request})
