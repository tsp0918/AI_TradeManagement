# セットアップガイド

このガイドでは、Patent Search Applicationを動作させるために必要な詳細な手順を説明します。

## 目次

1. [Google Cloud Platform のセットアップ](#google-cloud-platform-のセットアップ)
2. [Ollama のセットアップ](#ollama-のセットアップ)
3. [アプリケーションのセットアップ](#アプリケーションのセットアップ)
4. [動作確認](#動作確認)

---

## Google Cloud Platform のセットアップ

### 1. GCPアカウントの作成

1. [Google Cloud Console](https://console.cloud.google.com/)にアクセス
2. Googleアカウントでログイン
3. 初回の場合は無料トライアルに登録（$300のクレジット付与）

### 2. プロジェクトの作成

1. GCPコンソール上部の「プロジェクトを選択」をクリック
2. 「新しいプロジェクト」をクリック
3. プロジェクト名を入力（例: `patent-search-app`）
4. 「作成」をクリック
5. **プロジェクトIDをメモ** してください（後で使用します）

### 3. BigQuery API の有効化

1. 左側メニューから「APIとサービス」→「ライブラリ」を選択
2. 検索ボックスに「BigQuery API」と入力
3. 「BigQuery API」を選択
4. 「有効にする」をクリック

### 4. 課金の有効化

⚠️ **重要**: BigQueryを使用するには課金を有効化する必要がありますが、Google Patents Public Datasetsへのクエリは**最初の1TB/月まで無料**です。

1. 左側メニューから「お支払い」を選択
2. 課金アカウントを作成（クレジットカード情報が必要）
3. 無料枠内での使用を推奨

### 5. サービスアカウントの作成

1. 左側メニューから「IAMと管理」→「サービスアカウント」を選択
2. 「サービスアカウントを作成」をクリック
3. サービスアカウント名を入力（例: `patent-search-sa`）
4. 「作成して続行」をクリック
5. ロールの選択:
   - 「BigQuery」→「BigQuery ユーザー」を選択
   - 「続行」をクリック
6. 「完了」をクリック

### 6. 認証情報（JSONキー）のダウンロード

1. 作成したサービスアカウントをクリック
2. 「キー」タブを選択
3. 「鍵を追加」→「新しい鍵を作成」をクリック
4. キーのタイプで「JSON」を選択
5. 「作成」をクリック
6. JSONファイルが自動ダウンロードされます
7. **このファイルを安全な場所に保存**してください

### 7. Google Patents Public Datasetsの確認

1. BigQueryコンソールを開く
2. 左側の「エクスプローラー」で「+ データを追加」をクリック
3. 「一般公開データセット」を選択
4. 「Google Patents Public Datasets」を検索
5. データセットを確認（特に `patents-public-data.patents.publications` テーブル）

---

## Ollama のセットアップ

### 1. Ollama のインストール確認

Patent Search Applicationでは、Ollamaがすでにインストールされていることを前提としています。

```bash
ollama --version
```

バージョン情報が表示されればOKです。

### 2. 推奨モデルのダウンロード

特許用途抽出に推奨されるモデル:

#### llama2（推奨）- バランス型
```bash
ollama pull llama2
```

#### mistral - 高速
```bash
ollama pull mistral
```

#### llama2:13b - 高精度（大きいモデル）
```bash
ollama pull llama2:13b
```

### 3. モデルの確認

```bash
ollama list
```

ダウンロードしたモデルの一覧が表示されます。

### 4. モデルのテスト

```bash
ollama run llama2 "特許分析のテストです"
```

応答が返ってくればOKです。`Ctrl+D`で終了します。

### 5. Ollamaサーバーの起動

通常、Ollamaは自動的にバックグラウンドで起動しますが、起動していない場合:

```bash
ollama serve
```

デフォルトで `http://localhost:11434` で起動します。

---

## アプリケーションのセットアップ

### 1. プロジェクトディレクトリに移動

```bash
cd /Users/takehirosato/patent-search-app
```

### 2. Python仮想環境の作成

```bash
python -m venv .venv
```

### 3. 仮想環境の有効化

**Mac/Linux:**
```bash
source .venv/bin/activate
```

**Windows:**
```bash
.venv\Scripts\activate
```

仮想環境が有効化されると、プロンプトに `(.venv)` が表示されます。

### 4. 依存関係のインストール

```bash
pip install -r requirements.txt
```

これには数分かかる場合があります。

### 5. GCP認証情報の配置

ダウンロードしたJSONキーファイルを `credentials` フォルダに配置:

```bash
cp /path/to/downloaded-key.json credentials/gcp-service-account.json
```

または、手動でファイルをコピーしてください。

### 6. 環境変数の設定

.env.exampleをコピーして.envファイルを作成:

```bash
cp .env.example .env
```

.envファイルを編集:

```bash
# お好みのエディタで編集
nano .env
# または
code .env
```

**必須設定:**

```bash
# GCPプロジェクトIDを設定（GCPコンソールで確認したもの）
GCP_PROJECT_ID=your-actual-project-id

# 認証情報ファイルのパス（デフォルトのままでOK）
GOOGLE_APPLICATION_CREDENTIALS=./credentials/gcp-service-account.json

# Ollamaモデル（ダウンロードしたモデル名）
OLLAMA_MODEL=llama2
```

### 7. データディレクトリの確認

dataディレクトリは自動作成されますが、念のため確認:

```bash
ls -la data/
```

### 8. データベースの初期化

アプリケーション起動時に自動的にデータベースが作成されます。

---

## 動作確認

### 1. アプリケーションの起動

```bash
uvicorn app.main:app --reload
```

以下のようなメッセージが表示されればOK:

```
✓ Database initialized
✓ Patent Search Application v1.0.0 starting...
✓ Server running at http://127.0.0.1:8000
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

### 2. ブラウザでアクセス

ブラウザを開いて以下のURLにアクセス:

```
http://localhost:8000
```

または

```
http://127.0.0.1:8000
```

### 3. ヘルスチェック

APIが正常に動作しているか確認:

```
http://localhost:8000/api/health
```

以下のようなJSONが返ってくればOK:

```json
{
  "status": "healthy",
  "app_name": "Patent Search Application",
  "version": "1.0.0",
  "services": {
    "bigquery": "connected",
    "ollama": "connected"
  }
}
```

⚠️ **注意**:
- `bigquery: "disconnected"` の場合はGCP設定を確認
- `ollama: "disconnected"` の場合はOllamaサーバーを確認

### 4. 検索テスト

1. ホームページで検索キーワードを入力（例: "machine learning"）
2. 検索ボタンをクリック
3. 結果が表示されればBigQuery連携成功

### 5. 用途抽出テスト

1. 検索結果から特許を1つ「お気に入りに追加」
2. お気に入りページに移動
3. 「詳細・用途抽出」をクリック
4. 「AI分析で用途を抽出」ボタンをクリック
5. 数秒〜数十秒後に用途が表示されればOllama連携成功

---

## トラブルシューティング

### BigQuery接続エラー

**エラー**: `BigQuery client not initialized` または `bigquery: "disconnected"`

**解決方法**:
1. GCP認証情報ファイルが正しい場所にあるか確認
   ```bash
   ls -la credentials/gcp-service-account.json
   ```
2. .envファイルの`GCP_PROJECT_ID`が正しいか確認
3. サービスアカウントに適切な権限があるか確認
4. BigQuery APIが有効化されているか確認

### Ollama接続エラー

**エラー**: `ollama: "disconnected"` または用途抽出が動作しない

**解決方法**:
1. Ollamaが起動しているか確認
   ```bash
   curl http://localhost:11434/api/version
   ```
2. モデルがダウンロードされているか確認
   ```bash
   ollama list
   ```
3. .envファイルの`OLLAMA_MODEL`が存在するモデル名か確認
4. 必要に応じてOllamaを再起動
   ```bash
   ollama serve
   ```

### データベースエラー

**エラー**: SQLite関連のエラー

**解決方法**:
1. dataフォルダの書き込み権限を確認
2. データベースファイルを削除して再作成
   ```bash
   rm data/patents.db
   # アプリケーションを再起動すると自動再作成されます
   ```

### ポート競合

**エラー**: `Address already in use`

**解決方法**:
1. 別のポートを使用
   ```bash
   uvicorn app.main:app --reload --port 8001
   ```
2. または、.envファイルで`PORT=8001`に変更

---

## セキュリティに関する注意

1. **認証情報の管理**
   - `credentials/` フォルダと `.env` ファイルは絶対にGitにコミットしないでください
   - `.gitignore` に含まれていることを確認してください

2. **サービスアカウント権限**
   - 最小限の権限（BigQuery Userのみ）を付与してください

3. **課金アラート**
   - GCPコンソールで予算アラートを設定することを推奨
   - 予期しない課金を防ぐため

---

## 次のステップ

セットアップが完了したら、[README.md](README.md)の「使い方」セクションを参照してアプリケーションを使い始めてください。

質問や問題がある場合は、プロジェクトのIssuesで報告してください。
