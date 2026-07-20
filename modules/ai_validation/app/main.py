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

from platform_core.module_sdk import AuditMiddleware, ModuleInfo, build_lifespan, health_router

from app.routers.decision import router as decision_router
from app.routers.ui import router as ui_router
from app.routers.integration_export_control import router as integration_router
from app.routers.admin import router as admin_router
from app.routers.api_transactions import router as api_transactions_router
from app.routers.service_control import router as service_control_router
from app.routers.icp_diagnosis import router as icp_diagnosis_router
from app.db.session import engine


async def _ensure_columns() -> None:
    """
    起動時スキーマ管理（Alembic 非適用環境向け）。
    Layer A/B 静的インデックス移行に伴うテーブル再作成と新規カラム追加を行う。
    """
    from sqlalchemy import inspect as sa_inspect, text

    def _has_column(insp, table: str, col: str) -> bool:
        try:
            return any(c["name"] == col for c in insp.get_columns(table))
        except Exception:
            return False

    def _col_is_nullable(insp, table: str, col: str) -> bool:
        try:
            for c in insp.get_columns(table):
                if c["name"] == col:
                    return c.get("nullable", True)
        except Exception:
            pass
        return True

    with engine.connect() as conn:
        insp = sa_inspect(engine)

        # ── matrix_matches: matrix_rule_id を nullable 化（テーブル再作成）──
        if insp.has_table("matrix_matches") and not _col_is_nullable(insp, "matrix_matches", "matrix_rule_id"):
            conn.execute(text("DROP TABLE IF EXISTS matrix_matches_new"))
            conn.execute(text("""
                CREATE TABLE matrix_matches_new (
                    id                   INTEGER PRIMARY KEY,
                    ai_run_id            INTEGER NOT NULL REFERENCES ai_runs(id) ON DELETE CASCADE,
                    matrix_rule_id       INTEGER REFERENCES matrix_rules(id) ON DELETE SET NULL,
                    usage_requirement_id INTEGER REFERENCES usage_requirements(id) ON DELETE CASCADE,
                    layer_a_faiss_id     INTEGER,
                    layer_a_item_no      VARCHAR(64),
                    layer_a_source_type  VARCHAR(32),
                    match_type           VARCHAR(32) NOT NULL,
                    match_score          FLOAT NOT NULL,
                    decision             VARCHAR(16) NOT NULL DEFAULT 'hit',
                    evidence_json        TEXT,
                    created_at           DATETIME NOT NULL,
                    updated_at           DATETIME NOT NULL
                )
            """))
            conn.execute(text("""
                INSERT INTO matrix_matches_new
                    (id, ai_run_id, matrix_rule_id, usage_requirement_id,
                     match_type, match_score, decision, evidence_json,
                     created_at, updated_at)
                SELECT id, ai_run_id, matrix_rule_id, usage_requirement_id,
                       match_type, match_score,
                       COALESCE(decision, 'hit'),
                       evidence_json,
                       COALESCE(created_at, datetime('now')),
                       COALESCE(updated_at, datetime('now'))
                FROM matrix_matches
            """))
            conn.execute(text("DROP TABLE matrix_matches"))
            conn.execute(text("ALTER TABLE matrix_matches_new RENAME TO matrix_matches"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_matrix_matches_ai_run_id ON matrix_matches(ai_run_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_matrix_matches_run_rule  ON matrix_matches(ai_run_id, matrix_rule_id)"))
            # inspector を更新
            insp = sa_inspect(engine)

        # ── matrix_matches: Layer A 識別子カラム追加 ──────────────────────
        insp = sa_inspect(engine)  # テーブル再作成後に必ず再取得
        for col_name, col_type in [
            ("layer_a_faiss_id",    "INTEGER"),
            ("layer_a_item_no",     "VARCHAR(64)"),
            ("layer_a_source_type", "VARCHAR(32)"),
        ]:
            if insp.has_table("matrix_matches") and not _has_column(insp, "matrix_matches", col_name):
                try:
                    conn.execute(text(f"ALTER TABLE matrix_matches ADD COLUMN {col_name} {col_type}"))
                except Exception:
                    pass  # 既に存在する場合は無視

        # ── patent_retrievals: patent_id を nullable 化（テーブル再作成）──
        if insp.has_table("patent_retrievals") and not _col_is_nullable(insp, "patent_retrievals", "patent_id"):
            conn.execute(text("DROP TABLE IF EXISTS patent_retrievals_new"))
            conn.execute(text("""
                CREATE TABLE patent_retrievals_new (
                    id                   INTEGER PRIMARY KEY,
                    ai_run_id            INTEGER NOT NULL REFERENCES ai_runs(id) ON DELETE CASCADE,
                    usage_requirement_id INTEGER NOT NULL REFERENCES usage_requirements(id) ON DELETE CASCADE,
                    patent_id            INTEGER REFERENCES patents(id) ON DELETE SET NULL,
                    publication_number   VARCHAR(64),
                    layer_b_faiss_id     INTEGER,
                    score                FLOAT NOT NULL,
                    why                  TEXT,
                    created_at           DATETIME NOT NULL,
                    updated_at           DATETIME NOT NULL
                )
            """))
            conn.execute(text("""
                INSERT INTO patent_retrievals_new
                    (id, ai_run_id, usage_requirement_id, patent_id,
                     score, why, created_at, updated_at)
                SELECT id, ai_run_id, usage_requirement_id, patent_id,
                       score, why,
                       COALESCE(created_at, datetime('now')),
                       COALESCE(updated_at, datetime('now'))
                FROM patent_retrievals
            """))
            conn.execute(text("DROP TABLE patent_retrievals"))
            conn.execute(text("ALTER TABLE patent_retrievals_new RENAME TO patent_retrievals"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_patent_retrievals_ai_run_id  ON patent_retrievals(ai_run_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_patent_retrievals_run_usage  ON patent_retrievals(ai_run_id, usage_requirement_id)"))
            insp = sa_inspect(engine)

        # ── patent_retrievals: Layer B 識別子カラム追加 ────────────────────
        insp = sa_inspect(engine)  # テーブル再作成後に必ず再取得
        for col_name, col_type in [
            ("publication_number", "VARCHAR(64)"),
            ("layer_b_faiss_id",   "INTEGER"),
        ]:
            if insp.has_table("patent_retrievals") and not _has_column(insp, "patent_retrievals", col_name):
                try:
                    conn.execute(text(f"ALTER TABLE patent_retrievals ADD COLUMN {col_name} {col_type}"))
                except Exception:
                    pass  # 既に存在する場合は無視

        # ── transactions: 既存追加カラム + 輸出審査記録（法的要件）────────
        if insp.has_table("transactions"):
            existing_tx = {c["name"] for c in insp.get_columns("transactions")}
            for col_name, col_type in [
                # 証跡連鎖
                ("source_module",         "VARCHAR(32)"),
                ("parent_transaction_id", "INTEGER"),
                ("rnd_case_id",           "VARCHAR(64)"),
                # 輸出審査記録の法的要件（外為法 7年保存・CISTEC様式準拠）
                ("evaluator_name",        "VARCHAR(128)"),   # 判定者氏名
                ("evaluator_title",       "VARCHAR(128)"),   # 判定者役職
                ("judgment_no",           "VARCHAR(64)"),    # 判定書番号（社内管理番号）
                ("retention_until",       "DATE"),           # 保存期限（判定日 + 7年）
                ("destination_country",   "VARCHAR(8)"),     # 仕向国 ISO alpha-2
                ("end_user_name",         "VARCHAR(255)"),   # 最終需要者名
                ("end_use_description",   "TEXT"),           # 最終用途
                ("fdpr_judgment_json",    "TEXT"),           # FDPR 判定結果 JSON
                # 多拠点管理
                ("org_id",               "VARCHAR(36)"),    # 担当拠点 UUID
                ("formal_submitted_at",  "DATETIME"),       # 正式審査提出日時
                # サプライチェーン連携
                ("supply_chain_node_id", "VARCHAR(36)"),    # plat_supply_chain_node UUID
                ("de_minimis_result",    "TEXT"),           # De Minimis 計算スナップショット JSON
                # リスク分岐型承認フロー（Phase redesign）
                ("approval_tier",        "INTEGER"),        # 1=自動承認 2=標準 3=輸出許可確認
                ("required_steps",       "TEXT"),           # JSON: ["screening","ai_run","catchall"] etc.
                ("tier_reason",          "TEXT"),           # ティア判定理由
                ("tier_determined_at",   "DATETIME"),       # ティア確定日時
                ("linked_product_code",  "VARCHAR(64)"),    # 品目管理の product.code
                ("linked_product_eccn",  "VARCHAR(32)"),    # 品目管理から取得したECCN
                ("is_new_product_entry", "INTEGER"),        # 1=品目同時登録モード
                # みなし輸出連携
                ("deemed_export_personnel_id", "VARCHAR(36)"),  # rnd_assessment personnel_id
                # ERP 連携フィールド
                ("end_user_country",     "VARCHAR(8)"),     # 最終需要者所在国 ISO alpha-2
                ("erp_case_no",          "VARCHAR(64)"),    # ERP 側受注番号（case_no は内部管理番号）
                ("total_value_usd",      "REAL"),           # 取引総額 (USD)
                ("unit_price_usd",       "REAL"),           # 単価 (USD)
                ("quantity",             "REAL"),           # 数量
                ("hs_code",              "VARCHAR(20)"),    # HSコード
                ("incoterms",            "VARCHAR(10)"),    # インコタームズ
                # 輸出許可申請連携
                ("linked_license_id",    "VARCHAR(36)"),    # export_license application UUID
                ("linked_license_status","VARCHAR(32)"),    # draft/submitted/approved/rejected
            ]:
                if col_name not in existing_tx:
                    conn.execute(text(f"ALTER TABLE transactions ADD COLUMN {col_name} {col_type}"))

        # ── service_transactions テーブル作成（役務取引管理）────────────────────
        if not insp.has_table("service_transactions"):
            conn.execute(text("""
                CREATE TABLE service_transactions (
                    id                      INTEGER PRIMARY KEY,
                    case_no                 VARCHAR(64) UNIQUE,
                    title                   VARCHAR(255),
                    service_type            VARCHAR(32),
                    provider_name           VARCHAR(255),
                    provider_dept           VARCHAR(255),
                    recipient_name          VARCHAR(255),
                    recipient_organization  VARCHAR(255),
                    recipient_country       VARCHAR(8),
                    recipient_nationality   VARCHAR(8),
                    technology_description  TEXT,
                    eccn                    VARCHAR(32),
                    fefta_category          VARCHAR(64),
                    contract_start          DATETIME,
                    contract_end            DATETIME,
                    contract_value_jpy      FLOAT,
                    status                  VARCHAR(32) NOT NULL DEFAULT 'draft',
                    screening_status        VARCHAR(30),
                    screening_result_id     VARCHAR(36),
                    license_required        INTEGER,
                    license_no              VARCHAR(64),
                    evaluator_note          TEXT,
                    is_deemed_export        INTEGER NOT NULL DEFAULT 0,
                    evaluator_name          VARCHAR(128),
                    evaluator_title         VARCHAR(128),
                    judgment_no             VARCHAR(64),
                    retention_until         DATETIME,
                    created_at              DATETIME NOT NULL DEFAULT (datetime('now')),
                    updated_at              DATETIME NOT NULL DEFAULT (datetime('now')),
                    submitted_at            DATETIME
                )
            """))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_service_tx_case_no "
                "ON service_transactions(case_no)"
            ))

        # ── system_settings テーブル作成（管理者設定 KV ストア）──────────────
        if not insp.has_table("system_settings"):
            conn.execute(text("""
                CREATE TABLE system_settings (
                    key   VARCHAR(64) PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at DATETIME NOT NULL DEFAULT (datetime('now'))
                )
            """))
            # デフォルト値を投入
            conn.execute(text("""
                INSERT INTO system_settings (key, value) VALUES
                  ('matrix_match_threshold', '0.82'),
                  ('matrix_match_top_k',     '10')
            """))

        conn.commit()


async def _preload_faiss_model() -> None:
    """
    e5-large モデルと Layer A/B FAISS インデックスを起動時にプリロードする。
    完了まで 20〜30 秒かかるため app.state.faiss_ready フラグで管理する。
    """
    import logging
    from app.services.faiss_e5_service import preload
    logger = logging.getLogger(__name__)
    app.state.faiss_ready = False
    try:
        logger.info("FAISS e5-large preload starting (may take 20-30s)...")
        preload()
        app.state.faiss_ready = True
        logger.info("FAISS e5-large preload complete.")
    except Exception as exc:
        logger.error("FAISS preload failed: %s", exc)
        app.state.faiss_ready = False


MODULE = ModuleInfo(
    key="ai_validation",
    name="AI取引審査",
    base_url=os.environ.get("MODULE_AI_VALIDATION_URL", "http://localhost:8011"),
    description="外為法に基づく輸出管理の該非判定を AI で支援する",
    capabilities=["export_control", "matrix_run", "transaction_create"],
    data_contracts={
        "input":  ["Transaction"],
        "output": ["AiRun", "MatrixMatch"],
    },
)


_ERP_BASE           = os.environ.get("ERP_API_URL",       "http://localhost:8888")
_ERP_ADMIN_EMAIL    = os.environ.get("ERP_ADMIN_EMAIL",    "admin@example.com")
_ERP_ADMIN_PASSWORD = os.environ.get("ERP_ADMIN_PASSWORD", "admin1234")
_SELF_URL           = os.environ.get("MODULE_AI_VALIDATION_URL", "http://localhost:8011")
_DEMINIMIS_POLL_SEC = int(os.environ.get("DEMINIMIS_POLL_INTERVAL_SEC", "600"))


async def _erp_deminimis_poller() -> None:
    """ERP /gts/deminimis BREACH を定期取得し、未通知案件を ai_validation へ登録する。"""
    import asyncio
    import httpx

    await asyncio.sleep(30)  # 起動直後の一斉接続を避ける
    while True:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # JWT 取得
                r = await client.post(
                    f"{_ERP_BASE}/auth/token",
                    data={"username": _ERP_ADMIN_EMAIL, "password": _ERP_ADMIN_PASSWORD},
                )
                if r.status_code != 200:
                    raise RuntimeError(f"ERP auth failed: {r.status_code}")
                token = r.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                # 未通知 BREACH レコード取得
                r2 = await client.get(
                    f"{_ERP_BASE}/gts/deminimis",
                    params={"alert_level": "BREACH", "ai_tm_notified": "false", "limit": "100"},
                    headers=headers,
                )
                if r2.status_code != 200:
                    raise RuntimeError(f"ERP deminimis fetch failed: {r2.status_code}")

                records = r2.json()
                for rec in records:
                    # AI_TM 自身へ MATERIAL_ORIGIN_CHANGE イベントを投稿
                    payload = {
                        "event_type": "MATERIAL_ORIGIN_CHANGE",
                        "material_code": rec.get("fg_material_code"),
                        "from_country": None,
                        "to_country": None,
                        "max_us_content_pct": rec.get("us_content_pct", 0.0),
                        "exceeds_deminimis_threshold": True,
                        "affected_products": [rec.get("fg_material_code", "")],
                        "breach_lot_count": 1,
                        "breach_lots": rec.get("us_components", []),
                    }
                    r3 = await client.post(f"{_SELF_URL}/api/transactions/events", json=payload)
                    case_ref = r3.json().get("case_ref") if r3.status_code == 200 else None

                    # ERP 側 ai_tm_notified フラグを更新
                    await client.patch(
                        f"{_ERP_BASE}/gts/deminimis/{rec['id']}/mark-notified",
                        json={"ai_tm_case_ref": case_ref},
                        headers=headers,
                    )
        except Exception as exc:
            import logging
            logging.getLogger("ai_validation.poller").warning("ERP deminimis poll error: %s", exc)

        await asyncio.sleep(_DEMINIMIS_POLL_SEC)


async def _on_startup() -> None:
    import asyncio
    await _ensure_columns()
    await _preload_faiss_model()
    asyncio.ensure_future(_erp_deminimis_poller())


app = FastAPI(
    title="AI Validation (Trade Screening)",
    version="0.1.0",
    lifespan=build_lifespan(MODULE, on_startup=_on_startup),
)

app.add_middleware(AuditMiddleware, module_key="ai_validation")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
app.state.templates = templates
app.state.faiss_ready = False  # preload 完了前のデフォルト

app.include_router(health_router)
app.include_router(ui_router)
app.include_router(decision_router)
app.include_router(integration_router)
app.include_router(admin_router)
app.include_router(api_transactions_router)
app.include_router(service_control_router)
app.include_router(icp_diagnosis_router)
