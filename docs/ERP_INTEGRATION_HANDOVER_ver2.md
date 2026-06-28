# ERP システム — AI Trade Management 連携引き継ぎ書

**作成日**: 2026-06-27  
**文書バージョン**: ERP-AITM v2.0  
**作成者**: ERPシステム開発チーム  
**宛先**: AI Trade Management Platform 開発・運用チーム  
**対象 ERP バージョン**: Mini Global ERP Phase 1-4 (AI_TM Integration + Lot Traceability)  
**対応 AI_TM バージョン**: AI Trade Management Platform v2.4

> **v2.0 主な変更点**: ロットトレーサビリティ基盤の実装、原産国切り替えイベント → AI_TM 連携、EAR De Minimis (25%ルール) 自動評価、Webhook 受信実装完了、CO/QM/PIR モジュール追加、過去 12 ヶ月ダミーデータ投入完了。

---

## 1. システム構成概要

### ERP 内部アーキテクチャ（AI_TM 連携全体図）

```
ERP 内部モジュール (Mini Global ERP v4 / port 8888)
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│  MDM  ─── SD ─── MM ─── PP ─── CO ─── QM ─── FI ─── HR            │
│   │         │      │      │                                          │
│   │    ┌────▼──────▼──────▼────────────────────────────────────┐    │
│   │    │               GTS (Global Trade Service)               │    │
│   │    │                                                         │    │
│   │    │  ① transaction_review()    ← SO 作成時                 │    │
│   │    │  ② shipment_rescreen()     ← Delivery 作成時           │    │
│   │    │  ③ push_origin_change()    ← 原産国切り替え検知時 (NEW) │    │
│   │    │  ④ register_product()      ← 材料マスター登録時         │    │
│   │    │  ⑤ denied_party_check()    ← 取引先登録時              │    │
│   │    │  ⑥ judge_bom()             ← BOM 外為法判定 (手動)     │    │
│   │    └─────────────────────┬───────────────────────────────────┘    │
│   │                           │                                      │
│   │    Lot Traceability Layer (NEW)                                  │
│   │    ┌──────────────────────▼───────────────────────────────────┐  │
│   │    │  batches / batch_genealogy                                │  │
│   │    │  material_origin_change_logs                              │  │
│   │    │  lot_deminimus_assessments      ← US 含有率 25% 評価      │  │
│   │    └──────────────────────────────────────────────────────────┘  │
│   │                                                                  │
│   └─────────────────────────────────────────────────────────────────┘
│               │ REST API  (HTTP/JSON)                                │
└───────────────┼──────────────────────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│           AI Trade Management Platform v2.4                          │
│                                                                      │
│  :8002 ai_classification  (品目管理・HS/ECCN分類・BOM同期)            │
│  :8005 screening          (制裁リストスクリーニング)                  │
│  :8011 ai_validation      (取引審査・AI判定・再審査)                  │
│  :8012 export_license     (ライセンス残高照会) ← 未接続               │
└──────────────────────────────────────────────────────────────────────┘
```

### ERP サーバー情報

| 項目 | 値 |
|---|---|
| ローカル起動 URL | `http://localhost:8888` |
| Swagger UI | `http://localhost:8888/docs` |
| ダッシュボード UI | `http://localhost:8888/ui` |
| 総 API エンドポイント数 | **132** |
| 起動コマンド | `python -m uvicorn app.main:app --host 0.0.0.0 --port 8888` |
| DB | SQLite: `erp.db` (ローカル), PostgreSQL 切替可 |

> **注意**: port 8000 は AI_TM platform-core が使用。ERP は必ず **port 8888** で起動。

### AI_TM 接続先 URL

| AI_TM モジュール | ローカル URL | 用途 |
|---|---|---|
| ai_classification | `http://localhost:8002` | 品目登録・BOM同期 |
| screening | `http://localhost:8005` | 制裁スクリーニング |
| ai_validation | `http://localhost:8011` | 取引審査・再審査 |

ERP 設定: `app/core/config.py` (環境変数 `AITM_CLASSIFICATION_URL` / `AITM_SCREENING_URL` / `AITM_VALIDATION_URL`)

---

## 2. 認証方式

### 2-1. ERP → AI_TM（アウトバウンド）

```http
X-Organization-Id: {AITM_ORG_ID}
X-User-Id: erp-system@company.com
Content-Type: application/json
```

- Bearer Token 不要（イントラネット同一組織内通信）
- `X-Organization-Id`: AI_TM 側テナント ID（環境変数 `AITM_ORG_ID` で管理）
- `X-User-Id`: ERP システムのサービスアカウントメールアドレス

### 2-2. AI_TM → ERP（インバウンド Webhook）

```http
POST /gts/webhook/judgment-updated
Authorization: Bearer {AITM_WEBHOOK_SECRET}
Content-Type: application/json
```

**実装状態**: ✅ 実装済み (`app/modules/gts/router.py`)

- ERP 側で `AITM_WEBHOOK_SECRET` 環境変数を設定し Bearer トークン検証を実施
- 未設定の場合は全 Webhook を受け付ける（開発モード）

### 2-3. AI_TM → ERP（ERP API のプル利用）

AI_TM 側から ERP の Read API を呼び出す場合（後述 §4 参照）:

```http
GET /mm/batches?country_of_origin=US
Authorization: Bearer {ERP_JWT_TOKEN}
Content-Type: application/json
```

ERP の JWT トークン取得:
```http
POST /auth/token
Content-Type: application/x-www-form-urlencoded
username=admin@example.com&password=admin1234
```

レスポンス: `{"access_token": "eyJ...", "token_type": "bearer"}`

---

## 3. ERP から AI_TM へ送るインターフェース（アウトバウンド）

### 3-1. 品目登録・マスター同期

**呼び出しタイミング**: MDM 材料マスター登録・更新時  
**実装箇所**: `app/modules/gts/service.py` → `GTSService.register_product()`  
**エンドポイント**: `POST :8002/products/erp-sync`

#### リクエスト

```json
[
  {
    "code": "MAT-1000001",
    "name": "ArF Immersion Photoresist NSP-AR450",
    "eccn": "EAR99",
    "hs_code": "3707.90",
    "item_type": "component",
    "country_of_origin": "JP",
    "bom": [
      {
        "child_code": "MAT-9000001",
        "child_name": "PGMEA Solvent (Electronic Grade)",
        "quantity": 1.05,
        "unit": "KG",
        "origin_country": "US",
        "supplier_name": "Honeywell Performance Materials"
      }
    ]
  }
]
```

> **v2.0 変更**: `bom[].origin_country` にロット実績の原産国を反映するよう拡張。材料マスターの `country_of_origin` は製造国（JP固定）、BOM コンポーネントの `origin_country` は最新ロットの調達原産国を使用する。

#### ERP データソース

| AI_TM フィールド | ERP テーブル | ERP カラム | 備考 |
|---|---|---|---|
| `code` | `materials` | `material_code` | |
| `name` | `materials` | `description` | |
| `eccn` | `materials` | `eccn` | |
| `hs_code` | `materials` | `hs_code` | |
| `item_type` | `materials` | `item_type` / `material_type` | FERT→equipment, ROH→component |
| `country_of_origin` | `materials` | `country_of_origin` | 製造国 |
| `bom[].child_code` | `bom_components` | `component_code` | |
| `bom[].origin_country` | `batches` | `country_of_origin` | **最新入荷ロットの原産国** |

---

### 3-2. 取引審査案件作成（受注時）

**呼び出しタイミング**: `SalesOrderService.create()` — 受注登録時  
**実装箇所**: `app/modules/gts/service.py` → `GTSService.transaction_review()`  
**エンドポイント**: `POST :8011/api/transactions`

#### リクエスト（TransactionCreateRequest v2.4）

```json
{
  "title": "SO-10300002 / NSC Taiwan (TW)",
  "counterparty_name": "NSC Taiwan Semiconductor Co. Ltd.",
  "destination_country": "TW",
  "items": [
    {
      "item_name": "MAT-2000001",
      "item_description": "CMP Slurry for Tungsten NSC-WSL30"
    }
  ],
  "usage_requirements": [
    {
      "source": "ERP",
      "text": "Semiconductor process materials. Destination: TW. Customer: NSC Taiwan."
    }
  ],
  "source_module": "ERP"
}
```

#### レスポンス

```json
{
  "id": 1234,
  "case_no": "CASE-2026-0042",
  "title": "SO-10300002 / NSC Taiwan (TW)",
  "status": "draft",
  "url": "http://localhost:8011/transactions/1234",
  "screening_queued": true
}
```

#### SO ステータス制御ロジック

```
AI_TM judgment   →  export_check_status  →  sales_orders.status
─────────────────────────────────────────────────────────────────
APPROVED         →  PASSED               →  OPEN  ✓
REJECTED         →  BLOCKED              →  BLOCKED ✗
NEEDS_REVIEW     →  PENDING              →  BLOCKED ✗ (手動確認待ち)
Exception        →  ERROR                →  BLOCKED ✗ (要調査)
```

#### ERP 側保存先

| AI_TM フィールド | ERP テーブル | ERP カラム |
|---|---|---|
| `case_no` | `sales_orders` | `export_check_ref` |
| `id` | `ai_tm_transaction_links` | `review_id` |
| 判定結果 | `sales_orders` | `export_check_status` |

---

### 3-3. 出荷時取引再審査（納品書作成時）

**呼び出しタイミング**: `DeliveryService.create()` — 納品書作成時  
**実装箇所**: `app/modules/sd/service.py` → `DeliveryService._run_shipment_rescreen()`

#### Step 1: case_no で取引検索

```
GET :8011/api/transactions/search?q={case_no}
```

#### Step 2: 再審査トリガー

```
POST :8011/ui/transactions/{id}/run-screening
```
→ HTTP 303 (リダイレクト) = 成功

#### Step 3: 審査結果取得

```
GET :8011/api/transactions/{id}
```

#### 承認判定ロジック

```python
BLOCKED_STATUSES  = {"rejected", "REJECTED"}
BLOCKED_JUDGMENTS = {"BLOCKED", "REJECTED", "REQUIRES_PERMIT"}

approved = (
    tx_status not in BLOCKED_STATUSES
    and agent_judgment not in BLOCKED_JUDGMENTS
)
# Note: agent_judgment_status が null の場合は APPROVED として扱う（非同期処理中）
```

#### ERP 側保存先

| AI_TM フィールド | ERP テーブル | ERP カラム |
|---|---|---|
| `case_no` | `deliveries` | `aitm_case_no` |
| 承認結果 | `deliveries` | `aitm_approval_status` |
| `agent_judgment_status` | `ai_tm_shipment_links` | `ai_status` |
| 再審査日時 | `ai_tm_shipment_links` | `rescreen_at` |

---

### 3-4. 制裁リストスクリーニング（取引先登録時）

**エンドポイント**: `POST :8005/api/screening/batch`

```json
{
  "entities": [{"name": "CUST-CN-01 Corp", "country": "CN", "entity_type": "company"}],
  "sources": ["OFAC_SDN", "BIS_ENTITY", "METI_FUL", "EU_CONSOLIDATED"]
}
```

| スクリーニング status | ERP 処理 |
|---|---|
| `no_match` | 通常登録 (`is_denied_party = false`) |
| `possible_match` | コンプライアンス担当へ通知 |
| `match` / `CRITICAL` | `is_denied_party = true`、受注時 BusinessRuleError |

---

### 3-5. 原産国切り替えイベント通知（NEW — v2.0）

**呼び出しタイミング**: `POST /gts/origin-change-log/{id}/notify-aitm` — 手動トリガーまたは自動検知  
**実装箇所**: `app/modules/gts/lot_router.py` → `notify_aitm()` → `GTSService.push_origin_change_to_aitm()`  
**エンドポイント**: `POST {AITM_VALIDATION_URL}/events` (汎用イベントエンドポイント)

#### 背景・シナリオ

ERP はロット（Batch）単位で原料の `country_of_origin` を管理している。仕入先変更により原産国が変わった場合（例: PGMEA溶剤 JP→US）、EAR De Minimis ルール（US-origin 含有率 25% 超で EAR 適用）への影響を自動評価して AI_TM に通知する。

**実際に発生したシナリオ（テストデータ）**:

| 原料コード | 品目名 | 切り替え日 | 旧原産国/仕入先 | 新原産国/仕入先 | De Minimis 最大影響 |
|---|---|---|---|---|---|
| MAT-9000001 | PGMEA溶剤 | 2026-01-01 | JP / Daicel | US / Honeywell | 9.1% (OK) |
| MAT-9000004 | CMP添加剤/PAG重合体 | 2026-02-01 | JP / JSR | US / DuPont | **32.8% → BREACH** |

**影響製品**: CMP Slurry W (MAT-2000001) の US 含有率 41.9% → EAR De Minimis 閾値 (25%) 超過

#### リクエストペイロード（ERP → AI_TM）

```json
{
  "event_type": "MATERIAL_ORIGIN_CHANGE",
  "material_code": "MAT-9000004",
  "from_country": "JP",
  "to_country": "US",
  "effective_date": "2026-02-01",
  "max_us_content_pct": 32.8,
  "exceeds_deminimis_threshold": true,
  "affected_products": ["MAT-2000001", "MAT-2000002"],
  "breach_lot_count": 12,
  "breach_lots": [
    {
      "fg_batch_code": "FG-2000001-6000000074",
      "fg_material_code": "MAT-2000001",
      "us_content_pct": 41.92
    }
  ]
}
```

#### フォールバック

AI_TM 未接続時は `LOCAL-OCL-{material_code}-{uuid8}` 形式のローカル参照番号を生成し `material_origin_change_logs.ai_tm_case_ref` に記録。接続回復後に再送可能。

#### ERP 側の状態管理

```sql
-- 通知後のレコード状態
material_origin_change_logs:
  ai_tm_notification_sent = true
  ai_tm_notification_at   = NOW()
  ai_tm_case_ref          = "CASE-2026-OCL-xxxx"
  review_status           = "ACTION_REQUIRED"  -- BREACH の場合

lot_deminimus_assessments:
  ai_tm_notified          = true
  ai_tm_notified_at       = NOW()
  ai_tm_case_ref          = "CASE-2026-OCL-xxxx"
```

---

### 3-6. 品目 BOM 外為法判定（手動トリガー）

**エンドポイント**: `POST /gts/judge-bom` (ERP API) → `POST :8011/gaihi/judge-bom`

```json
{
  "material_code": "MAT-2000001",
  "plant_code": "1000"
}
```

---

## 4. AI_TM から ERP を参照するインターフェース（プル型 / AI_TM → ERP）

v2.0 より、AI_TM がコネクターを通じて ERP の Read API を直接呼び出すプル型連携を新設する。
これにより、AI_TM 側で最新の原産国情報・ロット情報・コンプライアンス状態をリアルタイムに参照できる。

### 4-1. US 原産ロット一覧（De Minimis 評価用）

```http
GET /mm/batches?country_of_origin=US&source_type=PURCHASED&limit=500
Authorization: Bearer {ERP_JWT_TOKEN}
```

**レスポンス**:
```json
[
  {
    "id": 42,
    "batch_code": "LOT-9000004-US-010",
    "material_code": "MAT-9000004",
    "plant_code": "1000",
    "quantity": 269.988,
    "unit": "L",
    "source_type": "PURCHASED",
    "source_reference": "GR-202602-9004",
    "country_of_origin": "US",
    "vendor_code": "VND-DUPONT-US",
    "quality_status": "RELEASED",
    "production_date": "2026-02-01",
    "expiry_date": "2027-02-01"
  }
]
```

**活用シナリオ**: AI_TM が定期ポーリングでUS原産ロットを確認し、新規入荷ロットを検知した場合に De Minimis 評価を再実行する。

---

### 4-2. ロット系譜トレース（上流/下流）

```http
GET /mm/batches/{batch_code}/genealogy
Authorization: Bearer {ERP_JWT_TOKEN}
```

**例**: CMP Slurry の製造ロット `FG-2000001-6000000074` の原料系譜を取得

**レスポンス**:
```json
{
  "batch_code": "FG-2000001-6000000074",
  "material_code": "MAT-2000001",
  "country_of_origin": "JP",
  "production_date": "2026-02-12",
  "quality_status": "RELEASED",
  "parents": [
    {
      "batch_code": "LOT-9000001-US-010",
      "material_code": "MAT-9000001",
      "country_of_origin": "US",
      "quantity": 1349.94,
      "unit": "KG",
      "source_type": "PURCHASED",
      "direction": "PARENT"
    },
    {
      "batch_code": "LOT-9000004-US-010",
      "material_code": "MAT-9000004",
      "country_of_origin": "US",
      "quantity": 224.99,
      "unit": "L",
      "source_type": "PURCHASED",
      "direction": "PARENT"
    }
  ],
  "children": []
}
```

**活用シナリオ**: 特定の出荷ロット（`fg_batch_code`）が含むUS原産原料の種類・量を検証し、輸出許可申請の原産性証明書作成に活用する。

---

### 4-3. De Minimis アラート一覧

```http
GET /gts/deminimis?alert_level=BREACH
Authorization: Bearer {ERP_JWT_TOKEN}
```

**レスポンス**:
```json
[
  {
    "id": 5,
    "fg_batch_code": "FG-2000001-6000000074",
    "fg_material_code": "MAT-2000001",
    "process_order_number": "6000000074",
    "us_origin_value": 521673.72,
    "total_bom_value": 553273.72,
    "us_content_pct": 41.92,
    "threshold_pct": 25.0,
    "alert_level": "BREACH",
    "us_components": [
      {
        "material_code": "MAT-9000001",
        "batch_code": "LOT-9000001-US-010",
        "country_of_origin": "US",
        "consumed_qty": 1349.94,
        "unit_cost_jpy": 3800.0,
        "value_jpy": 5129772.0,
        "pct_of_product": 9.12
      },
      {
        "material_code": "MAT-9000004",
        "batch_code": "LOT-9000004-US-010",
        "country_of_origin": "US",
        "consumed_qty": 224.99,
        "unit_cost_jpy": 82000.0,
        "value_jpy": 18449180.0,
        "pct_of_product": 32.8
      }
    ],
    "ai_tm_notified": true,
    "ai_tm_case_ref": "LOCAL-OCL-MAT-9000004-C993B24B",
    "assessed_at": "2026-06-27 13:25:00"
  }
]
```

---

### 4-4. 原産国切り替えログ

```http
GET /gts/origin-change-log?review_status=PENDING
Authorization: Bearer {ERP_JWT_TOKEN}
```

**レスポンス** (主要フィールド):
```json
[
  {
    "id": 2,
    "material_code": "MAT-9000004",
    "from_country": "JP",
    "to_country": "US",
    "effective_date": "2026-02-01",
    "old_vendor_code": "VND-JSR-JP",
    "new_vendor_code": "VND-DUPONT-US",
    "last_old_batch_code": "LOT-9000004-JP-009",
    "first_new_batch_code": "LOT-9000004-US-010",
    "affected_fg_codes": ["MAT-2000001", "MAT-2000002"],
    "max_deminimis_impact_pct": 32.8,
    "exceeds_threshold": true,
    "threshold_pct": 25.0,
    "ai_tm_notification_sent": true,
    "ai_tm_case_ref": "LOCAL-OCL-MAT-9000004-C993B24B",
    "review_status": "PENDING"
  }
]
```

---

### 4-5. 販売フォーキャスト vs 実績

```http
GET /sd/forecasts/summary?year=2026
Authorization: Bearer {ERP_JWT_TOKEN}
```

**レスポンス**:
```json
[
  {
    "material_code": "MAT-2000001",
    "year": 2026,
    "month": 7,
    "forecast_qty": 6796.0,
    "actual_qty": 0.0,
    "attainment_pct": 0.0,
    "forecast_value": 84950000.0,
    "actual_value": 0
  }
]
```

**活用シナリオ**: AI_TM が輸出ライセンス申請数量の計画値として PIR フォーキャストを参照し、年間許可上限数量の事前申請に活用する。

---

### 4-6. 品質証明書（CoA）照会

```http
GET /qm/certificates?limit=100
Authorization: Bearer {ERP_JWT_TOKEN}
```

**レスポンス** (主要フィールド):
```json
[
  {
    "cert_number": "COA-0000039",
    "lot_id": 39,
    "material_code": "MAT-2000001",
    "issue_date": "2026-02-14",
    "all_passed": true,
    "issued_by": "seed@example.com"
  }
]
```

---

## 5. AI_TM → ERP 受信インターフェース（インバウンド Webhook）

### 5-1. 審査完了 Webhook（✅ 実装済み）

**ERP 受信エンドポイント**: `POST /gts/webhook/judgment-updated`  
**実装ファイル**: `app/modules/gts/router.py`

#### リクエスト（AI_TM → ERP）

```json
{
  "event": "judgment_updated",
  "transaction_id": 1234,
  "case_no": "CASE-2026-0042",
  "material_code": "MAT-2000001",
  "new_judgment": "APPROVED",
  "new_eccn": null,
  "rationale": "EAR99 - no license required for TW"
}
```

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `event` | string | ✓ | 常に `"judgment_updated"` |
| `case_no` | string | ✓ | ERP の `export_check_ref` 参照キー |
| `material_code` | string | ✓ | 品目コード |
| `new_judgment` | string | ✓ | `APPROVED` / `REJECTED` / `NEEDS_REVIEW` |
| `new_eccn` | string | - | 更新 ECCN（変更があった場合のみ）|
| `rationale` | string | - | 判定理由テキスト |

#### ERP 側の処理（`GTSService.apply_judgment_update()`）

```
new_judgment == "APPROVED"
  → materials.eccn 更新 (new_eccn 指定時)
  → materials.fefta_judgment = "APPROVED"
  → delivery.status = OPEN (BLOCKED 解除)
  → ai_tm_shipment_links.shipment_ok = true

new_judgment == "REJECTED"
  → materials.fefta_judgment = "REJECTED"
  → sales_orders.status = CANCELLED (BLOCKED 状態の SO)
  → deliveries.status = BLOCKED
```

---

## 6. ERP データベース — AI_TM 連携カラム一覧

### 6-1. materials テーブル

| カラム | 型 | AI_TM 利用 |
|---|---|---|
| `material_code` | VARCHAR(20) | 品目コード（主キー相当） |
| `eccn` | VARCHAR(20) | ECCN 番号 (例: EAR99 / 3C001) |
| `hs_code` | VARCHAR(20) | HS コード (例: 3707.90) |
| `country_of_origin` | VARCHAR(2) | 製造国 (ISO 3166-1 alpha-2) |
| `fefta_judgment` | VARCHAR(20) | 外為法判定結果 |
| `export_control_status` | VARCHAR(20) | AI_TM 分類ステータス |
| `standard_price` | NUMERIC(15,2) | De Minimis 計算用標準原価 |

---

### 6-2. batches テーブル（ロットトレーサビリティ / NEW）

| カラム | 型 | AI_TM 利用 |
|---|---|---|
| `batch_code` | VARCHAR(50) | ロット番号（一意識別子）|
| `material_code` | VARCHAR(20) | 品目コード |
| `country_of_origin` | VARCHAR(2) | **ロットの原産国**（材料マスターより優先）|
| `vendor_code` | VARCHAR(20) | 調達仕入先 |
| `source_type` | VARCHAR(20) | `PURCHASED`（入荷ロット）/ `PRODUCED`（製造ロット）|
| `source_reference` | VARCHAR(50) | 入荷GR番号 または 製造指図番号 |
| `production_date` | DATE | 製造日 / 入荷日 |
| `expiry_date` | DATE | 有効期限 |
| `quality_status` | VARCHAR(20) | `RELEASED` / `BLOCKED` / `IN_TEST` |

**重要**: `batches.country_of_origin` は **材料マスターの `materials.country_of_origin` を上書きする**。  
De Minimis 計算には必ずロット単位の原産国を使用すること。

---

### 6-3. batch_genealogy テーブル（系譜 / NEW）

| カラム | 型 | AI_TM 利用 |
|---|---|---|
| `parent_batch_code` | VARCHAR(50) | 原料ロット |
| `child_batch_code` | VARCHAR(50) | 完成品ロット |
| `process_order_number` | VARCHAR(20) | 製造指図番号 |
| `consumed_quantity` | NUMERIC(15,4) | 消費数量 |
| `consumed_unit` | VARCHAR(5) | 単位 |
| `parent_material_code` | VARCHAR(20) | 原料品目コード |
| `child_material_code` | VARCHAR(20) | 完成品品目コード |
| `consumed_at` | DATETIME | 消費（仕掛払出）日時 |

---

### 6-4. material_origin_change_logs テーブル（NEW）

| カラム | 型 | AI_TM 利用 |
|---|---|---|
| `material_code` | VARCHAR(20) | 対象原料品目コード |
| `from_country` | VARCHAR(2) | 旧原産国 |
| `to_country` | VARCHAR(2) | 新原産国 |
| `effective_date` | DATE | 切り替え有効日 |
| `old_vendor_code` | VARCHAR(20) | 旧仕入先コード |
| `new_vendor_code` | VARCHAR(20) | 新仕入先コード |
| `last_old_batch_code` | VARCHAR(50) | 旧原産国最終ロット |
| `first_new_batch_code` | VARCHAR(50) | 新原産国初回ロット |
| `affected_fg_codes_json` | TEXT | 影響完成品コード一覧 (JSON) |
| `max_deminimis_impact_pct` | NUMERIC(6,2) | De Minimis 最大影響率 |
| `exceeds_threshold` | BOOLEAN | 25% 閾値超過フラグ |
| `ai_tm_notification_sent` | BOOLEAN | AI_TM 通知済みフラグ |
| `ai_tm_case_ref` | VARCHAR(50) | AI_TM 付番ケース番号 |
| `review_status` | VARCHAR(20) | `PENDING` / `REVIEWED` / `ACTION_REQUIRED` |

---

### 6-5. lot_deminimus_assessments テーブル（NEW）

| カラム | 型 | AI_TM 利用 |
|---|---|---|
| `fg_batch_code` | VARCHAR(50) | 評価対象完成品ロット |
| `fg_material_code` | VARCHAR(20) | 完成品品目コード |
| `process_order_number` | VARCHAR(20) | 製造指図番号 |
| `us_origin_value` | NUMERIC(18,2) | US 原産原料の総額 (JPY) |
| `total_bom_value` | NUMERIC(18,2) | BOM 総原料費 (JPY) |
| `us_content_pct` | NUMERIC(6,2) | US 含有率 (%) |
| `threshold_pct` | NUMERIC(5,1) | 閾値 (デフォルト 25.0) |
| `alert_level` | VARCHAR(10) | `OK` / `WARNING`(>10%) / `BREACH`(>25%) |
| `us_components_json` | TEXT | US 原産原料内訳 (JSON) |
| `ai_tm_notified` | BOOLEAN | AI_TM 通知済み |
| `ai_tm_case_ref` | VARCHAR(50) | AI_TM ケース参照番号 |

---

### 6-6. sales_orders テーブル（更新なし）

| カラム | 型 | AI_TM 利用 |
|---|---|---|
| `export_check_status` | VARCHAR(20) | `PENDING`/`PASSED`/`BLOCKED`/`ERROR` |
| `export_check_ref` | VARCHAR(50) | AI_TM case_no（主参照キー）|
| `export_check_message` | TEXT | AI_TM からのメッセージ |

---

### 6-7. deliveries テーブル（更新なし）

| カラム | 型 | AI_TM 利用 |
|---|---|---|
| `aitm_case_no` | VARCHAR(50) | SO の case_no を引き継ぎ |
| `aitm_approval_status` | VARCHAR(20) | `APPROVED`/`BLOCKED`/`ERROR`/`PENDING` |

---

### 6-8. export_declarations テーブル（更新なし）

| カラム | 型 | AI_TM 利用 |
|---|---|---|
| `ai_tm_transaction_id` | VARCHAR(50) | AI_TM case_no |
| `license_type` | VARCHAR(20) | `NLR` / `BIS_LICENSE` / `EAR_LICENSE_EXCEPTION_LVS` |
| `license_authority` | VARCHAR(20) | `METI` / `BIS` |
| `eccn` | VARCHAR(20) | ECCN 番号 |
| `destination_country` | VARCHAR(2) | 仕向国 |

---

## 7. 処理フロー図

### フロー A: 受注 → 請求書発行（Order-to-Cash with AI_TM）

```
1. SalesOrder 作成
   ├─ is_denied_party チェック → BusinessRuleError ✗
   └─ GTSService.transaction_review()
       └─ POST :8011/api/transactions
           → case_no → so.export_check_ref
           → APPROVED: so.status = OPEN ✓
           → BLOCKED:  so.status = BLOCKED ✗

2. Delivery 作成 (SO が OPEN/RELEASED の場合)
   └─ GTSService.shipment_rescreen()
       ├─ GET :8011/transactions/search?q={case_no}
       ├─ POST :8011/ui/transactions/{id}/run-screening (再審査)
       └─ GET :8011/transactions/{id}
           → delivery.aitm_approval_status = APPROVED ✓ / BLOCKED ✗

3. BillingDocument 作成 (delivery.aitm_approval_status != "BLOCKED")
   └─ bill.aitm_case_no = delivery.aitm_case_no

4. PDF 発行
   ├─ Commercial Invoice (aitm_case_no 記載)
   └─ Export Declaration (export_check_ref 記載)
```

### フロー B: 原産国切り替え → De Minimis 評価 → AI_TM 通知（NEW）

```
原料ロット入荷 (GoodsReceipt)
   │
   └─ Batch.country_of_origin が前回ロットと異なる
       │
       ├─ MaterialOriginChangeLog 作成
       │   ├─ from_country / to_country
       │   ├─ affected_fg_codes (BOM 分析)
       │   └─ max_deminimis_impact_pct 計算
       │
       └─ LotDeMinimusAssessment 計算
           ├─ 全 FG バッチの US 含有率を BatchGenealogy から計算
           ├─ us_content_pct >= 25% → alert_level = BREACH
           │
           └─ POST /gts/origin-change-log/{id}/notify-aitm
               └─ GTSService.push_origin_change_to_aitm(payload)
                   └─ POST {AITM_URL}/events
                       → ai_tm_case_ref 保存
                       → 12 件の BREACH アセスメントをマーク
```

### フロー C: 外為法 BOM 判定

```
POST /gts/judge-bom
   └─ ComplianceSnapshotService.build()
       ├─ BOM コンポーネント一覧取得
       ├─ 各コンポーネントの ECCN / CoO / FEFTA 情報付与
       └─ POST :8011/gaihi/judge-bom
           → judgment (PERMITTED / REQUIRES_PERMIT / PROHIBITED)
           → controlled_components: [...] 
           → foreign_origin_share_percent: 41.9
```

---

## 8. ERP API エンドポイント一覧（AI_TM コネクター向け）

### AI_TM から ERP への Read API（認証要）

```
GET  /mm/batches                      # ロット一覧（origin/source_type フィルタ可）
GET  /mm/batches/{batch_code}/genealogy # ロット系譜（上流/下流）
GET  /gts/origin-change-log            # 原産国切り替えイベント
GET  /gts/deminimis                    # De Minimis 評価（alert_level フィルタ可）
GET  /sd/forecasts/summary             # 販売フォーキャスト vs 実績
GET  /qm/certificates                  # 品質証明書（CoA）
GET  /mdm/materials                    # 材料マスター（eccn/hs_code 含む）
GET  /mdm/business-partners            # 取引先マスター（is_denied_party 含む）
GET  /sd/sales-orders                  # 受注（export_check_status フィルタ可）
GET  /gts/export-declarations          # 輸出申告
```

### ERP から AI_TM へのトリガー API

```
POST /gts/origin-change-log/{id}/notify-aitm  # 原産国変更イベント通知
POST /gts/judge-bom                             # BOM 外為法判定
POST /gts/check-material/{material_id}          # 品目分類チェック
POST /sd/sales-orders/{id}/recheck-export       # SO 輸出審査再実行
```

### AI_TM → ERP Webhook

```
POST /gts/webhook/judgment-updated    # 審査完了 Webhook 受信（✅ 実装済み）
```

---

## 9. 環境変数

| 環境変数 | デフォルト値 | 説明 |
|---|---|---|
| `AITM_CLASSIFICATION_URL` | `http://localhost:8002` | ai_classification ベース URL |
| `AITM_SCREENING_URL` | `http://localhost:8005` | screening ベース URL |
| `AITM_VALIDATION_URL` | `http://localhost:8011` | ai_validation ベース URL |
| `AITM_ORG_ID` | `default-org` | `X-Organization-Id` ヘッダー値 |
| `AITM_USER_ID` | `erp-system` | `X-User-Id` ヘッダー値 |
| `AITM_WEBHOOK_SECRET` | — | Webhook 受信時 Bearer 検証キー |
| `AITM_USE_MOCK` | `false` | `true` = _MockClient 使用（テスト用）|

設定ファイル: `app/core/config.py` / `app/integrations/ai_trade_management/client.py`

---

## 10. エラーハンドリング

| シナリオ | ERP の動作 |
|---|---|
| AI_TM 接続不可 (ConnectionError) | `export_check_status = ERROR`、`so.status = BLOCKED` |
| `POST /api/transactions` → 422 | `export_check_status = ERROR`、メッセージを `export_check_message` に保存 |
| case_no 検索 → None | `delivery.status = BLOCKED`、`aitm_approval_status = BLOCKED` |
| `agent_judgment_status` が null | 明示的な BLOCKED でなければ APPROVED 扱い（非同期処理中）|
| `aitm_approval_status = BLOCKED` で請求書発行 | `BusinessRuleError` を raise |
| `push_origin_change_to_aitm()` 失敗 | `LOCAL-OCL-{material}-{uuid8}` のローカル参照番号で代替保存 |
| `post_event()` が 404 / 500 | ログに記録し `exceeds_threshold` 判定は ERP 内で完結、AI_TM は再送待ち |

---

## 11. テストデータ（現在 DB に投入済み）

### デモデータ概要（CLIENT_ID = DEMO）

| カテゴリ | 件数 | 期間 |
|---|---|---|
| 材料マスター | 28 品目 | — |
| 取引先マスター | 20 社 | — |
| 受注 (SalesOrder) | 159 件 | 2025-06 ～ 2026-06 |
| 納品書 (Delivery) | 110 件 | 2025-06 ～ 2026-06 |
| 請求書 (BillingDocument) | 110 件 | 2025-06 ～ 2026-06 |
| 輸出申告 | 179 件 | 2025-06 ～ 2026-06 |
| 購買発注 | 52 件 | 2025-06 ～ 2026-06 |
| 製造指図 (ProcessOrder) | 110 件 | 2025-06 ～ 2026-06 |
| 原料ロット (Batch/purchased) | 57 件 | 2025-06 ～ 2026-06 |
| 完成品ロット (Batch/produced) | 105 件 | 2025-06 ～ 2026-06 |
| ロット系譜 (BatchGenealogy) | 201 件 | 2025-06 ～ 2026-06 |
| 検査ロット (InspectionLot) | 59 件 | 2025-06 ～ 2026-06 |
| 品質証明書 (CoA) | 59 件 | 2025-06 ～ 2026-06 |
| 原産国切り替えログ | 2 件 | 2026-01, 2026-02 |
| De Minimis アセスメント BREACH | **12 件** | 2026-02 ～ |
| 販売フォーキャスト (PIR) | 48 件 | 2026-07 ～ 2026-12 |

### 注目シナリオ（AI_TM コネクター検証用）

#### シナリオ 1: 輸出規制品の受注 BLOCK
- 顧客: `CUST-CN-01`（中国）
- 品目: `MAT-3000001` (BOE エッチャント, ECCN **3C001**)
- 結果: `sales_orders.status = BLOCKED`, `export_check_status = BLOCKED`
- 輸出申告: `license_type = BIS_LICENSE` × 6 件

#### シナリオ 2: 原産国切り替え → De Minimis BREACH
- 品目: `MAT-9000004` (CMP 添加剤)
- 切り替え: JP (JSR) → US (DuPont) @ 2026-02-01
- 影響: `MAT-2000001` (CMP Slurry W) の US 含有率 **41.9%**（閾値 25% 超過）
- 対象ロット: `FG-2000001-600000007x` 系 12 件
- AI_TM 通知: 送信済み（`ai_tm_case_ref = LOCAL-OCL-MAT-9000004-C993B24B`）

#### シナリオ 3: PGMEA 原産国変更（閾値以下）
- 品目: `MAT-9000001` (PGMEA 溶剤)
- 切り替え: JP (Daicel) → US (Honeywell) @ 2026-01-01
- 影響: ArF フォトレジスト US 含有率 9.1%（閾値以下 → `review_status = REVIEWED`）
- AI_TM 通知: 未送信（BREACH なし）

### データ再生成コマンド

```bash
# 全初期データ
python scripts/seed_demo.py          # 材料・取引先マスター
python scripts/seed_history.py       # 12ヶ月トランザクション
python scripts/seed_lot_traceability.py  # ロット系譜・De Minimis

# 個別モジュール
python scripts/seed_co_qm.py         # CO・QM マスター
python scripts/seed_production_plan.py  # 生産計画（オープン製造指図）
python scripts/seed_dram_mcu.py      # DRAM/MCU 品目（試験用）
```

---

## 12. モック クライアント仕様

`AITM_USE_MOCK=true` 時（デフォルト）の `_MockClient` 動作:

| メソッド | モック動作 |
|---|---|
| `register_product()` | `{"ok": true, "id": hash(code) % 10000}` |
| `transaction_review()` | case_no = `MOCK-{hash(title):04d}`、非 RESTRICTED 国 → APPROVED |
| `shipment_rescreen()` | 常に APPROVED |
| `denied_party_check()` | RESTRICTED 国 (`IR/KP/RU/BY/SY`) → is_match=True |
| `export_check()` | RESTRICTED 国 → BLOCKED、3C001 → NEEDS_LICENSE |
| `judge_bom()` | controlled_components を ECCN から抽出 |
| `post_event()` | `{"case_ref": "MOCK-EVT-{uuid8}"}` |

RESTRICTED_COUNTRIES（モック）: `{"IR", "KP", "RU", "BY", "SY"}`

---

## 13. 未接続・今後の対応事項

| 項目 | 状態 | 対応内容 |
|---|---|---|
| Webhook 受信 | ✅ 実装済み | `POST /gts/webhook/judgment-updated` |
| 原産国切り替え AI_TM 通知 | ✅ 実装済み | `POST /gts/origin-change-log/{id}/notify-aitm` |
| ポーリングジョブ | 未実装 | PENDING 状態の SO/Delivery を定期チェックし判定結果更新 |
| 原産国変更の自動検知 | 未実装 | GoodsReceipt 投入時に前回ロットと CoO 比較して自動 MaterialOriginChangeLog 作成 |
| ai_classification product_id 保存 | 未実装 | `materials.aitm_product_id` カラム追加・同期後に ID 保存 |
| BP スクリーニング日時・リスト保存 | 未実装 | `business_partners` に `denied_party_checked_at`, `matched_list` 追加 |
| export_license 連携 (Port 8012) | 未接続 | ライセンス残高照会フロー |
| rnd_assessment 連携 (Port 8003) | 未接続 | みなし輸出人物登録フロー |
| De Minimis 自動再計算トリガー | 未実装 | GoodsReceipt 投入時に当該原料使用 FG バッチを再評価 |
| `affected_so_numbers_json` 連携 | 未実装 | BREACH アセスメントに紐付く影響 SO 番号の自動抽出 |
| AI_TM プル型 API 認証トークン管理 | 設計中 | ERP JWT の有効期限管理・リフレッシュ機構 |

---

## 14. 変更履歴

| 日付 | バージョン | 変更内容 |
|---|---|---|
| 2026-06-03 | ERP-AITM v1.0 | 初版作成。AI_TM v2.4 対応 TransactionCreateRequest スキーマ更新 |
| 2026-06-07 | ERP-AITM v1.1 | 出荷時再審査フロー実装完了。Delivery/Billing AITM カラム追加。PDF 出力実装。引き継ぎ書 ERP→AI_TM 視点で全面改訂 |
| 2026-06-27 | ERP-AITM **v2.0** | ロットトレーサビリティ基盤 (Batch/BatchGenealogy) 実装。原産国切り替えイベントログ・De Minimis 自動評価・AI_TM 通知実装。CO(原価管理)/QM(品質管理)/PIR(販売フォーキャスト) モジュール追加。Webhook 受信実装完了。過去 12 ヶ月ダミーデータ投入 (受注 159 件、輸出申告 179 件、ロット 162 件、De Minimis BREACH 12 件)。プル型 AI_TM→ERP Read API 新設 (§4) |

---

## 15. 問い合わせ先

| 担当 | 連絡先 |
|---|---|
| ERP 開発チーム | tsp0918@gmail.com |
| AI Trade Management チーム | AI Trade Management Platform 管理者 |

---

*ERP コードベース: `/Users/takehirosato/Desktop/erp-system/`*  
*AI_TM 統合クライアント: `app/integrations/ai_trade_management/`*  
*AI_TM API ドキュメント: `http://localhost:{port}/docs`（各モジュール）*
