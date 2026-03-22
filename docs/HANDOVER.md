# 開発環境引き継ぎ書

**作成日**: 2026-03-23
**引き継ぎ元**: 旧 Mac（開発環境 A）
**引き継ぎ先**: 新 Mac（開発環境 B）

---

## 新環境セットアップ手順

### 1. リポジトリ確認

```bash
cd /path/to/AI_TradeManagement   # クローン済みのディレクトリ
git status
git log --oneline -5
```

現在のブランチは `branch_neurosymbolic`。main との差分が本実装内容です。

```bash
git checkout branch_neurosymbolic
git pull origin branch_neurosymbolic
```

---

### 2. Python 環境

Python 3.12 を推奨（`.pyenv` で管理）。

```bash
# 仮想環境作成
python3 -m venv .venv
source .venv/bin/activate

# 各モジュールの依存インストール
pip install -r platform-core/requirements.txt
pip install -r modules/ai_validation/requirements.txt
pip install -r modules/ai_classification/requirements.txt
pip install -r modules/rnd_assessment/requirements.txt
pip install -r modules/patent_search/requirements.txt
pip install -r modules/hs_classifier/requirements.txt
pip install -r modules/dap/requirements.txt

# 追加パッケージ（スクリプト用）
pip install python-pptx google-auth google-auth-oauthlib google-api-python-client
```

---

### 3. 環境変数・認証ファイル

以下のファイルは `.gitignore` 対象のため、旧環境から手動でコピーが必要：

| ファイル | 用途 | コピー先 |
|---|---|---|
| `credentials.json` | Google API OAuth クライアント情報 | プロジェクトルート |
| `token.json` | Google API アクセストークン | プロジェクトルート |
| `.env` (存在する場合) | 各種 API キー | プロジェクトルート |

> **Google 認証**: `token.json` が切れている場合、`scripts/upload_to_google_slides.py` を一度実行するとブラウザ認証が走り再生成されます。

---

### 4. データファイル（staging）

`data/staging/` 以下のファイルも `.gitignore` 対象。旧環境から `rsync` または手動コピー：

```bash
# 旧 Mac から新 Mac へ（旧 Mac 上で実行）
rsync -av /path/to/AI_TradeManagement/data/staging/ \
  new-mac:/path/to/AI_TradeManagement/data/staging/
```

主要ファイル：
- `hs_fefta_mapping_v2.json` — HS → 外為法マッピング（`_enrich_candidates` で必須）
- `layer_a.index` / `layer_a_meta.json` — FAISS Layer A インデックス
- `layer_b.index` / `layer_b_meta.json` — FAISS Layer B インデックス
- `ccl_eccn_entries_v8.json` — ECCN エントリ

---

### 5. FAISS インデックス確認

hs_classifier の起動時にインデックスが自動ロードされます：

```bash
cd modules/hs_classifier
PYTHONPATH=/path/to/platform-core uvicorn app.main:app --port 8006
# → {"status":"ok","index_size":5476,"index_built":true} が返れば OK
curl http://localhost:8006/health
```

インデックスが壊れている場合は `scripts/colab/06_rebuild_layer_a.ipynb` で再構築。

---

### 6. DBマイグレーション

各モジュールは SQLite を使用。初回起動時に自動 migrate される設計ですが、念のため確認：

```bash
# ai_validation
cd modules/ai_validation
alembic upgrade head

# ai_classification
cd modules/ai_classification
alembic upgrade head

# rnd_assessment
cd modules/rnd_assessment
alembic upgrade head
```

---

### 7. 全モジュール起動スクリプト

プロジェクトルートに以下のスクリプトを作成して使うと便利：

```bash
#!/bin/bash
# start_all.sh
BASE=$(cd "$(dirname "$0")" && pwd)
export PYTHONPATH="$BASE/platform-core"

cd "$BASE/platform-core" && uvicorn platform_core.main:app --port 8000 &
cd "$BASE/modules/ai_validation" && uvicorn app.main:app --port 8001 &
cd "$BASE/modules/ai_classification" && uvicorn app.main:app --port 8002 &
cd "$BASE/modules/rnd_assessment" && uvicorn app.main:app --port 8003 &
cd "$BASE/modules/patent_search" && uvicorn app.main:app --port 8004 &
cd "$BASE/modules/hs_classifier" && uvicorn app.main:app --port 8006 &
cd "$BASE/modules/dap" && uvicorn app.main:app --port 8010 &

echo "全モジュール起動完了。各ポート: 8000/8001/8002/8003/8004/8006/8010"
wait
```

---

### 8. VS Code + Claude Code セットアップ

1. VS Code で `AI_TradeManagement` フォルダを開く
2. Claude Code 拡張をインストール（既インストール済みなら省略）
3. 作業開始時に Claude Code に以下を読み込ませる：

```
以下のファイルを読んで開発状況を把握してから作業を開始してください：
- docs/HANDOVER.md（この引き継ぎ書）
- docs/DEV_STATUS.md（実装済み機能の詳細）
```

---

## 現在の開発状況サマリー

詳細は [DEV_STATUS.md](./DEV_STATUS.md) を参照。

### 実装完了（直近）
- ✅ P0-2: asyncio 規制動向スケジューラー（platform-core）
- ✅ P1-1: みなし輸出 × R&D ケース人物管理（rnd_assessment）
- ✅ P1-2: 規制動向アラートウィジェット（platform-core dashboard）
- ✅ P1-4: `/classify/sync` 同期エンドポイント（hs_classifier）
- ✅ P2-4: J-PlatPat フォールバック（patent_search）
- ✅ HS 判定精度改善: ECCN付加・heading クラスタリング再ランキング・モーダルUI刷新（ai_classification）

### 次回優先タスク
1. **P0**: DAP WAL 監査ログ統合
2. **P0**: patent_search WAL
3. **改善**: FAISS スコア均一問題（日本語クエリ精度）
4. **PR**: `branch_neurosymbolic` → `main` のマージ

---

## 重要なアーキテクチャメモ

### PYTHONPATH 設定が必要なモジュール
`rnd_assessment`, `patent_search`, `hs_classifier` は内部で `from platform_core ...` を import するため、必ず `PYTHONPATH=/path/to/platform-core` を設定すること。設定なしでは `No module named 'platform_core'` でクラッシュする。

### FAISS embed_text の精度問題
`hs_classifier` の FAISS インデックスは `embed_text` に日本語の FEFTA ラベル（例: `"外為法: 電子部品・集積回路"`）を含む。これが原因で日本語クエリ時に無関係な HS コードがヒットしやすい。`_rerank_by_heading()` による heading 単位クラスタリングで緩和済みだが、根本解決は embed_text 改善が必要。

### ai_classification の `--app-dir` フラグ問題
`uvicorn --app-dir modules/ai_classification app.main:app` のような起動方法は pydantic の "Extra inputs are not permitted" エラーを引き起こす。必ず `cd modules/ai_classification && uvicorn app.main:app` の形で起動すること。

---

## 連絡・参照

- GitHub リポジトリ: （プロジェクト設定で確認）
- 開発ブランチ: `branch_neurosymbolic`
