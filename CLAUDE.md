# AI_TradeManagement — Claude Code 設定

## プロジェクト概要

日本の外為法および国際輸出管理規制（EAR/ECCN/Wassenaar）対応の
モジュール型 AI コンプライアンスプラットフォーム。

| モジュール | ポート | DB | 役割 |
|---|---|---|---|
| platform-core | 8000 | PostgreSQL | 共通基盤・FAISS・知識グラフ・規制スケジューラー・Agent |
| ai_validation | 8011 | SQLite | AI取引審査（取引・品目・仕向地の輸出審査管理）※8001はDocker占有のため8011使用 |
| ai_classification | 8002 | SQLite | 品目管理・SDS解析・HS分類連携・国別規制プロファイル |
| rnd_assessment | 8003 | SQLite | R&Dリスク評価・みなし輸出・人物管理 |
| patent_search | 8004 | SQLite | 特許検索（BigQuery + J-PlatPatフォールバック） |
| screening | 8005 | PostgreSQL | 制裁リストスクリーニング（OFAC/BIS） |
| hs_classifier | 8006 | — | HSコード判定（FAISS Layer C、5,476vec） |
| dap | 8010 | SQLite | AIオーケストレーター・先輩担当者モード（Claude API） |

起動: `cd /Users/takehirosato/Desktop/AI_TradeManagement && ./start.sh`

---

## 開発完了時の必須チェックリスト

**開発が一区切りついたら、必ず以下の順番で実行すること。**

### Step 0: トンネルを再起動して最新状態を外部公開する

開発・確認が完了するたびに、最新コードが外部からアクセスできる状態にすること。

```bash
./start.sh --restart-tunnel   # アプリは止めずトンネルのみ再起動
./start.sh --tunnel-status    # 接続確認
```

- トンネルは **常時稼働** を維持する
- 再起動後は `https://app.tsp-aitrademanagement.com` で動作確認すること
- HTTP エラーが出た場合は必ずトンネル状態も確認する（後述）

### Step 1: MEMORY.md を更新する

`/Users/takehirosato/.claude/projects/-Users-takehirosato-Desktop-AI-TradeManagement/memory/` の各ファイルを最新状態に更新する。

- `dev_status_p3.md`: 完了タスク・未着手バックログを反映
- `project_overview.md`: モジュール構成・Layer vec 数に変化があれば反映
- その他関連ファイルも必要に応じて更新

### Step 2: 不整合・修正箇所をチェックして実行する

以下の観点でコードベースを確認し、問題があれば修正する。

- **URL 整合性**: 各モジュールのエンドポイント URL がハードコードされていないか
- **環境変数**: `.env` に必要なキーが揃っているか（末尾改行も確認）
- **インポート**: `main.py` に新規ルーターが登録されているか
- **DB マイグレーション**: 新テーブル・カラムが起動時に自動作成されるか
- **テンプレート**: 新エンドポイントへの UI ボタン・JS が追加されているか
- **既知の技術的負債**: `ROADMAP.md` Section 3 の未解決不整合が増えていないか

### Step 3: ROADMAP.md を更新し、次のアクションを提示する

1. `ROADMAP.md` を最新状態に更新する
   - 完了タスクを ✅ に変更
   - 実装済み機能サマリーに新機能を追記
   - 技術的負債セクションを更新
   - 優先開発計画の次フェーズを整理
2. ユーザーに現在のロードマップ状況を提示し、次に取り組む候補を提案する

---

## Cloudflare Tunnel（外部アクセス）

このシステムは常時 Cloudflare Tunnel 経由で外部公開されている。

| ドメイン | モジュール | ポート |
|----------|-----------|--------|
| app.tsp-aitrademanagement.com | platform-core (portal) | 8000 |
| validation.tsp-aitrademanagement.com | ai_validation | 8011 |
| classification.tsp-aitrademanagement.com | ai_classification | 8002 |
| rnd.tsp-aitrademanagement.com | rnd_assessment | 8003 |
| patent.tsp-aitrademanagement.com | patent_search | 8004 |
| screening.tsp-aitrademanagement.com | screening | 8005 |
| hs.tsp-aitrademanagement.com | hs_classifier | 8006 |
| dap.tsp-aitrademanagement.com | dap | 8010 |

### 環境変数（.env）

- `MODULE_*_URL` — サーバー間通信用（localhost）
- `MODULE_*_PUBLIC_URL` — ブラウザ向けリンク用（Cloudflare Tunnel ドメイン）
  - `MODULE_AI_VALIDATION_PUBLIC_URL=https://validation.tsp-aitrademanagement.com`
  - `MODULE_PLATFORM_PUBLIC_URL=https://app.tsp-aitrademanagement.com`
  - その他モジュールも同様に `MODULE_<NAME>_PUBLIC_URL` で設定

### 起動・確認コマンド

```bash
./start.sh                    # アプリ + トンネルを同時起動
./start.sh --tunnel-status    # トンネル状態確認
./start.sh --restart-tunnel   # トンネルのみ再起動
./start.sh --stop             # アプリ + トンネルを停止

# 手動ログ確認
tail -f /tmp/cloudflared.log
```

### HTTP エラー発生時の確認手順

**必ずトンネル状態も確認すること。**

```bash
# Step 1: アプリが動いているか
curl -s http://localhost:8000/health

# Step 2: トンネルが動いているか
./start.sh --tunnel-status
# または
pgrep -f "cloudflared tunnel run" && echo "UP" || echo "DOWN"

# Step 3: トンネルが停止していれば再起動
./start.sh --restart-tunnel
```

トンネルエラーの主な原因:
- Cloudflare 認証の期限切れ（`cloudflared tunnel login` で再認証）
- ネットワーク変更後の接続切断（`--restart-tunnel` で復旧）
- プロセスクラッシュ（`/tmp/cloudflared.log` でエラー確認）

---

## コーディング規約

- 過剰な抽象化・将来の拡張のためのコードは書かない
- エラーハンドリングは外部 API・DB 境界のみ
- 新機能は既存ファイルへの追記を優先（新ファイル作成は最小限）
- コミットメッセージは日本語 + `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`
