#!/usr/bin/env python3
"""E2E テスト環境シードスクリプト。

platform_db_e2e（PostgreSQL）と app_e2e.db（SQLite）に
再現性のある固定テストデータを投入する。

使い方:
    cd /Users/takehirosato/Desktop/AI_TradeManagement
    python scripts/seed_e2e.py          # 投入
    python scripts/seed_e2e.py --reset  # リセットして再投入
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# プロジェクトルートをパスに追加
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "platform-core"))
sys.path.insert(0, str(ROOT / "modules" / "ai_validation"))

# ── 固定 UUID（E2E 用。変更しないこと） ────────────────────────────────
# テナントは本番と同一 UUID を使用（CRM/ERP が同じ ID でリクエストするため）
TENANT_DEFAULT = "e07bab5b-9bdc-431e-ab39-f3ee75560903"
TENANT_US      = "a7dff84b-2376-4e09-a3bc-3cf4e0cc623a"

# 品目
ITEM_EAR99   = "e2e00001-0000-4000-a000-000000000001"
ITEM_3A001   = "e2e00001-0000-4000-a000-000000000002"
ITEM_3E001   = "e2e00001-0000-4000-a000-000000000003"
ITEM_CHEM    = "e2e00001-0000-4000-a000-000000000004"
ITEM_MECH    = "e2e00001-0000-4000-a000-000000000005"

# 取引先
PARTY_CLEAN_US = "e2e00002-0000-4000-a000-000000000001"
PARTY_CLEAN_JP = "e2e00002-0000-4000-a000-000000000002"
PARTY_CLEAN_DE = "e2e00002-0000-4000-a000-000000000003"
PARTY_RISK_CN  = "e2e00002-0000-4000-a000-000000000004"
PARTY_HIT_IR   = "e2e00002-0000-4000-a000-000000000005"

# 輸出許可申請
LICENSE_DRAFT     = "e2e00003-0000-4000-a000-000000000001"
LICENSE_SUBMITTED = "e2e00003-0000-4000-a000-000000000002"
LICENSE_APPROVED  = "e2e00003-0000-4000-a000-000000000003"

NOW = datetime.now(timezone.utc)


# ════════════════════════════════════════════════════════
#  PostgreSQL シード
# ════════════════════════════════════════════════════════

def seed_postgres(reset: bool) -> None:
    import psycopg2

    pg_url = os.environ.get(
        "PLATFORM_DATABASE_URL",
        "postgresql://platform_user:platform_pass@localhost:5432/platform_db_e2e",
    ).replace("postgresql+asyncpg://", "postgresql://").replace("+psycopg2", "")

    conn = psycopg2.connect(pg_url)
    cur = conn.cursor()

    if reset:
        print("  [PG] 既存 E2E データをリセット中...")
        cur.execute("DELETE FROM plat_export_license_application WHERE application_number LIKE 'E2E-%'")
        cur.execute("DELETE FROM plat_party WHERE id IN %s",
                    ((PARTY_CLEAN_US, PARTY_CLEAN_JP, PARTY_CLEAN_DE, PARTY_RISK_CN, PARTY_HIT_IR),))
        cur.execute("DELETE FROM plat_item WHERE id IN %s",
                    ((ITEM_EAR99, ITEM_3A001, ITEM_3E001, ITEM_CHEM, ITEM_MECH),))
        cur.execute("DELETE FROM scr_watchlist WHERE source_id LIKE 'E2E-%'")
        cur.execute("DELETE FROM plat_tenant WHERE id IN %s", ((TENANT_DEFAULT, TENANT_US),))
        conn.commit()

    # ── テナント ──────────────────────────────────────────────
    print("  [PG] テナント投入...")
    cur.execute("""
        INSERT INTO plat_tenant (id, name, slug, plan, is_active, country_code, export_regime,
                                  org_type, flag_emoji, created_at, updated_at)
        VALUES
          (%s, 'default (E2E)', 'default', 'enterprise', true, 'JP', 'FEFTA', 'manufacturer', '🇯🇵', %s, %s),
          (%s, 'AI Trade US Inc. (E2E)', 'us-sub', 'enterprise', true, 'US', 'EAR', 'distributor', '🇺🇸', %s, %s)
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, updated_at = EXCLUDED.updated_at
    """, (TENANT_DEFAULT, NOW, NOW, TENANT_US, NOW, NOW))

    # ── 品目 ──────────────────────────────────────────────────
    print("  [PG] 品目 5 件投入...")
    items = [
        (ITEM_EAR99, "E2E-ITEM-001", "E2Eテスト品目 汎用センサー",      "8543.70", "EAR99",  "EAR99"),
        (ITEM_3A001, "E2E-ITEM-002", "E2Eテスト品目 GaN パワーデバイス", "8541.10", "3A001.a","CONTROLLED"),
        (ITEM_3E001, "E2E-ITEM-003", "E2Eテスト品目 半導体製造技術",     None,       "3E001",  "CONTROLLED"),
        (ITEM_CHEM,  "E2E-ITEM-004", "E2Eテスト品目 工業用化学品",       "2806.10", "EAR99",  "EAR99"),
        (ITEM_MECH,  "E2E-ITEM-005", "E2Eテスト品目 精密機械部品",       "8483.10", "EAR99",  "EAR99"),
    ]
    for item in items:
        cur.execute("""
            INSERT INTO plat_item (id, item_code, name, hs_code, eccn,
                                    export_control_status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, updated_at = EXCLUDED.updated_at
        """, (*item, NOW, NOW))

    # ── 取引先 ────────────────────────────────────────────────
    print("  [PG] 取引先 5 件投入...")
    parties = [
        (PARTY_CLEAN_US, TENANT_DEFAULT, "E2E Test Corp USA",      "US", "end_user",   0.100, "clear"),
        (PARTY_CLEAN_JP, TENANT_DEFAULT, "E2E テスト商事（国内）", "JP", "distributor",0.050, "clear"),
        (PARTY_CLEAN_DE, TENANT_DEFAULT, "E2E GmbH Germany",       "DE", "end_user",   0.120, "clear"),
        (PARTY_RISK_CN,  TENANT_DEFAULT, "E2E High-Risk Tech CN",  "CN", "end_user",   0.750, "review_required"),
        (PARTY_HIT_IR,   TENANT_DEFAULT, "E2E Sanction Test IR",   "IR", "end_user",   0.990, "sanctioned"),
    ]
    for p in parties:
        cur.execute("""
            INSERT INTO plat_party (id, tenant_id, legal_name, country_code, party_type,
                                     risk_score, sanction_status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET legal_name = EXCLUDED.legal_name, updated_at = EXCLUDED.updated_at
        """, (*p, NOW, NOW))

    # ── スクリーニング用ウォッチリスト（E2E テスト用エントリ） ─────────
    print("  [PG] ウォッチリスト（E2E ヒットテスト用）3 件投入...")
    import uuid
    watchlist = [
        (str(uuid.uuid4()), "E2E-SDN", "E2E Sanction Test IR",          "IR",
         "E2E-001", "E2E テスト用制裁対象エンティティ", "high"),
        (str(uuid.uuid4()), "E2E-MEU", "E2E High-Risk Tech CN",          "CN",
         "E2E-002", "E2E テスト用軍事エンドユーザー", "high"),
        (str(uuid.uuid4()), "E2E-BIS", "E2E Denied Party Organization",  "KP",
         "E2E-003", "E2E テスト用 BIS 禁止令対象", "critical"),
    ]
    for w in watchlist:
        cur.execute("""
            INSERT INTO scr_watchlist (id, list_source, entity_name, country, source_id,
                                        reason, risk_level, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, true, %s, %s)
            ON CONFLICT (id) DO UPDATE SET entity_name = EXCLUDED.entity_name
        """, (*w, NOW, NOW))

    # ── 輸出許可申請 ─────────────────────────────────────────────
    print("  [PG] 輸出許可申請 3 件投入...")
    licenses = [
        (LICENSE_DRAFT, "E2E-LIC-2026-001", "general_bulk", "miti_form_1",
         "draft", "GaN パワーデバイス", "3A001.a", 50.0, "units", 250000.0,
         "US", "E2E Test Corp USA", "Santa Clara, CA 94025, USA",
         "研究開発用途", "E2E Test Corp USA",
         None, None, None),
        (LICENSE_SUBMITTED, "E2E-LIC-2026-002", "general_bulk", "miti_form_1",
         "submitted", "半導体製造技術移転", "3E001", 1.0, "lot", 5000000.0,
         "DE", "E2E GmbH Germany", "Munich, Bayern, Germany",
         "製造ライセンス供与", "E2E GmbH Germany",
         NOW, None, None),
        (LICENSE_APPROVED, "E2E-LIC-2026-003", "general_bulk", "miti_form_1",
         "approved", "汎用センサー（EAR99）", "EAR99", 200.0, "units", 80000.0,
         "US", "E2E Test Corp USA", "Santa Clara, CA 94025, USA",
         "OEM 組み込み用途", "E2E Test Corp USA",
         NOW, NOW, None),
    ]
    for lic in licenses:
        cur.execute("""
            INSERT INTO plat_export_license_application
              (id, application_number, license_type, form_type, status,
               item_description, eccn, quantity, quantity_unit, value_usd,
               destination_country, consignee_name, consignee_address,
               end_use_description, end_user_name,
               submitted_at, approved_at, expires_at, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
        """, (*lic, NOW, NOW))

    conn.commit()
    cur.close()
    conn.close()
    print("  [PG] 完了")


# ════════════════════════════════════════════════════════
#  SQLite シード（ai_validation app_e2e.db）
# ════════════════════════════════════════════════════════

def seed_sqlite(reset: bool) -> None:
    import importlib
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    db_path = ROOT / "modules" / "ai_validation" / "app_e2e.db"

    # スキーマを完全に作成（モデルと乖離している場合は DB を再作成）
    from app.db.base import Base
    import app.db.models.transaction  # noqa: F401 — モデル登録のため import

    def _make_engine():
        return create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )

    engine = _make_engine()
    Base.metadata.create_all(engine)

    # カラム不足チェック: モデルにあって DB にないカラムがあれば DB を再作成
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(engine)
    if insp.has_table("transactions"):
        db_cols = {c["name"] for c in insp.get_columns("transactions")}
        model_cols = {c.key for c in Base.metadata.tables["transactions"].columns}
        missing = model_cols - db_cols
        if missing:
            print(f"  [SQLite] カラム不足 {missing} を検出 → DB を再作成")
            engine.dispose()
            db_path.unlink(missing_ok=True)
            for f in (db_path.parent / (db_path.name + "-wal"),
                      db_path.parent / (db_path.name + "-shm")):
                f.unlink(missing_ok=True)
            engine = _make_engine()
            Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    db = Session()

    if reset:
        print("  [SQLite] 既存 E2E トランザクションをリセット中...")
        db.execute(text("DELETE FROM transactions WHERE case_no LIKE 'E2E-%'"))
        db.commit()

    # ── トランザクション 8 件 ─────────────────────────────────────
    print("  [SQLite] トランザクション 8 件投入...")

    txs = [
        # シナリオ 1: CRM → EAR99 → 承認済（正常系）
        dict(
            case_no="E2E-CRM-001", title="[E2E] CRM 正常系 EAR99 承認フロー",
            status="approved", source_module="crm",
            counterparty_name="E2E Test Corp USA",
            destination_country="US", end_user_name="E2E Test Corp USA",
            end_use_description="研究開発用途",
            linked_product_code="E2E-ITEM-001", linked_product_eccn="EAR99",
            total_value_usd=80000.0, quantity=200.0, unit_price_usd=400.0,
            org_id=TENANT_DEFAULT,
            agent_judgment_status="approved",
            approval_tier=1,
            linked_license_id=LICENSE_APPROVED,
            linked_license_status="approved",
        ),
        # シナリオ 2: CRM → 3A001 → 審査中（輸出許可申請必要）
        dict(
            case_no="E2E-CRM-002", title="[E2E] CRM 規制品 3A001 審査中",
            status="in_review", source_module="crm",
            counterparty_name="E2E Test Corp USA",
            destination_country="US", end_user_name="E2E Test Corp USA",
            end_use_description="通信基地局向け",
            linked_product_code="E2E-ITEM-002", linked_product_eccn="3A001.a",
            total_value_usd=250000.0, quantity=50.0, unit_price_usd=5000.0,
            org_id=TENANT_DEFAULT,
            agent_judgment_status="needs_review",
            approval_tier=2,
            linked_license_id=LICENSE_DRAFT,
            linked_license_status="draft",
        ),
        # シナリオ 3: ERP → EAR99 → 自動クリア済
        dict(
            case_no="E2E-ERP-001", title="[E2E] ERP 自動クリア EAR99",
            status="approved", source_module="erp",
            counterparty_name="E2E テスト商事（国内）",
            destination_country="JP", end_user_name="E2E テスト商事（国内）",
            end_use_description="国内販売",
            linked_product_code="E2E-ITEM-004", linked_product_eccn="EAR99",
            total_value_usd=32000.0, quantity=400.0, unit_price_usd=80.0,
            org_id=TENANT_DEFAULT,
            agent_judgment_status="approved",
            approval_tier=1,
            erp_case_no="ERP-E2E-0001",
        ),
        # シナリオ 4: ERP → 3E001 → 正式審査提出済
        dict(
            case_no="E2E-ERP-002", title="[E2E] ERP 規制品 3E001 正式審査提出",
            status="submitted", source_module="erp",
            counterparty_name="E2E GmbH Germany",
            destination_country="DE", end_user_name="E2E GmbH Germany",
            end_use_description="製造ライセンス供与",
            linked_product_code="E2E-ITEM-003", linked_product_eccn="3E001",
            total_value_usd=5000000.0, quantity=1.0, unit_price_usd=5000000.0,
            org_id=TENANT_DEFAULT,
            agent_judgment_status="needs_review",
            approval_tier=3,
            linked_license_id=LICENSE_SUBMITTED,
            linked_license_status="submitted",
            erp_case_no="ERP-E2E-0002",
        ),
        # シナリオ 5: CRM → スクリーニングヒット → 審査中
        dict(
            case_no="E2E-SCR-001", title="[E2E] スクリーニングヒット 制裁対象",
            status="in_review", source_module="crm",
            counterparty_name="E2E Sanction Test IR",
            destination_country="IR", end_user_name="E2E Sanction Test IR",
            end_use_description="不明",
            linked_product_code="E2E-ITEM-001", linked_product_eccn="EAR99",
            total_value_usd=15000.0, quantity=50.0, unit_price_usd=300.0,
            org_id=TENANT_DEFAULT,
            agent_judgment_status="blocked",
            screening_status="hit",
            approval_tier=3,
        ),
        # シナリオ 6: ERP → 高リスク取引先（中国）→ 要確認
        dict(
            case_no="E2E-SCR-002", title="[E2E] 高リスク取引先 中国向け 3A001",
            status="in_review", source_module="erp",
            counterparty_name="E2E High-Risk Tech CN",
            destination_country="CN", end_user_name="E2E High-Risk Tech CN",
            end_use_description="軍事用途の可能性あり",
            linked_product_code="E2E-ITEM-002", linked_product_eccn="3A001.a",
            total_value_usd=180000.0, quantity=30.0, unit_price_usd=6000.0,
            org_id=TENANT_DEFAULT,
            agent_judgment_status="needs_review",
            screening_status="review_required",
            approval_tier=3,
            erp_case_no="ERP-E2E-0003",
        ),
        # シナリオ 7: US子会社案件 → approved
        dict(
            case_no="E2E-US-001", title="[E2E] US子会社 EAR案件 正常承認",
            status="approved", source_module="crm",
            counterparty_name="E2E Test Corp USA",
            destination_country="CA", end_user_name="E2E Test Corp USA",
            end_use_description="カナダ向け再輸出",
            linked_product_code="E2E-ITEM-005", linked_product_eccn="EAR99",
            total_value_usd=45000.0, quantity=150.0, unit_price_usd=300.0,
            org_id=TENANT_US,
            agent_judgment_status="approved",
            approval_tier=1,
        ),
        # シナリオ 8: 新規未処理（E2E テスト投入の起点）
        dict(
            case_no="E2E-NEW-001", title="[E2E] 新規案件 未処理（テスト起点）",
            status="draft", source_module="crm",
            counterparty_name="E2E Test Corp USA",
            destination_country="US", end_user_name="E2E Test Corp USA",
            end_use_description="新製品評価サンプル",
            linked_product_code="E2E-ITEM-002", linked_product_eccn="3A001.a",
            total_value_usd=50000.0, quantity=10.0, unit_price_usd=5000.0,
            org_id=TENANT_DEFAULT,
        ),
    ]

    from app.db.models.transaction import Transaction
    for tx_data in txs:
        existing = db.query(Transaction).filter(Transaction.case_no == tx_data["case_no"]).first()
        if existing:
            for k, v in tx_data.items():
                setattr(existing, k, v)
            existing.updated_at = NOW
        else:
            db.add(Transaction(**tx_data))

    db.commit()
    db.close()
    print("  [SQLite] 完了")


# ════════════════════════════════════════════════════════
#  SQLite シード（ai_classification products_e2e.db）
# ════════════════════════════════════════════════════════

def seed_sqlite_classification(reset: bool) -> None:
    """ai_classification は PostgreSQL (platform_db_e2e) を使用する。"""
    import os
    from sqlalchemy import create_engine, text

    pg_url = os.environ.get(
        "CLASSIFICATION_DATABASE_URL",
        "postgresql+psycopg2://platform_user:platform_pass@localhost:5432/platform_db",
    )
    # E2E DB に切り替え
    import re
    e2e_url = re.sub(r'/([^/?#]+)(\?.*)?$', lambda m: f'/{m.group(1)}_e2e{m.group(2) or ""}', pg_url)
    engine = create_engine(e2e_url)

    conn = engine.connect()
    if reset:
        print("  [classification] 既存 E2E 品目をリセット中...")
        conn.execute(text("DELETE FROM products WHERE code LIKE 'E2E-%'"))
        conn.commit()

    products = [
        ("E2E-ITEM-001", "[E2E] 汎用センサー",      "E2E テスト用汎用センサーモジュール",         "8543.70", "EAR99"),
        ("E2E-ITEM-002", "[E2E] GaN パワーデバイス", "E2E テスト用 GaN 半導体デバイス（規制品）", "8541.10", "3A001.a"),
        ("E2E-ITEM-003", "[E2E] 半導体製造技術",     "E2E テスト用半導体製造技術移転ケース",       None,      "3E001"),
        ("E2E-ITEM-004", "[E2E] 工業用化学品",       "E2E テスト用工業化学品",                    "2806.10", "EAR99"),
        ("E2E-ITEM-005", "[E2E] 精密機械部品",       "E2E テスト用精密部品",                      "8483.10", "EAR99"),
    ]
    print("  [classification] 品目 5 件投入...")
    for code, name, desc, hs, eccn in products:
        conn.execute(text("""
            INSERT INTO products (code, name, description, hs_code, eccn,
              export_control_status, source, is_unconfirmed)
            VALUES (:code, :name, :desc, :hs, :eccn, 'pending', 'manual', false)
            ON CONFLICT (code) DO UPDATE SET
              name=EXCLUDED.name, description=EXCLUDED.description,
              hs_code=EXCLUDED.hs_code, eccn=EXCLUDED.eccn
        """), {"code": code, "name": name, "desc": desc, "hs": hs, "eccn": eccn})
    conn.commit()
    conn.close()
    engine.dispose()
    print("  [classification] 完了")


# ════════════════════════════════════════════════════════
#  SQLite シード（rnd_assessment rd_risk_app_e2e.db）
# ════════════════════════════════════════════════════════

def seed_sqlite_rnd(reset: bool) -> None:
    from sqlalchemy import create_engine, text, MetaData, Table, Column, String, Text, DateTime, JSON, Integer

    db_path = ROOT / "modules" / "rnd_assessment" / "rd_risk_app_e2e.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    # テーブル定義（最小限）
    meta = MetaData()
    Table("rd_cases", meta,
        Column("case_id", String, primary_key=True),
        Column("tenant_id", String, nullable=False),
        Column("external_project_id", String),
        Column("title", String, nullable=False),
        Column("description", Text),
        Column("status", String, nullable=False, default="draft"),
        Column("tech_sensitivity", String, nullable=False, default="public"),
        Column("access_org_ids", JSON),
        Column("created_by_user_id", String),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
    )
    Table("personnel", meta,
        Column("personnel_id", String, primary_key=True),
        Column("case_id", String),
        Column("tenant_id", String),
        Column("name", String, nullable=False),
        Column("role", String),
        Column("affiliation", String),
        Column("nationality", String(2)),
    )
    meta.create_all(engine)

    conn = engine.connect()
    if reset:
        print("  [rnd] 既存 E2E データをリセット中...")
        conn.execute(text("DELETE FROM personnel WHERE personnel_id LIKE 'e2e%'"))
        conn.execute(text("DELETE FROM rd_cases WHERE case_id LIKE 'e2e%'"))
        conn.commit()

    print("  [rnd] R&D案件 3 件投入...")
    cases = [
        ("e2e-rd-case-001", TENANT_DEFAULT,
         "[E2E] 化合物半導体研究プロジェクト",
         "GaN パワーデバイス向け研究開発案件（みなし輸出対象）",
         "active", "controlled"),
        ("e2e-rd-case-002", TENANT_DEFAULT,
         "[E2E] AI 画像認識アルゴリズム開発",
         "デュアルユース技術の可能性あり — E2E テスト用",
         "under_review", "restricted"),
        ("e2e-rd-case-003", TENANT_DEFAULT,
         "[E2E] 新材料研究（非規制）",
         "EAR99 相当の非規制研究開発プロジェクト",
         "draft", "public"),
    ]
    for case_id, tenant_id, title, desc, status, sensitivity in cases:
        conn.execute(text("""
            INSERT OR REPLACE INTO rd_cases
              (case_id, tenant_id, title, description, status, tech_sensitivity, created_at, updated_at)
            VALUES (:cid, :tid, :title, :desc, :status, :sens, :now, :now)
        """), {"cid": case_id, "tid": tenant_id, "title": title,
               "desc": desc, "status": status, "sens": sensitivity, "now": NOW})

    print("  [rnd] 研究者 4 件投入...")
    personnel_rows = [
        ("e2e-person-001", "e2e-rd-case-001", TENANT_DEFAULT,
         "E2E 研究者 田中 太郎", "researcher", "E2E テスト研究所", "JP"),
        ("e2e-person-002", "e2e-rd-case-001", TENANT_DEFAULT,
         "E2E Research Dr. Zhang Wei", "researcher", "E2E Test Institute CN", "CN"),
        ("e2e-person-003", "e2e-rd-case-002", TENANT_DEFAULT,
         "E2E Contractor Ivan Petrov", "contractor", "E2E Outsource RU", "RU"),
        ("e2e-person-004", "e2e-rd-case-003", TENANT_DEFAULT,
         "E2E 研究者 鈴木 花子", "employee", "AI Trade Management R&D", "JP"),
    ]
    for pid, cid, tid, name, role, affil, nat in personnel_rows:
        conn.execute(text("""
            INSERT OR REPLACE INTO personnel
              (personnel_id, case_id, tenant_id, name, role, affiliation, nationality,
               dual_employment_flag, created_at, updated_at)
            VALUES (:pid, :cid, :tid, :name, :role, :affil, :nat, 0, :now, :now)
        """), {"pid": pid, "cid": cid, "tid": tid, "name": name,
               "role": role, "affil": affil, "nat": nat, "now": NOW})

    conn.commit()
    conn.close()
    engine.dispose()
    print("  [rnd] 完了")


# ════════════════════════════════════════════════════════
#  メイン
# ════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="E2E テスト環境にシードデータを投入する")
    parser.add_argument("--reset", action="store_true", help="既存 E2E データを削除してから投入")
    args = parser.parse_args()

    print(f"\n{'='*55}")
    print("  E2E シードデータ投入")
    print(f"  reset={args.reset}")
    print(f"{'='*55}\n")

    print("[1/4] PostgreSQL (platform_db_e2e) ...")
    seed_postgres(args.reset)

    print("\n[2/4] SQLite ai_validation (app_e2e.db) ...")
    seed_sqlite(args.reset)

    print("\n[3/4] PostgreSQL ai_classification (platform_db_e2e) ...")
    seed_sqlite_classification(args.reset)

    print("\n[4/4] SQLite rnd_assessment (rd_risk_app_e2e.db) ...")
    seed_sqlite_rnd(args.reset)

    print(f"\n{'='*55}")
    print("  完了！  E2E 環境に切り替えてご確認ください。")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
