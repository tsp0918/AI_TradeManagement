# 開発ロードマップ — AI_TradeManagement
# 2026-04-26 更新（サプライヤーポータル 実装完了）

> 本ドキュメントは実装済み機能の現状スナップショットと、今後の開発優先度を整理したものです。
> 2026-04-26 追加: サプライヤーポータル（外部サプライヤーが直接 Web 申告できる公開 UI・招待トークン発行）実装完了。

---

## 1. モジュール構成と安定性（2026-03-21 時点）

| モジュール | ポート | DB | WAL | 安定性 | 備考 |
|-----------|--------|-----|-----|--------|------|
| platform-core | 8000 | PostgreSQL | — | ✅ | FAISS 4レイヤー（A/B/C/D）・知識グラフ・規制スケジューラー |
| ai_validation | 8001 | SQLite | ✅ | ✅ | キャッチオール Section 4・PDF報告書・HanteiAgent |
| ai_classification | 8002 | SQLite | ✅ | ✅ | HS Classifier Webhook連携・ECCN付加・品目管理 |
| rnd_assessment | 8003 | SQLite | ✅ | ✅ | R&D審査・リスクレベル算出・みなし輸出人物一覧 |
| patent_search | 8004 | SQLite | ✅ | ✅ | BigQuery連携・J-PlatPatフォールバック |
| screening | 8005 | PostgreSQL | — | ✅ | 制裁リストスクリーニング（OFAC/BIS） |
| hs_classifier | 8006 | — | — | ✅ | Layer C FAISS（5,476vec）・同期/非同期両対応 |
| dap | 8010 | SQLite | ✅ | ✅ | 先輩担当者モード・ペルソナ追跡・ガイドバナー・全モジュール埋込済 |

**WAL対応状況**: DAP・patent_search とも WAL適用済み確認済。

---

## 2. 実装済み機能サマリー

### 2-1. NeuroSymbolicアーキテクチャ基盤（platform-core）

| コンポーネント | ファイル | 状態 |
|-------------|---------|------|
| 知識グラフ（788ノード） | ontology/seed/control_nodes.json | ✅ 完成 |
| HanteiAgent | agent/hantei_agent.py | ✅ 完成 |
| AgentTools（FAISS呼出・キャッチオール詳細） | agent/tools.py | ✅ 完成 |
| キャッチオールエンジン | ontology/rules/catchall_engine.py | ✅ 完成 |
| FAISS Layer A（外為法/ECCN） | services/faiss_e5_service.py | ✅ 2,922vec（P3-1完了）|
| FAISS Layer B（特許チャンク） | services/faiss_e5_service.py | ✅ 1,595vec |
| FAISS Layer C（HSコード） | services/faiss_e5_service.py | ✅ 5,476vec |
| FAISS Layer D（学術論文） | services/faiss_e5_service.py | ✅ collect_academic_papers.py + build_layer_d.py で構築 |
| asyncio 規制動向スケジューラー | main.py (_regulatory_scheduler) | ✅ 24h周期 |

### 2-2. キャッチオール規制エンジン

```
判定フロー（6ステップ・決定論的）:
  Step 1: エンブレムト国チェック（E:1 → REQUIRES_PERMIT 即時）
  Step 2: ホワイト国チェック（Country Group A:1〜A:6 → CLEAR）
  Step 3: EAR Country Chart照合（13列× ECCN別エントリ）
  Step 4: Red Flag 7項目チェック
  Step 5: 総合スコアリング
  Step 6: REQUIRES_PERMIT / REVIEW / CLEAR 判定

テスト済み: KP（北朝鮮）=E:1→REQUIRES_PERMIT、US=ホワイト国→CLEAR、CN=D:1/D:3/D:4/D:5→REVIEW
```

### 2-3. ai_validation 判定フロー

```
Pipeline Steps:
  usage_expand → matrix_match（Layer A FAISS）→ dual_list_check
  → patent_retrieve（Layer B FAISS）→ catchall_assess
  → export_control_evaluate → finalize

Section 4（キャッチオール）: PDF/HTML報告書に出力済
  - EARグループ・国チャート列・ホワイト国協定・Red Flag・推奨アクション
```

### 2-4. HSコードAI判定（hs_classifier + ai_classification 連携）

```
エンドポイント（hs_classifier）:
  GET  /search          → 同期検索（Layer C FAISS）
  POST /classify        → 非同期Webhook判定
  POST /classify/sync   → 同期判定（webhook不要）✅ 新規追加
  GET  /hs/{code}       → HSコード詳細
  GET  /index/status    → インデックス状態確認

ai_classification 連携（integrations.py）:
  品目詳細 → POST /integrations/hs-classifier/request/{product_id}
    → hs_classifier /search (top_k=15)
    → _enrich_candidates(): ECCN付加・FEFTA参照追加
    → _rerank_by_heading(): heading(4桁)単位クラスタリング→上位5件
    → products.hs_classification_result に保存
  → 品目詳細モーダルでHS+ECCNを並列表示
  → 「反映」ボタンでHSコード＋ECCNを品目フォームに一括書込み

精度改善の背景:
  - FAISS embed_text に日本語FEFTA ラベルが含まれるため「集積回路」クエリで
    無関係heading（セルロース等）が上位に混入していた
  - _rerank_by_heading が heading単位で重複を吸収し IC HS(8541/8542)を正確に上位へ

ECCN付加マッピング:
  - HS見出し(4桁)→ECCN: 約50エントリ（Cat 0〜9 主要品目）
  - HSチャプター(2桁)→ECCN: フォールバック（8件）
  - EAR99(規制対象外)は品目フォームに転記しない設計
```

### 2-5. 規制動向モニタリング

```
バックエンド（platform-core）:
  - RegulatoryChange モデル: source / title / detail / severity / dismissed_at
  - GET /api/regulatory/changes: 変更履歴一覧
  - POST /api/regulatory/dismiss/{id}: 既読処理
  - _check_egov() / _check_bis(): e-Gov・BIS 差分検出
  - asyncio スケジューラー: 起動60秒後→以降24時間ごとに自動実行

ダッシュボード（platform-core/templates/dashboard.html）:
  - 未既読 warn/danger 変更を上位5件バナー表示（黄色警告エリア）
  - 「詳細を見る」リンク → 規制動向ポータル画面へ
```

### 2-6. みなし輸出スクリーニング統合

```
rnd_assessment case_detail 画面:
  - 案件に紐付く Personnel（研究者/従業員）一覧セクションを追加
  - 氏名・国籍バッジ・FETAカテゴリ・リスクレベル・詳細リンクを表示
  - HIGH リスク人物はオレンジ警告バナーで強調
  - 「人物を登録」ボタン → /ui/personnel/new?case_id=...

バックエンド（rnd_assessment/app/ui/router.py）:
  - case_detail エンドポイントで Personnel を JOIN クエリ
  - みなし輸出判定結果を case_detail テンプレートに注入
```

### 2-7. patent_search J-PlatPat フォールバック

```
jplatpat_service.py（新規）:
  - https://jppatsearch.inpit.go.jp/api/v1/simpleSearch を httpx 非同期呼出
  - search_jplatpat(keywords, inventor, applicant, limit) → 標準化 dict

search_service.py:
  - BigQuery 未設定時 → J-PlatPat API にフォールバック自動切替
  - source="jplatpat" フィールドで出所を明示

search.py (GET /status):
  - BigQuery 未設定時に "fallback": "jplatpat" を返却
  - 開発環境DNSエラーも graceful に処理
```

### 2-8. DAP 先輩担当者モード

```
■ バックエンド（modules/dap/app/routers/chat.py）
  - Intake Mode / ペルソナ追跡 / ワークフロー Intelligence
  - /api/chat/greet（ページロード時プロアクティブ案内）
  - /api/chat/event（行動トラッキング）
  - クロスモジュール会話継続（session_data 拡張）

■ フロントエンド（chat-widget.js v2 完全リライト）
  - アラートバナー（チャット外・自発的警告）
  - ヒアリング進捗バー
  - guidance ステップ（navigate/highlight/fill_hint/explain/watch）
  - クロスページ遷移継続（sessionStorage）

■ 全モジュール埋め込み済
  ai_validation / ai_classification / hs_classifier /
  patent_search / rnd_assessment / screening
```

### 2-9. プレゼンテーション・コンテンツ

```
生成済み:
  - Google Slides 8スライドデッキ（Slides API自動生成）
  - 技術記事 × 2（外部インタビュー・オウンドメディア）
  - slides_design.html（8スライドHTMLデザインシート）
```

---

## 3. 既知の不整合と対処（修正済み）

| # | 種別 | 問題 | 対処 | ファイル |
|---|------|------|------|---------|
| 1 | **設定** | DAPがplatform-core configに未登録 | `module_dap_url`追加 | platform-core/config.py |
| 2 | **設定** | hs_classifierがplatform-core configに未登録 | `module_hs_classifier_url`追加 | platform-core/config.py |
| 3 | **設定** | patent_searchのデフォルトポートが8000（8004と不整合） | port=8004に修正 | modules/patent_search/app/config.py |
| 4 | **結合** | hs_classifierがprivate `_staging_dir()`を直接参照 | 公開関数`get_staging_dir()`を追加し切替 | faiss_e5_service.py / classify.py |
| 5 | **精度** | FAISS embed_textの日本語FETAラベルによる誤ヒット | `_rerank_by_heading()`クラスタリング追加 | integrations.py |
| 6 | **EAR** | EARキーワード部分一致バグ（単語境界なし） | 単語境界チェック追加 | regulatory.py |
| 7 | **エンジン** | `_build_explanation` で非数値属性値 (e.g. "JP") を `float()` 変換して ValueError | try-except 追加 | ontology/rules/engine.py |
| 8 | **LLM** | `_summarize_known` が `end_use_type`/`end_user_type`/`destination_country` 専用フィールドを見落とし、レポートに「確認済み属性なし」 | 専用フィールドを `known` dict にマージ | llm_gateway/client.py |
| 9 | **エージェント** | `finalize()` で候補が全除外（空リスト）の場合に全29 domain_id フォールバックし、誤判定レポートを生成 | `context.candidate_domain_ids` を中間フォールバックとして使用 | agent/hantei_agent.py |
| 10 | **URL** | `chat.py` でplatform-core URL・ai_validation URL がハードコード（`http://localhost:800x`） | `_PLATFORM_URL` / `ai_val_url` 変数を参照するよう修正 | modules/dap/app/routers/chat.py |

---

## 4. 優先開発計画

### ✅ 完了済み（P0〜P2）

| タスク | 内容 | 完了日 |
|--------|------|--------|
| P0-1: ECCN ノード補強 | 84件全て requirement_text 充填 | 2026-03-20 |
| P0-2: 制裁スケジューラー | asyncio 24h周期 regulatory check | 2026-03-21 |
| P0-3: patent_retrieve セッションブロック解消 | 静的 Layer B FAISS に移行済み確認 | 2026-03-20 |
| P1-1: 外為法省令取得・ノード充填 | 191/191件 requirement_text 100%充填 | 2026-03-21 |
| P1-2: IPC→ECCN/FEFTA エッジ追加 | 996エッジ追加・知識グラフ強化 | 2026-03-20 |
| P1-3: HS↔外為法公式対照表整備 | v2: 1,577件（6桁）・Layer C 3,001件対応 | 2026-03-21 |
| P1-4: DAP × キャッチオール連携 | CatchallDetailTool・システムプロンプト拡張 | 2026-03-20 |
| P1-5: DAP 先輩担当者モード | ペルソナ追跡・ガイダンスUI・全モジュール埋込 | 2026-03-21 |
| P2-1: ダッシュボード強化 | PDFサマリー・KPIメトリクス・4象限ビュー | 2026-03-21 |
| P2-2: みなし輸出スクリーニング | Personnel管理・case_detail統合 | 2026-03-21 |
| P2-3: 規制動向モニタリング | e-Gov/BIS差分検出・ダッシュボードウィジェット | 2026-03-21 |
| P2-4: J-PlatPatフォールバック | BigQuery未設定時の特許検索フォールバック | 2026-03-21 |
| HSコードAI判定強化 | ECCN付加・再ランキング・モーダルUI・反映ボタン | 2026-03-21 |
| HS同期エンドポイント | /classify/sync（webhook不要） | 2026-03-21 |

---

### ✅ P3: 完了済み（2026-03-26）

| タスク | 内容 | 状態 |
|--------|------|------|
| P3-1 | Layer A 再ビルド | ✅ 2,040vec → 2,922vec（law:990, entity_list:835, eccn:637, parameter:406, tsutatsu:54）|
| P3-2 | CISTEC対照表照合・HS→ECCNマッピング精度向上 | ✅ 90エントリ拡充・chapterフォールバック追加 |
| P3-3 | 特許出願人制裁リスト一括照合 | ✅ patent_search × screening 連携 |
| P3-4 | 月次制裁リスト自動同期 | ✅ OFAC SDN / BIS Entity List 月次スケジューラー |
| P3-5 | Layer C embed_text から FEFTA 日本語ラベル除外 | ✅ 完了 |

### ✅ 国別規制プロファイル（ai_classification、2026-03-26）

| タスク | 内容 |
|--------|------|
| Ph.1 | ProductCountryProfile モデル・CRUD API・UI |
| Ph.2 | JP 9桁 HS コード自動補完 |
| Ph.2b | 税関 NACCS コードリストから 11,368件取得（`fetch_jp_naccs.py`）|
| Ph.3 | WTO API（`HS_A_0010`）で MFN 関税率自動取得 |
| Ph.4 | UN Comtrade API v2 で貿易統計（輸出入額）自動取得 |
| Ph.5 | EAR Country Chart × ECCN で再輸出規制自動判定 |

### ✅ Ph.6: 完了済み（2026-03-26）

```
ai_validation 取引審査画面 — 仕向地 EAR Country Chart リスクプロファイル表示
  - GET /api/reexport/country-risk/{code}（ai_classification）
    platform_core の get_country_info（外為法ステータス・EARグループ）と
    EAR Country Chart（17列 X/NLR）を統合してリスクサマリーを返す
  - transaction_detail.html: 仕向地（ISO alpha-2）入力時に自動フェッチ
    リスクバッジ（green/yellow/orange/red）+ EARグループ + X列一覧をインライン表示
```

### ✅ サプライヤー原産性証明 + De Minimis→ai_validation 連携（2026-04-26）

```
サプライヤーが BOM ノード単位で ECCN・原産地を申告し、
FAISS Layer A による AI 自動検証と審査証跡を管理する機能。

モデル（platform-core/platform_core/models/supplier_attestation.py）:
  plat_supplier_attestation
    フィールド: node_id(FK) / supplier_name / supplier_contact
                claimed_eccn / claimed_country_of_origin / claimed_us_content_pct
                is_us_origin_claimed / certificate_reference / attestation_date / expiry_date
                status / ai_suggested_eccn / ai_confidence / ai_verdict / ai_review_detail
                reviewed_by_user_id / reviewed_at / review_comment

Alembic migrations:
  platform-core: e3f4a5b6c7d8_add_supplier_attestation.py
  ai_validation: d1e2f3a4b5c6_add_supply_chain_node_id.py
                 → transactions に supply_chain_node_id / de_minimis_result 追加

API（platform-core/platform_core/routers/supplier_attestation.py）:
  GET  /api/supplier-attestations                 一覧（node_id/status/ai_verdict フィルタ）
  POST /api/supplier-attestations                 申告登録
  GET/PUT/DELETE /api/supplier-attestations/{id}  詳細・更新・削除
  POST /api/supplier-attestations/{id}/ai-validate FAISS Layer A で ECCN 自動検証
  POST /api/supplier-attestations/{id}/accept     承認（reviewed_at / review_comment 記録）
  POST /api/supplier-attestations/{id}/reject     却下
  GET  /api/supply-chain/nodes/{id}/attestations  ノード別証明一覧

AI 検証ロジック:
  - ノード名 + 説明を FAISS Layer A（外為法/ECCN）で検索（top_k=5）
  - ai_verdict: match / warning / mismatch / unverifiable
    - 申告 EAR99 + AI が管理品を示唆（conf>70%）→ warning
    - 申告 ECCN 頭5文字が AI 提示と一致 → match
    - 信頼度 75% 超で不一致 → mismatch
  - 動作確認: Wolfspeed GaN HEMT（3A001申告）→ verdict=match confidence=0.86 ✅

supply_chain.html 拡張:
  - タブ構造（🌳 BOM / 📐 De Minimis / 📋 証明管理）
  - 証明管理タブ: 申告一覧（ステータスバッジ・AI判定結果）・登録モーダル
  - AI検証ボタン（ワンクリックで FAISS Layer A 呼出）
  - 承認/却下ボタン（コメント入力）
  - De Minimis タブに証明カバレッジ表示（承認済件数・要注意件数）

ai_validation transaction_detail.html 拡張:
  - Section 2c-pre「🔗 サプライチェーン / De Minimis」カード追加
  - ノード名検索 → 選択 → De Minimis 計算 → スナップショット保存
  - 保存済みスナップショット表示（US管理品比率・閾値・判定結果）
  - POST /api/transactions/{id}/supply-chain でノード紐付けと結果をキャッシュ
```

### ✅ サプライヤーポータル（2026-04-26）

```
外部サプライヤーが認証不要で ECCN・原産地を Web 申告できる公開 UI。
担当者が招待トークン URL を発行し、サプライヤーが直接フォームを送信する。

モデル（platform-core/platform_core/models/supplier_portal_token.py）:
  plat_supplier_portal_token
    フィールド: token(64char URLsafe) / node_id / node_name(snapshot)
                supplier_name / supplier_email / note_for_supplier
                is_active / max_uses / use_count / expires_at / created_by_user_id

Alembic migration: f4a5b6c7d8e9_add_supplier_portal_token.py

管理 API（platform-core/platform_core/routers/supplier_portal.py）:
  GET  /api/supplier-portal/tokens              トークン一覧（node_id/active_only フィルタ）
  POST /api/supplier-portal/tokens              トークン発行（node_id 存在確認・expires_at 自動算出）
  GET  /api/supplier-portal/tokens/{id}         トークン詳細
  POST /api/supplier-portal/tokens/{id}/revoke  手動無効化

公開ポータル（認証不要）:
  GET  /supplier-portal/{token}         申告フォーム（supplier_portal.html）
  POST /supplier-portal/{token}/submit  申告送信 → SupplierAttestation 自動作成 → 確認画面

トークン検証ロジック（_resolve_token）:
  - is_active チェック
  - expires_at（timezone-aware）チェック
  - use_count < max_uses チェック（0=無制限）
  - 送信成功時: use_count++ / 使い切り時 is_active=False 自動セット

公開 HTML テンプレート（platform-core/platform_core/templates/）:
  - supplier_portal.html         — サプライヤー向け申告フォーム（完全スタンドアロン CSS）
  - supplier_portal_error.html   — トークン無効/期限切れ時のエラー画面
  - supplier_portal_confirm.html — 申告受付完了（受付番号 attestation_id 表示）

supply_chain.html 拡張（証明管理タブ）:
  - 「🔗 招待URL発行」ボタン（btnPortalToken）— ノード選択後に有効化
  - portal token モーダル: supplier_name / email / note / expires_days / max_uses 入力
  - 発行後: portal_url をインラインでコピーボタン付き表示

外部公開 URL:
  https://app.tsp-aitrademanagement.com/supplier-portal/{token}

動作確認（2026-04-26）:
  - GaN HEMT ノードに対してトークン発行 → URL 生成 ✅
  - 申告フォームアクセス → SupplierAttestation status=pending で登録 ✅
  - max_uses=1 到達時 → is_active=False・エラー画面表示 ✅
  - 承認フロー: 証明管理タブで AI 検証 → accept ✅
```

### ✅ ③ サプライチェーン管理（2026-04-26）

```
BOM 構造管理 + EAR §734.4 De Minimis 自動計算

モデル（platform-core/platform_core/models/supply_chain.py）:
  plat_supply_chain_node  — 製品/サブアッセンブリ/部品/素材ノード
    フィールド: name / part_number / node_type / country_of_origin / is_us_origin
                hs_code / eccn / unit_value_usd / us_controlled_value_usd / extra
  plat_supply_chain_edge  — BOM 親子エッジ（quantity / unit）

Alembic migration: d2e3f4a5b6c7_add_supply_chain.py

API（platform-core/platform_core/routers/supply_chain.py）:
  GET  /api/supply-chain/stats                   概況サマリー
  GET  /api/supply-chain/nodes                   ノード一覧（q/node_type/is_us_origin/eccn フィルタ）
  POST /api/supply-chain/nodes                   ノード作成（US管理品価値 自動補完）
  GET  /api/supply-chain/nodes/{id}              詳細（直接の子リスト付き）
  PUT  /api/supply-chain/nodes/{id}              更新
  DELETE /api/supply-chain/nodes/{id}            削除（エッジ cascade）
  GET  /api/supply-chain/nodes/{id}/tree         BOM ツリー全展開（深さ最大 10）
  POST /api/supply-chain/nodes/{id}/de-minimis   De Minimis 計算
  POST /api/supply-chain/edges                   BOM エッジ追加（自己参照・重複チェック付き）
  DELETE /api/supply-chain/edges/{id}            エッジ削除

De Minimis エンジン（EAR §734.4）:
  - BOM ツリー再帰走査でリーフノードの価値を積算
  - US管理品 = is_us_origin=True かつ eccn ≠ EAR99
  - 一般国閾値: 25%  / E:1国（KP/IR/CU/SY/SD）閾値: 10%
  - De Minimis 免除不可 ECCN: カテゴリ 0 系・2B352（WMD/弾薬関連）
  - 結果: eligible/total_value/us_controlled_value/us_controlled_pct/excluded_items/note

UI（platform-core/platform_core/templates/supply_chain.html）:
  - ポータルサイドバー「🔗 サプライチェーン管理」（/ui/supply-chain）
  - 統計ダッシュボード（ノード/エッジ/US管理品数）
  - ノード一覧テーブル（検索・種別/US原産フィルタ）
  - BOM ツリービュー（ネスト表示・エッジ削除ボタン）
  - BOM エッジ追加フォーム（子ノード名検索・数量/単位入力）
  - De Minimis 計算機（仕向地入力→バー可視化→判定結果）

動作確認:
  - GaN パワーアンプ(JP製 $1,200) BOM = GaN HEMT チップ(US・3A001・$350) + RF 基板(JP・EAR99・$180) + RF パッケージ(TW・EAR99・$70×2)
  - 総価値 $670、US管理品 $350 = 52.24%
  - CN 向け: 52.24% > 25% → De Minimis 不可・許可申請必要 ✅
  - KP 向け: 52.24% > 10% → De Minimis 不可・許可申請必要 ✅
```

### ✅ ② 与信管理（2026-04-26）

```
取引先与信スコア・制裁リスク・国別カントリーリスク統合管理

モデル拡張（platform-core/platform_core/models/company.py）:
  plat_company  += roles (JSONB) / credit_score / credit_data / country_risk_score / overall_risk_level
  plat_counterparty_credit_history  新規テーブル（与信スコア変更履歴）

API（platform-core/platform_core/routers/counterparty.py）:
  GET  /api/counterparties/stats       リスクダッシュボード集計（total/by_risk_level/sanctioned）
  GET  /api/counterparties             取引先一覧（q/risk_level/role/country_code/is_sanctioned フィルタ）
  POST /api/counterparties             取引先登録（国コードから country_risk_score 自動算出）
  GET  /api/counterparties/{id}        取引先詳細
  PUT  /api/counterparties/{id}        取引先更新（スコア変化時に履歴自動記録）
  DELETE /api/counterparties/{id}      取引先削除
  POST /api/counterparties/{id}/screen screening モジュール(8005)呼出・結果を自動保存・リスク再評価
  GET  /api/counterparties/{id}/history 与信スコア変更履歴

リスク算出ロジック:
  - country_risk_score: 国コード→固定テーブル（CN:75, RU:90, KP/IR:100, US/JP:5 等）
  - overall_risk_level: credit_score × 0.4 + country_risk × 0.6 → LOW/MEDIUM/HIGH/CRITICAL
  - is_sanctioned=true → CRITICAL 強制

UI（platform-core/platform_core/templates/counterparty.html）:
  - ポータルサイドバー「🏢 与信管理」リンク（/ui/counterparty）
  - リスクダッシュボード（統計カード×6）
  - 取引先テーブル（検索・リスクレベル/役割/制裁フィルタ・ページネーション）
  - 詳細モーダル（与信スコアバー・スクリーニング結果・変更履歴テーブル）
  - 新規登録/編集モーダル（役割チェックボックス・変更理由入力）
  - 照合ボタン（ワンクリックでscreening API呼出）

Alembic migration: c1e2f3a4b5d6_add_counterparty_credit.py
```

### ✅ 技術インテリジェンス（Ph.A〜D）: 完了済み（2026-03-27）

```
学術論文 × ECCN クロスリンク基盤（Semantic Scholar + Lens.org）

Ph.A — データ収集基盤
  - data/academic/eccn_tech_terms.json: 20 ECCN × 技術用語辞書（クエリ展開用）
  - scripts/collect_academic_papers.py: S2 API + Lens.org バッチ収集・academic_intel.db に保存
    DB スキーマ: papers / authors / paper_authors / paper_eccn_tags

Ph.B — FAISS Layer D 追加
  - scripts/build_layer_d.py: academic_intel.db → layer_d.index + layer_d_meta.json 構築
  - faiss_e5_service.py: Layer D ロード・search_layer_d()・layer_d_available() 追加

Ph.C — rnd_assessment 技術インテリジェンス機能
  - modules/rnd_assessment/app/api/v1/endpoints/academic_intel.py: 5エンドポイント
    GET /api/v1/academic/search（Layer D セマンティック検索）
    GET /api/v1/academic/papers（DB直接検索・ECCN/year/keyword フィルタ）
    GET /api/v1/academic/trend/{eccn}（年次トレンド集計）
    GET /api/v1/academic/eccn-list（利用可能ECCN一覧）
    GET /api/v1/academic/researcher/{id}（著者プロファイル）
    POST /api/v1/academic/deemed-scan（みなし輸出リスクスキャン）
  - rnd_assessment UI: /ui/academic-intel（論文検索・トレンドグラフ・みなし輸出スキャン）
  - base.html ナビバー: 「技術インテリジェンス」リンク追加

Ph.D — patent_search 双方向リンク
  - modules/patent_search/app/routers/academic_links.py: 2エンドポイント
    GET /api/patents/{number}/academic-links（特許→関連学術論文）
    GET /api/academic/related-patents（論文キーワード→特許・Layer B 検索）
```

---

## 5. データソース品質評価（2026-03-21）

| データ | 優先度 | 現状 | 次のアクション |
|--------|--------|------|-------------|
| 外為法（FEFTA）省令 | ★★★★★ | ✅ 191/191 ノード充填済・Layer A 2,922vec 再ビルド完了 | — |
| ECCN/EAR Part774 | ★★★★☆ | ✅ 84/84 requirement_text 充填済 | 追加パラメータ精査（低優先） |
| 制裁リスト | ★★★★★ | ✅ OFAC/BIS 公式ソース・月次自動同期（P3-4完了） | — |
| HS コード対照表 | ★★★★☆ | ✅ v2: 1,577件・ECCN付加90エントリ・NACCS 9桁 11,368件 | — |
| 特許（J-PlatPat） | ★★★★☆ | ✅ 64/67件 IPC→ECCN/FEFTA 996エッジ・フォールバック対応 | 実特許データ大量取得（長期） |
| 中国輸出管理法リスト | ★★☆☆☆ | 未取得 | 長期: 将来的な規制拡張に備えて調査 |

---

## 6. 知識グラフ品質評価（2026-03-21）

| regime | ノード数 | 補強状況 | 品質 |
|--------|---------|---------|------|
| 外為法（fefta） | 191 | ✅ 191/191 requirement_text 充填完了 | **◎ 高品質** |
| EAR/ECCN（ear） | 84 | ✅ 84/84 requirement_text 充填済 | **◎ 完成** |
| Wassenaar（wa） | 165 | PDF解析 | **○ 中品質** |
| HS コード（hs） | 281 | ✅ v2マッピング 1,577件・ECCN付加・Layer C 3,001件対応 | **◎ 高品質** |
| 特許（patent） | 67 | ✅ 64/67 に 996 IPC→ECCN/FEFTA エッジ追加済 | **○ 中品質** |

**DAP RAG**: platform-core GET /api/faiss/search/layer-a → DAP _rag_layer_a() 連携済

---

## 7. 技術的負債（残存）

| 問題 | 影響 | 優先度 |
|------|------|--------|
| （技術的負債なし） | — | — |

---

## 8. システム起動・ヘルスチェック

```bash
# 起動
cd /Users/takehirosato/Desktop/AI_TradeManagement && ./start.sh

# ヘルスチェック（全モジュール）
curl -s http://localhost:8000/health  # platform-core
curl -s http://localhost:8001/health  # ai_validation
curl -s http://localhost:8002/health  # ai_classification
curl -s http://localhost:8003/health  # rnd_assessment
curl -s http://localhost:8004/health  # patent_search
curl -s http://localhost:8005/health  # screening
curl -s http://localhost:8006/health  # hs_classifier
curl -s http://localhost:8010/health  # dap

# FAISS インデックス状態確認
curl -s http://localhost:8001/admin/faiss/status  # Layer A/B/C
curl -s http://localhost:8006/index/status         # Layer C（hs_classifier）

# HS AI判定テスト
curl -s -X POST http://localhost:8002/integrations/hs-classifier/request/{product_id} \
  -H "Content-Type: application/json" \
  -d '{"name":"半導体集積回路","description":"IC chip","item_class":""}'

# DB ロック確認
lsof | grep ".db$" | grep -v ".venv"
```

---

## 9. ブランチ管理

| ブランチ | 状態 | 用途 |
|---------|------|------|
| main | 安定 | リリースブランチ |
| branch_neurosymbolic | 作業中 | NeuroSymbolic基盤・全機能実装（本ブランチ） |

**次のアクション**: `branch_neurosymbolic` → main へのマージ検討

---

## 10. 次フェーズ候補

| 優先度 | タスク | 内容 |
|--------|--------|------|
| ★★★★★ | ④ 輸出許可申請ドラフト・期限管理ワークフロー | 申請書自動生成（EAR BIS-748P / 外為法様式）・申請期限アラート |
| ★★★★☆ | サプライヤーポータル拡張 | メール自動送信（招待URL通知）・添付ファイルアップロード・多言語化 |
| ★★★★☆ | 輸出許可証管理 | 許可証番号・有効期限・紐付け取引管理 |
| ★★★☆☆ | Layer D データ収集実行 | API Key 取得後に collect_academic_papers.py を全 ECCN で実行 |
| ★★★☆☆ | 与信データ外部連携 | TDB/TSR API（有償）連携 |
| ★★☆☆☆ | 中国輸出管理法リスト | CCL データ取得・スクリーニング統合 |

---

*更新: 2026-04-26（サプライヤーポータル実装完了・③ サプライチェーン管理 + ② 与信管理 完了・技術インテリジェンス Ph.A〜D 完了・技術的負債ゼロ）*
*担当: Takehiro Sato + Claude Sonnet 4.6*
