# Patent Search Application

Google Patents Public Datasetsを使用した特許検索Webアプリケーション。ローカルLLM（Ollama）を活用して特許の用途を自動抽出する機能を搭載しています。

## 特徴

- **高度な特許検索**: BigQueryを使ったGoogle Patentsデータベースの検索
- **詳細フィルタリング**: 日付範囲、発明者、出願人、国コードでの絞り込み
- **お気に入り管理**: 特許を保存して後で参照
- **AI用途抽出**: Ollamaを使って特許の実用的な用途を自動抽出（ユニーク機能）
- **検索履歴**: 過去の検索を追跡・再実行
- **データエクスポート**: CSV/JSON形式でのエクスポート機能

## 技術スタック

- **バックエンド**: FastAPI (Python 3.12+)
- **フロントエンド**: Jinja2テンプレート + Vanilla JavaScript
- **データベース**: SQLite（aiosqlite）
- **外部API**: Google BigQuery（Google Patents Public Datasets）
- **LLM**: Ollama（ローカル実行）
- **スタイリング**: カスタムCSS

## 前提条件

1. **Python 3.12以上**
2. **Google Cloud Platform アカウント**
   - BigQuery APIが有効化されたプロジェクト
   - サービスアカウントの認証情報（JSONキー）
3. **Ollama** - インストール済みでLLMモデルがダウンロードされていること

## セットアップ

詳細なセットアップ手順は [SETUP.md](SETUP.md) を参照してください。

### クイックスタート

1. **プロジェクトに移動**
   ```bash
   cd /Users/takehirosato/patent-search-app
   ```

2. **仮想環境を作成して有効化**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Macの場合
   ```

3. **依存関係をインストール**
   ```bash
   pip install -r requirements.txt
   ```

4. **環境変数を設定**
   ```bash
   cp .env.example .env
   # .envファイルを編集して必要な値を設定
   ```

5. **GCP認証情報を配置**
   ```bash
   # サービスアカウントのJSONキーを credentials/ フォルダに配置
   cp /path/to/your-key.json credentials/gcp-service-account.json
   ```

6. **Ollamaの準備**
   ```bash
   # Ollamaがインストールされていることを確認
   ollama --version

   # 推奨モデルをダウンロード
   ollama pull llama2
   ```

7. **アプリケーションを起動**
   ```bash
   uvicorn app.main:app --reload
   ```

8. **ブラウザでアクセス**
   ```
   http://localhost:8000
   ```

## プロジェクト構造

```
patent-search-app/
├── app/
│   ├── main.py                  # FastAPIアプリケーション
│   ├── config.py                # 設定管理
│   ├── database.py              # データベース接続
│   ├── models/                  # SQLAlchemyモデル
│   ├── routers/                 # APIエンドポイント
│   ├── services/                # ビジネスロジック
│   │   ├── bigquery_service.py  # BigQuery連携
│   │   ├── ollama_service.py    # LLM連携
│   │   ├── search_service.py    # 検索オーケストレーション
│   │   ├── favorites_service.py # お気に入り管理
│   │   └── export_service.py    # エクスポート機能
│   ├── templates/               # Jinja2テンプレート
│   └── static/                  # CSS/JS
├── data/                        # SQLiteデータベース
├── credentials/                 # GCP認証情報
├── requirements.txt             # Python依存関係
├── .env                         # 環境変数
└── README.md                    # このファイル
```

## 使い方

### 1. 特許を検索

1. ホームページでキーワードを入力
2. 必要に応じて詳細フィルターを設定（日付、発明者など）
3. 検索ボタンをクリック

### 2. お気に入りに追加

検索結果から「お気に入りに追加」ボタンをクリック

### 3. 用途を抽出（AI機能）

1. お気に入りページまたは特許詳細ページに移動
2. 「AI分析で用途を抽出」ボタンをクリック
3. Ollamaが特許の実用的な用途を自動生成

### 4. データをエクスポート

- 検索結果ページまたはお気に入りページで「CSV出力」または「JSON出力」をクリック

## API エンドポイント

### 検索
- `POST /api/search` - 特許検索
- `GET /api/search/history` - 検索履歴取得
- `DELETE /api/search/history/{id}` - 履歴削除

### お気に入り
- `GET /api/favorites` - お気に入り一覧
- `POST /api/favorites` - お気に入り追加
- `PUT /api/favorites/{id}` - メモ更新
- `DELETE /api/favorites/{id}` - お気に入り削除

### 用途抽出
- `POST /api/use-cases/extract/{patent_id}` - 用途抽出実行
- `GET /api/use-cases/{patent_id}` - 用途一覧取得
- `DELETE /api/use-cases/{id}` - 用途削除

### エクスポート
- `GET /api/export/favorites/csv` - お気に入りをCSV出力
- `GET /api/export/favorites/json` - お気に入りをJSON出力

### ヘルスチェック
- `GET /api/health` - システム状態確認

## トラブルシューティング

### BigQueryに接続できない

1. GCP認証情報が正しく配置されているか確認
2. BigQuery APIが有効化されているか確認
3. サービスアカウントに適切な権限（BigQuery User）があるか確認

### Ollamaが動作しない

1. Ollamaがインストールされているか確認: `ollama --version`
2. Ollamaサーバーが起動しているか確認
3. モデルがダウンロードされているか確認: `ollama list`
4. .envファイルのOLLAMA_HOSTが正しいか確認（デフォルト: http://localhost:11434）

### データベースエラー

1. data/フォルダが存在するか確認
2. 書き込み権限があるか確認
3. アプリケーションを再起動してデータベースを初期化

## 環境変数

主要な環境変数（詳細は.env.exampleを参照）：

```bash
# Google Cloud Platform
GCP_PROJECT_ID=your-gcp-project-id
GOOGLE_APPLICATION_CREDENTIALS=./credentials/gcp-service-account.json

# Ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama2

# アプリケーション
DEBUG=true
PORT=8000
```

## コスト

- **BigQuery**: Google Patents Public Datasetsへのクエリは最初の1TB/月まで無料
- **Ollama**: 完全無料（ローカル実行）
- **その他**: すべてオープンソース・無料

## ライセンス

このプロジェクトはMITライセンスの下で公開されています。

## 貢献

バグ報告や機能提案は Issues にお願いします。

## 参考リンク

- [Google Patents Public Datasets](https://console.cloud.google.com/marketplace/product/google_patents_public_datasets/google-patents-public-data)
- [Ollama](https://ollama.ai/)
- [FastAPI](https://fastapi.tiangolo.com/)
