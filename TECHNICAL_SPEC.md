# AI_TradeManagement — 技術仕様書

**バージョン**: 1.0.0  
**最終更新**: 2026-05-08  
**対象ブランチ**: main

---

## 目次

1. [プロジェクト概要](#1-プロジェクト概要)
2. [システムアーキテクチャ](#2-システムアーキテクチャ)
3. [モジュール仕様](#3-モジュール仕様)
4. [データモデル](#4-データモデル)
5. [APIインターフェース一覧](#5-apiインターフェース一覧)
6. [AIコンポーネント](#6-aiコンポーネント)
7. [DAP（先輩担当者モード）](#7-dap先輩担当者モード)
8. [マルチテナント・組織管理](#8-マルチテナント組織管理)
9. [認証・セキュリティ](#9-認証セキュリティ)
10. [モジュール間連携インターフェース](#10-モジュール間連携インターフェース)
11. [インフラ・デプロイメント](#11-インフラデプロイメント)
12. [環境変数・設定](#12-環境変数設定)
13. [開発規約](#13-開発規約)

---

## 1. プロジェクト概要

**AI_TradeManagement** は、日本の外為法および国際輸出管理規制（EAR/ECCN/Wassenaar）対応のモジュール型 AI コンプライアンスプラットフォームです。

### 1.1 対象規制

| 規制フレームワーク | 正式名称 | 主管機関 |
|-----------------|--------|--------|
| 外為法 | 外国為替及び外国貿易法 | 経済産業省 |
| EAR | Export Administration Regulations | BIS (米国商務省) |
| ITAR | International Traffic in Arms Regulations | DDTC (米国国務省) |
| EU Dual-Use | Council Regulation (EC) No 428/2009 | EU |
| Wassenaar | ワッセナー協約 | 多国間 |
| NSG | 核供給国グループ | 多国間 |
| MTCR | ミサイル技術管理レジーム | 多国間 |
| OFAC | Office of Foreign Assets Control | 米国財務省 |

### 1.2 主要機能

- AI を活用した品目の該非判定（外為法・EAR 両対応）
- 取引先制裁リストスクリーニング（OFAC/BIS 7 ソース）
- R&D リスク評価・みなし輸出管理
- HSコード自動付番（HS2022、6桁精度）
- 特許調査・技術インテリジェンス
- FTA 特恵税率照会（日本発 21 協定）
- サプライチェーン管理・De Minimis 算出
- 輸出許可申請ドラフト生成・証跡管理
- DAP 先輩担当者モード（UC ステップ伴走・プロアクティブアラート）

---

## 2. システムアーキテクチャ

### 2.1 モジュール構成

```
┌─────────────────────────────────────────────────────────────────┐
│  ユーザーブラウザ / 外部システム                                     │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTPS
                    Cloudflare Tunnel
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│  platform-core (Port 8000)                                       │
│  ┌─────────────┐ ┌───────────┐ ┌──────────┐ ┌───────────────┐  │
│  │ 認証/SSO    │ │ リバース  │ │ FAISS    │ │ 規制スケジュ  │  │
│  │ JWT/Google/ │ │ プロキシ  │ │ Layer A  │ │ ーラー        │  │
│  │ Microsoft  │ │           │ │ (外為法) │ │               │  │
│  └─────────────┘ └───────────┘ └──────────┘ └───────────────┘  │
│  ┌─────────────┐ ┌───────────┐ ┌──────────┐ ┌───────────────┐  │
│  │ 輸出許可    │ │ サプライ  │ │ FTA 管理 │ │ 取引先管理    │  │
│  │ 申請管理    │ │ チェーン  │ │          │ │               │  │
│  └─────────────┘ └───────────┘ └──────────┘ └───────────────┘  │
└──────┬────────┬────────┬────────┬─────────┬──────────┬─────────┘
       │        │        │        │         │          │
  ┌────▼──┐ ┌───▼───┐ ┌──▼────┐ ┌─▼────┐ ┌─▼───┐ ┌──▼─────┐
  │ai_val │ │ai_cls │ │rnd_   │ │patent│ │scr  │ │hs_cls  │
  │idation│ │sific  │ │assess │ │search│ │eening│ │sifier  │
  │:8011  │ │:8002  │ │:8003  │ │:8004 │ │:8005 │ │:8006   │
  └────────┘ └───────┘ └───────┘ └──────┘ └──────┘ └────────┘
                                                     ┌────────┐
                                                     │ dap    │
                                                     │ :8010  │
                                                     └────────┘
```

### 2.2 モジュール一覧

| モジュール | ポート | DB | 主要機能 |
|-----------|--------|----|----|
| **platform-core** | 8000 | PostgreSQL | 共通基盤・認証・FAISS Layer A/B・規制スケジューラー・プロキシ・組織管理 |
| **ai_validation** | 8011 | PostgreSQL | AI 該非判定（FAISS + HanteiAgent）・役務取引・外部連携 |
| **ai_classification** | 8002 | SQLite | 品目管理・SDS 解析・HS コード連携・国別規制プロファイル・BOM |
| **rnd_assessment** | 8003 | SQLite | R&D リスク評価・みなし輸出・人物管理・ICP 診断 |
| **patent_search** | 8004 | SQLite | 特許検索（BigQuery + J-PlatPat）・学術論文リンク |
| **screening** | 8005 | PostgreSQL | 制裁リストスクリーニング（OFAC/BIS/7 ソース） |
| **hs_classifier** | 8006 | メモリ (FAISS) | HS コード判定（Layer C、5,476 vec） |
| **dap** | 8010 | SQLite | AI オーケストレーター・先輩担当者モード（Claude API） |

### 2.3 技術スタック

| レイヤ | 技術 |
|-------|------|
| Web フレームワーク | FastAPI 0.110+ |
| ORM | SQLAlchemy 2.0 (sync + async), Alembic |
| DB | PostgreSQL 15 (platform-core, ai_validation, screening), SQLite (他) |
| ベクトル検索 | FAISS (IndexFlatIP + IndexHNSWFlat) |
| エンベディング | intfloat/multilingual-e5-large |
| LLM | Claude claude-sonnet-4-6 (Anthropic API) / GPT-4o (OpenAI, フォールバック) |
| テンプレート | Jinja2 (SSR HTML) |
| HTTP クライアント | httpx (async) |
| 認証 | JWT (HS256), Google OAuth 2.0, Microsoft OAuth 2.0 |
| インフラ | Cloudflare Tunnel, Docker (開発補助) |

---

## 3. モジュール仕様

### 3.1 platform-core (Port 8000)

**役割**: 全モジュールの共通基盤。認証・プロキシ・知識管理・規制スケジューラーを担う。

**主要コンポーネント**:
- `auth/` — JWT 発行・OAuth2 コールバック・セッション管理
- `routers/proxy.py` — 各モジュールへのリバースプロキシ（URL 書き換え含む）
- `routers/regulatory.py` — 規制変更トラッキング・org_id フィルタリング
- `routers/export_license.py` — 輸出許可申請 CRUD・価値控除・ドラフト生成
- `routers/supply_chain.py` — サプライチェーンノード・De Minimis 算出
- `routers/fta.py` — FTA 協定照会・特恵税率・origin_country フィルタ
- `routers/organizations.py` — 組織・拠点管理
- `agent/` — HanteiAgent・BaseContext・AgentTool 抽象基盤
- `services/faiss_e5_service.py` — FAISS Layer A/B 管理

**起動コマンド**:
```bash
cd platform-core && uvicorn platform_core.main:app --host 0.0.0.0 --port 8000
```

---

### 3.2 ai_validation (Port 8011)

**役割**: 外為法マトリクス照合・EAR ECCN 判定・キャッチオール審査・役務取引管理。

> **注意**: ポート番号は `8011`。`8001` は Docker により占有済み。

**主要コンポーネント**:
- `routers/decision.py` — AI 判定実行・2リスト照合・FDPR/EAR/EU チェック
- `routers/ui.py` — 案件 UI（一覧・新規・詳細・PDF 出力）
- `routers/api_transactions.py` — DAP 向け JSON API（案件 CRUD・recent・stuck）
- `services/two_list.py` — 2リスト（直接・参考）カウント計算
- `services/hantei_agent.py` — NeuroSymbolic 6 ステップキャッチオールエンジン

**起動コマンド**:
```bash
cd modules/ai_validation && uvicorn app.main:app --host 0.0.0.0 --port 8011
```

---

### 3.3 ai_classification (Port 8002)

**役割**: 品目マスター管理・SDS 危険性解析・HS コード連携・国別規制プロファイル・BOM 管理。

**主要コンポーネント**:
- `routers/products.py` — 品目 CRUD・スコア計算・輸出規制評価
- `routers/country_profiles.py` — 国別規制プロファイル（ローカル ECCN・許可要否）
- `routers/integrations.py` — ERP 連携・R&D 連携 Webhook
- `routers/hs_local.py` — HS2022 ローカル検索・サジェスト
- `routers/reexport_control.py` — 再輸出管理・国別リスクプロファイル

**起動コマンド**:
```bash
cd modules/ai_classification && uvicorn app.main:app --host 0.0.0.0 --port 8002
```

---

### 3.4 rnd_assessment (Port 8003)

**役割**: R&D リスク評価・みなし輸出審査・人物管理・ICP（特定重要技術）自己診断。

**主要コンポーネント**:
- `api/v1/` — R&D ケース・リスク評価・IP 審査 API
- `ui/` — ダッシュボード UI・学術インテリジェンス
- 人物管理: 外国籍研究者の技術アクセス管理・みなし輸出リスク分類

**起動コマンド**:
```bash
cd modules/rnd_assessment && uvicorn app.main:app --host 0.0.0.0 --port 8003
```

---

### 3.5 patent_search (Port 8004)

**役割**: 特許セマンティック検索（FAISS Layer B）・用途抽出・出願人スクリーニング・学術論文リンク。

**データソース**:
- BigQuery (Google Patents Public Data) — プライマリ
- J-PlatPat API — フォールバック
- Semantic Scholar / OpenAlex — 学術論文

**起動コマンド**:
```bash
cd modules/patent_search && uvicorn app.main:app --host 0.0.0.0 --port 8004
```

---

### 3.6 screening (Port 8005)

**役割**: 取引先・人物の制裁リストスクリーニング。FAISS による類似名寄せ。

**対応リスト**:
- OFAC SDN (米国財務省)
- BIS Entity List (米国商務省)
- BIS Denied Persons List
- BIS Unverified List
- UN Security Council Consolidated List
- EU Consolidated Sanctions List
- 日本外為法 外国ユーザーリスト

**起動コマンド**:
```bash
cd modules/screening && uvicorn app.main:app --host 0.0.0.0 --port 8005
```

---

### 3.7 hs_classifier (Port 8006)

**役割**: 品目説明文からの HS コード自動付番。FAISS Layer C を使用。

**インデックス仕様**:
- ベクトル数: 5,476
- 対象: HS2022 6桁（類・項・号）+ 説明文
- エンベディング: multilingual-e5-large

**起動コマンド**:
```bash
cd modules/hs_classifier && uvicorn app.main:app --host 0.0.0.0 --port 8006
```

---

### 3.8 dap (Port 8010)

**役割**: AI オーケストレーター。先輩担当者として UC ベースのステップ伴走・プロアクティブアラートを提供。

**主要コンポーネント**:
- `routers/chat.py` — Claude API 対話エンジン・ナレッジベース・FAQ
- `routers/workflow.py` — UC ステップ管理（UC1〜UC9）
- `static/chat-widget.js` — 埋め込みチャットウィジェット（全ページ注入）
- `app/db/models.py` — セッション・イベント・インターベンション永続化

**Claude モデル**: `claude-sonnet-4-6`

**起動コマンド**:
```bash
cd modules/dap && uvicorn app.main:app --host 0.0.0.0 --port 8010
```

---

## 4. データモデル

### 4.1 ai_validation — transactions

```sql
CREATE TABLE transactions (
    id                    SERIAL PRIMARY KEY,
    case_no               VARCHAR(50) UNIQUE NOT NULL,
    title                 TEXT NOT NULL,
    status                VARCHAR(20) DEFAULT 'draft',  -- draft/in_review/approved/rejected/archived
    counterparty_name     TEXT,
    destination_country   VARCHAR(4),
    end_user_name         TEXT,
    org_id                VARCHAR(100),                 -- 組織ID（マルチテナント）
    source_module         VARCHAR(50),                  -- 'dap' | 'item_version' | etc.
    screening_result_id   INTEGER,
    screening_status      VARCHAR(30),                  -- clean/match/possible_match
    agent_judgment_status VARCHAR(30),
    agent_judged_at       TIMESTAMP,
    evaluator_name        VARCHAR(100),
    judgment_no           VARCHAR(50),
    retention_until       DATE,                         -- 外為法7年保存
    supply_chain_node_id  VARCHAR(100),
    de_minimis_result     JSONB,
    created_at            TIMESTAMP DEFAULT NOW(),
    updated_at            TIMESTAMP DEFAULT NOW()
);
```

### 4.2 ai_validation — ai_runs

```sql
CREATE TABLE ai_runs (
    id              SERIAL PRIMARY KEY,
    transaction_id  INTEGER REFERENCES transactions(id),
    run_type        VARCHAR(30),     -- 'matrix_match' | 'catchall' | 'fdpr'
    status          VARCHAR(20),     -- 'pending' | 'running' | 'done' | 'error'
    model_name      VARCHAR(100),
    prompt_version  VARCHAR(50),
    params          JSONB,
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP,
    error           TEXT
);
```

### 4.3 ai_validation — matrix_matches (Layer A 照合結果)

```sql
CREATE TABLE matrix_matches (
    id                   SERIAL PRIMARY KEY,
    ai_run_id            INTEGER REFERENCES ai_runs(id),
    matrix_rule_id       INTEGER,
    usage_requirement_id INTEGER,
    layer_a_faiss_id     INTEGER,
    layer_a_item_no      VARCHAR(50),
    layer_a_source_type  VARCHAR(20),   -- 'jpn_eccn' | 'ear_eccn' | 'wassenaar'
    match_type           VARCHAR(20),   -- 'core' | 'expanded' | 'catchall'
    match_score          FLOAT,
    decision             VARCHAR(30),
    evidence_json        JSONB
);
```

### 4.4 platform-core — plat_export_license_application

```sql
CREATE TABLE plat_export_license_application (
    id                 SERIAL PRIMARY KEY,
    application_number VARCHAR(100) UNIQUE,
    license_type       VARCHAR(50),   -- 'individual' | 'bulk' | 'special_bulk'
    form_type          VARCHAR(50),   -- 'form1' | 'form4' | 'form7'
    status             VARCHAR(30),   -- 'draft' | 'submitted' | 'approved' | 'denied' | 'expired'
    item_description   TEXT,
    eccn               VARCHAR(20),
    destination_country VARCHAR(4),
    end_user_name      TEXT,
    declared_usage     TEXT,
    approved_amount    NUMERIC(18,2),
    consumed_amount    NUMERIC(18,2) DEFAULT 0,
    currency           VARCHAR(3)  DEFAULT 'USD',
    valid_from         DATE,
    valid_to           DATE,
    draft_content      JSONB,         -- METI 申請書ドラフト JSON
    org_id             VARCHAR(100),  -- マルチテナント
    transaction_id     INTEGER,       -- 紐付け案件
    created_at         TIMESTAMP DEFAULT NOW(),
    updated_at         TIMESTAMP DEFAULT NOW()
);
```

### 4.5 platform-core — plat_supply_chain_node

```sql
CREATE TABLE plat_supply_chain_node (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    node_type   VARCHAR(30),          -- 'factory' | 'distributor' | 'end_user'
    country     VARCHAR(4),
    supplier_id INTEGER,
    parent_id   INTEGER REFERENCES plat_supply_chain_node(id),
    de_minimis_us_ratio FLOAT,        -- 米国技術含有比率
    created_at  TIMESTAMP DEFAULT NOW()
);
```

### 4.6 platform-core — plat_fta_agreement

```sql
CREATE TABLE plat_fta_agreement (
    id               SERIAL PRIMARY KEY,
    code             VARCHAR(20) UNIQUE NOT NULL,   -- 'JPEPA' | 'RCEP' など
    name             VARCHAR(200) NOT NULL,
    partner_countries VARCHAR(500),                 -- カンマ区切り ISO-3166 Alpha-2
    effective_from   DATE,
    effective_to     DATE,
    origin_country   VARCHAR(4) NOT NULL DEFAULT 'JP',  -- Phase5 追加
    is_active        BOOLEAN DEFAULT TRUE
);
```

### 4.7 platform-core — regulatory_changes

```sql
CREATE TABLE regulatory_changes (
    id               SERIAL PRIMARY KEY,
    regulation_type  VARCHAR(50),     -- 'ECCN_UPDATE' | 'EMBARGO' | 'EAR_AMENDMENT' | 'FEFTA'
    title            TEXT NOT NULL,
    description      TEXT,
    source_url       TEXT,
    effective_date   DATE,
    detected_at      TIMESTAMP DEFAULT NOW(),
    relevant_org_ids JSONB            -- Phase5 追加: null=全組織, ["org1","org2"]=特定組織
);
```

### 4.8 ai_classification — products

```sql
CREATE TABLE products (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    code                   TEXT UNIQUE,
    name                   TEXT NOT NULL,
    description            TEXT,
    hs_code                TEXT,
    eccn                   TEXT,
    ghs_signal_word        TEXT,
    is_poison              BOOLEAN DEFAULT FALSE,
    is_kashinho            BOOLEAN DEFAULT FALSE,  -- 外為法規制品フラグ
    usage_summary          TEXT,
    export_control_status  TEXT,   -- 'NOT_REQUIRED' | 'REQUIRES_PERMIT' | 'PENDING'
    external_eval_status   TEXT,
    hs_classification_status TEXT,
    source_rnd_case_id     INTEGER,
    sovereignty_score      FLOAT,
    dual_use_score         FLOAT,
    economic_security_score FLOAT,
    created_at             TEXT,
    updated_at             TEXT
);
```

### 4.9 dap — dap_workflow_sessions

```sql
CREATE TABLE dap_workflow_sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT UNIQUE NOT NULL,
    uc_id          TEXT NOT NULL,
    current_step   INTEGER DEFAULT 1,
    completed_steps TEXT DEFAULT '[]',  -- JSON array
    status         TEXT DEFAULT 'active',  -- active | completed | abandoned
    context_data   TEXT DEFAULT '{}',   -- JSON
    started_at     TEXT,
    updated_at     TEXT
);
```

---

## 5. APIインターフェース一覧

### 5.1 ai_validation (Port 8011)

#### 案件管理

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/ui/transactions` | 案件一覧画面 |
| GET | `/ui/transactions/new` | 案件新規作成フォーム |
| POST | `/ui/transactions/new` | 案件作成 |
| POST | `/ui/transactions/csv-import` | CSV 一括インポート |
| GET | `/ui/transactions/{id}` | 案件詳細画面 |
| POST | `/ui/transactions/{id}/run` | AI 判定実行 |
| POST | `/ui/transactions/{id}/run-screening` | スクリーニング実行 |
| GET | `/ui/transactions/{id}/export/csv` | CSV 出力 |
| GET | `/ui/transactions/{id}/export/pdf` | PDF 出力 |

#### 判定 API

| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/{id}/run-and-two-lists` | 判定実行 + 2リスト照合（内部） |
| POST | `/{id}/save-agent-judgment` | エージェント判定保存 |
| POST | `/{id}/submit-formal-review` | 正式審査提出 |
| GET | `/{id}/review-checklist` | 審査チェックリスト JSON |
| POST | `/{id}/fdpr-check` | FDPR 判定 |
| GET | `/{id}/fdpr-result` | FDPR 結果 |
| POST | `/{id}/ear-check` | EAR 判定 |
| POST | `/{id}/eu-dual-use-check` | EU 二次用途判定 |
| GET | `/{id}/faiss-candidates` | FAISS 候補リスト |
| GET | `/{id}/two-lists` | 直接・参考リスト照合結果 |
| POST | `/{id}/catchall-judgment` | キャッチオール判定実行 |
| GET | `/{id}/catchall-result` | キャッチオール結果（DAP 向け） |

#### 案件 JSON API（DAP / 外部システム向け）

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/transactions/recent` | 直近 N 件 + ペンディングアクション |
| POST | `/api/transactions` | 案件新規作成（DAP ヒアリング完了後） |
| GET | `/api/transactions/stuck` | 審査停止案件一覧 |
| POST | `/api/transactions/{id}/supply-chain` | サプライチェーンリンク保存 |

**`GET /api/transactions/recent` クエリパラメータ**:

| パラメータ | 型 | デフォルト | 説明 |
|-----------|---|--------|------|
| `limit` | int | 5 | 取得件数（最大 20） |
| `all_orgs` | bool | false | true = 全拠点表示 |
| `X-Organization-Id` (header) | str | — | 自拠点フィルタ |

**レスポンス例**:
```json
{
  "transactions": [
    {
      "id": 42,
      "case_no": "API-20260508-1234",
      "title": "光学センサー輸出審査",
      "status": "draft",
      "counterparty_name": "XYZ Corp",
      "screening_status": "clean",
      "has_ai_run": true,
      "last_run_at": "2026-05-08T10:00:00",
      "counts": {"core_only": 2, "expanded_only": 1, "intersection": 0, "neither": 5},
      "pending_actions": [
        {
          "step": 3,
          "key": "export_pdf",
          "label": "報告書を出力 (PDF)",
          "url": "http://localhost:8011/ui/transactions/42/export/pdf",
          "method": "GET",
          "priority": "info"
        }
      ]
    }
  ],
  "total": 1
}
```

---

### 5.2 platform-core (Port 8000)

#### 輸出許可申請

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/export-licenses` | 申請一覧 |
| POST | `/api/export-licenses` | 申請新規作成 |
| GET | `/api/export-licenses/{id}` | 申請詳細 |
| PUT | `/api/export-licenses/{id}` | 申請更新 |
| DELETE | `/api/export-licenses/{id}` | 申請削除 |
| POST | `/api/export-licenses/{id}/submit` | 申請提出 |
| POST | `/api/export-licenses/{id}/approve` | 申請承認 |
| POST | `/api/export-licenses/{id}/deny` | 申請却下 |
| POST | `/api/export-licenses/{id}/use-value` | 価値控除（出荷額記録） |
| POST | `/api/export-licenses/draft-from-transaction` | 案件からドラフト生成 |
| GET | `/api/export-licenses/{id}/preview` | HTML プレビュー |
| GET | `/api/export-licenses/stats` | 統計サマリー |

#### FTA 管理

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/fta/agreements` | 協定一覧（`origin_country` フィルタ対応） |
| GET | `/api/fta/rates` | 税率一覧 |
| GET | `/api/fta/check` | FTA 適用可否照会（`origin_country`, `country`, `hs_code`） |
| POST | `/api/fta/seed` | シードデータ投入 |
| GET | `/ui/fta-check` | FTA 照会 UI |

**`GET /api/fta/check` クエリパラメータ**:

| パラメータ | 型 | デフォルト | 説明 |
|-----------|---|--------|------|
| `country` | str | 必須 | 仕向地（ISO-3166 Alpha-2） |
| `hs_code` | str | 必須 | HS コード（6桁） |
| `origin_country` | str | `JP` | 原産国 |

#### サプライチェーン・De Minimis

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/supply-chain/nodes` | ノード一覧 |
| POST | `/api/supply-chain/nodes` | ノード作成 |
| GET | `/api/supply-chain/nodes/{id}` | ノード詳細 |
| GET | `/api/supply-chain/nodes/{id}/tree` | ツリー構造 |
| POST | `/api/supply-chain/nodes/{id}/de-minimis` | De Minimis 算出 |
| POST | `/api/supply-chain/edges` | エッジ（関係）作成 |
| DELETE | `/api/supply-chain/edges/{id}` | エッジ削除 |

**`POST /api/supply-chain/nodes/{id}/de-minimis` レスポンス例**:
```json
{
  "node_id": 5,
  "us_content_ratio": 0.23,
  "threshold_general": 0.25,
  "threshold_terrorist": 0.10,
  "ear_applicable_general": false,
  "ear_applicable_terrorist": true,
  "fdpr_applicable": false,
  "components": [
    {"name": "US Chip A", "us_origin": true, "value": 120.0},
    {"name": "JP Sensor B", "us_origin": false, "value": 400.0}
  ]
}
```

#### 規制変更モニタリング

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/regulatory/changes` | 規制変更一覧（`X-Organization-Id` ヘッダーで組織フィルタ） |
| POST | `/api/regulatory/changes` | 変更登録 |
| GET | `/api/regulatory/changes/{id}` | 変更詳細 |
| PATCH | `/api/regulatory/changes/{id}/org-filter` | 対象組織 ID 設定 |
| GET | `/api/regulatory/pipeline` | コンプライアンスパイプライン |
| GET | `/api/regulatory/open-actions` | 未対応アクション一覧 |
| GET | `/api/regulatory/change-feed` | 規制変更フィード |

#### 組織管理

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/organizations` | 組織一覧 |
| POST | `/api/organizations` | 組織作成 |
| GET | `/api/organizations/{id}` | 組織詳細 |
| PUT | `/api/organizations/{id}` | 組織更新 |

#### モジュール自動登録（内部）

| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/internal/modules/register` | モジュール自己登録 |
| GET | `/internal/modules/{key}` | モジュール情報取得 |
| GET | `/internal/modules` | モジュール一覧 |

---

### 5.3 dap (Port 8010)

#### チャット API

| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/api/chat` | チャットメッセージ送受信 |
| POST | `/api/chat/greet` | 初期グリーティング（コンテキスト付き） |
| POST | `/api/chat/event` | ページ遷移イベント通知 |
| GET | `/api/chat/session/{id}` | セッション取得 |
| DELETE | `/api/chat/session/{id}` | セッション削除 |
| GET | `/api/chat/coaching-templates` | コーチングテンプレート一覧 |
| GET | `/api/chat/app-configs` | アプリ設定一覧 |
| PUT | `/api/chat/app-configs/{port}` | アプリ設定更新 |
| POST | `/api/chat/app-configs/{port}/apply-template` | テンプレート適用 |
| POST | `/api/chat/app-configs/apply-all-templates` | 全テンプレート一括適用 |

**`POST /api/chat` リクエスト例**:
```json
{
  "session_id": "sess_abc123",
  "message": "De Minimisの25%ルールを教えてください",
  "context": {
    "page": "supply-chain",
    "transaction_id": 42
  }
}
```

#### ワークフロー API

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/workflow/uc-list` | 利用可能 UC 一覧 |
| POST | `/api/workflow/start` | UC セッション開始 |
| GET | `/api/workflow/status` | 現在のステップ状態 |
| POST | `/api/workflow/complete_step` | ステップ完了・次の指示取得 |
| POST | `/api/workflow/abandon` | セッション中断 |

---

### 5.4 screening (Port 8005)

| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/api/screen` | スクリーニング実行 |
| GET | `/api/results` | 結果一覧 |
| GET | `/api/results/{id}` | 結果詳細 |
| GET | `/api/watchlist` | ウォッチリスト |
| POST | `/api/watchlist` | ウォッチリスト追加 |
| POST | `/api/watchlist/import` | バルクインポート |
| DELETE | `/api/watchlist/{id}` | エントリ削除 |
| POST | `/api/admin/sync-sanctions` | 制裁リスト同期 |
| POST | `/api/rebuild-index` | FAISS インデックス再構築 |

---

### 5.5 hs_classifier (Port 8006)

| メソッド | パス | 説明 |
|---------|------|------|
| POST | `/classify/sync` | HS コード判定（同期） |
| POST | `/classify` | HS コード判定（非同期 + webhook） |
| GET | `/search` | セマンティック検索 |
| GET | `/hs/{code}` | HS コード詳細 |
| GET | `/index/status` | インデックス状態 |
| GET | `/health` | ヘルスチェック |

**`POST /classify/sync` レスポンス例**:
```json
{
  "top_candidates": [
    {"hs_code": "903180", "description": "光学センサー", "score": 0.92, "rank": 1},
    {"hs_code": "854370", "description": "電子機器", "score": 0.78, "rank": 2}
  ],
  "recommended": "903180"
}
```

---

## 6. AIコンポーネント

### 6.1 FAISS インデックス構成

| Layer | 用途 | ベクトル数 | データ源 |
|-------|------|----------|--------|
| **Layer A** | 外為法マトリクス照合 / ECCN 候補 | ~2,999 | 外為法規制品目リスト・ECCN カテゴリ |
| **Layer B** | 特許セマンティック検索 | ~1,595 | Google Patents BigQuery / J-PlatPat |
| **Layer C** | HS コード自動付番 | 5,476 | HS2022 品目解説（6桁） |
| **Layer D** | 学術論文インテリジェンス | 動的追加 | Semantic Scholar / OpenAlex |

**エンベディングモデル**: `intfloat/multilingual-e5-large` (1024 次元)

**プリロードタイミング**:
- ai_validation 起動時: Layer A + B（約 20〜30 秒）
- hs_classifier 起動時: Layer C（即時）
- screening: 監視リストから動的構築

---

### 6.2 HanteiAgent — NeuroSymbolic キャッチオールエンジン

`platform_core/agent/` に実装された 6 ステップキャッチオール審査エンジン。

```
Step 1: E:1 チェック
        └─ 仕向地が EAR E:1 国（イラン・北朝鮮・シリア・キューバ・ロシア等）か判定
        └─ YES → 高優先度アラート

Step 2: White List チェック
        └─ 仕向地が Wassenaar / NSG ホワイトリスト国か確認
        └─ ホワイトリスト国 → キャッチオール不要の可能性

Step 3: EAR Country Chart 照合
        └─ 品目 ECCN × 仕向地の許可要件列（AT/CB/CC/CW/EI/FC/FT/MT/NP/NS/RS/SS/UN/WT）を照合
        └─ ヒット列をスコアリング

Step 4: Red Flag チェック
        └─ 8 つの Red Flag 要素を判定
           (1) 品目が宣言用途と不一致
           (2) 軍事関連用途の疑い
           (3) 不審な支払い方法
           (4) 迂回経路の疑い
           (5) 異常な保証拒否
           (6) 不審な発注量
           (7) 高リスク仕向地
           (8) 既知の懸念企業・エンティティ

Step 5: スコアリング
        └─ (EAR Country Chart ヒット数 × 重み) + (Red Flag 数 × 重み) = 総合スコア
        └─ スコア閾値: 0.75（設定可能）

Step 6: 結果生成
        └─ REQUIRES_PERMIT / NOT_REQUIRED / REVIEW_REQUIRED
        └─ 推奨アクション（許可申請 / 追加確認 / ライセンス例外適用）
```

**関連ファイル**:
- `platform-core/platform_core/agent/base_agent.py` — BaseContext・AgentTool 抽象クラス
- `platform-core/platform_core/agent/tools.py` — RunValidationTool・ScreeningTool・GetTransactionTool・CatchallDetailTool
- `modules/ai_validation/app/services/hantei_agent.py` — HanteiAgent 実装

---

### 6.3 DAP チャットエンジン

`modules/dap/app/routers/chat.py` に実装。

**システムプロンプト構成**:
```
[役割] 輸出管理の先輩担当者（Expert Companion）
[知識ベース]
  - 外為法マトリクス
  - EAR/ECCN/Wassenaar
  - BOM・De Minimis ルール（FDP Rule §734.9、25%/10%閾値、FDPR §734.9）
  - 輸出許可証管理（4要素ルール・価値ベース許可証・BIS申請リードタイム）
  - FTA 原産地規則（PSR・累積規則）
  - 海外品目マスター管理（ローカル ECCN・国別規制プロファイル）
[FAQ] 40+ 件
[コーチングテンプレート] ページ別（transactions/supply_chain/export_license 等）
[UC ナビゲーション] UC1〜UC9 ステップ解説
```

**プロアクティブアラートロジック**:
```python
# 輸出許可証期限切れアラート
if 0 <= days_until_expiry <= 30:
    severity = "danger" if days_until_expiry <= 7 else "warn"
    alerts.append({"type": "license_expiry", "severity": severity, ...})

# 残存許可額不足アラート
if consumed / approved >= 0.80:  # 80%消費 = 残20%以下
    alerts.append({"type": "license_balance", "severity": "warn", ...})

# 審査停止案件アラート
if has_ai_run and not agent_judged_at:
    alerts.append({"type": "stuck_review", "severity": "warn", ...})
```

---

## 7. DAP（先輩担当者モード）

### 7.1 アーキテクチャ

```
ユーザーブラウザ
    │
    ▼ (全ページに自動注入)
chat-widget.js (iframe)
    │
    ▼ POST /api/chat
dap module (Port 8010)
    │
    ├─ Claude API (claude-sonnet-4-6)
    │     └─ ナレッジベース / FAQ / UC解説
    │
    ├─ GET /api/chat/greet (ページ検知 → コーチング)
    │
    └─ POST /api/chat/event (ページ遷移イベント)
```

### 7.2 ユースケース一覧（UC1〜UC9）

| UC | タイトル | ペルソナ | ステップ数 |
|----|---------|---------|----------|
| UC1 | 新規品目の輸出審査 | 輸出管理担当者 | 5 |
| UC2 | R&D 起案から品目登録まで | R&D チームリーダー / 輸出管理担当者 | 5 |
| UC3 | 海外品目マスター管理・国別規制プロファイル | 輸出管理担当者 / グローバル調達担当者 | 5 |
| UC4 | 取引先デューデリジェンス | 輸出管理担当者 | 3 |
| UC5 | みなし輸出審査 | 輸出管理担当者 / 人事 | 4 |
| UC6 | 輸出許可申請ドラフト生成 | 輸出管理担当者 | 3 |
| UC7 | BOM 管理・De Minimis 算出 | 輸出管理担当者 / 調達担当者 | 5 |
| UC8 | 出荷ライセンス管理 | 輸出管理担当者 / 出荷担当者 | 5 |
| UC9 | 技術インテリジェンス調査 | R&D チームリーダー / 知財担当者 | 3 |

**合計**: 9 UC・38 ステップ

### 7.3 ワークフロー API フロー

```
1. POST /api/workflow/start   { "session_id": "xxx", "uc_id": "UC1" }
   → セッション作成・Step 1 の指示返却

2. POST /api/workflow/complete_step  { "session_id": "xxx", "step_num": 1 }
   → 完了記録・Step 2 のナビゲーション URL + ハイライト指示返却

3. 繰り返し... Step N まで完了

4. 全ステップ完了時: status = "completed"
   OR
   POST /api/workflow/abandon → セッション中断
```

---

## 8. マルチテナント・組織管理

### 8.1 テナント・組織モデル

```
plat_tenant (UUID)
    └─ plat_organization (org_id: VARCHAR)
           └─ plat_user (org_id)
```

### 8.2 org_id の伝播

組織 ID は `X-Organization-Id` HTTP ヘッダーで全モジュールに伝播します。

```
ブラウザ → platform-core (X-Organization-Id: "org_tokyo")
         → プロキシ時にヘッダーを転送
         → ai_validation (X-Organization-Id: "org_tokyo")
                └─ transactions WHERE org_id = "org_tokyo" OR org_id IS NULL
```

**ダッシュボード org トグル**（Phase 4 実装）:
```
?all_orgs=false → 自拠点のみ表示 (X-Organization-Id フィルタ)
?all_orgs=true  → 全拠点表示
```

JavaScript から `localStorage.getItem("org_id")` を取得してリンクに付与。

### 8.3 org_id を持つテーブル

| テーブル | org_id の使われ方 |
|---------|----------------|
| `transactions` | 案件の所属組織 |
| `plat_export_license_application` | 申請の所属組織 |
| `plat_fta_agreement` | 協定参照（将来的に組織別 FTA） |
| `regulatory_changes.relevant_org_ids` | JSONB 配列（null = 全組織対象） |

**`regulatory_changes` の組織フィルタ（Phase 5 実装）**:
```sql
WHERE relevant_org_ids IS NULL
   OR relevant_org_ids @> jsonb_build_array(:org_id)
```

---

## 9. 認証・セキュリティ

### 9.1 認証フロー

```
[JWT認証]
1. POST /auth/token   { email, password }
   → JWT Access Token (60分) + Refresh Token (30日)

2. 各リクエスト: Authorization: Bearer <token>
   → platform_core.auth.deps.get_current_user() で検証

[Google OAuth]
1. GET /auth/google/login → Google 認可ページ
2. GET /auth/google/callback?code=xxx → JWT 発行

[Microsoft OAuth]
1. GET /auth/microsoft/login → Microsoft 認可ページ  
2. GET /auth/microsoft/callback?code=xxx → JWT 発行

[内部サービス認証]
X-Internal-Service-Key: <INTERNAL_SERVICE_KEY>
→ 各モジュールの /internal/** エンドポイントで検証
```

### 9.2 監査ログ

`AuditMiddleware` が全リクエストを `plat_audit_log` に記録:

```json
{
  "timestamp": "2026-05-08T10:00:00Z",
  "user_id": 5,
  "action": "POST",
  "path": "/api/export-licenses/12/use-value",
  "resource_type": "export_license",
  "resource_id": 12,
  "ip_address": "192.168.1.1",
  "status_code": 200
}
```

### 9.3 セキュリティ対策

- SQL インジェクション: SQLAlchemy ORM / パラメータ化クエリ
- XSS: Jinja2 の自動エスケープ
- CSRF: FastAPI フォーム + セッション
- Rate Limiting: Cloudflare WAF
- HTTPS: Cloudflare Tunnel による強制 TLS

---

## 10. モジュール間連携インターフェース

### 10.1 R&D → 品目管理 連携

```
rnd_assessment:
    POST /products/from-rnd
    → ai_classification: products テーブルにレコード作成
    → 品目 ID を rd_case_profiles.promoted_product_id に記録
```

### 10.2 品目管理 → HS 分類器 連携

```
ai_classification:
    POST /hs-classifier/request/{product_id}
    → hs_classifier (Port 8006): POST /classify  { item_description, webhook_url }
    → 完了後: POST /hs-classifier/webhook  { product_id, hs_code, score }
    → products.hs_code 更新
```

### 10.3 品目管理 → 外部該非判定 連携

```
ai_classification:
    POST /export-control/request/{product_id}
    → 外部審査機関への API 送信
    → 完了後: POST /export-control/webhook  { product_id, eccn, result }
    → products.eccn + export_control_status 更新
```

### 10.4 DAP → ai_validation 連携

```
dap:
    POST /api/transactions  (ai_validation Port 8011)
    → ヒアリング完了後に案件を自動作成
    → { id, case_no, title, status, url } 返却

dap:
    GET /api/transactions/recent  (ai_validation Port 8011)
    → ダッシュボード向け案件サマリー + ペンディングアクション取得

dap:
    GET /{id}/catchall-result  (ai_validation Port 8011)
    → キャッチオール判定詳細取得（CatchallDetailTool）
```

### 10.5 platform-core → ai_validation 連携

```
platform-core:
    GET /api/transactions/recent  (ai_validation Port 8011)
    → ダッシュボード表示用（X-Organization-Id ヘッダー付き）

platform-core Agent:
    POST /{id}/run-and-two-lists  (ai_validation Port 8011)
    → RunValidationTool による自動判定実行
```

### 10.6 platform-core → screening 連携

```
platform-core Agent (ScreeningTool):
    POST /api/screen  (screening Port 8005)
    { "company_name": "XYZ Corp" }
    → { is_sanctioned: bool, sanction_lists: [...], max_score: float }
    → Context に screening_result 格納
```

### 10.7 プロキシ URL 書き換えルール

`platform_core/routers/proxy.py` による動的 URL 書き換え:

| 変換前 | 変換後 |
|-------|-------|
| `src="/static/..."` | `src="/proxy/{module_key}/static/..."` |
| `fetch('/api/...')` | `fetch('/proxy/{module_key}/api/...')` |
| `http://localhost:800X/` | `/proxy/{module_key}/` |
| `Location: /ui/...` (リダイレクト) | `Location: /proxy/{module_key}/ui/...` |

---

## 11. インフラ・デプロイメント

### 11.1 Cloudflare Tunnel ドメインマッピング

| ドメイン | モジュール | ポート |
|---------|-----------|--------|
| `app.tsp-aitrademanagement.com` | platform-core | 8000 |
| `validation.tsp-aitrademanagement.com` | ai_validation | 8011 |
| `classification.tsp-aitrademanagement.com` | ai_classification | 8002 |
| `rnd.tsp-aitrademanagement.com` | rnd_assessment | 8003 |
| `patent.tsp-aitrademanagement.com` | patent_search | 8004 |
| `screening.tsp-aitrademanagement.com` | screening | 8005 |
| `hs.tsp-aitrademanagement.com` | hs_classifier | 8006 |
| `dap.tsp-aitrademanagement.com` | dap | 8010 |

### 11.2 起動スクリプト

```bash
./start.sh                    # 全モジュール + Cloudflare Tunnel 起動
./start.sh --dev              # 開発モード（uvicorn --reload）
./start.sh --stop             # 全停止
./start.sh --tunnel-status    # トンネル状態確認
./start.sh --restart-tunnel   # アプリは止めずトンネルのみ再起動
```

### 11.3 プロセス管理

- 各モジュールは独立した uvicorn プロセスとして起動
- ログ: `/tmp/{module_key}.log`
- Cloudflare Tunnel ログ: `/tmp/cloudflared.log`
- PID ファイル: `/tmp/{module_key}.pid`

### 11.4 DB マイグレーション

```bash
# platform-core (Alembic)
cd platform-core && alembic upgrade head

# 各モジュール (SQLAlchemy create_all)
# 起動時に自動実行 (SQLite)
```

**Alembic マイグレーション履歴（platform-core）**:

| リビジョン | 内容 |
|-----------|------|
| `a1b2c3d4e5f6` | 初期スキーマ |
| `...` | (中略) |
| `g5h6i7j8k9l0` | Phase 4: ダッシュボード org フィルタ |
| `h6i7j8k9l0m1` | Phase 5: FTA origin_country + regulatory_changes relevant_org_ids |

---

## 12. 環境変数・設定

### 12.1 `.env` 全項目

```bash
# ── データベース ──
PLATFORM_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/platform_db
VALIDATION_DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/platform_db
CLASSIFICATION_DATABASE_URL=sqlite:///./classification.db
RND_DATABASE_URL=sqlite:///./rnd.db
PATENT_DATABASE_URL=sqlite:///./patent.db
DAP_DATABASE_URL=sqlite:///./dap.db
SCREENING_DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/platform_db

# ── 内部サービス認証 ──
INTERNAL_SERVICE_KEY=your-internal-key-min-32-chars-here
PLATFORM_CORE_URL=http://localhost:8000

# ── JWT ──
JWT_SECRET_KEY=your-secret-key-min-32-chars-here
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# ── OAuth2 (SSO) ──
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
MICROSOFT_CLIENT_ID=
MICROSOFT_CLIENT_SECRET=
MICROSOFT_TENANT_ID=
MICROSOFT_REDIRECT_URI=http://localhost:8000/auth/microsoft/callback

# ── LLM ──
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...          # フォールバック
DEFAULT_LLM_PROVIDER=anthropic
DEFAULT_LLM_MODEL=claude-sonnet-4-6

# ── BigQuery (特許検索) ──
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
BQ_PROJECT_ID=your-gcp-project

# ── ストレージ ──
STORAGE_BACKEND=local
STORAGE_LOCAL_PATH=./storage
# AWS_ACCESS_KEY_ID=
# AWS_SECRET_ACCESS_KEY=
# S3_BUCKET_NAME=

# ── モジュール URL ──
MODULE_AI_VALIDATION_URL=http://localhost:8011
MODULE_AI_CLASSIFICATION_URL=http://localhost:8002
MODULE_RND_ASSESSMENT_URL=http://localhost:8003
MODULE_PATENT_SEARCH_URL=http://localhost:8004
MODULE_SCREENING_URL=http://localhost:8005

# ── プラットフォーム ──
PLATFORM_ENV=development
PLATFORM_LOG_LEVEL=INFO
```

---

## 13. 開発規約

### 13.1 コーディング規約

- 過剰な抽象化・将来拡張のためのコードは書かない
- エラーハンドリングは外部 API・DB 境界のみ
- 新機能は既存ファイルへの追記を優先（新ファイル作成は最小限）
- コメントは "なぜ" が非自明な場合のみ（1行まで）

### 13.2 コミット規約

```
feat: <機能概要>
fix: <修正概要>
docs: <ドキュメント更新>
refactor: <リファクタリング>

# 末尾に必須:
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

### 13.3 新規モジュール追加手順

1. `/modules/{new_module}/` ディレクトリ作成
2. `app/main.py` で `ModuleInfo` 定義 + `/health` エンドポイント実装
3. `start.sh` に uvicorn 起動行追加
4. `.env` / `.env.example` に `MODULE_{NAME}_URL=http://localhost:{port}` 追加
5. `platform-core/routers/proxy.py` の `_MODULE_PORTS` にエントリ追加
6. 起動時に `POST /internal/modules/register` で自動登録

### 13.4 開発完了時チェックリスト

```
□ ./start.sh --restart-tunnel  (最新コードを外部公開)
□ https://app.tsp-aitrademanagement.com で動作確認
□ MEMORY.md を更新 (dev_status_p3.md)
□ URL 整合性チェック (ハードコード 8001 等が残っていないか)
□ .env に新規環境変数が揃っているか
□ main.py に新規ルーターが登録されているか
□ DB マイグレーションが適用されているか
□ ROADMAP.md の完了タスクを ✅ に更新
```

---

*本仕様書は 2026-05-08 時点のコードベースに基づいて作成されました。*  
*以降の変更は `ROADMAP.md` および `MEMORY.md` を参照してください。*
