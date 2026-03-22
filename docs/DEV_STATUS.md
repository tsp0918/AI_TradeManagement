# AI Trade Compliance Management — 開発実装状況

**最終更新**: 2026-03-23
**ブランチ**: `branch_neurosymbolic`
**ベースブランチ**: `main`

---

## モジュール一覧

| モジュール | ポート | ディレクトリ | 状態 |
|---|---|---|---|
| platform-core | 8000 | `platform-core/` | ✅ 稼働中 |
| ai_validation | 8001 | `modules/ai_validation/` | ✅ 稼働中 |
| ai_classification | 8002 | `modules/ai_classification/` | ✅ 稼働中 |
| rnd_assessment | 8003 | `modules/rnd_assessment/` | ✅ 稼働中 |
| patent_search | 8004/8005 | `modules/patent_search/` | ✅ 稼働中 |
| hs_classifier | 8006 | `modules/hs_classifier/` | ✅ 稼働中 |
| dap | 8010 | `modules/dap/` | ✅ 稼働中 |

---

## 実装済みフィーチャー（直近）

### P0-2 — asyncio 規制動向スケジューラー ✅
- **ファイル**: `platform-core/platform_core/main.py`
- APScheduler 不使用。`asyncio.create_task()` で24時間周期の `_regulatory_scheduler()` を lifespan に組み込み
- 起動60秒後に初回実行、以降 `_REG_CHECK_INTERVAL = 24 * 3600` で繰り返し
- `_check_egov()` / `_check_bis()` を呼び出し、結果を DB コミット・ログ出力

### P0-3 — HS Classifier Layer C UI 統合 ✅（既存実装確認）
- `modules/ai_classification/templates/product_edit.html` に `openHsModal()` 実装済み
- `/integrations/hs-classifier/request/{product_id}` をポーリングで呼び出し

### P1-1 — みなし輸出 × R&D ケース統合（人物管理） ✅
- **ファイル**: `modules/rnd_assessment/app/ui/router.py`
- `case_detail` に Personnel クエリを追加（`case_id` でフィルタ、`created_at` 降順）
- **ファイル**: `modules/rnd_assessment/app/ui/templates/case_detail.html`
  - Personnel セクション追加（高リスク人物バナー、氏名・国籍・FEFTA 区分・リスクレベル表示）
  - 「人物を登録」ボタン → `/ui/personnel/new?case_id=...`

### P1-2 — 規制動向ポータルウィジェット ✅
- **ファイル**: `platform-core/platform_core/routers/ui.py`
  - `dashboard()` に `RegulatoryChange` クエリ追加（未却下 warn/danger、最新5件）
  - `reg_alerts` をテンプレートに渡す
- **ファイル**: `platform-core/platform_core/templates/dashboard.html`
  - 黄色バナーウィジェット追加（タイトル・ソース・日付・詳細リンク）

### P1-4 — HS Classifier 同期モード `/classify/sync` ✅
- **ファイル**: `modules/hs_classifier/app/routers/classify.py`
- `ClassifySyncRequest` / `POST /classify/sync` 追加
- 内部で `classifier.classify()` を直接呼び出し、非同期キューを介さず即時 `ClassifyResult` を返す

### P2-4 — patent_search J-PlatPat フォールバック ✅
- **ファイル**: `modules/patent_search/app/services/jplatpat_service.py`（新規）
  - `async search_jplatpat(keywords, inventor, applicant, limit)` — httpx で J-PlatPat API 呼び出し
  - 結果を `"source": "jplatpat"` 付き標準 dict で返す
- **ファイル**: `modules/patent_search/app/services/search_service.py`
  - BigQuery 未設定時に J-PlatPat フォールバック分岐
- **ファイル**: `modules/patent_search/app/routers/search.py`
  - `/status` エンドポイントに `"fallback": "jplatpat"` / 注記を追加

### HS コード AI 判定強化（hs-classifier 精度改善）✅
- **ファイル**: `modules/ai_classification/app/routers/integrations.py`
  - `_HS_HEADING_TO_ECCN` / `_HS_CHAPTER_TO_ECCN` 辞書（50+ エントリ）
  - `_load_hs_fefta_reverse()` — `data/staging/hs_fefta_mapping_v2.json` の `reverse_index` を `lru_cache` でロード
  - `_enrich_candidates(candidates)` — ECCN / 外為法項番を各候補に付加
  - `_rerank_by_heading(candidates, top_n=5)` — 同一 HS heading（4桁）でクラスタリング、重複排除・再ランキング（top_k 5→15 でFAISS広く取り、heading 単位に絞り込む）
  - `request_hs_classification()` で `_rerank_by_heading(_enrich_candidates(...), top_n=5)` を呼び出し
- **ファイル**: `modules/ai_classification/app/routers/products.py`
  - `POST /products/{id}/hs/apply` に `eccn` / `fefta_ref` フォームフィールドを追加
- **ファイル**: `modules/ai_classification/templates/product_edit.html`
  - HS 判定モーダル刷新：推定 ECCN カラム（カラーバッジ）・外為法項番カラム追加
  - 行クリック廃止 → 各行に「反映」ボタン（`_applyBoth(hs, eccn, fefta)` で HS + ECCN を同時書き込み）
  - `#hs-apply-flash` 確認トースト追加

---

## 動作確認済み

| テスト | 結果 |
|---|---|
| `_rerank_by_heading("半導体集積回路")` | Rank1=HS854151, Rank3=HS854231（集積回路）上位3件が半導体系 |
| `/classify/sync` エンドポイント | 即時レスポンス確認済み |
| `case_detail` personnel セクション | テンプレート確認済み |
| 規制動向ウィジェット | dashboard.html 確認済み |

---

## 起動方法（全モジュール）

```bash
# platform-core
cd platform-core
uvicorn platform_core.main:app --port 8000 --reload

# ai_validation
cd modules/ai_validation
uvicorn app.main:app --port 8001 --reload

# ai_classification
cd modules/ai_classification
uvicorn app.main:app --port 8002 --reload

# rnd_assessment
cd modules/rnd_assessment
PYTHONPATH=/path/to/platform-core uvicorn app.main:app --port 8003 --reload

# patent_search
cd modules/patent_search
PYTHONPATH=/path/to/platform-core uvicorn app.main:app --port 8004 --reload

# hs_classifier
cd modules/hs_classifier
PYTHONPATH=/path/to/platform-core uvicorn app.main:app --port 8006 --reload

# dap
cd modules/dap
uvicorn app.main:app --port 8010 --reload
```

> **注意**: `rnd_assessment`, `patent_search`, `hs_classifier` は `PYTHONPATH` に `platform-core` のルートパスを設定する必要があります。

---

## 既知の課題・TODO

| 優先度 | タスク | メモ |
|---|---|---|
| P0 | DAP WAL 監査ログ統合 | 未着手 |
| P0 | patent_search WAL | 未着手 |
| P0 | hs_classifier Layer C 完全統合（webhook フロー） | 同期モードは完了、非同期 webhook フロー要確認 |
| 改善 | FAISS スコア均一問題 | 日本語クエリで全候補のスコアが 0.86〜0.88 に集中。embed_text の改善（日本語説明文を追加）またはクロスエンコーダー再ランカーの導入を検討 |
| 改善 | J-PlatPat DNS | 開発環境では `jppatsearch.inpit.go.jp` が名前解決不可。本番ネットワークでのみ動作 |

---

## データファイル（staging）

```
data/staging/
  hs_fefta_mapping_v2.json   # HS→外為法 reverse_index（_enrich_candidates で使用）
  ccl_eccn_entries_v8.json   # ECCN エントリ
  fefta_law_v5.json          # 外為法別表
  hs_all_merged.json         # HS コードマスタ
  layer_a_new_meta.json      # FAISS Layer A 新メタ
  layer_b_meta.json          # FAISS Layer B メタ
```

---

## Git ブランチ戦略

- `main` — 安定版
- `branch_neurosymbolic` — 現在の開発ブランチ（全 P0〜P2 実装含む）
- PR は `branch_neurosymbolic` → `main` で作成予定
