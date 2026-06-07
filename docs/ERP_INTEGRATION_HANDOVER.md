# ERP システム — AI Trade Management 連携引き継ぎ書

**作成日**: 2026-06-03  
**最終更新**: 2026-06-07  
**文書バージョン**: ERP-AITM v2.5  
**作成者**: ERPシステム開発チーム / AI Trade Management 開発チーム（共同改訂）  
**対象 ERP バージョン**: Mini Global ERP Phase 1-3 (AI_TM Integration)  
**対応 AI_TM バージョン**: AI Trade Management Platform **v2.5**

> **v2.5 主な変更点**: `POST /api/transactions` に ERP 連携フィールド追加 (`erp_case_no`, `product_code` 他 11 項目)。`GET /api/transactions/{id}` に正規化 `judgment` フィールド追加。`GET /api/transactions/search` 新設。

---

## 1. システム構成概要

### ERP 内部アーキテクチャ（AI_TM 連携関連）

```
ERP 内部モジュール
┌─────────────────────────────────────────────────────────┐
│  SD (Sales & Distribution)                              │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────┐ │
│  │SalesOrderSvc │  │ DeliveryService│  │BillingService│ │
│  └──────┬───────┘  └───────┬────────┘  └──────┬──────┘ │
│         │                  │                   │        │
│  GTS (Global Trade Compliance)                 │        │
│  ┌──────▼──────────────────▼──────────────────▼──────┐  │
│  │                  GTSService                        │  │
│  │  transaction_review()  ← SO 作成時                 │  │
│  │  shipment_rescreen()   ← Delivery 作成時           │  │
│  └─────────────────────┬──────────────────────────────┘  │
│                         │                               │
│  ┌──────────────────────▼──────────────────────────────┐ │
│  │        AI_TM Integration Client                      │ │
│  │  app/integrations/ai_trade_management/client.py     │ │
│  │  ┌──────────────┐  ┌────────────────┐               │ │
│  │  │ _HttpClient  │  │  _MockClient   │               │ │
│  │  │ (Live接続)   │  │ (テスト用)     │               │ │
│  │  └──────┬───────┘  └────────────────┘               │ │
│  └─────────┼───────────────────────────────────────────┘ │
└────────────┼──────────────────────────────────────────────┘
             │ REST API  (HTTP/JSON)
             ▼
┌────────────────────────────────────────────────────────────┐
│            AI Trade Management Platform v2.5               │
│                                                            │
│  :8002 ai_classification  (品目管理・HS/ECCN分類)          │
│  :8005 screening          (制裁リストスクリーニング)        │
│  :8011 ai_validation      (取引審査・AI判定)               │
│  :8012 export_license     (輸出許可申請管理)               │
└────────────────────────────────────────────────────────────┘
```

### 接続先 URL

| AI_TM モジュール | ローカル | Cloudflare Tunnel | 用途 |
|---|---|---|---|
| ai_classification | `http://localhost:8002` | `https://classification.tsp-aitrademanagement.com` | 品目登録・BOM同期 |
| screening | `http://localhost:8005` | `https://screening.tsp-aitrademanagement.com` | 制裁スクリーニング |
| ai_validation | `http://localhost:8011` | `https://validation.tsp-aitrademanagement.com` | 取引審査・再審査 |
| export_license | `http://localhost:8012` | — | 輸出許可申請管理（将来連携） |

ERP 設定ファイル: `app/integrations/ai_trade_management/client.py`  
設定値: 環境変数 `AITM_CLASSIFICATION_URL` / `AITM_SCREENING_URL` / `AITM_VALIDATION_URL`

---

## 2. 認証方式

### 2-1. ERP → AI_TM（送信側・アウトバウンド）

```http
X-Organization-Id: {org_id}
X-User-Id: erp-system@company.com
Content-Type: application/json
```

- Bearer Token 不要（イントラネット同一組織内通信）
- `X-Organization-Id`: AI_TM 側テナント ID（環境変数 `AITM_ORG_ID` で管理）
- `X-User-Id`: ERP システムのサービスアカウントメールアドレス

### 2-2. AI_TM → ERP（受信側・インバウンド Webhook）

```http
POST /api/gts/webhook/judgment-updated
Authorization: Bearer {AITM_WEBHOOK_SECRET}
Content-Type: application/json
```

- ERP 側で `AITM_WEBHOOK_SECRET` 環境変数を設定し、Bearer トークン検証を行う
- 未実装の場合は Webhook を受け付けない（ポーリング方式でフォールバック）

---

## 3. ERP から AI_TM へ送るインターフェース（アウトバウンド）

### 3-1. 品目登録・マスター同期

**呼び出しタイミング**: MDM モジュールで材料マスター登録・更新時  
**エンドポイント**: `POST :8002/products/erp-sync`

#### リクエスト

```json
[
  {
    "code": "CTRL-HC200",
    "name": "High-Capacity DRAM Controller v2",
    "eccn": "3A001.b.1",
    "hs_code": "8542.39.00",
    "item_type": "component",
    "bom": [
      {
        "child_code": "COMP-DRAM-DDR5",
        "child_name": "DDR5 DRAM Module",
        "quantity": 4,
        "unit_value_usd": 85.0,
        "origin_country": "US",
        "supplier_name": "Micron Technology"
      }
    ]
  }
]
```

#### レスポンス

```json
{"ok": true, "id": 51, "code": "CTRL-HC200", "created": true}
```

#### ERP データソース

| AI_TM フィールド | ERP テーブル | ERP カラム |
|---|---|---|
| `code` | `materials` | `material_code` |
| `name` | `materials` | `description` |
| `eccn` | `materials` | `eccn` |
| `hs_code` | `materials` | `hs_code` |
| `item_type` | `materials` | `item_type` |
| `bom[].child_code` | `bom_components` | `component_code` |
| `bom[].origin_country` | `bom_components` | `country_of_origin` |

#### BOM 更新時の品目バージョン自動記録（v2.5）

BOM コンポーネントの追加・削除が発生すると、AI_TM 側で `item-versions` イベントが自動記録される。  
サプライヤー変更ロット管理・コンプライアンス追跡に使用する。

---

### 3-2. 取引審査案件作成（受注時）

**呼び出しタイミング**: `SalesOrderService.create()` — 受注登録時 (`skip_export_check=False`)  
**実装箇所**: `app/modules/gts/service.py` → `GTSService.transaction_review()`  
**エンドポイント**: `POST :8011/api/transactions`

#### リクエスト（TransactionCreateRequest **v2.5**）

```json
{
  "title": "SO-10300002 / TechCorp GmbH (DE)",
  "counterparty_name": "TechCorp GmbH",
  "destination_country": "DE",
  "source_module": "erp",

  "erp_case_no": "SO-10300002",
  "product_code": "CTRL-HC200",
  "product_name": "High-Capacity DRAM Controller v2",
  "total_value_usd": 85000.00,
  "unit_price_usd": 850.00,
  "quantity": 100,
  "end_user": "Berlin Automation GmbH",
  "end_user_country": "DE",
  "intended_use": "Industrial automation. Destination: DE. Customer: TechCorp GmbH.",
  "hs_code": "8542.39.00",
  "incoterms": "CIF",

  "items": [
    {
      "item_name": "CTRL-HC200",
      "item_description": "High-Capacity DRAM Controller v2"
    }
  ],
  "usage_requirements": []
}
```

> **注**: `product_name` 指定時は `items` 省略可能（AI_TM 側が自動補完する）。  
> `intended_use` は `usage_requirements` として自動登録されるため重複不要。

**フィールド生成ルール**:

| AI_TM フィールド | ERP ソース |
|---|---|
| `title` | `"SO-{document_number} / {customer_name} ({country})"` |
| `counterparty_name` | `BusinessPartner.name` |
| `destination_country` | `BusinessPartner.country` (ISO 3166-1 alpha-2) |
| `erp_case_no` | `SalesOrder.document_number` (ERP 受注番号) |
| `product_code` | `SalesOrderItem.material_code` |
| `product_name` | `SalesOrderItem.description` |
| `total_value_usd` | `SalesOrderItem.net_value_usd` |
| `unit_price_usd` | `SalesOrderItem.unit_price_usd` |
| `quantity` | `SalesOrderItem.quantity` |
| `end_user` | `BusinessPartner.name` (ship-to party) |
| `end_user_country` | `BusinessPartner.country` (ship-to) |
| `intended_use` | `"Industrial automation. Destination: {country}. Customer: {name}."` |
| `hs_code` | `Material.hs_code` |
| `incoterms` | `SalesOrder.incoterms` |

#### レスポンス（TransactionCreateResponse v2.5）

```json
{
  "id": 1234,
  "case_no": "API-20260603-4821",
  "erp_case_no": "SO-10300002",
  "title": "SO-10300002 / TechCorp GmbH (DE)",
  "status": "draft",
  "linked_product_code": "CTRL-HC200",
  "url": "https://validation.tsp-aitrademanagement.com/ui/transactions/1234",
  "screening_queued": true
}
```

> **⚠ case_no フォーマット注意**: AI_TM が自動生成する `case_no` は `API-YYYYMMDD-XXXX` 形式（例: `API-20260603-4821`）。  
> ERP が受注番号（SO 番号）として送った `erp_case_no` とは別フィールド。  
> 再審査時の検索は **必ず `erp_case_no`** を使うこと（後述 §3-3 参照）。

#### ERP 側の保存先

| AI_TM レスポンス | ERP テーブル | ERP カラム |
|---|---|---|
| `case_no` | `sales_orders` | `export_check_ref` |
| `id` | `ai_tm_transaction_links` | `aitm_transaction_id` |
| `erp_case_no` | （送信値、確認用） | — |
| 審査結果（`judgment`） | `sales_orders` | `export_check_status` |

#### ERP SO ステータス制御ロジック

```
AI_TM judgment   →  sales_orders.export_check_status  →  sales_orders.status
─────────────────────────────────────────────────────────────────────────────
APPROVED         →  PASSED                             →  OPEN (通常処理続行)
NEEDS_REVIEW     →  PENDING                            →  BLOCKED (手動確認待ち)
REQUIRES_PERMIT  →  PENDING                            →  BLOCKED (許可申請必要)
REJECTED         →  BLOCKED                            →  BLOCKED (出荷不可)
PENDING          →  PENDING                            →  BLOCKED (AI判定待ち)
Exception        →  ERROR                              →  BLOCKED (要調査)
```

> **注**: 受注作成直後は AI_TM の審査が非同期のため `judgment = PENDING` が返る。  
> ポーリングまたは Webhook で判定完了を検知して SO ステータスを更新する（§4 参照）。

---

### 3-3. 出荷時 取引再審査（納品書作成時）

**呼び出しタイミング**: `DeliveryService.create()` — 納品書(Delivery)作成時  
**実装箇所**: `app/modules/sd/service.py` → `DeliveryService._run_shipment_rescreen()`

#### Step 1: erp_case_no で取引を検索（推奨）

**エンドポイント**: `GET :8011/api/transactions/search?erp_case_no={so_number}`

```
GET /api/transactions/search?erp_case_no=SO-10300002
```

**レスポンス（フラットリスト形式）**:
```json
[
  {
    "id": 1234,
    "case_no": "API-20260603-4821",
    "erp_case_no": "SO-10300002",
    "title": "SO-10300002 / TechCorp GmbH (DE)",
    "status": "reviewed",
    "source_module": "erp",
    "created_at": "2026-06-03"
  }
]
```

> **検索優先順位**: `erp_case_no`（完全一致）→ `q`（case_no/title ILIKE）→ 両方空なら直近20件。  
> `erp_case_no` で検索すると ERP 受注番号と AI_TM 案件が 1:1 で紐付く。  
> AI_TM の `case_no`（`API-YYYYMMDD-XXXX`）を検索するには `?q=API-20260603-4821` を使う。

#### Step 2: 再審査トリガー

**エンドポイント**: `POST :8011/ui/transactions/{id}/run-screening`

```
POST /ui/transactions/1234/run-screening
```

**レスポンス**: HTTP 303 (リダイレクト) = 成功  
**注意**: このエンドポイントは UI 向けのため 303 Redirect が正常応答。ERP クライアントはリダイレクトを追わず 303 を成功と判断する。

#### Step 3: 審査結果取得

**エンドポイント**: `GET :8011/api/transactions/{id}`

**レスポンス（TransactionDetailResponse v2.5）**:
```json
{
  "id": 1234,
  "case_no": "API-20260603-4821",
  "erp_case_no": "SO-10300002",
  "title": "SO-10300002 / TechCorp GmbH (DE)",
  "status": "reviewed",
  "agent_judgment_status": "not_controlled",
  "judgment": "APPROVED",
  "destination_country": "DE",
  "counterparty_name": "TechCorp GmbH",
  "linked_product_code": "CTRL-HC200",
  "items": [
    {"item_name": "CTRL-HC200", "spec_text": "High-Capacity DRAM Controller v2"}
  ],
  "ai_run": {
    "status": "completed",
    "run_type": "matrix_match",
    "finished_at": "2026-06-03T01:05:00"
  },
  "created_at": "2026-06-03T01:00:00",
  "updated_at": "2026-06-03T01:05:00",
  "url": "https://validation.tsp-aitrademanagement.com/ui/transactions/1234"
}
```

#### 正規化 `judgment` フィールド（v2.5 新設）

| AI_TM 内部値 (`agent_judgment_status`) | 正規化値 (`judgment`) | 意味 |
|---|---|---|
| `not_controlled` | `APPROVED` | 輸出規制対象外 → 出荷可 |
| `controlled` | `NEEDS_REVIEW` | ECCN 該当品 → 担当者確認必要 |
| `requires_review` | `NEEDS_REVIEW` | AI 判定要確認 → 担当者確認必要 |
| `requires_permit` | `REQUIRES_PERMIT` | 許可申請必要 → 輸出許可取得まで出荷不可 |
| `null` (未判定) | `PENDING` | AI 審査処理中 |
| `status == "rejected"` | `REJECTED` | 却下済み → 出荷不可 |

#### ERP 側 承認判定ロジック（v2.5 修正版）

```python
# ⚠ CRITICAL: v2.5 で正規化 judgment フィールドを使用すること
# agent_judgment_status の生の値は使わない

BLOCKED_JUDGMENTS = {"REJECTED", "NEEDS_REVIEW", "REQUIRES_PERMIT"}

judgment = tx_detail.get("judgment", "PENDING")  # 正規化フィールド

approved = judgment == "APPROVED"

# 旧コード（v2.4 以前）— 使用禁止
# BLOCKED_JUDGMENTS = {"BLOCKED", "REJECTED", "REQUIRES_PERMIT"}  # ← controlled が漏れる
# agent_judgment = tx_detail.agent_judgment_status  # ← 内部値で判定しない
```

> **⚠ v2.4 以前の判定ロジックの危険性**:  
> `agent_judgment_status = "controlled"` の場合（ECCN 該当品）、旧 `BLOCKED_JUDGMENTS` に含まれないため  
> 出荷可能（APPROVED）として扱われてしまう。必ず正規化 `judgment` フィールドを使用すること。

#### ERP 側の保存先

| AI_TM フィールド | ERP テーブル | ERP カラム |
|---|---|---|
| `case_no` | `deliveries` | `aitm_case_no` |
| `judgment` | — | 承認判定に使用（`approved` ブール値に変換） |
| 承認結果 (`approved`) | `deliveries` | `aitm_approval_status` (`APPROVED`/`BLOCKED`/`ERROR`) |
| `agent_judgment_status` | `ai_tm_shipment_links` | `ai_status` |
| 再審査日時 | `ai_tm_shipment_links` | `rescreen_at` |
| 再審査結果 | `ai_tm_shipment_links` | `rescreen_result` (`PASSED`/`CHANGED`) |

---

### 3-4. 制裁リストスクリーニング（取引先登録時）

**呼び出しタイミング**: `MDMService` — BusinessPartner 新規登録時  
**エンドポイント**: `POST :8005/api/screening/batch`

#### リクエスト（ScreeningBatchRequest）

```json
{
  "entities": [
    {
      "name": "TechCorp GmbH",
      "country": "DE",
      "entity_type": "company"
    }
  ],
  "sources": ["OFAC_SDN", "BIS_ENTITY", "METI_FUL", "EU_CONSOLIDATED"]
}
```

#### レスポンス（ScreeningBatchResponse）

```json
{
  "results": [
    {
      "name": "TechCorp GmbH",
      "status": "no_match",
      "score": 0.12,
      "matched_list": null,
      "matched_entity": null
    }
  ]
}
```

#### ERP 側の判定・保存

| スクリーニング status | ERP 処理 |
|---|---|
| `no_match` | 通常登録 (`is_denied_party = false`) |
| `possible_match` | コンプライアンス担当へ通知、手動確認 |
| `match` / `CRITICAL` | `is_denied_party = true`、受注時に `BusinessRuleError` |

**保存先**: `business_partners.is_denied_party` (BOOLEAN)

---

### 3-5. HS 分類（レガシー / オプション）

> **ステータス**: 現在 ERP フローから自動呼び出しなし。材料マスター登録画面からの手動トリガーのみ。

**エンドポイント**: `POST :8002/api/hs/classify` (レガシー) または `/api/products/classify`

```json
{"description": "DDR5 DRAM Controller", "material_code": "CTRL-HC200"}
```

---

## 4. AI_TM から ERP へ受け取るインターフェース（インバウンド）

### 4-1. 審査完了 Webhook（実装予定）

**ERP 受信エンドポイント**: `POST /api/gts/webhook/judgment-updated`  
**実装ファイル**: `app/modules/gts/router.py` (予定)  
**現在のステータス**: 未実装 — ポーリングで代替中

AI_TM から送信される Webhook ペイロード（期待値）:

```json
{
  "event": "judgment_updated",
  "transaction_id": 1234,
  "case_no": "API-20260603-4821",
  "erp_case_no": "SO-10300002",
  "judgment": "APPROVED",
  "agent_judgment_status": "not_controlled",
  "judged_at": "2026-06-07T10:00:00Z"
}
```

ERP 受信時の処理（実装予定）:
1. `erp_case_no` で `sales_orders.document_number` を検索
2. `judgment` に応じて `export_check_status` 更新
3. `APPROVED` → SO status を `OPEN` に変更 (BLOCKED 解除)
4. `AITMTransactionLink.judgment` 更新

> **注**: Webhook ペイロードの `judgment` は正規化済み値 (`APPROVED`/`NEEDS_REVIEW`/`REQUIRES_PERMIT`/`REJECTED`) を使用する。

---

### 4-2. ポーリング（現在の実装）

`GET :8011/api/transactions/{id}` を定期呼び出しして審査ステータス確認。  
推奨間隔: 30分。実装は `app/modules/gts/service.py` → `GTSService.poll_pending_transactions()` (予定)。

```python
# ポーリング実装例（GTSService.poll_pending_transactions）
pending_sos = db.query(SalesOrder).filter(
    SalesOrder.export_check_status == "PENDING"
).all()
for so in pending_sos:
    tx_id = db.query(AITMTransactionLink).filter_by(
        sales_order_id=so.id
    ).first().aitm_transaction_id
    detail = aitm_client.get_transaction(tx_id)
    judgment = detail.get("judgment", "PENDING")
    if judgment != "PENDING":
        _update_so_from_judgment(so, judgment)
```

---

## 5. ERP データベーステーブルとAI_TM連携カラム

### 5-1. materials テーブル

```sql
CREATE TABLE materials (
    id              INTEGER PRIMARY KEY,
    client_id       VARCHAR(20) NOT NULL,
    material_code   VARCHAR(50) NOT NULL,
    description     VARCHAR(200),
    -- AI_TM 連携カラム
    eccn            VARCHAR(20),     -- ECCN番号 (例: 3A001.b.1)
    hs_code         VARCHAR(20),     -- HSコード (例: 8542.39.00)
    item_type       VARCHAR(30),     -- equipment/component/software/material
    export_control_status VARCHAR(20) DEFAULT 'not_evaluated',
    -- (ai_classification 登録後の product_id は将来保存予定)
    ...
);
```

**AI_TM 同期状態**: `POST :8002/products/erp-sync` で同期  
**値域 `export_control_status`**: `not_evaluated` / `CLEAR` / `dual_use` / `munitions` / `EAR99`

---

### 5-2. business_partners テーブル

```sql
CREATE TABLE business_partners (
    id                    INTEGER PRIMARY KEY,
    client_id             VARCHAR(20) NOT NULL,
    bp_code               VARCHAR(50) NOT NULL,
    name                  VARCHAR(200),
    country               VARCHAR(10),
    -- AI_TM 制裁スクリーニング結果
    is_denied_party       BOOLEAN DEFAULT FALSE,
    -- (スクリーニング日時・マッチリストは将来拡張予定)
    ...
);
```

**制裁スクリーニング実行タイミング**: BP 新規登録時  
**受注時チェック**: `SalesOrderService.create()` → `customer.is_denied_party == True` → `BusinessRuleError`

---

### 5-3. sales_orders テーブル

```sql
CREATE TABLE sales_orders (
    id                    INTEGER PRIMARY KEY,
    client_id             VARCHAR(20) NOT NULL,
    document_number       VARCHAR(20) NOT NULL,  -- ERP 受注番号 (= erp_case_no として AI_TM に送信)
    status                VARCHAR(20),           -- OPEN/RELEASED/BLOCKED/COMPLETED
    customer_code         VARCHAR(50),
    -- AI_TM 取引審査結果
    export_check_status   VARCHAR(20),           -- PENDING/PASSED/BLOCKED/ERROR
    export_check_ref      VARCHAR(50),           -- AI_TM case_no (例: API-20260603-4821)
    export_check_message  TEXT,
    ...
);
```

**`export_check_ref`** = AI_TM の `case_no`（`API-YYYYMMDD-XXXX` 形式）。  
再審査・ExportDeclaration の参照キーとして保存する。  
ただし **検索には `erp_case_no`（= `document_number`）を使うこと**（§3-3 参照）。

**値域 `export_check_status`**:

| 値 | 意味 |
|---|---|
| `PENDING` | 審査中（AI_TM が非同期処理中、または NEEDS_REVIEW/PENDING 判定）|
| `PASSED` | 審査承認 (`judgment == "APPROVED"`) |
| `BLOCKED` | 審査却下 (`judgment == "REJECTED"`) |
| `ERROR` | AI_TM 接続エラー |

---

### 5-4. deliveries テーブル

```sql
CREATE TABLE deliveries (
    id                    INTEGER PRIMARY KEY,
    client_id             VARCHAR(20) NOT NULL,
    document_number       VARCHAR(20) NOT NULL,
    sales_order_id        INTEGER REFERENCES sales_orders(id),
    status                VARCHAR(20),          -- OPEN/BLOCKED/COMPLETED
    -- AI_TM 再審査結果
    aitm_case_no          VARCHAR(50),          -- SO の export_check_ref を引き継ぎ
    aitm_approval_status  VARCHAR(20),          -- APPROVED/BLOCKED/ERROR/PENDING
    ...
);
```

**`aitm_case_no`**: `so.export_check_ref` から引き継ぐ (Delivery 作成時にコピー)  
**`aitm_approval_status`** は Billing 発行の gate として機能する:

```
aitm_approval_status == "BLOCKED"  →  BillingService.create_from_delivery() が BusinessRuleError
aitm_approval_status == "APPROVED" →  請求書発行可能
aitm_approval_status == null/PENDING → 発行可能（非同期処理中と判断）
```

---

### 5-5. billing_documents テーブル

```sql
CREATE TABLE billing_documents (
    id                    INTEGER PRIMARY KEY,
    client_id             VARCHAR(20) NOT NULL,
    document_number       VARCHAR(20) NOT NULL,
    delivery_id           INTEGER REFERENCES deliveries(id),
    -- AI_TM 承認番号（Invoice への記載用）
    aitm_case_no          VARCHAR(50),          -- delivery.aitm_case_no から引き継ぎ
    ...
);
```

**用途**: Commercial Invoice PDF への AI_TM 承認番号記載 (緑色バナーで表示)

---

### 5-6. ai_tm_transaction_links テーブル（GTS モジュール）

```sql
CREATE TABLE ai_tm_transaction_links (
    id                    INTEGER PRIMARY KEY,
    client_id             VARCHAR(20) NOT NULL,
    sales_order_id        INTEGER REFERENCES sales_orders(id),
    aitm_transaction_id   INTEGER,              -- AI_TM 内部 id
    case_no               VARCHAR(50) INDEX,    -- AI_TM case_no (API-YYYYMMDD-XXXX)
    judgment              VARCHAR(20),          -- APPROVED/NEEDS_REVIEW/REQUIRES_PERMIT/REJECTED/PENDING
    review_id             VARCHAR(50),
    linked_existing       BOOLEAN DEFAULT FALSE,
    message               TEXT,
    created_at            DATETIME,
    ...
);
```

**1 受注 = 1 レコード**。取引審査の全結果を追跡する監査証跡。  
**`judgment`** には正規化値（`APPROVED`/`NEEDS_REVIEW`/`REQUIRES_PERMIT`/`REJECTED`/`PENDING`）を保存する。

---

### 5-7. ai_tm_shipment_links テーブル（GTS モジュール）

```sql
CREATE TABLE ai_tm_shipment_links (
    id                    INTEGER PRIMARY KEY,
    client_id             VARCHAR(20) NOT NULL,
    delivery_id           INTEGER REFERENCES deliveries(id),
    case_no               VARCHAR(50) INDEX,    -- SO の case_no
    shipment_ok           BOOLEAN,              -- 出荷承認フラグ
    rescreen_at           DATETIME,             -- 再審査実行日時
    rescreen_result       VARCHAR(20),          -- PASSED / CHANGED
    ai_status             VARCHAR(20),          -- AI_TM judgment 正規化値
    block_reason          TEXT,                 -- BLOCKED 時のメッセージ
    ...
);
```

**1 納品書 = 1 レコード**。再審査の詳細ログ・ブロック理由の監査証跡。  
**`ai_status`** には `judgment` 正規化値を保存すること（旧: `agent_judgment_status` 生値 → 廃止）。

---

### 5-8. export_declarations テーブル（GTS モジュール）

```sql
CREATE TABLE export_declarations (
    id                    INTEGER PRIMARY KEY,
    client_id             VARCHAR(20) NOT NULL,
    declaration_number    VARCHAR(20),
    delivery_id           INTEGER REFERENCES deliveries(id),
    sales_order_id        INTEGER REFERENCES sales_orders(id),
    -- AI_TM 参照
    ai_tm_transaction_id  VARCHAR(50),          -- AI_TM case_no (API-YYYYMMDD-XXXX)
    destination_country   VARCHAR(10),
    material_code         VARCHAR(50),
    hs_code               VARCHAR(20),
    eccn                  VARCHAR(20),
    quantity              NUMERIC,
    quantity_unit         VARCHAR(10),
    declared_value_usd    NUMERIC,
    license_type          VARCHAR(50),          -- NLR / EAR_LICENSE_EXCEPTION_LVS
    license_authority     VARCHAR(20),          -- METI / BIS
    status                VARCHAR(20),          -- DRAFT / FILED / APPROVED
    remarks               TEXT,
    ...
);
```

---

## 6. 処理フロー図

### フロー A: 受注 → 請求書発行（Order-to-Cash with AI_TM）

```
1. SalesOrder 作成 (SD)
   │
   ├─ GTSService.transaction_review(so, customer)
   │   └─ POST :8011/api/transactions  (erp_case_no = so.document_number)
   │       → case_no (API-20260603-4821) を so.export_check_ref に保存
   │       → erp_case_no (SO-10300002) は AI_TM 内に保存（検索キー）
   │       → AI_TM が非同期で審査開始 (judgment = PENDING)
   │
   │   判定結果 (ポーリング or Webhook で取得):
   │   APPROVED      → so.export_check_status = PASSED  → so.status = OPEN
   │   NEEDS_REVIEW  → so.export_check_status = PENDING → so.status = BLOCKED ❌
   │   REQUIRES_PERMIT → so.export_check_status = PENDING → so.status = BLOCKED ❌
   │   REJECTED      → so.export_check_status = BLOCKED → so.status = BLOCKED ❌
   │   ERROR         → so.export_check_status = ERROR   → so.status = BLOCKED ❌
   │
2. Delivery 作成 (SD) ← SO が OPEN/RELEASED の場合のみ
   │
   ├─ DeliveryService._run_shipment_rescreen(delivery, so)
   │   ├─ GET :8011/api/transactions/search?erp_case_no={so.document_number}
   │   │   → tx.id 取得
   │   ├─ POST :8011/ui/transactions/{id}/run-screening  → 再審査トリガー
   │   └─ GET :8011/api/transactions/{id}               → 最新判定取得 (judgment フィールド使用)
   │       → delivery.aitm_case_no = case_no
   │       → delivery.aitm_approval_status = APPROVED / BLOCKED
   │
   │   BLOCKED → delivery.status = BLOCKED ❌  (以降の請求書発行不可)
   │   APPROVED → delivery.status = OPEN ✓
   │
3. BillingDocument 作成 (SD) ← delivery.aitm_approval_status != "BLOCKED"
   │
   ├─ BillingService.create_from_delivery()
   │   ├─ aitm_approval_status == "BLOCKED" → BusinessRuleError ❌
   │   └─ OK → bill.aitm_case_no = delivery.aitm_case_no
   │
4. PDF 発行
   ├─ Commercial Invoice    (bill.aitm_case_no を緑バナーで記載)
   ├─ Packing List          (delivery.aitm_case_no / approval_status を記載)
   └─ Export Declaration    (so.export_check_ref を記載)
```

### フロー B: 取引先登録 → 制裁スクリーニング

```
MDM: BusinessPartner 登録
   │
   └─ POST :8005/api/screening/batch
       → status == "CRITICAL" / "match"
           → bp.is_denied_party = True
       → status == "no_match"
           → bp.is_denied_party = False (通常登録)

受注時チェック (SalesOrderService.create):
   customer.is_denied_party == True → BusinessRuleError("denied party")
```

---

## 7. 環境変数

### ERP 側

| 環境変数 | デフォルト値 | 説明 |
|---|---|---|
| `AITM_CLASSIFICATION_URL` | `http://localhost:8002` | ai_classification ベース URL |
| `AITM_SCREENING_URL` | `http://localhost:8005` | screening ベース URL |
| `AITM_VALIDATION_URL` | `http://localhost:8011` | ai_validation ベース URL |
| `AITM_ORG_ID` | `default-org` | X-Organization-Id ヘッダー値 |
| `AITM_USER_ID` | `erp-system` | X-User-Id ヘッダー値 |
| `AITM_WEBHOOK_SECRET` | — | Webhook 受信時 Bearer 検証キー (未実装) |
| `AITM_USE_MOCK` | `false` | `true` = _MockClient 使用 (テスト用) |

### AI_TM 側（参考）

| 環境変数 | 値 | 説明 |
|---|---|---|
| `MODULE_AI_VALIDATION_PUBLIC_URL` | `https://validation.tsp-aitrademanagement.com` | ブラウザ向け URL |
| `MODULE_AI_CLASSIFICATION_URL` | `http://localhost:8002` | サーバー間通信 URL |
| `MODULE_SCREENING_URL` | `http://localhost:8005` | サーバー間通信 URL |
| `MODULE_EXPORT_LICENSE_URL` | `http://localhost:8012` | 輸出許可申請管理 URL |

---

## 8. エラーハンドリング

| シナリオ | ERP の動作 |
|---|---|
| AI_TM 接続不可 (ConnectionError) | `export_check_status = ERROR`, `so.status = BLOCKED` |
| `POST /api/transactions` → 422 | `export_check_status = ERROR`, エラーメッセージを `export_check_message` に保存 |
| `search?erp_case_no=` → 結果なし | `delivery.status = BLOCKED`, `aitm_approval_status = BLOCKED` |
| `run_screening()` → 例外 | `delivery.status = BLOCKED`, `aitm_approval_status = ERROR` |
| `judgment == "PENDING"` (未判定) | 明示的な BLOCKED 系でなければ APPROVED 扱い（非同期処理中） |
| `judgment == "NEEDS_REVIEW"` | BLOCKED 系として扱う（担当者確認まで出荷不可） |
| `aitm_approval_status = BLOCKED` で請求書発行 | `BusinessRuleError` を raise し、請求書作成を阻止 |

---

## 9. 現在未接続・今後の対応事項

| 項目 | 状態 | 内容 |
|---|---|---|
| Webhook 受信エンドポイント | 未実装 | `POST /api/gts/webhook/judgment-updated` を GTS router に追加要 |
| ポーリングジョブ | 未実装 | PENDING 状態の SO/Delivery を定期チェックして判定結果を更新 |
| ai_classification product_id 保存 | 未実装 | `materials.aitm_product_id` カラム追加・同期後に ID 保存 |
| BP スクリーニング日時・リスト保存 | 未実装 | `business_partners` に `denied_party_checked_at`, `matched_list` 追加予定 |
| rnd_assessment 連携 (Port 8003) | 未接続 | みなし輸出人物登録フロー |
| export_license 連携 (Port 8012) | 未接続 | 許可申請番号照会・残高確認フロー（`judgment == "REQUIRES_PERMIT"` 時） |
| COO 変更トリガー | 未実装 | ロット原産国変更時に関連 SO を自動再審査 |
| ERP 判定ロジック v2.4 → v2.5 移行 | **要対応** | `BLOCKED_JUDGMENTS` 修正、`judgment` フィールド使用への切り替え |
| `skip_export_check` フラグ | 実装済み | 受注作成時に `true` を渡すと AI_TM チェックをスキップ（テスト・国内取引用） |

---

## 10. テスト・動作確認

### モッククライアント (`AITM_USE_MOCK=true`)

`_MockClient` が全 API 呼び出しをシミュレート:
- `create_transaction()` → `case_no = f"MOCK-{abs(hash(req.title)) % 10000:04d}"`、`judgment = "APPROVED"`
- `find_transaction_by_case_no(case_no)` → 常に `judgment = "APPROVED"` を返す
- `run_screening()` → 成功を返す

### ライブ接続テスト手順

```bash
# 1. AI_TM が起動していることを確認
curl http://localhost:8011/health   # → {"status": "ok"}
curl http://localhost:8002/health   # → {"status": "ok"}
curl http://localhost:8005/health   # → {"status": "ok"}

# 2. v2.5 API テスト（erp_case_no 付き取引作成）
curl -X POST http://localhost:8011/api/transactions \
  -H "Content-Type: application/json" \
  -d '{
    "title": "SO-10300002 / TechCorp GmbH (DE)",
    "counterparty_name": "TechCorp GmbH",
    "destination_country": "DE",
    "erp_case_no": "SO-10300002",
    "product_code": "CTRL-HC200",
    "product_name": "High-Capacity DRAM Controller v2",
    "total_value_usd": 85000.0,
    "intended_use": "Industrial automation. DE. Customer: TechCorp GmbH.",
    "source_module": "erp"
  }'
# → {"id": ..., "case_no": "API-YYYYMMDD-XXXX", "erp_case_no": "SO-10300002", ...}

# 3. erp_case_no で検索
curl "http://localhost:8011/api/transactions/search?erp_case_no=SO-10300002"
# → {"results": [{"id": ..., "case_no": "API-...", "erp_case_no": "SO-10300002", ...}]}

# 4. judgment フィールド確認
curl http://localhost:8011/api/transactions/{id}
# → {"judgment": "APPROVED" または "NEEDS_REVIEW" または "PENDING", ...}

# 5. 出荷書類発行スクリプト（エンドツーエンドテスト）
cd /path/to/erp-system
python scripts/issue_shipping_docs.py
```

---

## 11. 変更履歴

| 日付 | バージョン | 変更内容 |
|---|---|---|
| 2026-06-03 | ERP-AITM v1.0 | 初版作成。AI_TM v2.4 対応 TransactionCreateRequest スキーマ |
| 2026-06-07 | ERP-AITM v1.1 | 出荷時再審査フロー実装完了。Delivery/Billing の AITM カラム追加。PDF 出力実装。ERP→AI_TM 視点で全面改訂。 |
| 2026-06-07 | **ERP-AITM v2.5** | **AI_TM v2.5 対応。`erp_case_no` 等 11 フィールド追加。`judgment` 正規化フィールド新設。`GET /api/transactions/search` 新設。ERP 側 BLOCKED_JUDGMENTS 修正（CRITICAL）。判定ロジック v2.4→v2.5 移行手順を追記。** |

---

## 12. 問い合わせ先

| 担当 | 連絡先 |
|---|---|
| ERP 開発チーム | tsp0918@gmail.com |
| AI Trade Management チーム | AI Trade Management Platform 管理者 |

---

*ERP コード: `/path/to/erp-system/app/integrations/ai_trade_management/`*  
*AI_TM API ドキュメント: `http://localhost:{port}/docs` (各モジュール)*  
*AI_TM ソースコード: `/Users/takehirosato/Desktop/AI_TradeManagement/`*
