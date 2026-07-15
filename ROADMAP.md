# 開発ロードマップ — AI_TradeManagement
# 2026-07-15 更新（Phase 13 完了: コンテンツDB修正・制裁リスト全件取得強化 — BIS EL 1010件→3415件（eCFR）・UK OFSI 0件→5135件（CSVメタデータ行スキップ修正）・OFAC SDN XML名前空間自動検出・Trade.gov CSL API data.trade.gov移行・plat_hantei_kubanbang/threshold シード投入・control_nodes.json再構築・FAISS 22470→30064件）

> 本ドキュメントは実装済み機能の現状スナップショットと、今後の開発優先度を整理したものです。
> 2026-06-26（2回目）追加: **Phase 5 完了** — ①Layer A FAISS 再ビルド（2,184→2,192vec、jp_fx_2025:3件 EL-7-24/25/26 EUVフォトレジスト/先端AI GPU/HBM反映）②Screeningバッチ結果 CSV エクスポート（クライアントサイド BOM付、タイムスタンプファイル名）③`./start.sh --verify-demos` CI連携（ワンコマンドDEMO全6本検証）④輸出許可申請双方向連携強化（linked_license_id書戻し・license-status PATCH エンドポイント・承認/却下コールバック・Transaction Detail ステータスパネル）⑤ERP Webhook Bearer認証完全適用（pull_review.py 4エンドポイント＋AITM_WEBHOOK_SECRET環境変数）⑥Red Flag 詳細入力UI（「該当」選択時テキストエリア展開・note値送信・判定結果にRF内容/根拠表示）
> 2026-06-26 追加: **Phase 4 完了** — ①export_licenses.html Undefined.format()修正（防御的Jinja2）②役務取引UIフロー完成・テストデータ投入③DEMO全6本自動検証スクリプト（verify_demos.py）④FTA/EPA実データ拡充（15協定・61税率）⑤rd_case_items テストデータ6件⑥DEMO6新設（みなし輸出サービス取引審査）⑦２リスト閾値調整UI（system_settings + /ui/admin/settings）⑧screeningバッチ機能（POST /api/screen/batch・CSV最大200行）⑨Cloudflare Tunnel launchd自動復旧（com.tsp.cloudflared.plist）⑩ヘルスダッシュボードリアルタイム化（/ui/health-status + 30秒ポーリンググリッド）⑪SQLite alembic_version_validation スタンプ（g4h5i6j7k8l9）⑫matrix_rules バージョン更新（JP_FX_2025）+ 2025年改正3件追加（EUVフォトレジスト・先端AI演算GPU・HBM）
> 2026-05-31（2回目）追加: ①品目管理 UI 全面リデザイン（sticky セーブバー・ブロック順序最適化・GHS折り畳み・外部AI判定2カラム整理）②国別HSコードサマリーテーブル追加（国|役割|ローカルHS|品目説明|MFN関税率|EPA/FTA優遇|協定名|輸出許可）③EPA/FTA優遇税率（epa_tariff_rate）+ 協定名（epa_agreement）をDB・API・UIに追加④DAP DEMO3（OMEGA-300輸出審査フロー）完成・DEMO1/DEMO2の品番入力前「品目同時登録」チェック追加
> 2026-05-31（1回目）追加: ①サプライヤー申告フォームに HS コード必須フィールド追加・cert_confirmed チェックボックス（責任確認）追加・送信ボタン disabled 制御 ②社内手動証明書登録ルート（is_manual_registration フラグ・upload-doc エンドポイント・📎手動証明書登録モーダル）③サプライヤーは単一ノードのみ参照（BOM構造非公開）・?superadmin=1 でハイパーADMINモード（紫バナー・全ノード生JSON・コンソールヘルパー）④bom_attestation_summary の us_controlled_pct 104%バグ修正（edge.quantity直和 → unit_value_usd 金額加重計算）⑤フルDryRun完了: FG-001 OMEGA-300 → BOM 4品目 → SC sync → ポータル申告 → AI判定 → アクセプト → AI取引審査（API-20260531-1646、catchall_recommended:true）
> 2026-05-25（2回目）追加: ①BOMグラフノードパネルに ECCN同期・AI判定依頼ボタン追加（ワンクリックで sync-eccn / request-eccn、グラフリアルタイム更新）②サプライヤーポータル招待メール自動送信（smtplib STARTTLS、SMTP_HOST等env var設定で有効化）③R&D→AI判定フロー修正（form POST→JSON API統一、end_user_name counterparty_name引き継ぎ、FAISS自動起動）④取引先情報 GET /api/transactions/{id} に counterparty_name 追加⑤transaction_detail.html BOMコンテキストカード強化（JS動的取得: HSコード/ECCN/EARリスク/BOM構成品テーブル/BOMグラフリンク）⑥R&Dコンテキストカード追加（rnd_assessment起案時）⑦item_version再判定: item_code→SCノード逆引きでsupply_chain_node_id自動リンク。
> 2026-05-25（1回目）追加: URL整合性修正・データ一元化完了。①全モジュールのハードコードlocalhost URLを環境変数化（item_version.py/export_license.py/import_profiles.html/transaction_detail.html）②BOM→AI判定リンクのURLパス修正（/transactions/{id}→/ui/transactions/{id}）③transaction_detail.html BOMコンテキストカード追加（ai_classification起案時にspec_text key-valueグリッド・バックリンク表示）④ヒットなし時 FAISS 候補チップ表示（catchall_recommended の場合）⑤supply_chain_node_id を Transaction に自動リンク（portal_submit / request-eccn 双方）⑥FAISS + 2リスト自動実行（fire-and-forget）。
> 2026-05-24（5回目）追加: サプライヤーポータル → AI 2リスト該非判定 → BOM 自動更新フロー完全実装。Alembic m1n2o3p4q5r6（ECCN判定4フィールド）・supplier_portal.py AI判定自動トリガー・sync-eccn FAISS照合ロジック・bom_graph.html 判定ステータスバッジ（⏳/🤖/✅/❌）・Graphviz DOT スタイル可視化（cytoscape-dagre）。デモ: EUV-PR-001 BOM の EUV-RM-002（PAG） サプライヤー申告 1C010.a → FAISS Layer A 1C010 候補一致確認 → eccn_judgment_status=ai_completed → ECCN=1C010.a 自動書込。
> 2026-05-24（4回目）追加: Phase IV-2 輸出入統合ダッシュボード（GET /dashboard/import-export + GET /api/dashboard/import-export）。SQLite品目集計 + PostgreSQL ImportProfile/SupplyChainNode 集計・仕入先国バーチャート・品目種別バーチャート・BOMチェーン クイックビュー（Cytoscape.js）・要対応アクションリスト（high/medium/low）。ドライランデモ実行：全8モジュール稼働確認・BOM同期4品目・インパクト分析・スクリーニング/HS判定連動テスト完了。
> 2026-05-24（3回目）追加: Phase I-2 BOM統合（bom_json→plat_supply_chain_node 自動同期・SC同期ボタン）・Phase III-1 輸入品ECCN付番フロー（ai_validation連携・判定ステータス追跡）・Phase III-2 BOM上流輸入品影響分析（GET /api/supply-chain/impact/{code} 逆引き）・Phase III-3 US EAR再輸出許可申請自動トリガー（export_license連携）・Phase IV-1 BOMチェーン可視化（Cytoscape.js /bom/graph ページ）。
> 2026-05-24（2回目）追加: 輸入品管理基盤。item_type 6種確立（PURCHASED_PART/RAW_MATERIAL/INTERNAL_TRANSFER/SOFTWARE追加）・輸入品 Lookup 3タブ追加・plat_import_profile テーブル新設（Alembic: i7j8k9l0m1n2）・ImportProfile CRUD + 輸入規制チェック（化審法/REACH/CITES/EAR）+ fta_origin 自動照合。
> 2026-05-24（1回目）追加: キャッチオール自動評価（AI run 後 LOW 判定で自動 CatchallAssessment 生成）+ 制裁リスト無料3ソース（OFAC SDN CSV/UN SC/EU Consolidated）自動同期 + pending_actions にキャッチオール未判定アクション（Step 2.5）追加。
> 2026-05-08（3回目）追加: Phase 6 設計レビュー — platform-core から業務ドメイン機能を分離。counterparty→screening / item_version+supply_chain+supplier→ai_classification 統合（Phase 6A 高優先）、export_license / fta_origin 新モジュール抽出（Phase 6B 中優先）、trade_gate 新モジュール抽出（Phase 6C 低優先）。branch: refactor/module-separation。
> 2026-05-08（2回目）追加: Phase 2 R&Dアクセス制御（tech_sensitivity/みなし輸出自動検知）・DAP-B ワークフローモード（chat-widget.js UCセレクタ・進捗バー・自動ナビ）・Phase 3 グローバル品目マスター（local_eccn/license_required・国数バッジ）・Phase 4 トランザクション多テナント化（org_id/ダッシュボード拠点フィルタートグル）。
> 2026-05-08（1回目）追加: Phase 1 多拠点基盤（plat_tenant拡張・拠点スイッチャー・X-Organization-Idインターセプト）・DAP-A ワークフロー伴走（DapWorkflowSession・6 UC定義）・DAP-C 知識ベース更新（UC別ナビゲーションガイド）。
> 2026-05-07（4回目）追加: R&Dリスク管理モジュール UI/スコアリング刷新。Layer 1: ポート修正・推奨対応日本語化 / Layer 2: Explainability 構造化カード / Layer 3: 5ステップ進捗バー / Layer 4: スコアリングエンジン15+ルール・regulatory_risk独立化・全理由日本語化。
> 2026-05-07（3回目）追加: Priority B — 輸出許可証 申請番号自動採番・価値消費管理・期限アラートスケジューラー / サプライヤーポータル ファイルアップロード・ダウンロードAPI・証明管理UI / EPA/FTA 特恵税率 DB（日本締結10協定・8代表HS・Alembic e5f6g7h8i9j0）。
> 2026-05-07（2回目）追加: Priority A — US EAR規制理由エンジン（11種）・EU Dual-Use チェッカー（GEA EU001-EU008）・オープンクローズ戦略マトリクス（4象限）・ICP自己診断（CISTEC 8要素32問）。
> 2026-05-07（1回目）追加: ①経済安保法特許非公開リスクチェック・②CISTEC様式準拠輸出審査記録7年保存・③役務取引管理（外為法第25条）・④FDPR判定エンジン（4バリアント/De Minimis閾値）。
> 2026-04-29 追加: Layer A 再ビルド（2,922→2,999vec）。ECCN embed_text バグ修正（full body埋め込み）・外為法5項(化学/生物兵器製造装置)/8項(コンピュータ)12件ずつ追加・USML/EU/Wassenaar収録。
> 2026-04-28 追加: D2-3 Wassenaar Arrangement ML 22カテゴリ・グローバル規制UIレジームチェック・Screening→与信管理自動連携・Fterm検索統合完了。
> 2026-04-26 追加: 品目バージョン管理 + 仕様変更コンプライアンス影響検知 実装完了。
> 2026-04-26 追加: コンプライアンス進捗管理 Lookup（7ステージパイプライン / オープンアクション / 変化点フィード）実装完了。

---

## 1. モジュール構成と安定性（2026-03-21 時点）

| モジュール | ポート | DB | WAL | 安定性 | 備考 |
|-----------|--------|-----|-----|--------|------|
| platform-core | 8000 | PostgreSQL | — | ✅ | FAISS 4レイヤー（A/B/C/D）・知識グラフ・規制スケジューラー（業務ロジック分離済） |
| ai_validation | 8011 | SQLite | ✅ | ✅ | キャッチオール Section 4・PDF報告書・HanteiAgent・AI run 後キャッチオール自動評価 |
| ai_classification | 8002 | SQLite+PG | ✅ | ✅ | 品目管理・6種 item_type・輸入品プロファイル・BOM→SC自動同期・BOMグラフ可視化（cytoscape-dagre）・輸入品影響分析・ECCN付番フロー・US EAR再輸出申請トリガー・**サプライヤー→AI取引審査→BOM自動更新**・HS/ECCN申告+cert確認・手動証明書登録・Hyper-Admin mode・BOM US管理品比率（金額加重）・**国別HSサマリーテーブル・EPA/FTA優遇税率**|
| rnd_assessment | 8003 | SQLite | ✅ | ✅ | R&D審査・リスクレベル算出・みなし輸出人物一覧 |
| patent_search | 8004 | SQLite | ✅ | ✅ | BigQuery連携・J-PlatPatフォールバック |
| screening | 8005 | PostgreSQL | — | ✅ | 制裁リストスクリーニング（OFAC/BIS/UN/UK OFSI）・与信管理・ERP JSON一括インポート・FAISS 30,064件（BIS EL 3,415件 eCFR全件・UK OFSI 5,135件） |
| hs_classifier | 8006 | — | — | ✅ | Layer C FAISS（5,476vec）・同期/非同期両対応 |
| dap | 8010 | SQLite | ✅ | ✅ | 先輩担当者モード・ペルソナ追跡・ガイドバナー・全モジュール埋込済 |
| export_license | 8012 | PostgreSQL | — | ✅ | EAR BIS-748P / 外為法様式第1 ドラフト生成・申請ライフサイクル（Phase 6B-1） |
| trade_gate | 8013 | PostgreSQL | — | ✅ | ERP 取引伝票受付・AI 該非・スクリーニング連携・出荷 GO/NOGO（Phase 6C-1） |
| fta_origin | 8014 | PostgreSQL | — | ✅ | EPA/FTA 特恵税率照会・原産性ルール管理（Phase 6B-2） |

**WAL対応状況**: DAP・patent_search とも WAL適用済み確認済。

---

## 2. 実装済み機能サマリー

### 2-1. NeuroSymbolicアーキテクチャ基盤（platform-core）

| コンポーネント | ファイル | 状態 |
|-------------|---------|------|
| 知識グラフ（788ノード） | ontology/seed/control_nodes.json | ✅ 完成 |
| **輸出管理オントロジー（Phase O-1）** | **data/ontology/*** | **✅ 2026-05-10 新規構築** |
| ECCN 階層 JSON（637 ECCN・カテゴリ/PG/統制理由/CR） | data/ontology/eccn_hierarchy.json | ✅ 完成 |
| 外為法↔ECCN 対比表（16項目・ECCN逆引き付き） | data/ontology/fefta_eccn_xref.json | ✅ 完成 |
| IPC-ECCN 双方向（174 IPC prefix / 637 ECCN） | data/ontology/ipc_eccn_bidir.json | ✅ 完成 |
| HS-ECCN Confidence score（1,469件・exact/high/medium） | data/ontology/hs_eccn_scored.json | ✅ 完成 |
| 統合オントロジーグラフ API | data/ontology/ontology_graph.json | ✅ 完成 |
| /api/ontology/ エンドポイント（stats/lookup/expand/fefta/categories） | platform-core/routers/ontology.py | ✅ 完成 |
| **Country Control Matrix（Phase O-3-1）** | **data/ontology/country_control_matrix.json** | **✅ 2026-05-10 新規構築** |
| 65ヶ国 × カントリーグループ/外為法ゾーン分類 | country_control_matrix.json | ✅ A:38 B:8 D:1:14 E:1:5 |
| 45 ECCN prefix × 4 country_group → verdict サマリー | country_control_matrix.json | ✅ NLR/EXCEPTION/LICENSE_REQUIRED/PROHIBITED |
| /api/ontology/license-check（ECCN×仕向国→ライセンス判定） | platform-core/routers/ontology.py | ✅ verdict+rationale+推奨アクション |
| /api/ontology/countries（国一覧・グループ/ゾーンフィルタ） | platform-core/routers/ontology.py | ✅ 完成 |
| **NetworkX 多ホップ推論グラフ（Phase O-3-2）** | **data/ontology/nx_graph.gpickle** | **✅ 2026-05-10 新規構築** |
| 2,512ノード × 6,318エッジ（ECCN/HS/IPC/FEFTA/FTERM/CAT/PG） | nx_graph.gpickle | ✅ 7種エッジ |
| /api/ontology/path（最短パス: HS:854231→FEFTA:7 等） | platform-core/routers/ontology.py | ✅ 有向/無向フォールバック |
| /api/ontology/neighbors（深さ1〜4の多ホップ近傍探索） | platform-core/routers/ontology.py | ✅ エッジ種別フィルタ対応 |
| /api/ontology/graph/stats（グラフ統計）| platform-core/routers/ontology.py | ✅ 完成 |
| **F-term→ECCN マッピング拡充（Phase O-3-3）** | **data/staging/fterm_eccn_mapping.json** | **✅ 130→200件（2026-05-10）** |
| 全カテゴリ0-9 カバー（核/化学/製造設備/センサー/航法/海上/宇宙/AI/量子/先進材料） | fterm_eccn_mapping.json | ✅ 70 ECCN × 200 F-term |
| HanteiAgent | agent/hantei_agent.py | ✅ 完成 |
| AgentTools（FAISS呼出・キャッチオール詳細） | agent/tools.py | ✅ 完成 |
| キャッチオールエンジン | ontology/rules/catchall_engine.py | ✅ 完成 |
| FAISS Layer A（外為法/ECCN） | services/faiss_e5_service.py | ✅ 2,184vec（2026-05-10再ビルド・entity_list除外・eccn_tech 20件追加）|
| FAISS Layer B（特許チャンク） | services/faiss_e5_service.py | ✅ **10,783vec**（JP 6,144 + US/EP 4,639・639 ECCN タイプ・CPC コード対応・2026-05-12）|
| FAISS Layer C（HSコード） | services/faiss_e5_service.py | ✅ 5,476vec |
| FAISS Layer D（学術論文） | services/faiss_e5_service.py | ✅ **5,765vec**（S2: 508 + OpenAlex: 5,257・**25/25 ECCN 全カバー**・2026-05-12）|
| asyncio 規制動向スケジューラー | main.py (_regulatory_scheduler) | ✅ 24h周期 |
| **DAP 多層 RAG（Phase O-2）** | **modules/dap/app/routers/chat.py** | **✅ 2026-05-10 拡張** |
| _rag_multilayer（Layer A + Ontology + Country Control + Layer B + Layer D 並列） | chat.py | ✅ ECCN/HS/IPC + 仕向国を自動抽出・5レイヤー並列検索 |
| _rag_country_control（ECCN × 仕向国 → ライセンス要否 RAG注入） | chat.py | ✅ 2026-05-10 新規追加 |
| 説明チェーン（HS→ECCN→外為法→Layer B/D エビデンス） | /api/ontology/lookup | ✅ system prompt に注入 |
| **NeuroSymbolic 該非判定エージェント UX 再設計（Phase O-3-1 付帯）** | **chat.py** | **✅ 2026-05-10 完了** |
| 自然言語抽出レイヤー（_extract_attr_value / Haiku）→ 属性正規化 | chat.py | ✅ 日本語・英語・混在 回答に対応 |
| 文脈適応チョイスボタン（end_use_type / end_user_type / 仕向国 別） | chat.py | ✅ 用途別に最適ボタン生成 |
| 判定結果自然言語フォーマット（_format_judge_result） | chat.py | ✅ FEFTA/ECCN ID → 日本語説明 |
| 国名→ISO コード辞書（_COUNTRY_NAME_TO_ISO・25ヶ国語対応） | chat.py | ✅ 「中国」「China」→ CN 自動変換 |
| **DAP 7層 RAG（Phase O-4）** | **chat.py** | **✅ 2026-05-10 完了** |
| _rag_fterm（F-term JPO分類 → ECCN 近傍探索・特許分類コンテキスト注入） | chat.py | ✅ 特許F-term検出 → ontology/neighbors 経由 |
| _rag_graph_context（オントロジーグラフ接続関係 → FEFTA/IPC/HS 多ホップ） | chat.py | ✅ ECCN → depth=1 neighbors → 外為法別表注入 |
| **オントロジーエクスプローラー UI（Phase O-4）** | **templates/ontology_explorer.html** | **✅ 2026-05-10 完了** |
| 4タブ UI（コード検索 / パス探索 / ライセンス判定 / 近傍探索） | ontology_explorer.html | ✅ ダークテーマ・既存スタイル踏襲 |
| /ui/ontology ルート + ポータルナビ「専門ツール」セクション登録 | ui.py | ✅ _KNOWN_MODULES + GET /ui/ontology |

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

### ✅ Phase 4 完了（2026-06-26）

| タスク | 内容 | 完了日 |
|--------|------|--------|
| Phase 4-A① | export_licenses.html Undefined.format() 防御修正（`| default(none)` + `| float` ガード） | 2026-06-26 |
| Phase 4-A② | 役務取引UIフロー完成（service_transactions テストデータ2件）・rd_case_items テストデータ6件投入 | 2026-06-26 |
| Phase 4-A③ | DEMO全6本自動検証スクリプト（scripts/verify_demos.py）作成。DEMO2/DEMO3のhighlight修正 | 2026-06-26 |
| Phase 4-B① | FTA/EPA実データ拡充（10→15協定、23→61税率）。PH/ID/MN/AE/KR追加、HS8486/8541/8542/9031系追加 | 2026-06-26 |
| Phase 4-B② | rnd_assessment rd_case_items テストデータ6件投入（ArFフォトレジスト/EUV/CVD/AI化学合成） | 2026-06-26 |
| Phase 4-B③ | DEMO6新設（みなし輸出サービス取引審査フロー・5ステップ）uc_definitions.json に追加 | 2026-06-26 |
| Phase 4-B④ | ２リスト閾値調整UI実装（system_settingsテーブル・/ui/admin/settings・orchestrator DB読込） | 2026-06-26 |
| Phase 4-B⑤ | screeningバッチ機能（POST /api/screen/batch、CSV最大200行、サンプルCSVダウンロード） | 2026-06-26 |
| Phase 4-C① | Cloudflare Tunnel launchd自動復旧（~/Library/LaunchAgents/com.tsp.cloudflared.plist、KeepAlive=true） | 2026-06-26 |
| Phase 4-C② | ヘルスダッシュボードリアルタイム化（/ui/health-status 一括チェック・platform_dashboard 30秒ポーリング） | 2026-06-26 |
| Phase 4-C③ | alembic_version_validation スタンプ（SQLite app.db にg4h5i6j7k8l9 記録） | 2026-06-26 |
| Phase 4-C④ | matrix_rules バージョン付与（JP_FX_2025）・2025年改正3件追加（EL-7-24〜26: EUVフォトレジスト/先端AI GPU/HBM） | 2026-06-26 |

---

### ✅ Phase 5 完了（2026-06-26）

| タスク | 内容 | 完了日 |
|--------|------|--------|
| Phase 5-A | Layer A FAISS 再ビルド（2,184→2,192vec）。build_layer_a.py に jp_fx_2025 ローダー追加。EL-7-24/25/26 反映・EUV検索スコア 0.886 確認 | 2026-06-26 |
| Phase 5-B | Screeningバッチ結果 CSV エクスポート（「⬇ CSVエクスポート」ボタン追加・クライアントサイドCSV生成・タイムスタンプファイル名・BOM付） | 2026-06-26 |
| Phase 5-C | `./start.sh --verify-demos` CI連携（--verify-demos オプション追加・ワンコマンドで全6DEMO検証・Exit code 連動） | 2026-06-26 |
| Phase 5-D | 輸出許可申請双方向連携強化（linked_license_id/linked_license_status カラム追加・自動書戻し・PATCH /decision/{id}/license-status エンドポイント・export_license 承認/却下コールバック・Transaction Detail ライセンスステータスパネル） | 2026-06-26 |
| Phase 5-E | ERP Webhook Bearer認証完全適用（pull_review.py 4エンドポイントに verify_api_key 追加・AITM_WEBHOOK_SECRET 環境変数・config.py AliasChoices 対応・401/200 動作確認） | 2026-06-26 |
| Phase 5-F | Red Flag 詳細入力UI改善（「該当」選択時テキストエリア展開・rf1_note〜rf7_note 送信・判定結果に該当RF + 根拠メモ表示・toggleRfNote 関数追加） | 2026-06-26 |

---

### ✅ Phase 13 完了（2026-07-15）

| タスク | 内容 | コミット |
|--------|------|---------|
| [C1] Trade.gov CSL API 移行 | api.trade.gov → data.trade.gov、クエリパラメータ認証 → subscription-key ヘッダー（Azure APIM）、page_size=10/offset上限1000対応、429 retry ロジック | `6f97064` |
| [C2] plat_hantei_kubanbang シード投入 | 29件 kubanbang + 87件 threshold を PostgreSQL に直接 INSERT（`hantei_kubanbang.json` から読込） | `a1e39c7` |
| [C3] control_nodes.json 再構築 | `builder.py build()` 呼出 → 769ノード（regulation:191, eccn:84, wassenaar:146, hs:281, patent:67）、semantic_edges:310 | `a1e39c7` |
| [W1] UK OFSI 0件 → 5,135件 | CSV 16MB 先頭メタデータ行（`Last Updated,...`）をスキップする header 検出ロジック追加 | `57ae8e3` |
| [W2] layer_a_new.index 孤立ファイル削除 | Mar 21 の孤立 index（2231vec）削除。layer_a.index（Jun 26, 2192vec）が正式 | `57ae8e3` |
| BIS EL 1,010件 → 3,415件 | `fetch_bis_entity_list_ecfr()` 新設。eCFR XML（15 CFR Part 744 Suppl.4）を解析して全件取得。CSL API フォールバック維持 | `57ae8e3` |
| OFAC SDN XML 名前空間修正 | 旧 `http://tempuri.org/sdnList.xsd` → 新 `https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML` を動的検出 | `57ae8e3` |

**FAISS合計: 22,470件 → 30,064件**（BIS EL +2,405件、UK OFSI +5,135件）

---

### ✅ P3: 完了済み（2026-03-26）

| タスク | 内容 | 状態 |
|--------|------|------|
| P3-1 | Layer A 再ビルド（最新） | ✅ 2,184vec（law:1014, eccn:637, parameter:406, tsutatsu:54, eccn_tech:20, usml_itar:21, eu_dual_use:10, wassenaar_ml:22）2026-05-10。entity_list 835件除外済み |
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

### ✅ Priority A — グローバルコンプライアンス強化（2026-05-07）

```
① US EAR 規制理由・ライセンス例外エンジン（platform-core/ontology/rules/ear_reason_engine.py）
  - Reason for Control: NS1/NS2/AT1/AT2/NP1/NP2/MT/CB1/CB2/CB3/EI/RS1/RS2/CC/SS/SL/FC 全11種
  - License Exception: NLR/LVS/GBS/CIV/TSR/APP/ENC/GOV/RPL/STA 全10種
  - Country Group: A（ホワイト国）/ B / D:1 / E:1 で判定分岐
  - API: POST /decision/{id}/ear-check → verdict: NLR/EXCEPTION/LICENSE_REQUIRED/PROHIBITED
  - UI: transaction_detail Section 5c（teal ボーダー・AJAX）

② EU Dual-Use チェッカー（platform-core/ontology/rules/eu_dual_use_checker.py）
  - Regulation 2021/821 Annex I カテゴリ 0〜9（ECCN先頭数字から推定）
  - GEA: EU001（主要同盟国）/ EU007（グループ内移転）/ EU008（暗号）等
  - EU制裁国（RU/BY/KP/IR/SY）→ PROHIBITED
  - Annex IV 品目 → 域内移転でも個別許可
  - API: POST /decision/{id}/eu-dual-use-check
  - UI: transaction_detail Section 5d（blue ボーダー・AJAX）

③ オープンクローズ戦略マトリクス（rnd_assessment）
  ファイル: modules/rnd_assessment/app/services/open_close_matrix.py
  - 判定結果: MANDATORY_CLOSE / CLOSE / CONDITIONAL / OPEN の4象限
  - 入力: ECCN（感度 HIGH/MEDIUM/LOW）× 特許非公開リスク × 競合状況 × 技術成熟度
  - MANDATORY_CLOSE: 経済安保法 HIGH リスク → 事前確認必須・出願不可
  - 出願・権利化の具体的指針（国内/PCT/クレーム設計）を生成
  - UI: /ui/open-close（フォーム+結果）
  - ポータル: 専門ツールセクション「🔓 オープンクローズ戦略」

④ ICP 自己診断（ai_validation）
  ファイル: modules/ai_validation/app/routers/icp_diagnosis.py
  - CISTEC 8要素 × 32問: はい(2)/一部(1)/いいえ(0) → 64点満点
  - レベル4（≥85%）/3（≥65%）/2（≥40%）/1（<40%）の4段階評価
  - 要素別スコアバー + 優先改善事項（スコア最下位3要素）
  - 印刷/PDF保存対応・全32問を5分程度で完了可能
  - UI: /ui/icp（フォーム）→ /ui/icp (POST) → 結果
  - ポータル: 専門ツールセクション「📊 ICP 自己診断」
```

### ✅ 安全保障貿易管理強化（2026-05-07）

```
① 経済安全保障推進法 第4章 特許非公開リスクチェック（rnd_assessment）
  ファイル: modules/rnd_assessment/app/services/patent_disclosure_check.py
  - 10指定技術カテゴリ（武器・航空・宇宙・原子力・サイバー・先端材料・半導体・量子・AI自律・生物）
  - R&D案件タイトル+説明でキーワードマッチ → HIGH/MEDIUM/NONE 判定
  - case_detail.html に赤/黄アラートバナーを追加（特許出願前確認 → 経済産業省への事前相談）
  - 法令参照: 経済安全保障推進法 第4章 第65条（2024年5月施行・違反=2年以下の懲役）

② CISTEC様式準拠 輸出審査記録（外為法第67条 7年保存）
  - transactions モデルに 8カラム追加（evaluator_name/evaluator_title/judgment_no/retention_until/
    destination_country/end_user_name/end_use_description/fdpr_judgment_json）
  - 「審査提出」時に判定書番号（JDG-{case_no}-{date}）・保存期限（提出日+7年2日）を自動設定
  - 輸出報告書 CSV を CISTEC様式16行ヘッダーに拡張（判定書番号・仕向国・最終需要者・7年保存義務）
  - transaction_new.html / transaction_detail.html に CISTEC様式フィールドを追加

③ 役務取引管理（外為法 第25条 技術役務規制）
  ファイル: modules/ai_validation/app/routers/service_control.py（新規7エンドポイント）
         modules/ai_validation/app/db/models/service_transaction.py（28カラム）
         modules/ai_validation/templates/services.html / service_new.html / service_detail.html
  - 役務種別: technology_guidance / software_license / cloud_service / consulting / education / research / other
  - みなし輸出フラグ（外為法 第25条第1項ただし書き）
  - 提出時に自動スクリーニング連携（screening:8005）・保存期限（+7年）・判定書番号自動採番
  - ステータス管理: draft → under_review → approved/license_required/license_granted/rejected/withdrawn
  - ポータルナビに「📜 役務取引管理」追加（/proxy/ai_validation/ui/services）

④ FDPR判定エンジン（15 CFR §734.9）
  ファイル: platform-core/platform_core/ontology/rules/fdpr_engine.py
  - 4バリアント: Russia/Belarus（0%）・China MEU（0%）・Advanced Computing・General（25%）
  - De Minimis 閾値: E:1=0%・D:1∩D:5=0%・D:1=10%・default=25%
  - 既存 catchall_concern_countries.json を @lru_cache で再利用
  - transaction_detail.html に Section 5b として FDPR判定フォーム+結果表示カードを追加
  - 結果を fdpr_judgment_json カラムに保存・再表示対応
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
| 外為法（FEFTA）省令 | ★★★★★ | ✅ 191/191 ノード充填済・Layer A 2,184vec（entity_list除外・eccn_tech追加・2026-05-10）| — |
| ECCN/EAR Part774 | ★★★★☆ | ✅ 84/84 requirement_text 充填済・eccn_tech 25 ECCN の技術用語辞書追加 | Semantic Scholar API Key で論文収集拡充 |
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
**Layer B/D HTTP API**: GET /api/faiss/search/layer-b・/api/faiss/search/layer-d 追加済（2026-05-10）

---

## 7. 技術的負債（残存）

| 問題 | 影響 | 優先度 |
|------|------|--------|
| platform-core 業務ドメインルーター — Phase 6A〜6C で全7本をプロキシスタブ化済み ✅ | — | 解消済み |
| 各モジュールの pg_session.py が個別実装 | 接続パラメータ変更時に全モジュール修正が必要 | 低（env var 統一で対応済み） |
| ハードコードURL → 全モジュール環境変数化 ✅（2026-05-24 修正） | — | 解消済み（item_version.py / export_license.py / transaction_detail.html / import_profiles.html）|
| `bom_attestation_summary` us_controlled_pct 104% ✅（2026-05-31 修正） | — | 解消済み（edge.quantity 直和 → unit_value_usd 金額加重計算）|
| `api/products/by-code/{code}` が id/code/name のみ返却 | BOM取得等でフル情報が取れない | 低（次フェーズ候補）|
| BIS DPL 残 586件（1,010/1,596件） | CSL API の offset 上限 1,000 により未取得。eCFR Part 764 Suppl.1 は「標準条件文」でリスト非掲載。代替ソース未発見 | 低（公開APIに制約あり・受入済み）|
| EU Consolidated Sanctions 0件 | `webgate.ec.europa.eu` が 403 Forbidden（IP制限と推定）。EEAS/data.europa.eu も同様 | 低（IP制限・OpenSanctions 有料プランが回避策）|

---

## 8. システム起動・ヘルスチェック

```bash
# 起動
cd /Users/takehirosato/Desktop/AI_TradeManagement && ./start.sh

# ヘルスチェック（全モジュール）
curl -s http://localhost:8000/health  # platform-core
curl -s http://localhost:8011/health  # ai_validation
curl -s http://localhost:8002/health  # ai_classification
curl -s http://localhost:8003/health  # rnd_assessment
curl -s http://localhost:8004/health  # patent_search
curl -s http://localhost:8005/health  # screening
curl -s http://localhost:8006/health  # hs_classifier
curl -s http://localhost:8010/health  # dap
curl -s http://localhost:8012/health  # export_license
curl -s http://localhost:8013/health  # trade_gate
curl -s http://localhost:8014/health  # fta_origin

# FAISS インデックス状態確認
curl -s http://localhost:8011/admin/faiss/status  # Layer A/B/C
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
| branch_neurosymbolic | マージ済み | NeuroSymbolic基盤・全機能実装 |
| refactor/module-separation | **作業中** | Phase 6: platform-core からの業務ドメイン分離 |

**現在の作業ブランチ**: `refactor/module-separation`

---

## 10. 完了済み機能（④ 輸出許可申請）

```
④ 輸出許可申請管理（2026-04-26 完了）
  - plat_export_license_application テーブル（Alembic: a1b2c3d4e5f6）
  - platform-core/routers/export_license.py
    - CRUD（draft / update / delete）
    - POST /submit → submitted 状態遷移
    - POST /approve → approved + ライセンス番号・発行機関・期限登録
    - POST /deny → denied 状態遷移
    - POST /draft-from-transaction → ai_validation 取引からフォーム自動生成
    - GET /preview → BIS-748P HTML / 外為法様式第1 HTML レンダリング
    - GET /stats → ダッシュボード統計（期限切れ・期限間近カウント）
  - BIS-748P: Block 1/3/4/7/8/9/11/13/21/22 自動充填
  - 外為法様式第1: ECCN prefix → 輸出令別表第1 項番 自動マッピング
  - export_license.html: 期限ダッシュボード・申請一覧・承認モーダル・プレビュー
```

## 11. 完了済み機能（⑤ 品目バージョン管理）

```
⑤ 品目バージョン管理 + 仕様変更コンプライアンス影響検知（2026-04-26 完了）
  - plat_item_version / plat_compliance_change_event（Alembic: b2c3d4e5f6a7）
  - platform-core/routers/item_version.py
    - GET  /api/item-versions/stats            ダッシュボード統計
    - GET  /api/item-versions/items            品目一覧（現行バージョン付き）
    - GET  /api/item-versions/items/{id}       品目詳細（全バージョン履歴 + 影響イベント）
    - POST /api/item-versions/items/{id}/versions  新バージョン登録 + 自動アセスメント
    - POST /api/item-versions/webhook          外部システムWebhook受信（PLM/SDS/ERP）
    - GET  /api/item-versions/events           影響イベント一覧（フィルタ対応）
    - POST /api/item-versions/events/{id}/resolve  解決
    - POST /api/item-versions/events/{id}/dismiss  却下
  - 差分アセスメントエンジン:
    - ECCN変更       → HIGH（AI再判定・ライセンス更新アクション）
    - 原産国変更     → HIGH（De Minimis 再計算・原産性証明再取得）
    - 組成変更       → HIGH（SDS/GHS 再確認）
    - 工程変更       → MEDIUM / サプライヤー変更 → MEDIUM
    - US content 率が De Minimis 閾値（10%/25%）を跨ぐ → HIGH 昇格
  - item_version.html: 影響イベント一覧・バージョン履歴・新バージョン登録 UI
```

## 12. データ拡充フェーズ D（2026-04-28 着手）

### D1 フェーズ（即時対応）

| 優先度 | タスク | 内容 | 状態 |
|--------|--------|------|------|
| D1-1 | Layer D 学術論文インデックス再構築 | 501論文・14ECCN（S2無料枠収集済み）。eccn_tech_terms.json を 20→25 ECCN に拡張済み | ✅ 完了（2026-05-10）|
| D1-2 | 制裁リスト全量収録 | OFAC SDN全量 + EU統合制裁 + UK OFSI + BIS UVL/MEU/DPL | ✅ 完了（7ソース対応） |
| D1-3 | BIS 3リスト完全収録 | Entity List全量 + Unverified List + MEU List | ✅ 完了（D1-2に統合） |
| D1-4 | Fターム → 外為法/ECCNマッピング | 130テーマコード × 47 ECCN、patent_search 照合API追加 | ✅ 完了 |
| D1-5 | IPC完全マッピング | 174エントリ完成済み（追加拡張は次サイクル） | ✅ 既存 |

### D2 フェーズ（1ヶ月以内）

| 優先度 | タスク | 内容 | 状態 |
|--------|--------|------|------|
| D2-1 | ITAR/USML 収録 | 22 CFR Part 121 全21カテゴリ + regulatory.py API | ✅ 完了 |
| D2-2 | EU Dual-Use Regulation Annex I | EU 2021/821 全10カテゴリ + regime-check API | ✅ 完了 |
| D2-3 | Wassenaar ML/TN リスト | Wassenaar Arrangement ML全22カテゴリ + regulatory API拡張 | ✅ 完了 |
| D2-4 | EPA/FTA 特恵税率 DB | 日本締結 10協定 + 代表 HS 8コード × 複数協定税率（GET /api/fta/check・/ui/fta-check） | ✅ 完了 |
| D2-5 | JP/EP特許の定期収集 | patent_search → ai_validation 連携 + 定期収集スケジューラー | ⬜ 未着手 |

### D3 フェーズ（四半期以内）

| タスク | 内容 |
|--------|------|
| 中国輸出管理法 | MOFCOM 輸出管制法リスト収録（2023年改正対応） |
| EU TARIC / 米国 HTS | 関税番号体系の完全収録（現在: JP HS 11,368件のみ） |
| WMD Red Flag事例DB | 実際の違反事例テキストから FAISS Layer A 拡充 |

## 13. 次フェーズ候補（機能開発）

| 優先度 | タスク | 内容 |
|--------|--------|------|
| ✅ | Integration A | ItemVersion → AI Validation 「AI再判定」ボタン |
| ✅ | Integration B | AI Validation → 輸出許可申請 自動ドラフト |
| ✅ | Integration C | SupplyChainNode ↔ plat_item UUID FK 紐付け |
| ✅ | グローバル規制レジーム UI | /ui/regime-check 画面（ITAR/EU/MTCR/NSG/AG/Wassenaar 一括照合） |
| ✅ | Fターム検索統合 | patent_search 検索結果にF-term規制照合パネル + キーワード候補提案 |
| ✅ | Screening → 与信管理 自動連携 | 取引先登録時に screening API を BackgroundTasks で自動呼出し |
| ✅ | Layer A インデックス品質改善 | ECCN embed_text修正・5項/8項追加・USML/EU/Wassenaar収録。entity_list除外・eccn_tech追加 → 2,184vec（2026-05-10） |
| ✅ | サプライヤーポータル ファイルアップロード | enctype="multipart/form-data" + uploads/supplier/{id}/ 保存 + ダウンロード API |
| ✅ | 輸出許可申請拡張 | EL-{TYPE}-{YEAR}-{SEQ:04d} 自動採番・POST /use-value 価値控除・期限アラートスケジューラー |
| ✅ | D2-4 EPA/FTA 特恵税率 DB | 日本締結10協定・代表HS 8コード税率・/ui/fta-check・ポータルナビ追加 |
| ✅ | R&D モジュール UI/スコア刷新 | 4層改修: 進捗バー・Explainability カード・15+ルール・regulatory_risk独立・全根拠日本語化 |
| ✅ | グローバル多拠点 Phase 1 | plat_tenant 拡張・組織 CRUD /tree・拠点スイッチャー・X-Organization-Id 自動付与 |
| ✅ | DAP-A ワークフロー伴走 | DapWorkflowSession + /api/workflow/ 6エンドポイント（UC1/2/4/5/6/9） |
| ✅ | DAP-C 知識ベース更新 | system_prompt に UC別画面遷移ガイド・FAQ 7件追加 |
| ✅ | Phase 2 R&Dアクセス制御 | tech_sensitivity / RndAccessLog / みなし輸出フラグ自動記録・case_detail 機密設定 UI |
| ✅ | DAP-B ワークフローモード | chat-widget.js に UC 選択・進捗バー・navigate_to 自動実行・highlight スポットライト |
| ✅ | Phase 3 グローバル品目マスター | ProductCountryProfile に local_eccn/license_required 追加。品目一覧 国数バッジ・モーダルフォーム |
| ✅ | Phase 4 トランザクション多テナント化 | Transaction/ExportLicense に org_id 追加（Alembic）。ダッシュボード 自拠点/全拠点トグル |
| ✅ | Phase 5 グローバル規制・FTA 拡張 | FtaAgreement: origin_country 追加。RegulatoryChange: relevant_org_ids・拠点別フィルタリング |
| ✅ | BOMグラフ ECCN sync-eccnボタン | ノードクリックパネルに同期・AI判定依頼ボタン追加。cytoscape リアルタイム更新（2026-05-25） |
| ✅ | サプライヤーポータル メール送信 | smtplib STARTTLS 招待メール自動送信・SMTP env var ゲート（2026-05-25） |
| ★★★☆☆ | キャッチオール Red Flag 詳細入力 UI 改善 | RF1〜RF7 項目別詳細質問フロー・自動評価結果オーバーライド入力 |
| ★★★☆☆ | R&D→取引審査 counterparty 引き継ぎ UI | R&D 登録済み end_user を取引フェーズ取引先として編集可能な形でプリポップ |
| ★★★☆☆ | AI取引審査 モジュール名称変更 | ai_validation → AI取引審査（UI表示名変更・ナビラベル統一。Neurosymbolic 該非判定は DAP 経由で呼び出す形に整理） |
| ★★★☆☆ | FTA 税率データ拡充 | 現在は代表HSコードのみ。実務向けに品目単位の全量収録 |
| ✅ | Layer D データ収集・構築 | OpenAlex API 追加・25/25 ECCN 完全カバー → 5,765vec（S2: 508 + OpenAlex: 5,257）（2026-05-12）|
| ✅ | Layer A 品質改善 | entity_list 835件除外・eccn_tech 20件追加・2,184vec（2026-05-10） |
| ✅ | Layer B ECCN タグ付与 | build_layer_b_enhanced.py でIPC→ECCN マッピングから ECCN タグ付与・Layer B HTTP API 追加 |
| ★★★☆☆ | Layer D 拡充（Semantic Scholar API Key） | API Key 取得後に collect_academic_papers.py を全 25 ECCN で実行（現在 14/25 ECCN・508論文。残り11 ECCN は無料枠でヒットなし）|
| ✅ | JP 特許 Layer B 追加（Phase 1+2+3） | Phase 1+2: JP 4,549件（50 IPC コード）。Phase 3: US/EP 4,639件（CPC コード対応）→ 合計 10,783vec / 639 ECCN（2026-05-12）|
| ★★☆☆☆ | D2-5 JP/EP特許 定期収集 | patent_search → ai_validation 連携 + 定期収集スケジューラー |
| ✅ | スクリーニング精度修正（2026-05-16） | Step0 SQL ILIKE + エイリアス検索追加。サフィックス除去後スコアリング。ZTE/SMIC等の短名・頭字語ヒット。Sony等の偽陽性除去 |
| ✅ | ERP JSON一括インポート（2026-05-16） | POST /api/counterparties/import-batch（最大500件・同期スクリーニング・upsert・flagged_list即時返却） |
| ✅ | is_hit バグ修正（2026-05-16） | counterparty.py: `"hit"` → `in ("match", "possible_match")` / `list_type` → `list_source` |
| ✅ | **DAP Phase 1-4: 先輩担当者モード完全強化（2026-05-30）** | UC定義外部化(9UC)・fill_field_from_context・4段階フィールドマッチ・デモモード（DEMO1/DEMO2・typewriterFill・pause/fill_demo）|
| ✅ | **品目管理 UI リデザイン + EPA/FTA対応（2026-05-31）** | sticky セーブバー・ブロック順序最適化（基本情報→該非判定→BOM→国別プロファイル）・GHS折り畳み・外部AI判定2カラム化・国別HSサマリーテーブル・epa_tariff_rate/epa_agreement DB追加・DEMO3完成 |
| ✅ | **DAP Intake 3モード拡張（2026-05-31）** | 品目登録ヒアリング（product_registration）・R&D起案ヒアリング（rnd_project）追加。POST /api/products JSON エンドポイント新設。`_detect_intake_mode()` でメッセージから自動判別 → モード別システムプロンプト・action_plan・完了後ナビゲーション |
| ✅ | **ERP連携 v2.5 全機能実装（2026-06-07）** | TransactionCreateRequest v2.5（erp_case_no等11フィールド）・judgment正規化フィールド（APPROVED/NEEDS_REVIEW/REQUIRES_PERMIT/REJECTED/PENDING）・GET /api/transactions/search erp_case_no検索・POST webhook/judgment-updated ERP Webhook受信・GET pending-erp ERPポーリング・POST /api/export-licenses/draft-from-transaction プロキシ・requires_permit確定時export_licenseドラフト自動生成・base.htmlナビ「輸出許可申請」追加 |
| ✅ | **ERP_INTEGRATION_HANDOVER.md v2.5改訂（2026-06-07）** | ERP視点(v1.1)とAI_TM v2.5を統合。CRITICAL: BLOCKED_JUDGMENTS修正手順・case_noフォーマット・v2.5リクエストスキーマ全面更新 |
| ★★★☆☆ | 制裁リスト全量同期 | OFAC SDN URL変更対応済。Trade.gov API キー取得で BIS EL 完全収録（現在 watchlist 15件のみ）|

---

## 14. Phase 6: プラットフォーム設計最適化（branch: refactor/module-separation）

### 設計原則

```
platform-core の責務 = インフラ・接着剤
  ✓ 認証/SSO・組織/テナント管理
  ✓ モジュール登録・リバースプロキシ
  ✓ FAISS 共有サービス・監査ログ
  ✓ 規制インテリジェンス（横断的）
  ✓ コンプライアンス集約ダッシュボード
  ✗ 業務ドメインロジック（→ 各モジュールへ）
```

### Phase 6A — 高優先度（業務機能を既存モジュールへ統合）

| # | 移動元（platform-core） | 移動先 | DB移行 | 状態 |
|---|----------------------|--------|--------|------|
| 6A-1 | `routers/counterparty.py` (453行) | `screening` モジュール (8005) | PostgreSQL → PostgreSQL (共有) | ✅ 完了 |
| 6A-2 | `routers/supply_chain.py` (493行) | `ai_classification` モジュール (8002) | PostgreSQL async → SQLite sync | ✅ 完了 |
| 6A-3 | `routers/supplier_attestation.py` (371行) | `ai_classification` モジュール (8002) | PostgreSQL async → SQLite sync | ✅ 完了 |
| 6A-4 | `routers/supplier_portal.py` (335行) | `ai_classification` モジュール (8002) | PostgreSQL async → SQLite sync | ✅ 完了 |
| 6A-5 | `routers/item_version.py` (714行) | `ai_classification` モジュール (8002) | PostgreSQL async → SQLite sync | ✅ 完了 |

**合計削減行数**: 約 2,366 行 / platform-core から除去

### Phase 6B — 中優先度（新独立モジュールとして抽出）

| # | 抽出元（platform-core） | 新モジュール | ポート | 状態 |
|---|----------------------|------------|--------|------|
| 6B-1 | `routers/export_license.py` (762行) | `export_license` | 8012 | ✅ 完了（ai_validation proxy・ERP連携・自動ドラフト生成済み）|
| 6B-2 | `routers/fta.py` (397行) | `fta_origin` | 8014 | ⬜ 未着手 |

### Phase 6C — 低優先度（ERP連携成熟後に抽出）

| # | 抽出元（platform-core） | 新モジュール | ポート | 状態 |
|---|----------------------|------------|--------|------|
| 6C-1 | `routers/transaction_review.py` (560行) | `trade_gate` | 8013 | ⬜ ERP連携が安定後 |

### 完了後の platform-core 残存ルーター（インフラのみ）

```
proxy.py / internal.py / modules.py / users.py / tenants.py
organizations.py / projects.py / regulatory.py / metrics.py
compliance_lookup.py / faiss_search.py / ui.py / auth/
```

---

### ✅ Phase 8: コンプライアンス進捗クリックナビ + モジュール間ID整合（2026-06-27 完了）

| タスク | 内容 |
|--------|------|
| 8-1 | `compliance_lookup.html` 全インタラクティブ要素にクリックナビゲーション追加（パイプライン品目・ステージ✅・アクションカード・変化点フィード） |
| 8-2 | UUID vs Integer ナビゲーションバグ修正: platform-core Item UUID を ai_classification `/products/{id}/edit`（Integer期待）に渡していた問題を検索ベースURLに変更 |
| 8-3 | `ai_classification.Product` に `plat_item_id` カラム追加（SQLite `_ensure_columns()` 自動ALTER） |
| 8-4 | `compliance_lookup.py` に `_fetch_classification_product_id()` 追加（`by-code` → `json-search` フォールバック） |
| 8-5 | `ai_classification` に `/api/products/by-plat-id/{plat_id}` エンドポイント追加（platform-core UUID → Integer product_id 解決） |
| 8-6 | `screening.ScreeningResult` に `transaction_id: Integer` カラム追加（Alembic migration: d56214c5e744） |
| 8-7 | `screening.ScreenRequest` に `transaction_id: int | None` フィールド追加（ERP等の呼び出し元で省略可） |
| 8-8 | `ai_validation._screen_counterparty_bg()` が screening API に `transaction_id` を渡すよう修正 |
| 8-9 | **ERP screening adapter `is_match` バグ修正**: `result_status == "hit"` → `in ("match", "possible_match")`（制裁ヒット時も常に `is_match: false` だった重大バグ） |

**動作確認（全APIテスト済み）**:
- ERP `/screening/denied-party` ZTE → `is_match: true, confidence: 1.0` ✅
- ERP `/screening/denied-party` Sony → `is_match: false` ✅  
- ERP `/screening/denied-party` KP国 → `is_match: true` (embargo) ✅
- 直接 `POST /api/screen` with `transaction_id: 999` → DB に `tx_id=999` 保存確認 ✅
- ai_validation 取引 127 作成 → `screening_queued: true` → screening DB `tx_id=127` 自動記録 ✅
- ai_classification `plat_item_id` カラム存在確認 ✅
- ERP `/gaihi/judge`, `/hs/classify`, `/transaction/review`, `/export/precheck` 全正常 ✅
- ERP 401 認証なし拒否 ✅

---

### ✅ Phase 10: ERP v2.0 連携インターフェース実装（2026-06-28 完了）

ERP_INTEGRATION_HANDOVER_ver2.md のギャップ分析に基づき、ERP v2.0 との4つの連携インターフェースを実装・E2E確認済み。

| タスク | 内容 | 確認 |
|--------|------|------|
| 10-1 | **JSON バッチスクリーニング** `POST /api/screening/batch`（screening:8005）新設。ERP v2.0 が JSON 配列で最大500件のエンティティをスクリーニング可能。結果: `{total, flagged_count, clear_count, flagged_list, results}`。ZTE/Huawei フラグ、Sony クリア確認 | ✅ |
| 10-2 | **MATERIAL_ORIGIN_CHANGE イベント受信** `POST /api/transactions/events`（ai_validation:8011）新設。`exceeds_deminimis_threshold=true` の場合、De Minimis BREACH 案件（`[ERP De Minimis BREACH] {mat} 原産国変更...`）を自動生成して erp_case_no, UsageRequirement 付きで案件を登録。非BREACH は case_ref=null でログのみ | ✅ |
| 10-3 | **判定完了 outbound webhook** `_notify_erp_judgment()` を `save_agent_judgment` 内（db.commit 後）で fire-and-forget スレッドで実行。`ERP_WEBHOOK_URL=http://localhost:8888/gts/webhook/judgment-updated`、`ERP_WEBHOOK_BEARER=dev-erp-integration-key`。ERP 側 `fefta_judgment` が実際に更新されることを SQLite 直接確認（REJECTED→値が変化）| ✅ |
| 10-4 | **erp-sync BOM v2.0** `_ErpBomComponent.origin_country` フィールド追加（ロット実績原産国）。`bom` 配列を `bom_json` に保存・原産国変更時 ai_validation `/events` へ自動通知。`POST /products/erp-sync/batch`（最大200件）エンドポイント追加。レスポンスに `aitm_product_id` 追加（ERP 側で `materials.aitm_product_id` に保存可能）| ✅ |

**E2E 動作確認（全テスト済み）**:
- `POST /api/screening/batch` ZTE+Huawei フラグ、Sony クリア ✅
- `POST /api/transactions/events` BREACH → CASE-2026-0001 自動生成 ✅
- `POST /api/transactions/events` 非BREACH → case_ref=null ✅
- `POST /products/erp-sync` with BOM v2.0 → aitm_product_id=58 返却 ✅
- `POST /products/erp-sync/batch` → synced=2 ✅
- `save_agent_judgment` → background thread → ERP `fefta_judgment=REJECTED` SQLite 直接確認 ✅

**変更ファイル**:
- `modules/screening/app/routers/screening.py` — `_BatchJsonEntity`, `_BatchJsonRequest` スキーマ追加・`/screening/batch` JSON エンドポイント追加
- `modules/ai_validation/app/routers/api_transactions.py` — `_ErpEventPayload`, `_handle_origin_change_event()`, `_generate_case_no()` 追加・`POST /events` エンドポイント追加
- `modules/ai_validation/app/routers/decision.py` — `_notify_erp_judgment()` 追加・`save_agent_judgment` 内でコール
- `modules/ai_classification/app/routers/products.py` — `_ErpBomComponent.origin_country` 追加・`bom` フィールド追加・`_erp_sync_one()` 共通化・`aitm_product_id` 返却・`/products/erp-sync/batch` 追加
- `.env` — `ERP_WEBHOOK_URL`, `ERP_WEBHOOK_BEARER` 追加

---

### ✅ Phase 11: モジュール間リスク伝導・未接続箇所実装（2026-06-28 完了）

Phase 10 で残存した4つの未接続箇所を全て実装・E2E確認済み。

| タスク | 内容 | 確認 |
|--------|------|------|
| 11-1 | **compliance_lookup plat_item 自動登録** `POST /products/erp-sync` 完了後に `_register_plat_item_bg()` を fire-and-forget で呼び出し、ai_classification の item-versions API を通じて `plat_item`（PostgreSQL）へ upsert。これによりコンプライアンス進捗 Lookup に ERP 経由の品目が自動表示される。COO 変更時は `_trigger_coo_change_item_version()` でバージョンイベントも自動生成 | ✅ |
| 11-2 | **export_license → ERP 許可証通知** `approve_license` 承認時に `_notify_erp_license_approved()` を fire-and-forget で実行。linked transaction の `linked_product_code` を ai_validation から取得して `material_code` に使用し、ERP `/gts/webhook/judgment-updated` へ `new_judgment=APPROVED`, `license_number`, `expires_at` を通知。否認時も同様に `_notify_erp_license_denied()` で `REJECTED` 通知。ERP `fefta_judgment`: `NOT_APPLICABLE` → `APPROVED` を SQLite 直接確認 | ✅ |
| 11-3 | **ERP Pull ポーラー（De Minimis BREACH 定期取得）** `ai_validation/main.py` の `_on_startup` から asyncio background task `_erp_deminimis_poller()` を起動。起動 30 秒後から `DEMINIMIS_POLL_INTERVAL_SEC`（デフォルト 600 秒 = 10 分）間隔で実行。ERP JWT ログイン → `GET /gts/deminimis?alert_level=BREACH&ai_tm_notified=false` → 未通知レコード毎に `POST /api/transactions/events` (MATERIAL_ORIGIN_CHANGE) → `PATCH /gts/deminimis/{id}/mark-notified` で ERP 側 `ai_tm_notified=true` に更新。ERP に `PATCH /gts/deminimis/{id}/mark-notified` エンドポイント新設 | ✅ |
| 11-4 | **rnd_assessment → ai_validation DEEMED_EXPORT_RISK** `assessments_run` で `risk_level in ("HIGH", "CRITICAL")` の場合、`_notify_ai_validation_deemed_export()` を fire-and-forget で実行。ai_validation `POST /api/transactions/events` の新イベントタイプ `DEEMED_EXPORT_RISK` を処理する `_handle_deemed_export_risk_event()` を追加。`[みなし輸出リスク HIGH] {案件名} 関係者: {person_name}` タイトルで取引審査案件を自動生成。CASE-2026-0003 自動生成を確認 | ✅ |

**E2E 動作確認（全テスト済み）**:
- `POST /api/transactions/events` `DEEMED_EXPORT_RISK` → CASE-2026-0003 自動生成 ✅
- `POST /api/export-licenses/{id}/approve` → ERP `fefta_judgment=APPROVED`（MAT-1000001 SQLite 確認）✅
- `POST /api/export-licenses/{id}/deny` → ERP `fefta_judgment=REJECTED` ✅
- `PATCH /gts/deminimis/11/mark-notified` → `{"ok": true, "ai_tm_case_ref": "CASE-2026-TEST"}` ✅

**変更ファイル**:
- `modules/ai_classification/app/routers/products.py` — `_register_plat_item_bg()`, `_trigger_coo_change_item_version()` 追加・`_erp_sync_one` で呼び出し
- `modules/export_license/app/routers/export_license.py` — `_notify_erp_license_approved()`, `_notify_erp_license_denied()` 追加・`approve_license`, `deny_license` から呼び出し。`_ERP_WEBHOOK_URL`, `_ERP_WEBHOOK_BEARER` env var 追加
- `modules/ai_validation/app/main.py` — `_erp_deminimis_poller()` asyncio background task 追加・`_on_startup` で起動
- `modules/ai_validation/app/routers/api_transactions.py` — `_ErpEventPayload` に DEEMED_EXPORT_RISK フィールド追加・`_handle_deemed_export_risk_event()` 追加・`receive_erp_event` でルーティング
- `modules/rnd_assessment/app/ui/router.py` — `_notify_ai_validation_deemed_export()` 追加・`assessments_run` で HIGH/CRITICAL 時に呼び出し
- `erp-system/app/modules/gts/lot_router.py` — `PATCH /gts/deminimis/{id}/mark-notified` エンドポイント追加
- `.env` — `ERP_ADMIN_EMAIL`, `ERP_ADMIN_PASSWORD`, `DEMINIMIS_POLL_INTERVAL_SEC` 追加

**残存未実装**:
- `materials.aitm_product_id` ERP側保存（ERP schema 変更必要）
- `denied_party_checked_at` フィールド（ERP スキーマ拡張）

---

*更新: 2026-06-28（Phase 11: モジュール間リスク伝導・未接続箇所全実装完了）*
*担当: Takehiro Sato + Claude Sonnet 4.6*
