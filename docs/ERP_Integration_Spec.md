# ERP ↔ AI Trade Management 連携仕様書

**バージョン**: 1.0  
**対象**: ERP 開発者・コンプライアンス担当者  
**AI_TM アダプター**: `http://localhost:5001` (本番: `https://app.tsp-aitrademanagement.com`)

---

## 1. 概要

ERP は AI Trade Management (AI_TM) のアダプター層 (port 5001) を通じてすべての輸出コンプライアンス処理を委譲する。
**ERP は AI_TM から OK が返るまで出荷手配を進めてはならない。**

```
ERP ──[取引伝票作成]──→ POST /transaction/review ──→ AI_TM 審査
ERP ──[出荷伝票作成]──→ POST /shipment/rescreen  ──→ AI_TM 再確認
AI_TM ──[判定更新]──→ POST :8888/gts/webhook/judgment-updated ──→ ERP
```

---

## 2. ERP 側に追加するデータフィールド

### 2-1. 取引伝票拡張テーブル `ZSD_AI_TM_LINK`

| フィールド名 | 型 | 説明 |
|---|---|---|
| `AI_TM_REVIEW_ID` | CHAR(36) | AI_TM 側の審査 UUID |
| `AI_TM_ITEM_ID` | CHAR(36) | AI_TM 側の品目 UUID |
| `AI_TM_PARTNER_ID` | CHAR(36) | AI_TM 側の取引先 UUID |
| `AI_TM_REVIEW_STATUS` | CHAR(20) | `APPROVED` / `REJECTED` / `NEEDS_REVIEW` / `PENDING` |
| `AI_TM_REVIEW_LEVEL` | CHAR(10) | `AUTO`（AI自動） / `MANUAL`（担当者承認済） |
| `AI_TM_ECCN` | CHAR(20) | AI_TM が判定した ECCN |
| `AI_TM_APPROVED_AT` | DATS+TIMS | 承認日時 |
| `AI_TM_EXPIRES_AT` | DATS+TIMS | 審査有効期限 |
| `AI_TM_LINKED_EXISTING` | CHAR(1) | `X` = 既存審査に紐づけ済み |
| `AI_TM_LAST_SYNC_AT` | DATS+TIMS | 最終同期日時 |

### 2-2. 出荷伝票拡張テーブル `ZSD_AI_TM_SHIP`

| フィールド名 | 型 | 説明 |
|---|---|---|
| `AI_TM_REVIEW_ID` | CHAR(36) | 紐づけた取引審査 UUID |
| `AI_TM_SHIPMENT_OK` | CHAR(1) | 出荷 OK フラグ (`X` = OK / `` = NG/未確認) |
| `AI_TM_RESCREEN_AT` | DATS+TIMS | 再スクリーニング実行日時 |
| `AI_TM_RESCREEN_RESULT` | CHAR(10) | `PASSED` / `CHANGED` |
| `AI_TM_BLOCK_REASON` | TEXT | ブロック理由（`CHANGED` の場合） |

---

## 3. API 仕様

すべてのリクエストに `Authorization: Bearer {AI_TM_API_KEY}` ヘッダーを付与すること。  
デフォルトキー: `dev-erp-integration-key`（本番環境では `.env` で変更）

### 3-1. 品目 HS 分類

```
POST /hs/classify
```

**リクエスト**

```json
{
  "description": "GaN HEMT Power Amplifier 40GHz",
  "material_code": "MAT-001",
  "country_of_origin": "JP"
}
```

**レスポンス**

```json
{
  "hs_code": "8542.33",
  "confidence": 0.87,
  "rationale": "Monolithic integrated circuits - Other"
}
```

---

### 3-2. 品目 該非判定

```
POST /gaihi/judge
```

**リクエスト**

```json
{
  "material_code": "MAT-001",
  "description": "GaN HEMT Power Amplifier 40GHz output power 5W",
  "hs_code": "8542.33",
  "chemical_composition": null
}
```

**レスポンス**

```json
{
  "judgment": "APPLICABLE",
  "eccn": "3A001",
  "item_number": "7",
  "rationale": "Top match score: 0.891 | ECCN: 3A001 | 外為法 7項",
  "requires_license": true
}
```

**副作用（バックグラウンド）**:
- AI_TM の `plat_item` に品目が自動登録・更新される
- `POST :8888/gts/webhook/judgment-updated` で ERP に判定結果がコールバックされる

---

### 3-3. ① / ② 取引伝票審査

```
POST /transaction/review
```

取引伝票作成時に呼び出す。  
既存の有効な審査があれば紐づけて即返す（①）、なければ新規審査を実行して返す（②）。

**リクエスト**

```json
{
  "erp_transaction_id": "SO-2026-0042",
  "item_code": "MAT-001",
  "item_name": "GaN HEMT Power Amplifier",
  "item_description": "GaN HEMT Power Amplifier 40GHz output power 5W CW",
  "hs_code": "8542.33",
  "eccn": null,
  "counterparty_name": "Beijing Tech Ltd",
  "counterparty_country": "CN",
  "counterparty_address": "No. 1 Zhongguancun St, Beijing",
  "destination_country": "CN",
  "quantity": 10,
  "value_usd": 50000
}
```

**レスポンス**

```json
{
  "review_id": "a1b2c3d4-...",
  "erp_transaction_id": "SO-2026-0042",
  "judgment": "NEEDS_REVIEW",
  "review_level": "AUTO",
  "review_completed": false,
  "approved": false,
  "linked_existing": false,
  "eccn": "3A001",
  "message": "Review requires manual confirmation."
}
```

**ERP 側処理**:
1. `AI_TM_REVIEW_ID` ← `review_id`
2. `AI_TM_REVIEW_STATUS` ← `judgment`
3. `AI_TM_REVIEW_LEVEL` ← `review_level`
4. `AI_TM_ECCN` ← `eccn`
5. `judgment == "APPROVED" && approved == true` の場合のみ出荷手配可能フラグを立てる
6. `judgment == "NEEDS_REVIEW"` の場合は出荷を **ブロック** し、担当者通知を送信する
7. `judgment == "REJECTED"` の場合は取引を **キャンセル** する

---

### 3-4. ③ 出荷伝票 再スクリーニング

```
POST /shipment/rescreen
```

出荷伝票作成時・出荷指示前に呼び出す。  
紐づいた審査レコードで制裁リストを再照合し、変化の有無で出荷 OK/NG を返す。

**リクエスト**

```json
{
  "review_id": "a1b2c3d4-...",
  "erp_shipment_id": "DEL-2026-0088"
}
```

**レスポンス（変化なし）**

```json
{
  "review_id": "a1b2c3d4-...",
  "erp_shipment_id": "DEL-2026-0088",
  "approved": true,
  "rescreen_changed": false,
  "judgment": "APPROVED",
  "message": "Re-screening passed. Shipment approved."
}
```

**レスポンス（変化あり）**

```json
{
  "review_id": "a1b2c3d4-...",
  "erp_shipment_id": "DEL-2026-0088",
  "approved": false,
  "rescreen_changed": true,
  "judgment": "NEEDS_REVIEW",
  "message": "Re-screening detected changes. Manual review required."
}
```

**ERP 側処理**:
1. `AI_TM_SHIPMENT_OK` ← `approved ? "X" : ""`
2. `AI_TM_RESCREEN_RESULT` ← `rescreen_changed ? "CHANGED" : "PASSED"`
3. `approved == false` の場合は出荷指示を **ブロック**し、AI_TM 担当者確認待ちにする
4. `approved == true` の場合のみ出荷指示を発行する

---

### 3-5. 制裁スクリーニング（単体）

```
POST /screening/denied-party
```

**リクエスト**

```json
{
  "name": "Beijing Tech Ltd",
  "country": "CN",
  "address": "No. 1 Zhongguancun St, Beijing"
}
```

**レスポンス**

```json
{
  "is_match": false,
  "list_name": null,
  "confidence": 0.0,
  "rationale": "No match found"
}
```

---

### 3-6. BOM 判定

```
POST /gaihi/judge-bom
```

**リクエスト**

```json
{
  "material_code": "ASSY-001",
  "plant_code": "JP01",
  "product_eccn": null,
  "components": [
    {
      "level": 1,
      "material_code": "MAT-001",
      "description": "GaN HEMT Chip",
      "quantity": "1",
      "unit": "PC",
      "hs_code": "8542.33",
      "eccn": null,
      "country_of_origin": "US"
    }
  ]
}
```

**レスポンス**

```json
{
  "judgment": "APPLICABLE",
  "aggregate_eccn": "3A001",
  "risk_factors": ["Controlled component (AI): MAT-001 score=0.891"],
  "controlled_components": ["MAT-001"],
  "foreign_origin_share_percent": 100.0,
  "rationale": "1/1 components controlled. Foreign origin: 100.0%."
}
```

---

### 3-7. AI_TM → ERP コールバック（受信側実装）

AI_TM が判定を更新した際、ERP の以下エンドポイントに通知する。

```
POST http://localhost:8888/gts/webhook/judgment-updated
```

**受信データ**

```json
{
  "material_code": "MAT-001",
  "new_judgment": "APPROVED",
  "new_eccn": "EAR99",
  "rationale": "Updated by compliance officer after manual review."
}
```

**ERP 側実装要件**:
- `material_code` で品目を特定し `ZSD_AI_TM_LINK` の審査ステータスを更新する
- `new_judgment == "APPROVED"` かつ出荷ブロック中の伝票があれば自動解除する
- `new_judgment == "REJECTED"` の場合は当該品目の全未出荷伝票をキャンセルする
- 受信後は HTTP 200 を返す（リトライなしの fire-and-forget）

---

## 4. 出荷ブロック制御ロジック

```
取引伝票作成
  └→ POST /transaction/review
       ├ APPROVED     → 出荷手配フラグ ON（ZSD_AI_TM_LINK.AI_TM_SHIPMENT_OK = 'X'）
       ├ NEEDS_REVIEW → 出荷ブロック。担当者通知。AI_TM で手動承認後にコールバック。
       └ REJECTED     → 取引キャンセル。

出荷指示
  └→ POST /shipment/rescreen
       ├ approved=true, changed=false  → 出荷指示発行 OK
       ├ approved=false, changed=true  → 出荷指示ブロック。担当者通知。
       └ approved=false, changed=false → 元の審査未完了（NEEDS_REVIEW）のまま継続ブロック
```

---

## 5. 品目マスタ同期（AI_TM → ERP）

AI_TM 側で「ERP へ品目同期」ボタンを押すと、AI_TM に登録されている ECCN 付き品目が
ERP の `/gts/webhook/judgment-updated` に一括プッシュされる。

**ERP 側で受け取るデータ例**

```json
{
  "material_code": "MAT-001",
  "new_judgment": "APPLICABLE",
  "new_eccn": "3A001",
  "rationale": "Sync from AI Trade Management. Name: GaN HEMT Power Amplifier"
}
```

**ERP 側実装要件**:
- `material_code` で品目マスタ (`MARA` / `Z_AI_TM_ITEM`) を検索し ECCN を更新する
- 存在しない品目は無視してよい（AI_TM から登録済みのものだけが対象）
- 成功 / 失敗を問わず HTTP 200 を返す

---

## 6. エラーハンドリング

| HTTP ステータス | 意味 | ERP 側処置 |
|---|---|---|
| 200 / 201 | 正常 | レスポンスを処理する |
| 400 | リクエスト不正 | ログ記録・担当者通知 |
| 401 | 認証エラー | `AI_TM_API_KEY` を確認 |
| 404 | 審査 ID 不明 | `review_id` を再取得 |
| 502 | 上流サービス障害 | リトライ（最大 3 回、指数バックオフ）|
| 500 | サーバーエラー | ログ記録・担当者通知・出荷ブロック継続 |

**重要**: AI_TM が応答しない・エラーを返す場合は、出荷を **進めずにブロック** すること。
タイムアウトはデフォルト 30 秒。

---

## 7. 環境変数（ERP 側設定）

```env
# AI_TM アダプター URL
AI_TM_BASE_URL=http://localhost:5001

# 認証キー（AI_TM の .env に設定されている値と一致させる）
AI_TM_API_KEY=dev-erp-integration-key

# コールバック受信ポート
AI_TM_CALLBACK_PORT=8888

# 審査タイムアウト秒数
AI_TM_TIMEOUT_SEC=30

# 取引審査有効期間（日数、この期間内は再審査不要）
AI_TM_REVIEW_VALID_DAYS=365
```

---

## 8. テスト手順

### 8-1. 疎通確認

```bash
curl http://localhost:5001/health
# → {"status": "ok", "upstream": {...}}
```

### 8-2. 取引伝票審査テスト

```bash
curl -X POST http://localhost:5001/transaction/review \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer dev-erp-integration-key" \
  -d '{
    "erp_transaction_id": "TEST-001",
    "item_code": "MAT-TEST",
    "item_name": "テスト品目",
    "item_description": "GaN Power Amplifier for defense radar",
    "counterparty_name": "Test Corp",
    "counterparty_country": "US",
    "destination_country": "US",
    "quantity": 1,
    "value_usd": 1000
  }'
```

### 8-3. 出荷再スクリーニングテスト

```bash
# review_id は 8-2 のレスポンスから取得
curl -X POST http://localhost:5001/shipment/rescreen \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer dev-erp-integration-key" \
  -d '{
    "review_id": "<review_id から取得>",
    "erp_shipment_id": "SHIP-TEST-001"
  }'
```

### 8-4. AI_TM 審査キュー確認

ブラウザで `http://localhost:8000/ui` → 「取引審査キュー」から審査結果・手動承認が可能。

---

## 9. 変更履歴

| 日付 | バージョン | 変更内容 |
|---|---|---|
| 2026-05-04 | 1.0 | 初版作成 |
