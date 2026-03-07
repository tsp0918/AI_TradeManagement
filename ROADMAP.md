# 開発ロードマップ & 現状整理
# AI_TradeManagement — 2026-03-08 時点

> 本ドキュメントは `ip_export_control_econ_security_reference.md` に定義された設計思想との対照として、
> 現在の実装状況・不整合・優先度付き開発計画をまとめたものです。

---

## 1. 現状スナップショット

### 1-1. モジュール構成と安定性

| モジュール | ポート | DB | NullPool+WAL | 安定性 | 備考 |
|-----------|--------|-----|-------------|--------|------|
| platform-core | 8000 | PostgreSQL | — | ✅ | 知識グラフ管理 |
| ai_validation | 8001 | SQLite | **✅ 適用済** | ✅ | pipeline 正常動作確認済 |
| ai_classification | 8002 | SQLite | **✅ 適用済** | ✅ | |
| rnd_assessment | 8003 | SQLite | **✅ 適用済** | ✅ | |
| patent_search | 8004 | SQLite(async) | **❌ 未適用** | ⚠️ | WAL未設定, locked リスク残存 |
| screening | 8005 | PostgreSQL(async) | — | ✅ | |
| hs_classifier | 8006 | — | — | ✅ | FAISS のみ |
| dap | 8010 | SQLite | **❌ 未適用** | ⚠️ | NullPool/WAL なし, locked リスク |

**即時対応が必要:** `dap` と `patent_search` の SQLite が旧来のデフォルトプールのまま。
チャット利用が増えると DAP の `dap.db` が locked エラーを起こす。

---

### 1-2. 知識グラフ（control_nodes.json）の現状

**ビルド結果（2026-03-02 時点）: 788 ノード**

| regime | ノード数 | 補強状況 | 品質評価 |
|--------|---------|---------|---------|
| 外為法（fefta） | 191 | 168/191 XML補強済 | **◎ 高品質** |
| EAR/ECCN（ear） | 84 | 13/84 PDF補強のみ | **△ 71件がスタブ** |
| Wassenaar（wa） | 165 | PDF解析 | **○ 中品質** |
| HS コード（hs） | 281 | 5,613件から近似抽出 | **△ 公式対照表なし** |
| 特許（patent） | 67 | スタブのみ | **× 形式的な存在** |

**最大の問題:** ECCN 84件のうち 71件が中身のないスタブ。
AI 判定パイプラインの matrix_match ステップの FAISS 精度に直結する。

---

### 1-3. データソース対応状況

| データ | 入手状況 | 形式 | 課題 |
|--------|---------|------|------|
| 外為法・輸出貿易管理令 | ✅ XML取得済 | 構造化XML | 告示別表（省令）が未取得 |
| ECCN/EAR (Part774) | ✅ PDF取得済 | PDF(非構造化) | pdfplumberで13件のみ抽出。残71件は手動or別手法必要 |
| Wassenaar Arrangement | ✅ PDF取得済 | PDF(非構造化) | テキスト抽出品質を要検証 |
| HS コード 2022 | ✅ JSON取得済 | 構造化JSON(5,613件) | 外為法との公式対照表が未入手 |
| 特許 (synthetic) | ✅ 7,001件DB | SQLite | J-PlatPatで実データ補完中 |
| 制裁リスト | ✅ JSON取得済 | 非公式データセット | 更新頻度・出典不明。OFAC/METI公式データに切替要 |
| みなし輸出・省令 | ❌ 未取得 | PDF想定 | 外為法施行規則・経済産業省令が必要 |
| 中国輸出管理法リスト | ❌ 未取得 | — | Section 2.4 の観点で将来必要 |

---

### 1-4. 設計思想との不整合ポイント

**高-level vision（reference.md）vs 現実装のギャップ：**

| 思想上の要件 | 現在の実装状況 | ギャップ規模 |
|------------|-------------|------------|
| 技術ライフサイクル追跡（R&D→特許→製品→輸出） | R&D→品目→AI判定の3段連鎖は実装済 | **小** (特許→JV段階が未接続) |
| 4象限マッピング (主権価値×規制感度) | 未実装。個別判定のみ | **大** |
| 3シナリオストレステスト | 未実装 | **大** |
| クロスリファレンス (IPC↔ECCN↔HS) | 部分実装 (HS←→ECCN近似) | **中** |
| C-Levelナラティブ出力 | 判定根拠テキストあり。チャットで補完 | **中** |
| みなし輸出の人物スクリーニング | 未実装 | **大** |
| 特許所有者の制裁リスト照合 | 未実装 | **中** |
| Adversary adaptation モデリング | 未実装 | **大** (長期研究項目) |
| 動的モニタリング (規制更新追跡) | 未実装 | **大** |

---

## 2. 緊急対応（明日から: Phase 0）

**基盤安定化 — 開発を続ける前提条件**

### P0-1: DAP の SQLite NullPool+WAL 対応 【最優先】

```
対象: modules/dap/app/db.py
内容: NullPool + WAL pragma 追加（ai_validation と同一パターン）
理由: チャットウィジェット利用が増えると dap.db が locked エラーになる
工数: 30分
```

### P0-2: patent_search の SQLite WAL 対応

```
対象: modules/patent_search/app/database.py
内容: async engine への WAL pragma 追加（async版は event listener ではなくconnect_args or 起動後PRAGMA）
工数: 1時間
```

### P0-3: パイプライン実行の長時間ブロック問題の解消

```
問題: step_patent_retrieve.py が FAISS ビルド時に DB セッションを保持したまま
      7,001件エンコードで30分超のセッション保持 → 外部からの書き込みがブロックされる
解決策: FAISS ビルドをセッション外で実行（read→close→encode→reopen→write）
工数: 2時間
```

### P0-4: 起動スクリプト整合性確認

```
対象: start.sh
確認: 全モジュールが正しいポートで起動するか、PYTHONPATH が正しいか
     DAP ポートが 8010 か 8011 か（現在 8011 で起動していた）
工数: 1時間
```

---

## 3. 短期ロードマップ（Phase 1: 〜2週間）

### P1-1: ECCN ノード補強【データ品質向上】

```
現状: 84件中 71件がスタブ（label のみ、内容なし）
目標: 71件に requirement_text・parameters・説明文を補完
手法:
  A) BIS 公式 XML フィード（regulations.gov / BIS data feeds）の取得を試みる
  B) ECCN PDF を章ごとに分割して pdfplumber で再処理
  C) Claude API でバッチ補完（構造化出力）
効果: matrix_match FAISS の精度向上（現在スタブ71件は検索にほぼ寄与しない）
工数: 2〜3日
```

### P1-2: 外為法省令（告示別表）の取得・追加

```
現状: 輸出貿易管理令XML は取得済だが、経済産業省令（省令第49号等）の
      詳細技術パラメータが未取得
目標: 外為法リスト項の技術パラメータ（数値要件）を control_nodes に追加
手法: e-Gov からPDF/XMLで取得 → 構造化
効果: AI判定の「なぜ規制対象か」の根拠精度が向上
工数: 2〜3日
```

### P1-3: 制裁リストの公式データソース切替

```
現状: sanctions_screening_dataset.json（出典・更新日不明）
目標: OFAC SDN リスト (XML公開) + METI End-User List への切替
手法:
  - OFAC: https://ofac.treasury.gov/system/files/sanctions/SDN.XML (公開)
  - METI: EUL CSV/PDF（月次更新）
  - 定期更新スクリプト作成
効果: スクリーニングの信頼性・監査証跡の確保
工数: 2日
```

### P1-4: J-PlatPat → 知識グラフ同期

```
現状: ai_validation DB に 7,001件の特許。control_nodes の patent ノードは 67件のスタブ
問題: patent_search で取得した実特許データが知識グラフに反映されていない
目標: J-PlatPatで取得した特許（IPC コード付き）を control_nodes に反映し
      IPC ↔ 外為法項 のエッジを自動生成
工数: 2〜3日
```

### P1-5: DAP チャットウィジェット — システムプロンプト強化

```
現状: 業務フロー全体像は記述済み。規制の具体的内容は未組み込み
目標: ip_export_control_econ_security_reference.md の規制情報をシステムプロンプトに
      コンパクトに組み込み（外為法体系、みなし輸出、セキュリティクリアランス等）
効果: Claudeがより正確な規制アドバイスを提供できる
工数: 1日
```

---

## 4. 中期ロードマップ（Phase 2: 〜2ヶ月）

### P2-1: 4象限マッピング UI 【戦略機能の核心】

```
概要: 技術主権価値（Y軸）× 規制感度（X軸）の2次元マップ
実装:
  - ai_classification の Product に sovereignty_score, regulation_score フィールド追加
  - AI判定結果から regulation_score を自動算出（ECCN リスト命中度、外為法該当度）
  - 品目管理の一覧画面に散布図ビュー追加（Chart.js or Plotly）
  - ユーザーが sovereignty_score を入力（将来は自動推定）
効果: 「要塞技術」「無防備な至宝」等の戦略分類が視覚化される
```

### P2-2: HS コード ↔ 外為法 公式対照表の整備

```
問題: 現在は近似ルールベースマッピング。公式の「輸出令別表第一の関係HS番号一覧表」
      （METI）が入手困難
方針:
  A) METI/CISTEC への問い合わせ・購入検討
  B) 暫定: CISTEC 公開の品目分類ガイドから対照表を手作成
  C) 長期: HS コードと IPC コードの相関から推論モデル構築
```

### P2-3: みなし輸出スクリーニング（人物管理）

```
概要: reference.md Section 2.1「みなし輸出の3カテゴリ」対応
実装:
  - 研究者/従業員テーブル（国籍、居住年数、所属、二重雇用フラグ）
  - 「この技術を提供する人物」チェック → みなし輸出該当性判定
  - rnd_assessment の需要者要件欄と連携
```

### P2-4: 規制動向モニタリング

```
概要: Wassenaar 年次改正、BIS 中間規則、外為法改正の自動検知
実装:
  - 各規制機関の更新フィードをポーリング（月次）
  - control_nodes の差分検出 → 管理者通知
  - DAP で「規制更新あり」バナーを表示
```

### P2-5: 輸出判定根拠のPDF出力強化

```
現状: 判定結果はUI表示のみ
目標: 監査証跡として提出可能な判定報告書PDF
内容: 品目情報、判定フロー、該当条文引用、スクリーニング結果、担当者署名欄
```

---

## 5. データソース開発方針

### 外為法（FEFTA）

```
優先度: ★★★★★
現状: 輸出貿易管理令 XML 取得済（191ノード、168補強）
Next:
  1. 経済産業省令（貨物等省令）の取得: e-Gov API or PDF
     → 技術パラメータ（数値閾値）の構造化
  2. 外国為替令（役務取引）の取得
     → みなし輸出関連条文の追加
  3. 経済安保推進法（特許非公開制度）の条文追加
品質目標: 全191ノードにrequirement_text+parameters を充填
```

### ECCN/EAR（米国輸出管理）

```
優先度: ★★★★☆
現状: PDF のみ、84ノード中71がスタブ
Next:
  1. BIS 公式 XML/JSONフィード調査（regulations.gov API）
  2. pdfplumber の処理精度改善（章構造を正しく認識させる）
  3. 不足71件を Claude API バッチ補完（Part774 テキストを入力）
品質目標: 84ノード全て requirement_text 充填
注意: EAR は定期改正（Oct 2022, Oct 2023等）。改正追跡の仕組みが必要
```

### HS コード

```
優先度: ★★★☆☆
現状: hs2022_6digit.json（5,613件）→ control_nodes に281件を近似マッピング
Next:
  1. METI「輸出令別表第一の関係HS番号一覧表」の入手試行
     （CISTEC会員向け資料。問い合わせ要）
  2. 暫定: 外為法 XML の品目説明とHS説明のセマンティックマッチングで対照表生成
  3. WCO HS 2022 改正（2022年施行）との整合性確認
品質目標: 外為法項とHSコードの対応を信頼できる形で整備
```

### 特許

```
優先度: ★★★★☆
現状: synthetic 7,001件 + J-PlatPat取得実特許（少数）
     control_nodes の patent ノード: 67件（スタブ）
Next:
  1. J-PlatPat からのバッチ取得強化（半導体・先端材料・AI分野）
     → IPC コード付き実特許 → control_nodes に反映
  2. Google Patents API or Lens.org API での補完検討
  3. IPC サブクラス ↔ 外為法項 のマッピングテーブル作成
     （例: IPC H01L → 外為法 EL-7 半導体関連）
  4. 特許出願人の制裁リスト照合機能
品質目標: 特許 → 規制リスト のセマンティックブリッジを確立
```

### 制裁リスト（Screening）

```
優先度: ★★★★★
現状: 非公式 JSON（出典・更新日不明）
Next:
  1. OFAC SDN/Consolidated List: 公式XML取得 (ofac.treasury.gov)
  2. BIS Entity List: 公式CSV取得 (bis.doc.gov)
  3. METI 外国ユーザーリスト: PDF解析または手動メンテ
  4. EU Consolidated Sanctions List: 公式データ取得
  5. 月次自動更新スクリプト作成
品質目標: 公式データソースへの完全切替 + 自動更新
```

---

## 6. 開発優先度マトリクス

```
【緊急×重要】 ← 明日から取り組む
  P0-1: DAP NullPool+WAL
  P0-2: patent_search WAL
  P0-3: patent_retrieve セッションブロック解消
  P0-4: start.sh ポート整合性確認
  P1-3: 制裁リスト公式化

【重要・計画的に進める】
  P1-1: ECCN ノード補強（判定精度直結）
  P1-2: 外為法省令追加（判定根拠強化）
  P1-4: J-PlatPat → 知識グラフ同期
  P1-5: DAP システムプロンプト強化

【戦略的・中期】
  P2-1: 4象限マッピング UI（差別化機能）
  P2-2: HS ↔ 外為法 公式対照表
  P2-3: みなし輸出人物スクリーニング
  P2-4: 規制動向モニタリング
  P2-5: 判定報告書 PDF 出力
```

---

## 7. システム全体稼働チェックリスト（毎回起動時）

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

# DB ロック確認（起動直後に実行）
lsof | grep ".db$" | grep -v ".venv"
```

---

## 8. 技術的負債・既知の問題

| 問題 | 影響範囲 | 根本原因 | 解決優先度 |
|------|---------|---------|-----------|
| DAP SQLite NullPool 未適用 | DAP全体 | db.py が旧実装 | **即時** |
| patent_search WAL 未設定 | 特許検索 | async engine に pragma なし | 今週中 |
| patent_retrieve セッション長時間保持 | ai_validation pipeline | FAISS build中にDBセッション開放しない | 今週中 |
| ECCN 71件スタブ | AI判定精度 | PDF解析の限界 | 1週間以内 |
| 制裁リスト非公式ソース | screening信頼性 | 公式フィード未整備 | 1週間以内 |
| HS↔外為法対照が近似 | HS分類精度 | 公式対照表未入手 | 2週間以内 |
| DAP ポート 8011 で起動 | 設定不整合 | run.sh の設定 | 今週中 |
| 特許→知識グラフ未同期 | 知識グラフ品質 | J-PlatPatデータの反映なし | 2週間以内 |
| みなし輸出チェック欠如 | コンプライアンス | 未実装 | 中期 |

---

*作成: 2026-03-08*
*担当: Takehiro Sato + Claude Sonnet 4.6*
