# AI_TradeManagement — FastAPI 実装開始 指示書
# Claude Code 初回起動時にこのファイルを読み込ませてください

## プロジェクト概要
外為法・EAR（EAR/ECCN）に基づく輸出管理 該非判定 AI プラットフォーム。
FastAPI + FAISS + Claude API による判定エンジンを構築する。

## リポジトリ構造（実装前）
```
AI_TradeManagement/
├── data/staging/                      ← 収集済みデータ（変更不要）
│   ├── ccl_eccn_entries_v8.json       (637件, items_controlled 100%充足)
│   ├── ipc_eccn_mapping.json          (640件, ECCN↔IPC対応)
│   ├── fefta_law_v5.json              (2,285件)
│   ├── hs_all_merged.json             (5,476件, v5.1)
│   ├── hs_fefta_mapping.json          (36件)
│   ├── patents_chunks.json            (1,595件, US特許)
│   ├── layer_a.index                  (2,040 vectors, 8.0 MB)
│   ├── layer_a_meta.json
│   ├── layer_b.index                  (1,595 vectors, 6.2 MB)
│   └── layer_b_meta.json
└── app/                               ← これから実装（現時点では空）
```

## データセット詳細

| ファイル | 件数 | 内容 |
|---------|------|------|
| `ccl_eccn_entries_v8.json` | 637件 | BIS CCL 全エントリ。`entries[]` キー。`items_controlled` 100%充足 |
| `ipc_eccn_mapping.json` | 640件 | IPC↔ECCN対応。`mappings[]` + `ipc_to_eccn_index{}` |
| `fefta_law_v5.json` | 2,285件 | 外為法条文・通達・parameter・entity_list。`records[]` キー |
| `hs_all_merged.json` | 5,476件 | HSコード6桁 Ch25-97。`records[]` キー |
| `hs_fefta_mapping.json` | 36件 | HS→外為法項番対応。`records[]` キー |
| `patents_chunks.json` | 1,595件 | US特許チャンク。`records[]` キー |
| `layer_a.index` + `layer_a_meta.json` | 2,040vec | 外為法+ECCN FAISS インデックス |
| `layer_b.index` + `layer_b_meta.json` | 1,595vec | 特許エビデンス FAISS インデックス |

## FAISSインデックス仕様
- モデル: intfloat/multilingual-e5-large (dim=1024)
- インデックス型: IndexFlatIP（内積 = コサイン類似度）
- クエリ時プレフィックス: "query: {検索文}"
- 登録時プレフィックス:  "passage: {テキスト}"
- Layer A self_recall: 19/20、second_score_mean: 0.9518
- Layer B self_recall: 20/20、second_score_mean: 0.8806
- スコア閾値目安: Layer A=0.80〜0.85、Layer B=0.75〜0.80

## フェーズ2 実装タスク（優先順）

### Step 1: app/ ディレクトリ構造とFAISSサービス
```
app/
├── main.py
├── core/
│   └── config.py
├── services/
│   ├── faiss_service.py   ← Layer A + B 統合検索
│   ├── hs_service.py      ← HSコード → 外為法項番変換
│   └── ipc_eccn_service.py ← IPC ↔ ECCN 変換
├── api/
│   └── classify.py        ← /api/v1/classify エンドポイント
└── models/
    └── schemas.py         ← Pydantic モデル
```

### Step 2: /api/v1/classify エンドポイント

```python
# リクエスト
POST /api/v1/classify
{
  "query": "フォトレジスト EUV 露光装置",  # 製品説明（日英どちらも可）
  "hs_code": "3707.90",                    # オプション: HSコード
  "ipc_code": "G03F7/00"                   # オプション: IPCコード
}

# レスポンス
{
  "query": "フォトレジスト EUV 露光装置",
  "top_items": ["7", "6"],                 # 候補外為法項番
  "confidence": 0.89,
  "law_evidence": [                        # Layer A 検索結果
    {
      "score": 0.913,
      "source_type": "fefta_law",
      "item_no": "7",
      "item_label": "電子部品・集積回路",
      "text": "貨物等省令第6条..."
    }
  ],
  "patent_hits": [                         # Layer B 検索結果
    {
      "score": 0.867,
      "publication_number": "US-9184360-B2",
      "ipc_codes": "G03F7/00,H01L21/02",
      "title": "Photoresist composition for EUV lithography",
      "fefta_items": ["7"]
    }
  ],
  "eccn_hints": ["3B001", "3A001"],        # IPC→ECCN変換結果
  "hs_fefta": {                            # HS→外為法変換（hs_code指定時）
    "hs_code": "370790",
    "description": "Photographic plates...",
    "fefta_items": ["7"]
  },
  "claude_summary": "...(Claude Sonnetによる最終判定コメント)..."
}
```

### Step 3: Claude Sonnet 統合

レイヤーA/B の検索結果を Claude Sonnet に渡して最終判定コメントを生成。
- モデル: claude-sonnet-4-20250514
- 法的解釈・根拠文書の要約・リスク評価

### Step 4: その他エンドポイント
- GET /api/v1/hs/{hs_code}  → HSコード情報取得
- GET /api/v1/health        → ヘルスチェック

## 依存関係
```
fastapi
uvicorn
faiss-cpu  # GPU環境では faiss-gpu
sentence-transformers
anthropic
pydantic
python-dotenv
```

## 環境変数（.env）
```
ANTHROPIC_API_KEY=...
MODEL_NAME=intfloat/multilingual-e5-large
DATA_DIR=./data/staging
ECCN_JSON=./data/staging/ccl_eccn_entries_v8.json
IPC_ECCN_JSON=./data/staging/ipc_eccn_mapping.json
FEFTA_JSON=./data/staging/fefta_law_v5.json
HS_JSON=./data/staging/hs_all_merged.json
HS_FEFTA_JSON=./data/staging/hs_fefta_mapping.json
PATENTS_JSON=./data/staging/patents_chunks.json
FAISS_LAYER_A_INDEX=./data/staging/layer_a.index
FAISS_LAYER_A_META=./data/staging/layer_a_meta.json
FAISS_LAYER_B_INDEX=./data/staging/layer_b.index
FAISS_LAYER_B_META=./data/staging/layer_b_meta.json
SCORE_THRESHOLD_A=0.80
SCORE_THRESHOLD_B=0.75
```

## 注意事項
- FAISSインデックスは起動時に1回だけロード（グローバルシングルトン）
- sentence-transformers モデルも起動時に1回ロード
- Claude API 呼び出しは /classify のみ（コスト管理）
- HSコードの6桁→4桁→2桁フォールバック検索を実装すること
- fefta_law_v5.json の `records[]` の `source_type` は law / tsutatsu / entity_list / parameter の4種
- ccl_eccn_entries_v8.json のトップキーは `entries[]`（`records[]` ではない）
