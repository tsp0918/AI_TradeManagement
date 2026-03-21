# 開発ロードマップ — AI_TradeManagement
# 2026-03-21 更新（HSコードAI判定強化・ECCN付加・再ランキング全面実装）

> 本ドキュメントは実装済み機能の現状スナップショットと、今後の開発優先度を整理したものです。
> 2026-03-21 追加: HSコードAI判定精度改善（ECCN付加マッピング・見出し番号クラスタリング再ランキング・
> モーダルUI刷新・反映ボタン）、asyncio制裁スケジューラー、規制動向ウィジェット、
> みなし輸出×R&Dケース統合、HS同期エンドポイント、J-PlatPatフォールバックを反映。

---

## 1. モジュール構成と安定性（2026-03-21 時点）

| モジュール | ポート | DB | WAL | 安定性 | 備考 |
|-----------|--------|-----|-----|--------|------|
| platform-core | 8000 | PostgreSQL | — | ✅ | FAISS 3レイヤー・知識グラフ・規制スケジューラー |
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
| FAISS Layer A（外為法/ECCN） | services/faiss_e5_service.py | ✅ 2,040vec |
| FAISS Layer B（特許チャンク） | services/faiss_e5_service.py | ✅ 1,595vec |
| FAISS Layer C（HSコード） | services/faiss_e5_service.py | ✅ 5,476vec |
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

### P3: 次期開発バックログ

#### P3-1: Layer A 全体再ビルド（Colab）

```
背景:
  - e-Gov API で取得した 外為法省令（191 regulation ノード）を
    layer_a_meta.json に追加済みだが FAISS 本体は 2040vec のまま
  - macOS CPU では e5-large の OOM でビルド不可
  - Colab（GPU）環境で再ビルドが必要

作業:
  - layer_a_meta.json の faiss_id pending タグ付きエントリを embed
  - Layer A: 2040 → 2231 vec 見込み（+191件）
  - scripts/colab/06_rebuild_layer_a.ipynb 実行
```

#### P3-2: CISTEC公式対照表との照合（P1-3b）

```
背景:
  - hs_fefta_mapping_v2.json はキーワードマッチベースで生成
  - CISTEC「輸出令別表第一関係 HS 番号一覧表」公式版と照合が必要

作業:
  - CISTEC PDF 取得・パース
  - v2 マッピングの誤マッピング修正・カバレッジ向上
```

#### P3-3: 特許出願人の制裁リスト照合

```
概要: patent_search で取得した特許の出願人 → screening に自動照合
実装:
  - 特許メタデータの inventor/assignee → screening API 呼出
  - 「制裁リスト関連出願人の特許」フラグ → ai_validation pipeline に連携
```

#### P3-4: 月次制裁リスト cron 設定

```
概要: OFAC SDN XML / BIS Entity List の月次自動同期
現状: UI ボタン（POST /api/admin/sync-sanctions）で手動実行可能
実装: macOS launchd または Linux cron での定期実行
```

#### P3-5: HS判定精度さらなる向上

```
方針案:
  a) Layer C embed_text から FEFTA日本語ラベルを除外して再ビルド
     → FAISSノイズの根本解決（ただし Layer C 全再構築が必要）
  b) HS heading辞書との クロスチェック（Section/Chapter フィルタ）
  c) 英語クエリ変換（品目名・説明を EN に翻訳してから検索）
```

---

## 5. データソース品質評価（2026-03-21）

| データ | 優先度 | 現状 | 次のアクション |
|--------|--------|------|-------------|
| 外為法（FEFTA）省令 | ★★★★★ | ✅ 191/191 ノード充填済 | Colab で Layer A 全体再ビルド（P3-1） |
| ECCN/EAR Part774 | ★★★★☆ | ✅ 84/84 requirement_text 充填済 | 追加パラメータ精査（低優先） |
| 制裁リスト | ★★★★★ | ✅ OFAC/BIS 公式ソース対応済 | 月次 cron の設定（P3-4） |
| HS コード対照表 | ★★★★☆ | ✅ v2: 1,577件（6桁）・ECCN付加マッピング追加 | CISTEC 公式照合（P3-2） |
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
| Layer A 再ビルド未実施 | 外為法省令 191件が FAISS 未反映 | **P3-1** |
| HS↔外為法対照が近似（非公式） | HS分類の不確実性 | **P3-2** |
| Layer C embed_text に日本語FEFTA ラベル混入 | 再ランキングで緩和済みだが根本解決は再ビルド要 | **P3-5** |
| 制裁リスト自動同期が手動 | 月次更新漏れリスク | **P3-4** |
| 特許→制裁リスト照合未実装 | コンプライアンスギャップ | **P3-3** |

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

*更新: 2026-03-21*
*担当: Takehiro Sato + Claude Sonnet 4.6*
