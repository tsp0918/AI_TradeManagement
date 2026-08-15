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
from platform_core.routers.compliance_assess import router as compliance_assess_router
from platform_core.routers.transaction_review import router as transaction_review_router
from platform_core.routers.fta import router as fta_router
from platform_core.routers.organizations import router as organizations_router
from platform_core.routers.erp_pull_proxy import router as erp_pull_proxy_router
from platform_core.routers.ux_events import router as ux_events_router
from platform_core.routers.minerals import router as minerals_router
from platform_core.routers.hantei_records import router as hantei_records_router
from platform_core.routers.webhook_mgmt import router as webhook_mgmt_router
from platform_core.routers.party import router as party_router
from platform_core.routers.monitoring import router as monitoring_router

logger = logging.getLogger(__name__)
_STATIC_DIR = pathlib.Path(__file__).parent / "static"
_REG_CHECK_INTERVAL = 24 * 3600  # 24時間ごと
_WEBHOOK_POLL_INTERVAL = 30  # Webhook リトライワーカーのポーリング間隔（秒）


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


async def _webhook_retry_worker() -> None:
    """バックグラウンド: 30秒ごとにリトライ対象 Webhook 配信を処理する。"""
    from platform_core.db.session import AsyncSessionLocal
    from platform_core.services.webhook import WebhookDispatcher

    await asyncio.sleep(60)  # 起動直後は他のスケジューラーより後に起動
    while True:
        try:
            async with AsyncSessionLocal() as db:
                count = await WebhookDispatcher.retry_pending(db)
                if count:
                    logger.info("WebhookRetryWorker: processed %d deliveries", count)
        except Exception as exc:
            logger.warning("WebhookRetryWorker failed: %s", exc)
        await asyncio.sleep(_WEBHOOK_POLL_INTERVAL)


_MONITORING_INTERVAL = 24 * 3600   # 24時間ごとに継続監視を実行
_MONITORING_SCREENING_URL_ENV = "MODULE_SCREENING_URL"


async def _monitoring_worker() -> None:
    """バックグラウンド: 24時間ごとにアクティブな監視購読を処理する。

    処理フロー:
    1. monitor_until < today の購読を自動で非アクティブ化
    2. subject_type='party' / trigger_type='sanction_change' の購読に対して
       スクリーニングモジュールへ再スクリーニングを依頼
    3. sanction_status が変化した場合は Webhook で通知（IF-16/IF-23）
    """
    import os
    from datetime import date, datetime, timezone
    import httpx
    from sqlalchemy import select, update
    from platform_core.db.session import AsyncSessionLocal
    from platform_core.models.monitoring import MonitoringSubscription
    from platform_core.models.party import Party
    from platform_core.services.webhook import WebhookDispatcher

    screening_url = os.environ.get(_MONITORING_SCREENING_URL_ENV, "http://localhost:8005")

    await asyncio.sleep(180)  # 他のワーカーより後に起動
    while True:
        try:
            async with AsyncSessionLocal() as db:
                today = date.today()

                # Step 1: 期限切れ購読を非アクティブ化
                expired_result = await db.execute(
                    select(MonitoringSubscription).where(
                        MonitoringSubscription.is_active.is_(True),
                        MonitoringSubscription.monitor_until < today,
                    )
                )
                expired_subs = expired_result.scalars().all()
                if expired_subs:
                    for s in expired_subs:
                        s.is_active = False
                    await db.commit()
                    logger.info("MonitoringWorker: deactivated %d expired subscriptions", len(expired_subs))

            # Step 2: party / sanction_change 購読のスクリーニング再実行
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(MonitoringSubscription).where(
                        MonitoringSubscription.is_active.is_(True),
                        MonitoringSubscription.subject_type == "party",
                        MonitoringSubscription.trigger_type == "sanction_change",
                    )
                )
                active_subs = result.scalars().all()
                hits = 0
                for sub in active_subs:
                    party = await db.get(Party, sub.subject_id)
                    if not party:
                        continue
                    try:
                        async with httpx.AsyncClient(timeout=10.0) as client:
                            resp = await client.post(
                                f"{screening_url}/api/screen",
                                json={"name": party.legal_name, "country": party.country_code or ""},
                            )
                        if resp.status_code != 200:
                            continue
                        data = resp.json()
                        new_status = data.get("result_status", "clear")
                    except Exception as exc:
                        logger.debug("MonitoringWorker: screening call failed for %s: %s", party.id, exc)
                        continue

                    prev_status = party.sanction_status or "clear"
                    if new_status != prev_status:
                        party.sanction_status = new_status
                        party.last_screened_at = datetime.now(timezone.utc)
                        await db.flush()
                        # Webhook 通知（IF-16/IF-23）
                        try:
                            await WebhookDispatcher.enqueue(
                                db,
                                tenant_id=party.tenant_id,
                                event_type="party.sanction_status.changed",
                                payload={
                                    "party_id": str(party.id),
                                    "legal_name": party.legal_name,
                                    "prev_status": prev_status,
                                    "new_status": new_status,
                                    "checked_at": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                        except Exception as exc:
                            logger.warning("MonitoringWorker: webhook enqueue failed: %s", exc)
                        hits += 1

                if hits:
                    await db.commit()
                    logger.info("MonitoringWorker: %d sanction status changes detected", hits)
                else:
                    logger.debug("MonitoringWorker: no sanction status changes (%d checked)", len(active_subs))

        except Exception as exc:
            logger.warning("MonitoringWorker failed: %s", exc)
        await asyncio.sleep(_MONITORING_INTERVAL)


_ALLOC_EXPIRE_INTERVAL = 3600  # 1時間ごとに期限切れ引当を自動解放


async def _license_allocation_expiry_worker() -> None:
    """バックグラウンド: 期限切れになった仮引当（LicenseAllocation）を自動的に解放する。

    valid_until < today の allocated レコードを released に更新する。
    """
    from datetime import date, datetime, timezone
    from sqlalchemy import select, update
    from platform_core.db.session import AsyncSessionLocal
    from platform_core.models.license_quota import LicenseAllocation

    await asyncio.sleep(120)
    while True:
        try:
            async with AsyncSessionLocal() as db:
                today = date.today()
                result = await db.execute(
                    select(LicenseAllocation)
                    .where(LicenseAllocation.status == "allocated")
                    .where(LicenseAllocation.valid_until < today)
                )
                expired = result.scalars().all()
                if expired:
                    now = datetime.now(timezone.utc)
                    for a in expired:
                        a.status = "expired"
                        a.released_at = now
                    await db.commit()
                    logger.info("LicenseAllocationExpiryWorker: expired %d allocations", len(expired))
        except Exception as exc:
            logger.warning("LicenseAllocationExpiryWorker failed: %s", exc)
        await asyncio.sleep(_ALLOC_EXPIRE_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 全モジュールを並行起動 (fire-and-forget)
    start_all_modules()
    # FAISS 全レイヤーをバックグラウンドでプリロード（起動後の初回レイテンシ解消）
    def _bg_preload():
        try:
            from platform_core.services.faiss_e5_service import preload
            preload(layers=frozenset({"a", "b", "c", "d", "e", "f"}))
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("FAISS preload failed: %s", exc)
    asyncio.get_event_loop().run_in_executor(None, _bg_preload)
    # 規制動向スケジューラー起動
    _sched_task = asyncio.create_task(_regulatory_scheduler())
    # 輸出許可証期限アラートスケジューラー起動
    _alert_task = asyncio.create_task(_license_alert_scheduler())
    # Webhook リトライワーカー起動
    _webhook_task = asyncio.create_task(_webhook_retry_worker())
    # ライセンス引当期限切れ自動解放ワーカー起動
    _alloc_expiry_task = asyncio.create_task(_license_allocation_expiry_worker())
    # 継続モニタリングワーカー起動
    _monitoring_task = asyncio.create_task(_monitoring_worker())
    yield
    # 終了時: スケジューラー・モジュールサブプロセスを停止
    _sched_task.cancel()
    _alert_task.cancel()
    _webhook_task.cancel()
    _alloc_expiry_task.cancel()
    _monitoring_task.cancel()
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
    app.include_router(compliance_assess_router)
    app.include_router(transaction_review_router)
    app.include_router(fta_router)
    app.include_router(organizations_router)
    app.include_router(erp_pull_proxy_router)
    app.include_router(ux_events_router)
    app.include_router(minerals_router)
    app.include_router(hantei_records_router)
    app.include_router(webhook_mgmt_router)
    app.include_router(party_router)
    app.include_router(monitoring_router)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/ui")

    @app.get("/health", tags=["system"])
    async def health():
        try:
            from platform_core.services.faiss_e5_service import (
                is_ready, layer_b_available, layer_c_available,
                layer_d_available, layer_e_available, layer_f_available,
            )
            faiss_status = {
                "a": is_ready(), "b": layer_b_available(),
                "c": layer_c_available(), "d": layer_d_available(),
                "e": layer_e_available(), "f": layer_f_available(),
            }
        except Exception:
            faiss_status = {}
        return {
            "status": "ok",
            "env": settings.platform_env,
            "faiss_layers": faiss_status,
        }

    return app


app = create_app()
