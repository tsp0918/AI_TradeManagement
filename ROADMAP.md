# 開発ロードマップ — AI_TradeManagement
# 2026-03-21 更新（DAP 先輩コレーグ・ペルソナ追跡・ガイダンスUI全面実装）

> 本ドキュメントは実装済み機能の現状スナップショットと、今後の開発優先度を整理したものです。
> 2026-03-21 追加: DAP 先輩担当者モード（ペルソナ追跡・ワークフロー分析・ガイドバナー・
> クロスモジュールガイドツアー）・全モジュールへの chat-widget.js 埋め込みを反映。

---

## 1. モジュール構成と安定性（2026-03-20 時点）

| モジュール | ポート | DB | WAL | 安定性 | 最終更新 |
|-----------|--------|-----|-----|--------|---------|
| platform-core | 8000 | PostgreSQL | — | ✅ | FAISS e5-large 3レイヤー・知識グラフ・キャッチオールエンジン |
| ai_validation | 8001 | SQLite | ✅ | ✅ | キャッチオールSection 4・PDF報告書・HAnteiAgent連携 |
| ai_classification | 8002 | SQLite | ✅ | ✅ | HS Classifier Webhook連携・品目管理 |
| rnd_assessment | 8003 | SQLite | ✅ | ✅ | R&D審査・リスクレベル算出 |
| patent_search | 8004 | SQLite | ✅ | ✅ | BigQuery連携・FAISS特許検索 |
| screening | 8005 | PostgreSQL | — | ✅ | 制裁リストスクリーニング |
| hs_classifier | 8006 | — | — | ✅ | Layer C FAISS（5,476ベクトル）・同期/非同期両対応 |
| dap | 8010 | SQLite | ✅ | ✅ | 先輩担当者モード・ペルソナ追跡・ガイドバナー・全モジュール埋込済 |

**WAL対応状況（2026-03-08からの変更）**: DAP・patent_search とも WAL適用済みと確認。
旧ROADMAPの「❌ 未適用」表記は誤りだった。技術的負債として残っていた当該項目はクローズ。

---

## 2. 実装済み機能サマリー

### 2-1. NeuroSymbolicアーキテクチャ基盤（platform-core）

| コンポーネント | ファイル | 状態 |
|-------------|---------|------|
| 知識グラフ（788ノード） | ontology/seed/control_nodes.json | ✅ 完成 |
| HanteiAgent | agent/hantei_agent.py | ✅ 完成 |
| AgentTools（FAISS呼出） | agent/tools.py | ✅ 完成 |
| キャッチオールエンジン | ontology/rules/catchall_engine.py | ✅ 完成 |
| FAISS Layer A（外為法/ECCN） | services/faiss_e5_service.py | ✅ 2,040vec |
| FAISS Layer B（特許チャンク） | services/faiss_e5_service.py | ✅ 1,595vec |
| FAISS Layer C（HSコード） | services/faiss_e5_service.py | ✅ 5,476vec |

### 2-2. キャッチオール規制エンジン

```
判定フロー（6ステップ・決定論的）:
  Step 1: エンブレムト国チェック（E:1 → REQUIRES_PERMIT 即時）
  Step 2: ホワイト国チェック（Country Group A:1〜A:6 → CLEAR）
  Step 3: EAR Country Chart照合（13列× ECCN別エントリ）
  Step 4: Red Flag 7項目チェック
  Step 5: 総合スコアリング
  Step 6: REQUIRES_PERMIT / REVIEW / CLEAR 判定

テスト済みケース: KP（北朝鮮）= E:1確認→REQUIRES_PERMIT、US=ホワイト国→CLEAR、CN=D:1/D:3/D:4/D:5確認→REVIEW
```

### 2-3. ai_validation 判定フロー

```
Pipeline Steps:
  usage_expand → matrix_match（Layer A FAISS） → dual_list_check
  → patent_retrieve（Layer B FAISS） → catchall_assess（Step新規）
  → export_control_evaluate → finalize

Section 4（キャッチオール）: PDF/HTML報告書に出力済
  - EARグループ・国チャート列・ホワイト国協定・Red Flag・推奨アクション
```

### 2-4. hs_classifier Layer C統合

```
エンドポイント:
  GET  /search      → 同期検索（Layer C FAISS）
  POST /classify    → 非同期Webhook判定
  GET  /hs/{code}   → HSコード詳細
  GET  /index/status → インデックス状態確認

ai_classification連携:
  品目登録 → POST /classify → Webhook → product.hs_code_candidates更新
```

### 2-5. プレゼンテーション・コンテンツ

```
生成済み:
  - Google Slides 8スライドデッキ（Slides API自動生成）
  - デモ動画 Drive埋め込み
  - 技術記事 × 2（外部インタビュー・オウンドメディア）
  - slides_design.html（8スライドHTMLデザインシート）
```

---

## 3. 既知の不整合と対処（2026-03-20 修正済み）

| # | 種別 | 問題 | 対処 | ファイル |
|---|------|------|------|---------|
| 1 | **設定** | DAPがplatform-core configに未登録 | `module_dap_url`追加 | platform-core/config.py |
| 2 | **設定** | hs_classifierがplatform-core configに未登録 | `module_hs_classifier_url`追加 | platform-core/config.py |
| 3 | **設定** | patent_searchのデフォルトポートが8000（8004と不整合） | port=8004に修正 | modules/patent_search/app/config.py |
| 4 | **結合** | hs_classifierがprivate `_staging_dir()`を直接参照 | 公開関数`get_staging_dir()`を追加し切替 | faiss_e5_service.py / classify.py |
| 5 | **監視** | Layer C未ロード時に`ntotal=0`と表示され区別不可 | `layer_c_available`フィールドを追加 | ai_validation/routers/admin.py |

---

## 4. 優先開発計画（P0〜P2）

### P0: 即時対応 — **2026-03-20 セッションで全項目完了**

#### P0-1: ECCN ノード補強 ✅ 完了

```
実施内容（2026-03-20）:
  - 事前調査で「71件スタブ」は誤り → 実際は 5件のみスタブが残存
  - 5件（1B001/1B102/1B118/1C002/1C010）に requirement_text を補完
    → data/unified/control_nodes.json 更新
  - ccl_eccn_entries_v8.json の同5件に full_text/items_controlled も補完
  - Layer A embed_text は heading ベースで生成済み → 再構築不要

結果: ECCN 84件全て requirement_text 充填完了
```

#### P0-2: 制裁リスト公式データソース切替 ✅ 実装済み確認

```
確認内容（2026-03-20）:
  - sanctions_sync.py: OFAC SDN XML / BIS Entity List 取得済実装
  - POST /api/admin/sync-sanctions: 完全実装（論理削除→挿入→FAISS再構築）
  - UI: ウォッチリスト画面に「制裁リスト同期 (OFAC / BIS)」ボタン実装済
  - scripts/fetch_sanctions_lists.py: standalone 取得スクリプト実装済

残作業: 月次 cron の設定（macOS launchd or Linux cron）
        → 初回インポートは UI ボタンで実行可能
```

#### P0-3: patent_retrieve セッションブロック解消 ✅ 解決済み確認

```
確認内容（2026-03-20）:
  - step_patent_retrieve.py が静的 Layer B FAISS インデックスに移行済み
  - 動的 FAISS ビルドは廃止されており、セッションブロック問題は解消済み
  - orchestrator.py のフロー: usage_extract → patent_retrieve(静的FAISS) → ...
```

---

### P1: 短期（〜2週間）

#### P1-1: 外為法省令（告示別表）の取得・追加 ✅ 完了（2026-03-21）

```
実施内容（2026-03-21）:
  - e-Gov API より 貨物等省令（403M50000400049）XML (1.5MB) を取得
  - 36 Article → 別表第一マッピング解析スクリプト作成
  - 191 regulation ノード全件に requirement_text 充填（43件: 省令直接抽出、5件: 個別対応）
    ・以前: 143/191 件に requirement_text あり（48件 None）
    ・以後: 191/191 件 完全充填（100%）
  - layer_a_meta.json に 191 regulation records を追加（faiss_id pending タグ付き）
  - Layer A FAISS 本体（2040vec）は変更なし
    → macOS CPU 環境での e5-large OOM のため Colab 再ビルドをバックログへ

残作業: Colab 環境で e5-large を使って Layer A 全体再ビルド（2040 → 2231 vec）
```

#### P1-2: J-PlatPat → 知識グラフ IPC エッジ追加 ✅ 完了

```
実施内容（2026-03-20）:
  - ipc_eccn_mapping.json（174 IPC サブクラス × 640マッピング）を活用
  - 特許ノード 64/67件 に IPC→ECCN/FEFTA derived エッジを自動生成
  - 追加エッジ: 996件（ipc_eccn_derived + ipc_fefta_derived）
  - 信頼度 high/medium のみを採用（low は除外）
  - control_nodes.json 更新・build_manifest 更新完了

効果: HanteiAgent が特許→規制リストの関連性をグラフトラバーサルで参照可能に
```

#### P1-3: HS ↔ 外為法 公式対照表整備 ✅ 完了（2026-03-21）

```
実施内容（2026-03-21）:
  - hs_fefta_mapping_v2.json 生成（data/staging/）
    ・v1: 36件（Chapter/Heading レベル）
    ・v2: 1,577件（6桁 HS コードレベル）
      - heading_expansion: 1,118 件（既存 heading マッピングから 6 桁展開）
      - keyword_match:      459 件（英語キーワードマッチング追加）
    ・対応外為法カテゴリ: EL-2〜EL-16（核兵器〜工作機械）
  - layer_c_meta.json 更新
    ・fefta_items 付与: 2,876 → 3,001 件（+125 件）
    ・search_layer_c の fefta_filter が自動的に新マッピングを参照
  - hs_suggest パイプラインステップが新マッピングを自動活用（再起動後）

残作業（P1-3b）: CISTEC 公式対照表（輸出令別表第一関係 HS 番号一覧表）との照合
```

#### P1-4: DAP チャット × キャッチオールエンジン連携 ✅ 完了

```
実施内容（2026-03-20）:
  1. GET /decision/{tx_id}/catchall-result エンドポイントを追加
     → ai_validation/app/routers/decision.py
  2. CatchallDetailTool を HanteiAgent tools に追加（4番目のツール）
     → platform-core/platform_core/agent/tools.py
  3. DAP システムプロンプトにキャッチオール6ステップフロー説明を追加
     → modules/dap/app/routers/chat.py

効果: ユーザーが「この仕向地でキャッチオール規制が適用される理由は？」と
      聞いた場合、エージェントが get_catchall_detail ツールで判定根拠を
      参照して EAR Country Chart 列・Red Flag 数・推奨アクションを回答可能
```

---

#### P1-5: DAP 先輩担当者モード 全面実装 ✅ 完了（2026-03-21）

```
実施内容:

■ バックエンド（modules/dap/app/routers/chat.py）
  1. Intake Mode（ヒアリングモード）
     - _INTAKE_TRIGGERS / _init_intake_state / _build_intake_system_prompt
     - respond_intake ツール: intake_updates / risk_flags / gaps / action_plan
     - _execute_action_plan → _create_transaction / _run_screening / _run_pipeline
     - POST /api/transactions（ai_validation）を新規追加
  2. User Persona Tracking
     - _init_persona / _update_persona / _persona_context_str
     - 専門用語スコア（_EXPERT_TERMS 20語）× ノービスシグナル（_NOVICE_SIGNALS 8パターン）
       から業務レベル自動推定（unknown → novice / intermediate / expert）
     - モジュール訪問カウント・知識ギャップ語句を session_data["persona"] に蓄積
  3. Workflow Intelligence（_analyze_workflow_state）
     - 前工程未実施ギャップ検出（_WORKFLOW_ORDER: 8003→8002→8001→8005）
     - スクリーニング未実施警告 / 仕向国リスク警告 / ヒアリング未完了警告
     - 各アラートに安定 guide_id 付与（重複表示防止）
  4. ChatResponse 拡張
     - guidance: list[dict]   → ステップ別ガイドツアー
     - alert:    dict         → 自発的アラート
     - persona_summary: dict  → ユーザー理解状態
  5. respond ツール拡張
     - guidance_steps（navigate/highlight/fill_hint/explain/watch）
     - proactive_alert（risk_warning/workflow_gap/info）
  6. /api/chat/greet エンドポイント（ページロード時プロアクティブ案内）
     - ワークフローアラート → アラートバナーとして返却
     - 初回訪問+過去履歴あり → 前工程への誘導
     - ヒアリング中断 → 再開促進
  7. /api/chat/event エンドポイント（行動トラッキング）
     - page_view / guide_shown / guide_dismissed / button_click を記録
     - shown_guides: セッション内同一ガイドの重複表示を排除
  8. クロスモジュール会話継続（session_data 拡張）
     - page_visits / actions_taken / shown_guides を session_data に追加

■ フロントエンド（modules/dap/app/static/chat-widget.js）— 完全リライト v2
  - アバター・ブランド: 🤖 AI アシスタント → 👔 先輩担当者 (DAP)
  - アラートバナー: チャット外に自発的警告（severity色分け・30秒自動消去）
  - ヒアリング進捗バー: 品目・仕向国・ターン数・リスクフラグをヘッダー下に表示
  - guidance ステップ実行:
      navigate  → クロスページ遷移（残りは sessionStorage に保存して再開）
      highlight → ツールチップ付き要素ハイライト
      fill_hint → フィールドヒントオーバーレイ（例文付き）
      explain   → ガイダンスメッセージバブル
      watch     → 次操作待ちインジケーター
  - ガイド重複排止: sessionStorage + サーバー shown_guides の二重管理
  - callGreet(): ページロード 1.8秒後にプロアクティブ案内
  - trackEvent(): page_view を自動記録
  - sendMessage() が全新フィールド（guidance/alert/intake_state/persona_summary）を取得

■ 全モジュール埋め込み（chat-widget.js スクリプトタグ追加）
  - modules/ai_validation/templates/base.html
  - modules/ai_classification/templates/base.html
  - modules/hs_classifier/app/templates/base.html
  - modules/patent_search/app/templates/base.html
  - modules/rnd_assessment/app/ui/templates/base.html
  - modules/screening/app/templates/base.html

■ ai_validation: POST /api/transactions 追加
  - DAP ヒアリング完了後に JSON API 経由で案件を新規作成
  - TransactionItem / UsageRequirement を自動生成
  - source_module="dap" でトレーサビリティ確保

設計思想: 「Claude Code でのやりとりが理想」に基づく先輩コレーグ体験
  - ユーザーが気づいていないリスク・前工程ギャップを先回りして指摘
  - 専門知識レベルに応じてトーン・深度を自動適応
  - クロスモジュールで作業を誘導し、コンプライアンスアウトカムを最大化
```

---

### P2: 中期（〜2ヶ月）

#### P2-1: 4象限マッピング UI【戦略可視化の核心】

```
概要: 技術主権価値（Y軸）× 規制感度（X軸）の2次元マップ
実装:
  - ai_classification の Product に sovereignty_score, regulation_score 追加
  - AI判定結果から regulation_score 自動算出（ECCN命中度・外為法該当度）
  - 品目一覧画面に散布図ビュー追加（Chart.js or Plotly）
効果: 「要塞技術」「無防備な至宝」等の戦略分類を視覚化
```

#### P2-2: みなし輸出スクリーニング（人物管理）

```
概要: 外為法「みなし輸出」3カテゴリへの対応
実装:
  - 研究者/従業員テーブル（国籍・居住年数・所属・二重雇用フラグ）
  - 技術提供対象の人物 → みなし輸出該当性判定
  - rnd_assessment の需要者要件欄と連携
```

#### P2-3: 規制動向モニタリング

```
概要: Wassenaar 年次改正、BIS 中間規則、外為法改正の自動検知
実装:
  - 各規制機関の更新フィード（月次ポーリング）
  - control_nodes の差分検出 → 管理者通知
  - DAP で「規制更新あり」バナー表示
```

#### P2-4: 特許出願人の制裁リスト照合

```
概要: patent_search で取得した特許の出願人 → screening に自動照合
実装:
  - 特許メタデータの inventor/assignee → screening API 呼出
  - 「制裁リスト関連出願人の特許」フラグ → ai_validation pipeline に連携
```

---

## 5. データソース開発方針

| データ | 優先度 | 現状 | 次のアクション |
|--------|--------|------|-------------|
| 外為法（FEFTA）省令 | ★★★★★ | ✅ 191/191 ノード充填済 | Colab で Layer A 全体再ビルド（2040→2231vec） |
| ECCN/EAR Part774 | ★★★★☆ | 84/84 requirement_text 充填済 | 追加パラメータ精査（低優先） |
| 制裁リスト | ★★★★★ | OFAC/BIS 公式ソース対応済 | 月次 cron の設定 |
| HS コード対照表 | ★★★☆☆ | ✅ v2: 1,577件（6桁）・Layer C 更新済 | CISTEC 公式対照表との照合（P1-3b） |
| 特許（J-PlatPat） | ★★★★☆ | 64/67件 IPC→ECCN/FEFTA 996エッジ追加済 | 実特許データ取得（長期） |
| 中国輸出管理法リスト | ★★☆☆☆ | 未取得 | 長期: 将来的な規制拡張に備えて調査 |
| みなし輸出・省令 | ★★★☆☆ | 未取得 | 外為法施行規則 PDF → 構造化 |

---

## 6. 知識グラフ品質評価（2026-03-21）

| regime | ノード数 | 補強状況 | 品質 |
|--------|---------|---------|------|
| 外為法（fefta） | 191 | **191/191 requirement_text 充填完了** | **◎ 高品質** |
| EAR/ECCN（ear） | 84 | 84/84 requirement_text 充填済 | **◎ 完成** |
| Wassenaar（wa） | 165 | PDF解析 | **○ 中品質** |
| HS コード（hs） | 281 | ✅ v2マッピング 1,577件・Layer C 3,001件対応 | **◎ 高品質** |
| 特許（patent） | 67 | 64/67 に 996 IPC→ECCN/FEFTA エッジ追加済 | **○ 中品質** |

**DAP RAG**: platform-core GET /api/faiss/search/layer-a → DAP _rag_layer_a() 連携済（2026-03-21）

---

## 7. 技術的負債（2026-03-20 時点）

| 問題 | 影響 | 優先度 |
|------|------|--------|
| ECCN 71件スタブ | matrix_match FAISS精度直結 | **P0-1** |
| 制裁リスト非公式ソース | 審査結果の監査証跡として使用不可 | **P0-2** |
| patent_retrieve セッションブロック | ai_validation pipeline の並行書込みブロック | **P0-3** |
| HS↔外為法対照が近似 | HS分類の不確実性 | **P1-3** |
| 特許→知識グラフ未同期 | 知識グラフの patent ノードがほぼ空 | **P1-2** |
| みなし輸出チェック欠如 | コンプライアンスギャップ | **P2-2** |

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

# DB ロック確認
lsof | grep ".db$" | grep -v ".venv"
```

---

## 9. ブランチ管理

| ブランチ | 状態 | 用途 |
|---------|------|------|
| main | 安定 | リリースブランチ |
| branch_faiss_modification | 7 commits ahead | Layer C FAISS 統合作業 |
| branch_neurosymbolic | 42 files ahead | NeuroSymbolic基盤（未マージ） |

**次のマージ推奨順序**:
1. `branch_faiss_modification` → main（Layer C完成）
2. `branch_neurosymbolic` → main（NeuroSymbolic基盤・キャッチオール・HanteiAgent）
3. 統合後に P0 タスクを main で進める

---

*更新: 2026-03-20*
*担当: Takehiro Sato + Claude Sonnet 4.6*
