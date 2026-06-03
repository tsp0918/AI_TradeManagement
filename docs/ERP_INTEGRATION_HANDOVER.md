# AI Trade Management — ERP 連携インターフェース引き継ぎ書

**作成日**: 2026-06-03  
**対象システム**: AI Trade Management Platform v2.3  
**作成者**: 安全保障貿易管理チーム  
**宛先**: ERP 開発担当チーム

---

## 1. 概要・接続先

### システム全体構成

```
ERP システム
    │
    ▼  REST API (JSON over HTTPS)
┌───────────────────────────────────────────────────────┐
│             AI Trade Management Platform               │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │ rnd_assessment│  │ai_validation │  │ai_classif.  │ │
│  │ Port 8003    │  │ Port 8011    │  │ Port 8002   │ │
│  │ R&Dリスク評価│  │ 取引審査     │  │ 品目管理    │ │
│  └──────────────┘  └──────────────┘  └─────────────┘ │
│  ┌──────────────┐  ┌──────────────┐                   │
│  │  screening   │  │ platform-core│                   │
│  │ Port 8005    │  │ Port 8000    │                   │
│  │ 制裁照合     │  │ 共通基盤     │                   │
│  └──────────────┘  └──────────────┘                   │
└───────────────────────────────────────────────────────┘
```

### ベース URL

| 環境 | ベース URL |
|---|---|
| 本番（外部アクセス） | `https://app.tsp-aitrademanagement.com` |
| 開発（ローカル） | `http://localhost:8000` |

各モジュールへの直接アクセス（サーバー間）:

| モジュール | 役割 | ローカルURL |
|---|---|---|
| platform-core | 共通基盤・サプライチェーン | `http://localhost:8000` |
| ai_validation | 取引審査・AI判定 | `http://localhost:8011` |
| ai_classification | 品目管理・HS分類 | `http://localhost:8002` |
| rnd_assessment | R&Dリスク評価 | `http://localhost:8003` |
| screening | 制裁リストスクリーニング | `http://localhost:8005` |

---

## 2. 認証

現時点では HTTP Basic 認証なし（イントラネット限定アクセス前提）。  
ERP 連携ではリクエストヘッダーに組織・ユーザー情報を付与してください。

```http
X-Organization-Id: your-org-id
X-User-Id: erp-user@company.com
Content-Type: application/json
```

---

## 3. ERP 連携ユースケース別 API 仕様

### 3-1. 品目マスター連携（ai_classification）

#### 3-1-1. 品目登録

```
POST http://localhost:8002/api/products
```

**リクエスト本文:**
```json
{
  "code": "ERP-PRODUCT-001",          // ERP 品目コード（必須・一意）
  "name": "精密センサーユニット A型",   // 品目名（必須）
  "usage_summary": "工業計測用途。分解能0.1nm。最終使用地：日本・台湾。",
  "item_type": "equipment",           // equipment / component / software / material
  "eccn": "3A001",                    // ECCN番号（判明している場合）
  "hs_code": "9025190040",           // HSコード（日本・9桁）
  "ai_classification": "dual_use",   // dual_use / munitions / EAR99 / not_applicable
  "export_control_status": "not_evaluated"
}
```

**レスポンス:**
```json
{
  "id": 51,
  "code": "ERP-PRODUCT-001",
  "name": "精密センサーユニット A型",
  "export_control_status": "not_evaluated",
  "created_at": "2026-06-03T01:00:00"
}
```

**エラーコード:**
- `409 Conflict`: 同一 code が既存
- `422 Unprocessable Entity`: バリデーションエラー

---

#### 3-1-2. 品目取得（コード指定）

```
GET http://localhost:8002/api/products/by-code/{code}
```

例: `GET /api/products/by-code/ERP-PRODUCT-001`

**レスポンス:**
```json
{
  "id": 51,
  "code": "ERP-PRODUCT-001",
  "name": "精密センサーユニット A型",
  "eccn": "3A001",
  "hs_code": "9025190040",
  "export_control_status": "CLEAR",
  "ai_judgment_result": { ... }
}
```

---

#### 3-1-3. 国別規制プロファイル登録

```
POST http://localhost:8002/products/{product_id}/country-profiles
```

**対応国コード（17カ国）:**

| グループ | コード |
|---|---|
| 東アジア | JP / CN / KR / TW |
| 北米・欧州 | US / EU / GB / DE / FR / AU |
| ASEAN | TH / VN / ID / MY / SG / PH |
| 南アジア | IN |

**リクエスト本文:**
```json
{
  "country_code": "TH",
  "role": "destination",              // origin / destination / both
  "local_hs_code": "9025190000",     // 現地HSコード
  "local_eccn": "3A001",             // 現地ECCN（任意）
  "tariff_rate": 0.10,               // MFN関税率（0.10 = 10%）
  "epa_tariff_rate": 0.0,            // EPA/FTA優遇税率
  "epa_agreement": "AJCEP",         // 協定名
  "license_required": "no_license"   // no_license / nLR / license_required / prohibited
}
```

---

#### 3-1-4. BOM 登録

BOM は製品詳細ページのフォームから登録するか、JSON インポートで一括登録します。

```
POST http://localhost:8002/products/erp-sync
Content-Type: application/json
```

**リクエスト本文（ERP 一括インポート形式）:**
```json
[
  {
    "code": "ERP-PRODUCT-001",
    "name": "精密センサーユニット A型",
    "eccn": "3A001",
    "bom": [
      {
        "child_code": "COMP-LASER-001",
        "child_name": "905nm レーザーダイオード",
        "quantity": 1,
        "unit_value_usd": 380.0,
        "origin_country": "US",
        "supplier_name": "Lumentum Holdings"
      }
    ]
  }
]
```

---

### 3-2. R&D プロジェクト連携（rnd_assessment）

#### 3-2-1. R&D ケース作成

```
POST http://localhost:8003/api/v1/cases
```

**リクエスト本文:**
```json
{
  "title": "新素材センシングシステム NOVA-X",
  "external_project_id": "ERP-RND-2026-0042",  // ERP のプロジェクトコード
  "tenant_id": "your-tenant-id",
  "description": "開発内容の概要説明",
  "created_by_user_id": "yamada.hanako@company.com"
}
```

**レスポンス:**
```json
{
  "case_id": "f54b6dca-b992-49a2-a489-5ca2058d1ff1",
  "title": "新素材センシングシステム NOVA-X",
  "external_project_id": "ERP-RND-2026-0042",
  "status": "draft",
  "created_at": "2026-06-03T01:00:00"
}
```

> **注意**: `external_project_id` は promote-to-item（R&D→品目管理への転送）時に品目コードとして引き継がれます。ERP の品目コードを設定することで重複を防止できます。

---

#### 3-2-2. みなし輸出対象人物登録（新規 JSON API）

```
POST http://localhost:8003/api/v1/personnel
```

**リクエスト本文:**
```json
{
  "case_id": "f54b6dca-b992-49a2-a489-5ca2058d1ff1",  // 紐付けるケースID（任意）
  "tenant_id": "your-tenant-id",
  "name": "Kim Junho",
  "role": "researcher",              // researcher / employee / contractor
  "affiliation": "KAIST",
  "nationality": "KR",              // ISO 3166-1 alpha-2
  "residence_country": "JP",
  "years_in_japan": 2.5,
  "dual_employment_flag": false,
  "tech_access_eccn": "3E001",      // アクセスする技術の ECCN
  "tech_access_fefta": "16-2(1)",   // 外為法項番（任意）
  "note": "KAIST 共同研究者。週2回来訪"
}
```

**レスポンス（登録直後に自動スクリーニング実行）:**
```json
{
  "personnel_id": "88e77a75-3abd-4e97-a6a4-93e57e672647",
  "name": "Kim Junho",
  "nationality": "KR",
  "years_in_japan": 2.5,
  "deemed_export_category": "C",   // A: 二重雇用 / B: 技術指示 / C: 配偶者・在日
  "deemed_export_risk": "medium",  // high / medium / low / none
  "deemed_export_reason": ["在日年数 2.5年（5年未満）", "規制技術へのアクセス(3E001)"],
  "screened_at": "2026-06-03T01:28:53"
}
```

**人物一覧取得:**
```
GET http://localhost:8003/api/v1/personnel?case_id={case_id}
```

---

### 3-3. 取引審査連携（ai_validation）

#### 3-3-1. 取引審査案件作成（ERP 出荷情報から自動起票）

```
POST http://localhost:8011/api/transactions
```

**リクエスト本文:**
```json
{
  "case_no": "ERP-TX-2026-0123",      // ERP 受注番号・案件番号
  "counterparty_name": "Shanghai Tech Co., Ltd.",
  "destination_country": "CN",
  "product_code": "ERP-PRODUCT-001",
  "product_name": "精密センサーユニット A型",
  "product_id": 51,                    // ai_classification の品目ID
  "quantity": 10,
  "unit_price_usd": 5000.0,
  "total_value_usd": 50000.0,
  "intended_use": "工場自動化向けセンシングシステム",
  "end_user": "Shanghai Auto Factory",
  "end_user_country": "CN",
  "incoterms": "CIF",
  "hs_code": "9025190040"
}
```

**レスポンス:**
```json
{
  "id": 62,
  "case_no": "ERP-TX-2026-0123",
  "status": "pending",
  "catchall_status": null,
  "ai_status": null,
  "created_at": "2026-06-03T01:00:00"
}
```

---

#### 3-3-2. スクリーニング実行

```
POST http://localhost:8011/api/transactions/{id}/screening
```

**レスポンス:**
```json
{
  "ok": true,
  "screening_result": {
    "status": "no_match",   // no_match / possible_match / match
    "matched_entities": []
  }
}
```

---

#### 3-3-3. AI 判定実行

```
POST http://localhost:8011/api/transactions/{id}/ai-judge
```

**レスポンス:**
```json
{
  "status": "REVIEW",          // CLEAR / REVIEW / REQUIRES_PERMIT
  "catchall_status": "indeterminate",
  "matrix_matches": [
    {
      "rule_item_no": "16の2第1項",
      "decision": "intersection",
      "match_score": 0.82,
      "evidence": { "rule_item_no": "16の2第1項" }
    }
  ],
  "catchall_result": {
    "verdict": "indeterminate",  // clear / low / medium / high / requires_permit / indeterminate
    "risk_score": 2,
    "red_flag_answers": {}       // Red Flag 7項目が未回答の場合は空
  }
}
```

> **重要**: `catchall_status = "indeterminate"` の場合、Red Flag 7項目の回答が必要です（後述 §3-3-4）。

---

#### 3-3-4. キャッチオール詳細・Red Flag 情報取得

```
GET http://localhost:8011/api/transactions/{id}/catchall-detail
```

**レスポンス:**
```json
{
  "transaction_id": 62,
  "verdict": "indeterminate",
  "risk_score": 2,
  "country_group": "D:1",
  "ear_controls": ["NS1", "AT1"],
  "red_flag_answers": {
    "rf1_abnormal_use": null,    // null = 未回答
    "rf2_quantity": null,
    "rf3_payment": null,
    "rf4_routing": null,
    "rf5_use_change": null,
    "rf6_no_service": null,
    "rf7_unusual_destination": null
  },
  "recommendations": ["Red Flag 7項目の回答を入力して審査を完結してください"]
}
```

---

#### 3-3-5. 取引審査結果取得（ERP への結果連携）

```
GET http://localhost:8011/api/transactions/{id}
```

**レスポンス（審査完了後）:**
```json
{
  "id": 62,
  "case_no": "ERP-TX-2026-0123",
  "status": "reviewed",
  "ai_status": "REQUIRES_PERMIT",
  "catchall_status": "high",
  "final_judgment": "REQUIRES_PERMIT",
  "export_license_number": "H26-001234",
  "reviewed_at": "2026-06-03T10:00:00",
  "reviewer_id": "suzuki.saburo@company.com"
}
```

**ERP 連携ポーリング推奨間隔**: 30分ごと  
**Webhook 対応**: `POST /api/transactions/{id}/webhook` でコールバック URL を登録可能（status 変更時に ERP へ通知）

---

### 3-4. 制裁スクリーニング連携（screening）

#### 3-4-1. 一括スクリーニング（ERP 取引先リスト）

```
POST http://localhost:8005/api/screening/batch
```

**リクエスト本文:**
```json
{
  "entities": [
    {
      "name": "Shanghai Tech Co., Ltd.",
      "country": "CN",
      "entity_type": "company"
    },
    {
      "name": "Kim Junho",
      "country": "KR",
      "entity_type": "individual"
    }
  ],
  "sources": ["OFAC_SDN", "BIS_ENTITY", "METI_FUL", "EU_CONSOLIDATED"]
}
```

**レスポンス:**
```json
{
  "results": [
    {
      "name": "Shanghai Tech Co., Ltd.",
      "status": "no_match",      // no_match / possible_match / match / CRITICAL
      "score": 0.32,
      "matched_list": null
    },
    {
      "name": "Kim Junho",
      "status": "CRITICAL",
      "score": 0.857,
      "matched_list": "OFAC_SDN",
      "matched_entity": "KIM JUNHO (DPRK agent)"
    }
  ]
}
```

---

### 3-5. 輸出ライセンス管理連携（export_license）

#### 3-5-1. 輸出ライセンス登録

```
POST http://localhost:8012/api/export-licenses
```

**リクエスト本文:**
```json
{
  "transaction_id": 62,
  "application_number": "H26-001234",
  "license_type": "individual",     // individual / general / blanket
  "authority": "METI",             // METI / BIS / DDTC / EU_COMPETENT_AUTH
  "product_code": "ERP-PRODUCT-001",
  "destination_country": "CN",
  "end_user": "Shanghai Auto Factory",
  "value_usd": 500000.0,           // 許可総額
  "issued_at": "2026-06-01",
  "expires_at": "2027-05-31",
  "status": "approved"
}
```

#### 3-5-2. ライセンス残高照会（出荷管理）

```
GET http://localhost:8012/api/export-licenses/{id}/balance
```

**レスポンス:**
```json
{
  "id": "33ee7721-...",
  "application_number": "H26-001234",
  "value_usd": 500000.0,
  "used_value_usd": 50000.0,
  "remaining_value_usd": 450000.0,
  "remaining_ratio": 0.90,
  "expires_at": "2027-05-31",
  "days_until_expiry": 362,
  "alert_level": null           // null / warn (20%以下) / danger (7日以内)
}
```

---

## 4. ERP 連携フロー図

### フロー A: 新規品目マスター登録

```
ERP 品目マスター更新
    │
    ├─ POST /api/products           → 品目登録（product_id 取得）
    │
    ├─ POST /products/{id}/country-profiles × N カ国
    │       （TH/VN/ID/MY/SG等のASEAN + JP/US/CN/KR）
    │
    └─ POST /products/{id}/bom      → BOM 部材登録（US由来部材の把握）
```

### フロー B: 受注時 輸出審査起票

```
ERP 受注登録
    │
    ├─ POST /api/screening/batch    → 取引先・最終需要者のスクリーニング
    │       ↓ no_match 以外 → コンプライアンス担当へエスカレーション
    │
    ├─ POST /api/transactions       → 取引審査案件作成
    │
    ├─ POST /api/transactions/{id}/screening  → 制裁照合（取引審査と連動）
    │
    ├─ POST /api/transactions/{id}/ai-judge   → AI 判定実行
    │       ↓ 判定結果に応じて分岐
    │       CLEAR        → 出荷許可通知を ERP へ返却
    │       REVIEW       → 担当者が手動確認
    │       REQUIRES_PERMIT → UC6（輸出許可申請）フローへ
    │       indeterminate → Red Flag 7項目回答フローへ
    │
    └─ GET /api/transactions/{id}   → 最終審査結果を ERP へポーリング
```

### フロー C: R&D プロジェクト → 品目管理連携

```
ERP R&D プロジェクト起票
    │
    ├─ POST /api/v1/cases           → R&D ケース作成
    │       （external_project_id = ERP プロジェクトコードを設定）
    │
    ├─ POST /api/v1/personnel       → みなし輸出対象者の登録
    │       （登録直後に制裁スクリーニング + みなし輸出リスク自動算定）
    │
    └─ promote-to-item（ポータル操作）
            → ai_classification に品目を自動作成
            → product_code = external_project_id で引き継ぎ
```

---

## 5. データモデル・フィールド対応表

### 品目マスター対応

| ERP フィールド | AI Trade API フィールド | モジュール | 備考 |
|---|---|---|---|
| 品目コード | `code` | ai_classification | 一意制約あり |
| 品目名 | `name` | ai_classification | |
| 用途説明 | `usage_summary` | ai_classification | 工程/装置/性能/最終使用地の4要素を含めること |
| 品目区分 | `item_type` | ai_classification | equipment/component/software/material |
| ECCN | `eccn` | ai_classification | 例: 3A001 |
| HSコード | `hs_code` | ai_classification | 日本：9桁 |
| 仕向国HSコード | `local_hs_code` | country_profiles | 国別プロファイルで管理 |
| MFN関税率 | `tariff_rate` | country_profiles | 0.10 = 10% |
| EPA/FTA税率 | `epa_tariff_rate` | country_profiles | |
| 輸出許可要否 | `license_required` | country_profiles | no_license/nLR/license_required/prohibited |

### 取引審査対応

| ERP フィールド | AI Trade API フィールド | モジュール | 備考 |
|---|---|---|---|
| 受注番号 | `case_no` | ai_validation | |
| 取引先名 | `counterparty_name` | ai_validation | 英語正式法人名推奨 |
| 仕向国 | `destination_country` | ai_validation | ISO 3166-1 alpha-2 |
| 品目コード | `product_code` | ai_validation | ai_classification と連携 |
| 金額 | `total_value_usd` | ai_validation | USD 建て |
| 用途 | `intended_use` | ai_validation | |
| 最終需要者 | `end_user` | ai_validation | |
| 審査結果 | `ai_status` | ai_validation | CLEAR/REVIEW/REQUIRES_PERMIT |
| 許可証番号 | `export_license_number` | ai_validation | 許可取得後に更新 |

### みなし輸出人物対応

| ERP フィールド | API フィールド | モジュール | 備考 |
|---|---|---|---|
| 氏名 | `name` | rnd_assessment | |
| 所属 | `affiliation` | rnd_assessment | |
| 国籍 | `nationality` | rnd_assessment | ISO 3166-1 alpha-2 |
| 在日年数 | `years_in_japan` | rnd_assessment | 自動スクリーニングに使用 |
| 二重雇用フラグ | `dual_employment_flag` | rnd_assessment | true/false |
| アクセス技術ECCN | `tech_access_eccn` | rnd_assessment | |
| みなし輸出リスク | `deemed_export_risk` | rnd_assessment | APIが自動算定（読み取り専用） |

---

## 6. エラーハンドリング

### 標準エラーレスポンス形式

```json
{
  "detail": "エラーメッセージ（日本語）"
}
```

### 主要エラーコード

| HTTP Status | 意味 | 対応 |
|---|---|---|
| 400 Bad Request | リクエスト不正 | フィールド値を確認 |
| 404 Not Found | リソース不存在 | ID・コードを確認 |
| 409 Conflict | 重複登録（code重複等） | 既存レコードを UPDATE で更新 |
| 422 Unprocessable Entity | バリデーションエラー | `detail` の内容を確認 |
| 503 Service Unavailable | モジュール未起動 | ヘルスチェック実行 |

### ヘルスチェック

```
GET http://localhost:{port}/health
```

レスポンス: `{"status": "ok"}`

---

## 7. 開発要件・ERP 側対応事項

### 7-1. 必須対応事項（P0）

| No. | 要件 | 詳細 |
|---|---|---|
| 1 | **品目コード一致** | ERP の品目コードを `code` フィールドに設定し、`external_project_id`（R&Dケース）と整合させること |
| 2 | **スクリーニング連動** | 新規取引先を ERP に登録する際、`POST /api/screening/batch` を呼び出して制裁照合を実施すること |
| 3 | **審査結果ポーリング** | `GET /api/transactions/{id}` を30分ごとにポーリングして `ai_status` の変化を検知し、ERP の審査ステータスに反映すること |
| 4 | **みなし輸出人物同期** | 外国籍の研究者・共同研究者が ERP に登録された場合、`POST /api/v1/personnel` を呼び出すこと |

### 7-2. 推奨対応事項（P1）

| No. | 要件 | 詳細 |
|---|---|---|
| 5 | **Webhook 受信** | `POST /api/transactions/{id}/webhook` でコールバック URL を登録し、プッシュ通知でステータス変化を受け取ること（ポーリング削減） |
| 6 | **BOM 同期** | BOM 部材（特に US 由来部材）が ERP で更新された際、`/products/erp-sync` で同期すること（De Minimis 計算の精度維持） |
| 7 | **国別プロファイル同期** | ERP の仕向国マスターに国を追加した際、`/products/{id}/country-profiles` を更新すること |

### 7-3. データ品質要件

| フィールド | 品質要件 |
|---|---|
| `counterparty_name` | **英語正式法人名**を使用すること（スクリーニングの精度向上）。カタカナ・漢字名のみでは照合精度が低下する |
| `intended_use` | 工程・装置・性能・最終使用地の4要素を含めること（AI判定精度に直結） |
| `nationality` | ISO 3166-1 alpha-2（2文字コード）を使用すること（KR/CN/US等） |
| `destination_country` | ISO 3166-1 alpha-2（2文字コード）を使用すること |
| `value_usd` | De Minimis 計算に使用するため、実勢価格（販売価格ベース）で登録すること |

---

## 8. 変更履歴

| 日付 | バージョン | 変更内容 |
|---|---|---|
| 2026-06-03 | v2.3 | 初版作成。Personnel JSON API 追加、ASEAN 対応国拡充（5→17カ国）、Red Flag 連携フロー追加 |

---

## 9. 問い合わせ先

| 担当 | 連絡先 |
|---|---|
| 安全保障貿易管理チーム | tsp0918@gmail.com |
| システム管理 | AI Trade Management Platform 管理者 |

---

*このドキュメントは AI Trade Management Platform v2.3 のAPI仕様に基づきます。*  
*APIの最新仕様は各モジュールの `http://localhost:{port}/docs` で確認できます。*
