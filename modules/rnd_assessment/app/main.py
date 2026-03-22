import os

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from platform_core.module_sdk import AuditMiddleware, ModuleInfo, build_lifespan

from app.core.logging import setup_logging
from app.api.v1.api import api_router
from app.ui.router import router as ui_router
from app.db.base import Base
from app.db.session import engine
import app.models  # noqa: F401 — モデルを登録してから create_all を呼ぶ


async def _init_db() -> None:
    """SQLite 開発環境向け: 未作成テーブルを自動生成し、不足カラムを追加する。"""
    from sqlalchemy import inspect as sa_inspect, text

    Base.metadata.create_all(bind=engine)

    needed: dict[str, list[tuple[str, str]]] = {
        "rd_case_profiles": [
            ("promoted_product_id", "INTEGER"),
            ("promoted_at",         "DATETIME"),
        ],
        "personnel": [
            ("case_id",               "VARCHAR(64)"),
            ("tenant_id",             "VARCHAR(64)"),
            ("name",                  "VARCHAR(200)"),
            ("role",                  "VARCHAR(100)"),
            ("affiliation",           "VARCHAR(200)"),
            ("nationality",           "VARCHAR(2)"),
            ("residence_country",     "VARCHAR(2)"),
            ("years_in_japan",        "REAL"),
            ("dual_employment_flag",  "BOOLEAN"),
            ("dual_employer_name",    "VARCHAR(200)"),
            ("dual_employer_country", "VARCHAR(2)"),
            ("tech_access_eccn",      "VARCHAR(100)"),
            ("tech_access_fefta",     "VARCHAR(100)"),
            ("deemed_export_category","VARCHAR(1)"),
            ("deemed_export_risk",    "VARCHAR(20)"),
            ("deemed_export_reason",  "TEXT"),
            ("screened_at",           "DATETIME"),
            ("note",                  "TEXT"),
            ("created_at",            "DATETIME"),
            ("updated_at",            "DATETIME"),
        ],
    }
    with engine.connect() as conn:
        inspector = sa_inspect(engine)
        for table, cols in needed.items():
            try:
                existing = {c["name"] for c in inspector.get_columns(table)}
            except Exception:
                continue
            for col_name, col_type in cols:
                if col_name not in existing:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"
                    ))
        conn.commit()


MODULE = ModuleInfo(
    key="rnd_assessment",
    name="R&Dリスク評価",
    base_url=os.environ.get("MODULE_RND_ASSESSMENT_URL", "http://localhost:8003"),
    description="R&Dプロジェクトの知財オープン/クローズ戦略と安全保障貿易管理の統合リスク評価",
    capabilities=["rnd_case_create", "risk_assess", "ip_review"],
    data_contracts={
        "input":  ["RdCase"],
        "output": ["Assessment", "IpReview"],
    },
)


def create_app() -> FastAPI:
    setup_logging()

    app = FastAPI(
        title="R&D Risk App",
        version="0.1.0",
        lifespan=build_lifespan(MODULE, on_startup=_init_db),
    )

    app.add_middleware(AuditMiddleware, module_key="rnd_assessment")
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(ui_router)

    @app.get("/", include_in_schema=False)
    def _root():
        return RedirectResponse(url="/ui/")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
