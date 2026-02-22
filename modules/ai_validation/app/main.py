# app/main.py の先頭（他の import より前に置く）
import os

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

# HuggingFace のキャッシュを固定（並列DL/競合を減らす）
os.environ.setdefault("HF_HOME", os.path.join(os.getcwd(), ".hf_cache"))
os.environ.setdefault("TRANSFORMERS_CACHE", os.path.join(os.getcwd(), ".hf_cache"))

# app/main.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from app.routers.decision import router as decision_router
from app.routers.ui import router as ui_router
from app.routers.integration_export_control import router as integration_router

from app.db.session import engine
from app.db.base import Base

# create_all が拾うようにモデルを import
# （integration.py の場所はあなたの現状に合わせてOK）
from app.db.models import integration  # noqa: F401

# テーブル作成（PoC向け：Alembic導入後は削除してOK）
Base.metadata.create_all(bind=engine)

app = FastAPI(title="AI Validation (Trade Screening)", version="0.1.0")

# static
app.mount("/static", StaticFiles(directory="static"), name="static")

# templates (ui.py で利用)
templates = Jinja2Templates(directory="templates")
app.state.templates = templates

# routers
app.include_router(ui_router)
app.include_router(decision_router)
app.include_router(integration_router)


@app.get("/")
def health_check():
    return {"status": "ok"}
