# 3システム連携 開発計画書
## AI_TM × ERP × CRM 統合実装プラン

**作成日**: 2026-08-15  
**対象設計書**: `AI_TM_連携引き継ぎ書.md`（2,195行）  
**担当**: AI_TM 開発チーム  
**ステータス**: 実装着手前レビュー完了

---

## 1. 設計書レビューサマリー

### 1.1 設計書の品質評価

| 観点 | 評価 | 備考 |
|------|------|------|
| API仕様の完成度 | ◎ | IF-01〜IF-24 全て JSON例付きで完備 |
| DB DDL | ◎ | 13テーブルの CREATE TABLE + ALTER TABLE 完備 |
| 認証設計 | ○ | HMAC-SHA256 + タイムスタンプ検証の仕様明確 |
| 工数見積 | △ | 設計書139pd → 現状コードベース考慮後 **144pd**（後述） |
| ERP回帰リスク | △ | 明示されているが回帰テスト手順が未定義 |
| SQLite制約 | × | Phase 4（ライセンスクォータ）の行レベルロック要件が現行SQLite構成と非整合 |

### 1.2 コードベース現状との最大ギャップ

設計書が「追加」と想定している箇所の多くが、現状では **ゼロから実装** となる。

| 機能 | 設計書の想定 | 現状 | 実態 |
|------|-------------|------|------|
| HMAC署名 | 既存Bearerに追加 | Bearer Tokenのみ、HMAC皆無 | 全モジュール横断で新規実装 |
| Webhook Dispatcher | 拡張 | ERP向けのみ、リトライ・DLQ無し | 完全新規 |
| Party Registry | 新規 | テーブル・ロジック一切なし | 完全新規（最大リスク） |
| compliance_override | 新規 | テーブル・ロジック一切なし | 完全新規 |
| ライセンスクォータ | 拡張 | 申請管理のみ、枠管理なし | 完全新規 + DB移行問題あり |
| テナントマッピング | 拡張 | `plat_tenant`テーブルは存在 | `settings` JSONを活用可能 |

---

## 2. ギャップ詳細分析（Phase別）

### Phase 0 — 基盤整備（設計書見積: 18pd）

#### A0-1: テナントマッピング
**現状**: `plat_tenant`（PostgreSQL）に `id`, `slug`, `settings(JSON)` 存在  
**不足**: `crm_tenant_id`, `erp_tenant_code`, `crm_signing_secret`, `erp_signing_secret` フィールド  
**推奨実装**: Alembicマイグレーションで4カラム追加（既存`settings`JSONへの詰め込みは避ける。型安全と検索性のため専用カラムが優位）

```sql
-- alembic migration
ALTER TABLE plat_tenant ADD COLUMN crm_tenant_id VARCHAR(64);
ALTER TABLE plat_tenant ADD COLUMN erp_tenant_code VARCHAR(64);
ALTER TABLE plat_tenant ADD COLUMN crm_signing_secret VARCHAR(256);
ALTER TABLE plat_tenant ADD COLUMN erp_signing_secret VARCHAR(256);
```

#### A0-2: HMAC受信認証
**現状**: コードベース全体に `hmac`, `X-Signature`, `inbound_auth` の記述ゼロ  
**不足**: 全受信エンドポイントのHMAC検証  
**推奨実装**: `platform-core/platform_core/auth/hmac.py` に共有ユーティリティ実装。各モジュールは `from platform_core.auth.hmac import verify_hmac_signature` を使用

```python
# platform_core/auth/hmac.py（新規）
import hashlib, hmac, time

def verify_hmac_signature(body: bytes, signature: str, secret: str, 
                           timestamp: str, tolerance_sec: int = 300) -> bool:
    # タイムスタンプ検証（リプレイ攻撃防止）
    if abs(time.time() - int(timestamp)) > tolerance_sec:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, 
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

**注意**: CRMからの受信（IF-01, IF-02, IF-05等）には必須。ERP既存フローへの影響なし（ERP側は既存Bearerを継続）

#### A0-3: `review_type` カラム追加
**現状**: ai_validation SQLite `transaction` テーブルに `review_type` 無し  
**必要カラム**: `review_type`, `parent_case_no`（文字列型。現存の`parent_transaction_id`は整数FK → **別途維持**）, `review_key_hash`, `valid_until`, `inherited_from`, `revision`, `crm_quote_id`, `crm_contract_id`, `crm_engagement_id`

**SQLite ALTER TABLE の制約**:
- SQLite は NOT NULL + DEFAULT なしカラムの追加は既存データが空でないと失敗
- すべてのカラムは `nullable=True` またはデフォルト値付きで追加すること

#### A0-4〜A0-7: Webhook Dispatcher（完全新規）
**現状**: CRMへのWebhook送信コード皆無。ERP向けも単純HTTP POSTのみ  
**必要**: 配信テーブル + 指数バックオフリトライ + DLQ + 手動再送UI + HMAC署名付与

**推奨実装場所**: `platform-core` PostgreSQL（理由: SQLiteでは並行リトライ管理が不安定）

```sql
-- platform-core Alembic で追加
CREATE TABLE webhook_endpoint (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES plat_tenant(id),
    target_system VARCHAR(20) NOT NULL,  -- 'crm' | 'erp'
    url TEXT NOT NULL,
    signing_secret VARCHAR(256) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE webhook_delivery (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    endpoint_id UUID REFERENCES webhook_endpoint(id),
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',  -- pending/delivered/failed/dlq
    attempt_count INTEGER DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    delivered_at TIMESTAMPTZ
);
```

---

### Phase 1 — 2段階審査（設計書見積: 23pd）

**依存**: A0-3完了後

**IF-01（仮審査作成）実装ポイント**:
- `source_module='crm'` ブランチ追加。**ERP既存の`source_module='erp'`/`'manual'`ブランチを絶対に変更しない**
- `case_no` フォーマット: `CRM-PROV-{YYYYMM}-{SEQ6}` (provisional) / `CRM-FORM-{YYYYMM}-{SEQ6}` (formal)
- `review_key_hash` 生成: `hashlib.sha256(sorted_product_codes + quantities + dest_country + end_user_party_id + end_use + value_bucket)` — 全フィールドをソートして連結してからハッシュ

**IF-02（正式審査作成）実装ポイント**:
- `review_key_hash` 一致 + `valid_until` 未到来 → 仮審査結果を継承（`inherited_from` セット）して承認
- ハッシュ不一致 or 有効期限切れ → 全工程再審査

**ERP回帰リスク管理**（RED FLAG）:
- Phase 1実装前に `tests/test_erp_regression.py` を作成すること
- `POST /api/transactions?source=erp` の既存動作をすべてカバーするE2Eテストを先行実装
- CI/CDに組み込んでからPhase 1コードをマージ

---

### Phase 2 — Party Registry / 名寄せ（設計書見積: 29pd → 実態: 35pd）

**依存**: A0-2完了後  
**最重要アーキテクチャ判断** → §3参照

**新規テーブル（platform-core PostgreSQL推奨）**:
```sql
CREATE TABLE party (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES plat_tenant(id),
    legal_name TEXT NOT NULL,
    country_code CHAR(2),
    party_type VARCHAR(20),  -- 'company' | 'individual' | 'gov'
    risk_score NUMERIC(4,3),
    sanction_status VARCHAR(20),
    last_screened_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE party_identifier (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    party_id UUID REFERENCES party(id),
    system VARCHAR(20) NOT NULL,  -- 'crm' | 'erp' | 'aitm' | 'duns'
    external_id VARCHAR(128) NOT NULL,
    UNIQUE(system, external_id)
);

CREATE TABLE party_merge_candidate (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    party_a_id UUID REFERENCES party(id),
    party_b_id UUID REFERENCES party(id),
    score NUMERIC(4,3),
    status VARCHAR(20) DEFAULT 'pending',  -- pending/merged/rejected
    reviewer_id UUID,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

**名寄せ処理フロー**:
1. スコア ≥ 0.95 → 自動マージ（`party_identifier` にID追加）
2. 0.85〜0.95 → `party_merge_candidate` に登録、人手レビューUI表示
3. < 0.85 → 新規 `party` 作成

**スクリーニングモジュール変更**:
- `POST /api/screen` に `party_ref: {crm_account_id, legal_name, country}` を追加受付（後方互換: `company_name` も維持）
- レスポンスに `aitm_party_id` 追加
- `enable_watch=true` で `monitoring_subscription` レコード作成

---

### Phase 3 — オーバーライド管理強化（設計書見積: 15pd）

**依存**: Phase 1完了後

**現状**: UIにオーバーライド入力があるが `compliance_override` テーブルなし  
**不足**: 必須有効期限・承認者（役職+メール）・理由（最低文字数）・スコープ・証跡書類・部門長承認フロー

```sql
-- ai_validation SQLite に追加
CREATE TABLE compliance_override (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER REFERENCES transaction(id),
    overridden_by VARCHAR(128) NOT NULL,
    approver_name VARCHAR(128) NOT NULL,
    approver_title VARCHAR(128) NOT NULL,
    approver_email VARCHAR(256) NOT NULL,
    reason TEXT NOT NULL CHECK(length(reason) >= 50),
    scope VARCHAR(20) NOT NULL,
    valid_until DATETIME NOT NULL,
    evidence_path TEXT,
    department_head_approval BOOLEAN DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**ビジネスルール**:
- `rejected (match)` の場合は `department_head_approval=TRUE` が必須
- 仮審査オーバーライドは正式審査に継承されない（正式審査で再審査・再承認）

---

### Phase 4 — ライセンスクォータ管理（設計書見積: 25pd → 実態: 30pd）

**依存**: Phase 2完了後（`party_id` 参照のため）  
**重大問題**: **行レベルロック不可（SQLite制限）**

#### SQLite vs PostgreSQL 判断

現在の `export_license` モジュールは SQLite。設計書の要件（同時アロケーション競合防止）はSQLiteのテーブルロックでは不十分。

**推奨**: `export_license` モジュールに PostgreSQL 接続を追加する（platform-coreと同じDB）

```python
# modules/export_license/app/db/session.py を PostgreSQL 対応に変更
# 既存SQLiteデータのマイグレーションスクリプトも作成
```

**新規テーブル**:
```sql
-- PostgreSQL (platform-core DB または export_license 専用DB)
CREATE TABLE export_license_quota (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    license_no VARCHAR(64) UNIQUE NOT NULL,
    product_code VARCHAR(64),
    eccn VARCHAR(20),
    destination_country CHAR(2),
    total_value_usd NUMERIC(18,2),
    remaining_value_usd NUMERIC(18,2),
    unit_count INTEGER,
    remaining_unit INTEGER,
    valid_from DATE NOT NULL,
    valid_until DATE NOT NULL,
    status VARCHAR(20) DEFAULT 'active'
);

CREATE TABLE license_allocation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    license_id UUID REFERENCES export_license_quota(id),
    transaction_id VARCHAR(64) NOT NULL,
    allocated_value_usd NUMERIC(18,2),
    allocated_unit INTEGER,
    status VARCHAR(20) DEFAULT 'provisional',  -- provisional/confirmed/cancelled
    allocated_at TIMESTAMPTZ DEFAULT now(),
    confirmed_at TIMESTAMPTZ,
    CONSTRAINT chk_positive_value CHECK(allocated_value_usd > 0)
);
```

**行レベルロック実装（PostgreSQL）**:
```sql
-- アロケーション時
SELECT * FROM export_license_quota WHERE id = $1 FOR UPDATE;
-- remaining_value_usd の減算
UPDATE export_license_quota SET remaining_value_usd = remaining_value_usd - $amount WHERE id = $1;
```

---

### Phase 5 — 継続モニタリング（設計書見積: 29pd → 実態: 25pd）

**依存**: Phase 2（`party_id`参照）+ Phase 1（`transaction`の`valid_until`基準）

```sql
CREATE TABLE monitoring_subscription (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_type VARCHAR(20) NOT NULL,  -- 'party' | 'transaction'
    subject_id UUID NOT NULL,
    trigger_type VARCHAR(30) NOT NULL,  -- 'sanction_change' | 'contract_end' | etc.
    monitor_until DATE,
    is_active BOOLEAN DEFAULT TRUE,
    created_from_if VARCHAR(10),  -- 'IF-01' | 'IF-02'
    created_at TIMESTAMPTZ DEFAULT now()
);
```

バックグラウンドワーカー: `platform-core` の APScheduler で日次実行。モニタリング対象のスクリーニング再実行 → 変化検知時に Webhook Dispatcher 経由でCRM/ERPに通知（IF-22/23/24）。

---

## 3. 実装前のアーキテクチャ決定事項

以下は **コードを1行も書く前に** 決定が必要な事項。実装開始後の変更は大幅なリワークを引き起こす。

### 決定事項 A: Party Registry の配置場所

| 選択肢 | メリット | デメリット |
|--------|---------|-----------|
| **platform-core PostgreSQL（推奨）** | テナント管理と同一DB、JOINが可能、PostgreSQLのACID保証 | platform-coreの責務拡大 |
| screening SQLite | モジュール独立性 | 行レベルロック不可、他モジュールから参照困難 |

**推奨: platform-core PostgreSQL。** `party`, `party_identifier`, `party_merge_candidate` を `plat_` プレフィックスで platform-core Alembic 管理下に置く。

### 決定事項 B: Webhook Dispatcher の配置場所

| 選択肢 | メリット | デメリット |
|--------|---------|-----------|
| **platform-core（推奨）** | 全モジュール共用、一元管理、CRM/ERP両対応 | platform-coreへの依存追加 |
| ai_validation 内蔵 | 依存なし | 他モジュールから使えない |

**推奨: platform-core に `WebhookDispatcher` サービスクラスを実装。** 各モジュールは HTTP 経由（`POST /internal/webhooks/dispatch`）で呼び出し、または直接インポート。

### 決定事項 C: HMAC 検証の適用範囲

CRM由来のエンドポイントにのみ適用するか、全受信エンドポイントに適用するか。

**推奨**: CRM受信エンドポイント（IF-01, IF-02, IF-05, IF-08等）に `@require_crm_hmac` デコレータを適用。ERP既存エンドポイントは変更しない（Bearer維持）。段階的にERPもHMACへ移行予定ならば§12に記載。

### 決定事項 D: export_license モジュールのDB移行

現行SQLite → PostgreSQL移行。既存データ（ライセンス申請データ）の移行スクリプトが必要。

**推奨**: platform-core の PostgreSQL DB に `el_` プレフィックスのテーブルとして統合。`export_license` モジュールの SQLite 接続を廃止。ただし開発工数に **+5pd** 追加計上。

### 決定事項 E: `case_no` の一意制約範囲

現在の `UNIQUE(case_no)` はシステム全体で一意。設計書では `UNIQUE(org_id, case_no)` にしてテナントスコープ化を要求。

**推奨**: マルチテナント本番運用まではグローバル一意のまま維持。CRM統合フェーズではプレフィックス（`CRM-PROV-`, `CRM-FORM-`）で衝突回避。テナントスコープ化は Phase 2 と同時に実施。

---

## 4. リスク評価マトリクス

| # | リスク | 深刻度 | 発生確率 | 対策 |
|---|--------|--------|---------|------|
| R-1 | **ERP既存フロー回帰** | 🔴 高 | 中 | Phase 1前にE2E回帰テスト整備必須 |
| R-2 | **SQLite並行ロック（ライセンス）** | 🔴 高 | 高 | Phase 4前にPostgreSQL移行確定 |
| R-3 | **Party Registry 設計判断遅延** | 🟠 中 | 中 | Phase 2着手前に決定事項A確定 |
| R-4 | **名寄せスコア閾値の誤設定** | 🟠 中 | 低 | ステージング環境で実データ試験 |
| R-5 | **Webhook DLQ 増加** | 🟡 低 | 中 | 監視アラート + 手動再送UI |
| R-6 | **review_key_hash 不一致** | 🟡 低 | 中 | ハッシュ生成ロジックの単体テスト必須 |
| R-7 | **30日有効期限の境界バグ** | 🟡 低 | 中 | UTCで統一、DST考慮 |
| R-8 | **仮審査オーバーライド誤継承** | 🔴 高 | 中 | 設計書§13の注記通り、継承ロジックに明示的フラグ |

---

## 5. 改訂開発計画

### 5.1 工数サマリー

| Phase | 設計書 | 実態修正 | 差異理由 |
|-------|--------|---------|---------|
| Phase 0（基盤） | 18pd | **22pd** | HMAC全モジュール対応 +4pd |
| Phase 1（2段階） | 23pd | **20pd** | 既存`source_module`流用でやや短縮 |
| Phase 2（Party Registry） | 29pd | **35pd** | 完全新規+名寄せUI +6pd |
| Phase 3（Override） | 15pd | **12pd** | 既存UI活用で短縮 |
| Phase 4（ライセンス） | 25pd | **30pd** | DB移行スクリプト +5pd |
| Phase 5（モニタリング） | 29pd | **25pd** | Webhook Dispatcher再利用で短縮 |
| **合計** | **139pd** | **144pd** | |

### 5.2 実装順序と依存グラフ

```
[A0-1] テナントマッピング
    ↓
[A0-2] HMAC共有ユーティリティ ────────────────────────────────────┐
    ↓                                                              ↓
[A0-3] review_type/review_key_hash カラム追加               [Phase 2] Party Registry
    ↓                                                              ↓
[A0-4〜7] Webhook Dispatcher                               [Phase 3] Override強化
    ↓                                                              ↓
[Phase 1] 2段階審査（IF-01/02）                          [Phase 4] ライセンスクォータ
    ↓                                                              ↓
[ERP回帰テスト整備] ← Phase 1と並行                       [Phase 5] 継続モニタリング
```

**並行実施可能なもの**:
- A0-2（HMAC）と A0-4〜7（Webhook Dispatcher）は並行実施可
- Phase 3（Override）と Phase 4（ライセンス）は Phase 2完了後に並行可

### 5.3 フェーズ別タスクリスト

#### Phase 0 — 基盤整備（22pd）

| タスク | ファイル | 工数 |
|--------|---------|------|
| A0-1: `plat_tenant` に CRM/ERP マッピングカラム追加（Alembic） | `platform-core/alembic/versions/` | 1pd |
| A0-2: `platform_core/auth/hmac.py` 共有ユーティリティ | 新規 | 1pd |
| A0-2: 全受信エンドポイントに `@require_crm_hmac` 適用 | 各モジュール router | 3pd |
| A0-3: `transaction` テーブル 9カラム追加（SQLite migration） | `ai_validation/app/db/models/transaction.py` | 2pd |
| A0-3: `case_no` 生成ロジック（CRM用プレフィックス対応） | `api_transactions.py` | 1pd |
| A0-3: `review_key_hash` 生成ユーティリティ | `ai_validation/app/utils/` | 1pd |
| A0-4: `webhook_endpoint` + `webhook_delivery` テーブル（Alembic） | `platform-core/alembic/versions/` | 1pd |
| A0-5: `WebhookDispatcher` サービス実装（送信 + HMAC署名） | `platform_core/services/webhook.py` | 3pd |
| A0-6: リトライワーカー（APScheduler、指数バックオフ + ジッター） | `platform_core/workers/webhook_retry.py` | 3pd |
| A0-7: DLQ管理 + 手動再送UI | `platform_core/routers/webhook_mgmt.py` | 3pd |
| A0-8: README ポート修正（8001 → 8011） | `README.md` | 0.1pd |
| ERP回帰テスト先行作成 | `tests/test_erp_regression.py` | 3pd |

#### Phase 1 — 2段階審査（20pd）

| タスク | ファイル | 工数 |
|--------|---------|------|
| IF-01: `POST /api/crm/provisional-review` | `ai_validation/app/routers/crm_review.py` (新規) | 4pd |
| IF-02: `POST /api/crm/formal-review` | 同上 | 4pd |
| IF-03: `GET /api/crm/review-status/{case_no}` | 同上 | 1pd |
| review_key_hash 比較・継承ロジック | `ai_validation/app/services/review_inherit.py` | 3pd |
| valid_until 30日チェック | 同上 | 1pd |
| CRM向け Webhook 送信（IF-15: status_changed）連携 | `ai_validation/app/services/` | 3pd |
| 単体テスト（review_key_hash生成、継承ロジック） | `tests/unit/` | 2pd |
| E2E統合テスト IT-01〜IT-05 | `tests/integration/` | 2pd |

#### Phase 2 — Party Registry / 名寄せ（35pd）

| タスク | ファイル | 工数 |
|--------|---------|------|
| `party`, `party_identifier`, `party_merge_candidate` テーブル（Alembic） | `platform-core/alembic/versions/` | 2pd |
| 名寄せスコアリングロジック（Jaro-Winkler + 国コード） | `platform_core/services/party_resolution.py` | 5pd |
| IF-09: `POST /api/parties/resolve` | `platform_core/routers/parties.py` | 3pd |
| IF-10: `GET /api/parties/{aitm_party_id}` | 同上 | 1pd |
| IF-11: マージ候補UI + 承認API | `platform_core/routers/parties.py` + UI | 5pd |
| スクリーニング: `party_ref` 受け付け対応、`aitm_party_id` レスポンス追加 | `screening/app/routers/screening.py` | 3pd |
| `enable_watch` → `monitoring_subscription` 作成 | `screening` or `platform_core` | 2pd |
| ai_validation: `counterparty_party_id`, `end_user_party_id` 使用 | `ai_validation/app/routers/` | 4pd |
| エンドユーザー独立スクリーニング呼び出し | 同上 | 3pd |
| `case_no` UNIQUE 制約をテナントスコープ化 | `ai_validation/app/db/models/transaction.py` | 2pd |
| 統合テスト IT-06〜IT-10 | `tests/integration/` | 5pd |

#### Phase 3 — オーバーライド管理強化（12pd）

| タスク | ファイル | 工数 |
|--------|---------|------|
| `compliance_override` テーブル作成（SQLite） | `ai_validation/app/db/models/` | 1pd |
| オーバーライドAPI（作成・取得・失効） | `ai_validation/app/routers/override.py` | 3pd |
| 部門長承認フロー | 同上 | 2pd |
| UI: 必須フィールド追加（有効期限・承認者役職・メール・理由50字以上） | `templates/` | 3pd |
| CRM Webhook送信（IF-17: override_recorded） | `ai_validation/app/services/` | 1pd |
| テスト | `tests/` | 2pd |

#### Phase 4 — ライセンスクォータ管理（30pd）

| タスク | ファイル | 工数 |
|--------|---------|------|
| export_license モジュールを PostgreSQL 接続に移行 | `modules/export_license/app/db/` | 5pd |
| 既存SQLiteデータ移行スクリプト | `scripts/migrate_license_to_pg.py` | 3pd |
| `export_license_quota`, `license_allocation`, `license_consumption` テーブル（Alembic） | `platform-core/alembic/versions/` | 2pd |
| IF-06: `GET /api/licenses/quota-check` | `modules/export_license/app/routers/` | 3pd |
| IF-07: `POST /api/licenses/allocate`（行レベルロック付き） | 同上 | 4pd |
| IF-21: `POST /api/licenses/consumptions`（ERP消費確定） | 同上 | 3pd |
| クォータ残高アラート（80%/100%でWebhook） | `platform_core/workers/` | 3pd |
| 統合テスト（並行アロケーション競合テスト必須） | `tests/integration/` | 5pd |
| 管理UI（クォータ一覧・残高グラフ） | `templates/` | 2pd |

#### Phase 5 — 継続モニタリング（25pd）

| タスク | ファイル | 工数 |
|--------|---------|------|
| `monitoring_subscription` テーブル（Alembic） | `platform-core/alembic/versions/` | 1pd |
| 日次モニタリングワーカー（APScheduler） | `platform_core/workers/monitoring.py` | 5pd |
| 制裁リスト変化検知ロジック | 同上 | 4pd |
| IF-22/23/24 Webhook送信（Dispatcher経由） | 同上 | 3pd |
| 契約終了日モニタリング（`valid_until` ベース） | 同上 | 2pd |
| モニタリングサブスクリプション管理UI | `templates/` | 3pd |
| 統合テスト RT-01〜RT-05（回帰テスト含む） | `tests/` | 5pd |
| ドキュメント更新 | `docs/` | 2pd |

---

## 6. テスト戦略

### 6.1 回帰テスト（Phase 1着手前に必須）

```python
# tests/test_erp_regression.py に実装すること
# カバーすべきシナリオ:
# RT-01: ERP -> POST /api/transactions (source_module='erp') 正常系
# RT-02: ERP -> GET /api/transactions/{case_no}/status 正常系
# RT-03: ERP からの status webhook 受信（Bearerトークン認証）
# RT-04: 複数品目トランザクション（products配列）
# RT-05: approved → ERP へのステータスWebhook送信
```

### 6.2 統合テストの実行順序

設計書§12のIT-01〜IT-20を以下の順で実施:

1. **IT-01〜05**: 2段階審査基本フロー（Phase 1完了後）
2. **IT-06〜10**: Party Registry + 名寄せ（Phase 2完了後）
3. **IT-11〜15**: ライセンスクォータ（Phase 4完了後）
4. **IT-16〜20**: モニタリング + E2E（Phase 5完了後）

### 6.3 セキュリティテスト

- HMAC リプレイ攻撃テスト（タイムスタンプ±5分超過）
- HMAC 署名改ざんテスト
- `review_key_hash` 衝突テスト（異なる内容で同一ハッシュ != 正当）
- 並行アロケーション競合テスト（10スレッド同時POSTでクォータ超過しないこと）

---

## 7. 環境変数追加リスト

設計書§10に加えて、現状`.env`に不足している変数:

```bash
# テナントマッピング（plat_tenant設定後に各テナントへ）
CRM_TENANT_ID=crm-tenant-abc123
ERP_TENANT_CODE=ERP-001

# CRM Webhook署名検証
CRM_INBOUND_SIGNING_SECRET=crm-secret-xxxxx

# CRM Webhook送信先（パスは env var で管理、ハードコード禁止）
CRM_WEBHOOK_BASE_URL=https://crm.example.com
CRM_WEBHOOK_PATH_REVIEW_UPDATED=/webhooks/aitm/review-updated
CRM_WEBHOOK_PATH_SCREENING_ALERTED=/webhooks/aitm/screening-alert
CRM_WEBHOOK_PATH_OVERRIDE_RECORDED=/webhooks/aitm/override-recorded
CRM_WEBHOOK_PATH_LICENSE_CHANGED=/webhooks/aitm/license-changed
CRM_WEBHOOK_PATH_MONITOR_ALERTED=/webhooks/aitm/monitor-alert

# ライセンスクォータ（PostgreSQL移行後）
EXPORT_LICENSE_DB_URL=postgresql://...
QUOTA_ALERT_THRESHOLD_PCT=80
```

---

## 8. 既知の技術的負債（実装前確認事項）

| # | 負債 | 対処タイミング |
|---|------|--------------|
| TD-1 | README のポート記述 `8001` → 実際は `8011` | Phase 0 Day 1（即時修正） |
| TD-2 | `parent_transaction_id` は整数FK。設計書は文字列 `parent_case_no` | A0-3 で文字列カラムを追加し、整数FKは廃止予定フラグ |
| TD-3 | `counterparty_name` 文字列。`counterparty_party_id` への移行 | Phase 2（並行維持 → 廃止） |
| TD-4 | `end_user_name`/`end_user_country` 文字列。`end_user_party_id` への移行 | Phase 2 |
| TD-5 | `_push_erp_status()` はリトライ・DLQ なし | A0-7 完了後に Dispatcher 経由に切り替え |
| TD-6 | `UNIQUE(case_no)` がグローバルスコープ | Phase 2 で `UNIQUE(org_id, case_no)` に変更 |

---

## 9. 推奨 着手アクション（次の5営業日）

### Day 1-2: アーキテクチャ決定
- [ ] §3の決定事項 A〜E を全員で合意・記録
- [ ] Party Registry の配置場所を確定
- [ ] export_license の PostgreSQL 移行可否を確定
- [ ] README ポート修正（30分作業）

### Day 3: 基盤実装開始
- [ ] `platform_core/auth/hmac.py` 実装（A0-2）
- [ ] ERP回帰テスト先行作成開始（Phase 1着手条件）

### Day 4-5: Alembic マイグレーション準備
- [ ] `plat_tenant` に CRM/ERP マッピングカラム追加
- [ ] Webhook Dispatcher 用テーブル設計最終確認
- [ ] ai_validation SQLite に `review_type` 等のカラム追加スクリプト確認（既存データへの影響テスト）

---

## 10. 参照ドキュメント

| ドキュメント | 場所 | 用途 |
|------------|------|------|
| 3システム連携引き継ぎ書 | `AI_TM_連携引き継ぎ書.md` | 全 IF/API/DDL の仕様原本 |
| CRM連携引き継ぎ書 | `docs/CRM_INTEGRATION_HANDOVER.md` | CRM開発チーム向け |
| 運用ユーザーガイド | `docs/USER_GUIDE_OPERATIONS.md` | 業務担当者向け |
| ROADMAP | `ROADMAP.md` | 全体開発ロードマップ |
| CLAUDE.md | `CLAUDE.md` | 開発規約 |

---

*本計画書は設計書レビュー時点（2026-08-15）の現状コードベース調査に基づく。各 Phase 着手前に最新状態を再確認すること。*
