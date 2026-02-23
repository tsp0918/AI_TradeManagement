# AI Trade Management

**外為法対応・輸出管理 AI プラットフォーム**
モジュール型アーキテクチャにより、各機能を独立した FastAPI サービスとして運用します。

---

## アーキテクチャ概要

```
AI_TradeManagement/
├── platform-core/          # 共通基盤 (認証・DB・モジュールレジストリ・ポータル UI)
├── modules/
│   ├── ai_validation/      # 🔐 AI該非判定         (port 8001)
│   ├── ai_classification/  # 📦 品目管理            (port 8002)
│   ├── rnd_assessment/     # 🔬 R&Dリスク評価       (port 8003)
│   ├── patent_search/      # 📋 AI特許検索          (port 8004)
│   ├── screening/          # 🛡️ 懸念取引先スクリーニング (port 8005)
│   └── dap/               # 🔗 DAP/AIオーケストレーター (port 8010)
├── .venv/                  # Python 仮想環境
├── start.sh                # 起動スクリプト
└── README.md
```

platform-core が起動すると、全モジュールと Ollama を **自動的にサブプロセスで起動** します。
停止も platform-core 終了時に一括で行われます。

---

## 必要環境

| 依存 | バージョン | 備考 |
|---|---|---|
| Python | 3.12 | x86_64 / ARM64 |
| PostgreSQL | 14+ | platform-core / 各モジュール DB |
| Ollama | 0.15+ | AI機能 (patent_search, ai_classification) に使用 |
| ネットワーク | — | Google Fonts / HuggingFace Hub (初回のみ) |

> **Ollama について**
> `ollama serve` が未起動の場合、platform-core 起動時に自動で立ち上げます。
> Ollama 未インストールの場合は AI 機能が制限されますが、他機能は正常動作します。

---

## セットアップ

### 1. 仮想環境と依存パッケージ

```bash
python3 -m venv .venv

# 基本パッケージ (platform-core + 全モジュール共通)
.venv/bin/pip3 install \
  fastapi uvicorn[standard] jinja2 python-multipart \
  sqlalchemy[asyncio] alembic asyncpg \
  httpx pydantic pydantic-settings python-dotenv \
  authlib bcrypt python-jose[cryptography] \
  aiosqlite \
  numpy pandas pypdf2 python-dateutil \
  scikit-learn huggingface-hub \
  ollama anthropic \
  google-cloud-bigquery google-auth

# ML パッケージ (ai_validation に必要)
# Intel Mac の場合: torch は 2.2.x まで対応 (CPU-only)
.venv/bin/pip3 install "numpy<2"   # torch 2.2 互換
.venv/bin/pip3 install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip3 install "sentence-transformers<5" faiss-cpu transformers
```

### 2. 環境変数 (.env)

```bash
cp platform-core/.env.example platform-core/.env
# DATABASE_URL, SECRET_KEY などを設定
```

主な環境変数:

```env
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/ai_trade
SECRET_KEY=your-secret-key-here
PLATFORM_ENV=development

# モジュール URL (デフォルトはローカルポート)
MODULE_AI_VALIDATION_URL=http://localhost:8001
MODULE_AI_CLASSIFICATION_URL=http://localhost:8002
MODULE_RND_ASSESSMENT_URL=http://localhost:8003
MODULE_PATENT_SEARCH_URL=http://localhost:8004
MODULE_SCREENING_URL=http://localhost:8005
MODULE_DAP_URL=http://localhost:8010

# AI API (将来: Claude API に統合予定)
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. DB マイグレーション

```bash
# platform-core
PYTHONPATH=platform-core/ .venv/bin/alembic -c platform-core/alembic.ini upgrade head

# 各モジュール (必要に応じて)
PYTHONPATH=modules/ai_validation:platform-core/ \
  .venv/bin/alembic -c modules/ai_validation/alembic.ini upgrade head
```

---

## 起動 / 停止

### 通常起動

```bash
./start.sh
```

以下が自動で起動します:
- Ollama サーバー (port 11434)
- platform-core portal (port 8000)
- 全6モジュール (ports 8001〜8010)

ブラウザで → **http://localhost:8000**

### 開発モード (ホットリロード付き)

```bash
./start.sh --dev
```

> 注意: --dev モードでは platform-core のコードのみリロードされます。
> モジュールサブプロセスはリロードされません（ポート使用中のためスキップ）。

### 停止

```bash
./start.sh --stop
```

または Ctrl+C で platform-core を停止すると、全モジュールも自動停止します。

### 手動起動 (1コマンド)

```bash
PYTHONPATH=platform-core/ .venv/bin/uvicorn platform_core.main:app \
  --host 0.0.0.0 --port 8000
```

---

## モジュール一覧

| モジュール | ポート | 概要 | AI依存 |
|---|---|---|---|
| platform-core | 8000 | ポータル・認証・共通API | — |
| ai_validation | 8001 | AI該非判定 (外為法) | FAISS + sentence-transformers |
| ai_classification | 8002 | 品目管理・SDS解析 | Ollama |
| rnd_assessment | 8003 | R&Dリスク評価 | — |
| patent_search | 8004 | 特許検索・用途抽出 | Ollama + BigQuery |
| screening | 8005 | 懸念取引先スクリーニング | — |
| dap | 8010 | AI オーケストレーター | Claude API (Anthropic) |

---

## AI 層の設計方針

### 現在の構成

```
ai_classification / patent_search  →  Ollama (ローカル LLM)
ai_validation                      →  FAISS + sentence-transformers (ローカル埋め込み)
dap                                →  Anthropic Claude API
```

### 将来的な移行計画 (Claude API への統合)

```
全モジュールの AI 推論
  →  Claude API (claude-3-5-sonnet / claude-3-opus)
  →  Claude Embeddings API (埋め込みベクトル生成)
  →  ローカル FAISS は外部ベクトルDBに移行 (Pinecone / pgvector 等)
```

**環境非依存化のロードマップ:**

| フェーズ | 対応 |
|---|---|
| Phase 1 (現在) | ローカル実行 (Ollama + FAISS)。Intel Mac / M-series Mac で動作。 |
| Phase 2 | Claude API 統合。Ollama 依存を排除し、ネットワーク接続のみで動作。 |
| Phase 3 | Docker コンテナ化。ユーザー PC の OS/CPU アーキテクチャ非依存。 |
| Phase 4 | クラウドデプロイ (AWS / GCP / Azure)。PC 側は Web ブラウザのみ。 |

> Intel Mac では `torch>=2.4` が利用不可のため `sentence-transformers<5` + `torch==2.2` を使用。
> Phase 2 以降の Claude API 移行により、この制約は解消されます。

---

## API ドキュメント

各サービスの Swagger UI:

| サービス | URL |
|---|---|
| platform-core | http://localhost:8000/docs |
| ai_validation | http://localhost:8001/docs |
| ai_classification | http://localhost:8002/docs |
| rnd_assessment | http://localhost:8003/docs |
| patent_search | http://localhost:8004/docs |
| screening | http://localhost:8005/docs |
| dap | http://localhost:8010/docs |

---

## ヘルスチェック

```bash
# platform-core
curl http://localhost:8000/health

# 全モジュールのヘルス (portal 経由)
curl http://localhost:8000/ui/health/ai_validation
curl http://localhost:8000/ui/health/screening
# ... 他モジュールも同様
```

---

## トラブルシューティング

### モジュールが起動しない

```bash
# ポート確認
lsof -i :8001   # ai_validation
lsof -i :8002   # ai_classification

# 手動起動でエラーを確認
PYTHONPATH=modules/ai_validation:platform-core/ \
  .venv/bin/uvicorn app.main:app --port 8001 \
  --app-dir modules/ai_validation
```

### Ollama が起動しない

```bash
# 手動起動
ollama serve &

# モデル確認
ollama list
ollama pull llama3   # 推奨モデル
```

### numpy / torch バージョンエラー (Intel Mac)

```bash
.venv/bin/pip3 install "numpy<2"
.venv/bin/pip3 install "sentence-transformers<5"
```
