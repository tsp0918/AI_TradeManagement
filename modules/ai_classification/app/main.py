import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from platform_core.module_sdk import AuditMiddleware, ModuleInfo, build_lifespan, health_router

from .routers.products import router as products_router
from .routers.sds import router as sds_router
from .routers.integrations import router as integrations_router
from .routers.country_profiles import router as country_profiles_router
from .routers.hs_local import router as hs_local_router
from .routers.tariff_fetch import router as tariff_fetch_router
from .database import engine


async def _ensure_columns() -> None:
    """SQLite 起動時カラム自動追加・テーブル自動作成（Alembic 非適用環境向け）"""
    from sqlalchemy import inspect as sa_inspect, text

    needed: dict[str, list[tuple[str, str]]] = {
        "products": [
            ("source_rnd_case_id",          "VARCHAR(64)"),
            ("source_rnd_transaction_id",   "INTEGER"),
            ("ui_validation_transaction_id","INTEGER"),
            ("export_control_items",        "TEXT"),
            ("country_of_origin",           "VARCHAR(10)"),
            ("regulation_score",            "REAL"),
            ("sovereignty_score",           "REAL"),
            ("sovereignty_note",            "TEXT"),
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

    # product_country_profiles テーブルを初回起動時に作成
    from .models import ProductCountryProfile  # noqa: F401 – ensure mapping registered
    from sqlalchemy import inspect as _insp
    with engine.connect() as conn:
        if not _insp(engine).has_table("product_country_profiles"):
            from .database import Base
            Base.metadata.create_all(engine, tables=[
                Base.metadata.tables["product_country_profiles"]
            ])


MODULE = ModuleInfo(
    key="ai_classification",
    name="品目管理",
    base_url=os.environ.get("MODULE_AI_CLASSIFICATION_URL", "http://localhost:8002"),
    description="取り扱い品目の法規制情報管理・SDS解析・該非判定連携",
    capabilities=["product_manage", "sds_analyze", "regulation_check"],
    data_contracts={
        "input":  ["Product", "SDS"],
        "output": ["Product", "RegulationResult"],
    },
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Classification (Product Management)",
        version="0.1.0",
        lifespan=build_lifespan(MODULE, on_startup=_ensure_columns),
    )

    app.add_middleware(AuditMiddleware, module_key="ai_classification")

    if os.path.isdir("static"):
        app.mount("/static", StaticFiles(directory="static"), name="static")

    app.include_router(health_router)
    app.include_router(products_router)
    app.include_router(sds_router)
    app.include_router(integrations_router)
    app.include_router(country_profiles_router)
    app.include_router(hs_local_router)
    app.include_router(tariff_fetch_router)

    return app


app = create_app()
