import os

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("HF_HOME", os.path.join(os.getcwd(), ".hf_cache"))
os.environ.setdefault("TRANSFORMERS_CACHE", os.path.join(os.getcwd(), ".hf_cache"))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from platform_core.module_sdk import ModuleInfo, build_lifespan, health_router

from app.routers.decision import router as decision_router
from app.routers.ui import router as ui_router
from app.routers.integration_export_control import router as integration_router

MODULE = ModuleInfo(
    key="ai_validation",
    name="AI該非判定",
    base_url=os.environ.get("MODULE_AI_VALIDATION_URL", "http://localhost:8001"),
    description="外為法に基づく輸出管理の該非判定を AI で支援する",
    capabilities=["export_control", "matrix_run", "transaction_create"],
    data_contracts={
        "input":  ["Transaction"],
        "output": ["AiRun", "MatrixMatch"],
    },
)

app = FastAPI(
    title="AI Validation (Trade Screening)",
    version="0.1.0",
    lifespan=build_lifespan(MODULE),
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
app.state.templates = templates

app.include_router(health_router)
app.include_router(ui_router)
app.include_router(decision_router)
app.include_router(integration_router)
