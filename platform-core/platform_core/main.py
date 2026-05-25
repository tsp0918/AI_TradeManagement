"""platform-core FastAPI アプリケーション。

エンドポイント:
- /                 → /ui へリダイレクト
- /ui               ポータルホーム画面 (Jinja2)
- /ui/health/{key}  モジュールヘルスチェックプロキシ
- /auth/*           認証 (ローカルJWT / Google SSO / Microsoft SSO)
- /admin/*          管理 (tenants / users / modules)
- /api/projects/*   案件管理 (Project / PatentLink)
- /internal/*       内部 API (モジュール自動登録・モジュール間通信)
- /api/metrics/*    成果評価メトリクス (クロスモジュール KPI)
- /health           ヘルスチェック
"""

import asyncio
import logging
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from platform_core.auth.router import router as auth_router
from platform_core.config import settings
from platform_core.middleware.audit import AuditMiddleware
from platform_core.routers import admin_router
from platform_core.routers.internal import router as internal_router
from platform_core.routers.projects import router as projects_router
from platform_core.routers.ui import router as ui_router
from platform_core.routers.ui import start_all_modules, stop_all_modules
from platform_core.routers.proxy import router as proxy_router
from platform_core.agent.router import router as agent_router
from platform_core.routers.metrics import router as metrics_router
from platform_core.routers.faiss_search import router as faiss_search_router
from platform_core.routers.ontology import router as ontology_router
from platform_core.routers.regulatory import router as regulatory_router
from platform_core.routers.counterparty import router as counterparty_router  # proxy → screening
from platform_core.routers.supply_chain import router as supply_chain_router
from platform_core.routers.supplier_attestation import router as supplier_attestation_router
from platform_core.routers.supplier_portal import router as supplier_portal_router
from platform_core.routers.export_license import router as export_license_router
from platform_core.routers.item_version import router as item_version_router
from platform_core.routers.compliance_lookup import router as compliance_lookup_router
from platform_core.routers.transaction_review import router as transaction_review_router
from platform_core.routers.fta import router as fta_router
from platform_core.routers.organizations import router as organizations_router
from platform_core.routers.erp_pull_proxy import router as erp_pull_proxy_router

logger = logging.getLogger(__name__)
_STATIC_DIR = pathlib.Path(__file__).parent / "static"
_REG_CHECK_INTERVAL = 24 * 3600  # 24時間ごと


async def _regulatory_scheduler() -> None:
    """バックグラウンド: 24時間ごとに規制動向チェックを実行する。"""
    from platform_core.db.session import AsyncSessionLocal
    from platform_core.routers.regulatory import _check_egov, _check_bis

    await asyncio.sleep(60)  # 起動直後は少し待つ
    while True:
        try:
            async with AsyncSessionLocal() as session:
                egov = await _check_egov(session)
                bis = await _check_bis(session)
                await session.commit()
                logger.info("Scheduled RegMonitor: egov=%d bis=%d", egov, bis)
        except Exception as exc:
            logger.warning("Scheduled RegMonitor failed: %s", exc)
        await asyncio.sleep(_REG_CHECK_INTERVAL)


async def _license_alert_scheduler() -> None:
    """バックグラウンド: 24時間ごとに期限切れ近い輸出許可証をチェックしアラートを生成する。"""
    from datetime import timezone as tz
    from platform_core.db.session import AsyncSessionLocal
    from platform_core.models.export_license import ExportLicenseApplication
    from platform_core.models.regulatory_change import RegulatoryChange
    from sqlalchemy import select

    await asyncio.sleep(90)  # 規制スケジューラーより少し後に起動
    while True:
        try:
            async with AsyncSessionLocal() as session:
                now = __import__("datetime").datetime.now(tz=tz.utc)
                result = await session.execute(
                    select(ExportLicenseApplication).where(
                        ExportLicenseApplication.status == "approved",
                        ExportLicenseApplication.expires_at.isnot(None),
                        ExportLicenseApplication.alert_sent == False,
                    )
                )
                apps = result.scalars().all()
                alerted = 0
                for a in apps:
                    exp = a.expires_at if a.expires_at.tzinfo else a.expires_at.replace(tzinfo=tz.utc)
                    days = (exp - now).days
                    if days <= 90:
                        severity = "danger" if days <= 30 else "warn"
                        label = a.application_number or str(a.id)[:8]
                        title = f"輸出許可証 期限アラート: {label}"
                        detail = (
                            f"許可証 {label}（{a.license_type} / {a.destination_country or '仕向地未設定'}）の"
                            f"有効期限まで残り {days} 日です（{exp.strftime('%Y-%m-%d')}）。"
                            f" 更新申請または出荷完了の確認を行ってください。"
                        )
                        session.add(RegulatoryChange(
                            source="license_alert",
                            title=title,
                            detail=detail,
                            severity=severity,
                        ))
                        a.alert_sent = True
                        alerted += 1
                await session.commit()
                if alerted:
                    logger.info("LicenseAlert: %d alerts generated", alerted)
        except Exception as exc:
            logger.warning("LicenseAlert scheduler failed: %s", exc)
        await asyncio.sleep(_REG_CHECK_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 全モジュールを並行起動 (fire-and-forget)
    start_all_modules()
    # 規制動向スケジューラー起動
    _sched_task = asyncio.create_task(_regulatory_scheduler())
    # 輸出許可証期限アラートスケジューラー起動
    _alert_task = asyncio.create_task(_license_alert_scheduler())
    yield
    # 終了時: スケジューラー・モジュールサブプロセスを停止
    _sched_task.cancel()
    _alert_task.cancel()
    stop_all_modules()


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Trade Management - Platform Core",
        version="0.1.0",
        description="共通プラットフォーム基盤 (認証・テナント・共有データ・モジュールレジストリ)",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.platform_env == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuditMiddleware, module_key="platform-core")

    # 静的ファイル (CSS / JS)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ルーター登録
    app.include_router(ui_router)
    app.include_router(proxy_router)
    app.include_router(auth_router)
    app.include_router(admin_router, prefix="/admin")
    app.include_router(projects_router, prefix="/api/projects", tags=["projects"])
    app.include_router(internal_router)
    app.include_router(agent_router)
    app.include_router(metrics_router)
    app.include_router(faiss_search_router)
    app.include_router(ontology_router)
    app.include_router(regulatory_router)
    app.include_router(counterparty_router)
    app.include_router(supply_chain_router)
    app.include_router(supplier_attestation_router)
    app.include_router(supplier_portal_router)
    app.include_router(export_license_router)
    app.include_router(item_version_router)
    app.include_router(compliance_lookup_router)
    app.include_router(transaction_review_router)
    app.include_router(fta_router)
    app.include_router(organizations_router)
    app.include_router(erp_pull_proxy_router)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/ui")

    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok", "env": settings.platform_env}

    return app


app = create_app()
