"""
expand_fterm_mapping.py
========================
fterm_eccn_mapping.json の F-term → ECCN マッピングを拡充する。

現在: 130件（主に Category 3/4/5 エレクトロニクス系）
目標: 250+ 件（全 Category 0-9 をカバー）

追加対象カテゴリ:
  Cat 0: 核・放射線      (0A001/0B001/0C001/0E001)
  Cat 1: 特殊材料・化学  (1C010/1C111/1C350/1C351/1A001)
  Cat 2: 製造設備        (2B001/2B350/2D001)
  Cat 6: センサー・レーザ (6A001/6A002/6A003/6A008/6A005)
  Cat 7: 航法・航空電子  (7A001/7A002/7A003/7A006)
  Cat 8: 海上機器        (8A001/8A002)
  Cat 9: 宇宙・推進      (9A004/9A012/9E101)
"""
from __future__ import annotations

import json
from pathlib import Path

_ROOT   = Path(__file__).resolve().parents[1]
_OUT    = _ROOT / "data" / "staging" / "fterm_eccn_mapping.json"

# ── 追加エントリ（F-term → [{eccn, confidence, rationale, fefta}]）─────────

NEW_ENTRIES: dict[str, list[dict]] = {

    # ── Category 0: 核・放射線（外為法 第14項/第2項）───────────────────────
    "G21C": [
        {"eccn": "0A001", "fefta": "14項", "confidence": "high",
         "rationale": "核反応炉・炉心構成部品。臨界アセンブリ・試験炉含む"},
        {"eccn": "0B001", "fefta": "14項", "confidence": "high",
         "rationale": "核燃料製造設備（ウラン濃縮・再処理装置）"},
    ],
    "G21F": [
        {"eccn": "0A001", "fefta": "14項", "confidence": "high",
         "rationale": "放射線防護材料・遮蔽材料"},
        {"eccn": "0C001", "fefta": "14項", "confidence": "high",
         "rationale": "放射性物質（天然ウラン・トリウム・同位体）"},
    ],
    "G21G": [
        {"eccn": "0C001", "fefta": "14項", "confidence": "high",
         "rationale": "放射性同位体の変換・製造（同位体分離・照射）"},
        {"eccn": "0C002", "fefta": "14項", "confidence": "medium",
         "rationale": "特殊核分裂性物質（Pu-238除く）"},
    ],
    "G21H": [
        {"eccn": "0A001", "fefta": "14項", "confidence": "medium",
         "rationale": "放射線の利用（医療・工業用放射線装置含む）"},
    ],
    "G21B": [
        {"eccn": "0E001", "fefta": "14項", "confidence": "high",
         "rationale": "核融合炉技術。0E001: 核融合炉開発・生産技術"},
    ],
    "C22B": [
        {"eccn": "0C001", "fefta": "14項", "confidence": "medium",
         "rationale": "ウラン・トリウムの製錬・精製。同位体分離装置対象"},
    ],

    # ── Category 1: 特殊材料・化学・生物（外為法 第1項/第12項/第13項）────────
    "C06B": [
        {"eccn": "1C111", "fefta": "15項", "confidence": "high",
         "rationale": "爆発物・推進薬。ミサイル推進剤（HTPB/AP/RDX等）は1C111対象"},
        {"eccn": "1A007", "fefta": "15項", "confidence": "medium",
         "rationale": "爆発物取扱設備（起爆装置・点火装置）"},
    ],
    "C06C": [
        {"eccn": "1C111", "fefta": "15項", "confidence": "high",
         "rationale": "火工品（信管・導火線・スクイブ）。1C111.a 推進薬関連"},
    ],
    "C07C": [
        {"eccn": "1C350", "fefta": "12項", "confidence": "high",
         "rationale": "有機化合物。化学兵器前駆物質（TDI/PFIB等）1C350対象"},
        {"eccn": "1C111", "fefta": "12項", "confidence": "medium",
         "rationale": "有機過酸化物・エネルギー材料"},
    ],
    "C07F": [
        {"eccn": "1C350", "fefta": "12項", "confidence": "high",
         "rationale": "有機リン化合物（サリン・VX前駆体含む）。1C350 Schedule 2/3"},
    ],
    "C01B": [
        {"eccn": "1C350", "fefta": "12項", "confidence": "medium",
         "rationale": "無機化合物（塩素・フッ素・ホスゲン前駆体含む）"},
    ],
    "D01F": [
        {"eccn": "1C010", "fefta": "11項", "confidence": "high",
         "rationale": "炭素繊維・アラミド繊維。比強度が規制閾値（1C010.a/b）"},
        {"eccn": "1C210", "fefta": "11項", "confidence": "medium",
         "rationale": "炭素繊維複合材（輸出令別表 11項: 先進複合材）"},
    ],
    "D01D": [
        {"eccn": "1C010", "fefta": "11項", "confidence": "medium",
         "rationale": "繊維の製造（溶融紡糸・ドライジェット湿式）"},
    ],
    "C30B": [
        {"eccn": "1C003", "fefta": "14項", "confidence": "high",
         "rationale": "単結晶育成（GaAs/InP/SiC基板）。半導体・核技術用"}
    ],
    "B32B": [
        {"eccn": "1A002", "fefta": "11項", "confidence": "medium",
         "rationale": "複合材積層板・サンドイッチ構造（繊維強化プラスチック）"},
    ],
    "C12N": [
        {"eccn": "1C351", "fefta": "13項", "confidence": "high",
         "rationale": "微生物・遺伝子工学。生物剤（BSL-3/4病原体）は1C351対象"},
        {"eccn": "1C352", "fefta": "13項", "confidence": "medium",
         "rationale": "動物由来病原体（口蹄疫ウイルス等）"},
    ],
    "C12Q": [
        {"eccn": "1C352", "fefta": "13項", "confidence": "medium",
         "rationale": "核酸検出・診断（生物剤検知技術）"},
    ],

    # ── Category 2: 製造設備・工作機械（外為法 第10項）───────────────────────
    "B23Q": [
        {"eccn": "2B001", "fefta": "10項", "confidence": "high",
         "rationale": "工作機械の位置決め・制御装置。数値制御精度が規制閾値（2B001.b.1: ±0.9μm）"},
    ],
    "B24B": [
        {"eccn": "2B001", "fefta": "10項", "confidence": "high",
         "rationale": "研削盤・研磨機。非球面研削精度（2B001.b.1）対象"},
    ],
    "B23P": [
        {"eccn": "2B001", "fefta": "10項", "confidence": "medium",
         "rationale": "金属加工機械（旋盤・フライス）。精度規制値に注意"},
    ],
    "B01D": [
        {"eccn": "2B350", "fefta": "12項", "confidence": "high",
         "rationale": "分離装置（蒸留・吸収・遠心分離）。デュアルユース化学製造設備"},
        {"eccn": "2B001", "fefta": "10項", "confidence": "low",
         "rationale": "精密分離・濾過装置（加工精度規制との関連）"},
    ],
    "B01J": [
        {"eccn": "2B350", "fefta": "12項", "confidence": "high",
         "rationale": "化学反応器・触媒プロセス。化学兵器前駆体製造設備（2B350.a）"},
    ],
    "B22F": [
        {"eccn": "2B004", "fefta": "14項", "confidence": "high",
         "rationale": "等方圧加圧装置（HIP）。核・ミサイル構造材製造（2B004: 圧力閾値 69MPa以上）"},
    ],
    "C21D": [
        {"eccn": "2B004", "fefta": "10項", "confidence": "medium",
         "rationale": "金属熱処理炉（真空炉・雰囲気炉）。精密部品焼結・焼入れ"},
    ],

    # ── Category 6: センサー・レーザー（外為法 第6項）──────────────────────
    "G01S": [
        {"eccn": "6A001", "fefta": "6項", "confidence": "high",
         "rationale": "ソナー・レーダー・LIDAR。水中音響システム（6A001.a: 周波数・深度規制）"},
        {"eccn": "6A008", "fefta": "6項", "confidence": "high",
         "rationale": "レーダーシステム（6A008: 解像度・反射断面積規制）"},
        {"eccn": "7A003", "fefta": "7項", "confidence": "medium",
         "rationale": "航法用レーダー（7A003.a: 精度規制）"},
    ],
    "H01S": [
        {"eccn": "6A005", "fefta": "6項", "confidence": "high",
         "rationale": "レーザー装置。パルスレーザー（6A005.a: 出力・波長規制）"},
        {"eccn": "6A205", "fefta": "6項", "confidence": "medium",
         "rationale": "半導体レーザー（規制閾値未満のEAR管理品目）"},
    ],
    "G01J": [
        {"eccn": "6A002", "fefta": "6項", "confidence": "high",
         "rationale": "光学センサー・光電変換（赤外線検出器 InGaAs/HgCdTe: 6A002.a.1）"},
    ],
    "G02B": [
        {"eccn": "6A004", "fefta": "6項", "confidence": "high",
         "rationale": "光学部品（望遠鏡・レンズ系）。軍用光学スコープ（6A004.a）"},
        {"eccn": "6A002", "fefta": "6項", "confidence": "medium",
         "rationale": "光学系（夜視装置・赤外線カメラの光学系）"},
    ],
    "G01N": [
        {"eccn": "6C002", "fefta": "6項", "confidence": "medium",
         "rationale": "材料分析・検査（蛍光分析・質量分析装置）"},
        {"eccn": "1C350", "fefta": "12項", "confidence": "low",
         "rationale": "化学分析（化学兵器前駆体検知・分析装置）"},
    ],
    "H04N": [
        {"eccn": "6A003", "fefta": "6項", "confidence": "high",
         "rationale": "撮像カメラ（赤外線・夜視カメラ: 6A003.b 冷却型検出器）"},
        {"eccn": "6A002", "fefta": "6項", "confidence": "medium",
         "rationale": "イメージセンサー・CCDカメラ（宇宙用高放射線耐性含む）"},
    ],
    "G02F": [
        {"eccn": "6A002", "fefta": "6項", "confidence": "medium",
         "rationale": "電気光学デバイス（光変調器・Q-switch）"},
    ],

    # ── Category 7: 航法・航空電子機器（外為法 第7項）──────────────────────
    "G01C": [
        {"eccn": "7A001", "fefta": "7項", "confidence": "high",
         "rationale": "加速度計・慣性センサー（7A001: 閾値 0.05 mg）。INS部品対象"},
        {"eccn": "7A002", "fefta": "7項", "confidence": "high",
         "rationale": "ジャイロスコープ（7A002: 角速度バイアス安定性 0.5 deg/h以下）"},
        {"eccn": "7A003", "fefta": "7項", "confidence": "high",
         "rationale": "慣性航法システム（INS）統合（7A003.a: 位置精度規制）"},
    ],
    "G01P": [
        {"eccn": "7A001", "fefta": "7項", "confidence": "high",
         "rationale": "加速度・速度測定（高精度加速度計: 7A001.a, .b）"},
    ],
    "G01V": [
        {"eccn": "7A006", "fefta": "7項", "confidence": "high",
         "rationale": "重力計・重力勾配計（7A006: 地形追従・潜水艦航法用）"},
    ],
    "G05D": [
        {"eccn": "7A003", "fefta": "7項", "confidence": "high",
         "rationale": "自動制御システム（誘導装置・オートパイロット）"},
        {"eccn": "9A012", "fefta": "8項", "confidence": "medium",
         "rationale": "無人機（UAV）オートパイロット・飛行制御系"},
    ],
    "B64D": [
        {"eccn": "7A994", "fefta": "7項", "confidence": "medium",
         "rationale": "航空機搭載電子機器（民間規制管理品目）"},
        {"eccn": "9A004", "fefta": "8項", "confidence": "medium",
         "rationale": "宇宙機・衛星搭載機器"},
    ],

    # ── Category 8: 海上機器（外為法 第8項）────────────────────────────────
    "B63H": [
        {"eccn": "8A001", "fefta": "8項", "confidence": "high",
         "rationale": "船舶推進システム（水中翼・エア・クッション艇）。軍用低騒音設計"},
        {"eccn": "8A002", "fefta": "8項", "confidence": "high",
         "rationale": "海中機器（AUV・ROV推進装置）"},
    ],
    "B63B": [
        {"eccn": "8A001", "fefta": "8項", "confidence": "medium",
         "rationale": "船体設計（低磁気・消磁設計・ステルス船型）"},
    ],
    "B63G": [
        {"eccn": "8A002", "fefta": "8項", "confidence": "high",
         "rationale": "水中機器・機雷対策装置。8A002.a: 自律型海中ビークル"},
    ],

    # ── Category 9: 宇宙・推進（外為法 第8項/第9項）────────────────────────
    "B64G": [
        {"eccn": "9A004", "fefta": "8項", "confidence": "high",
         "rationale": "宇宙機・衛星・打上げ機（9A004.a: 機密性衛星。MTCR対象）"},
        {"eccn": "9A515", "fefta": "8項", "confidence": "medium",
         "rationale": "宇宙ビークル部品（EAR管理品目。ITAR Item 9A515）"},
    ],
    "F02K": [
        {"eccn": "9A004", "fefta": "9項", "confidence": "high",
         "rationale": "ロケットエンジン・ジェット推進（9A004: MTCR Category I 推進）"},
        {"eccn": "1C111", "fefta": "9項/15項", "confidence": "high",
         "rationale": "固体推進薬（AP/HMX/RDX系: 1C111.a.1）"},
    ],
    "B64C": [
        {"eccn": "9A012", "fefta": "8項", "confidence": "high",
         "rationale": "無人機・UAV（9A012: 航続距離/積載量/制御規制）"},
        {"eccn": "9A004", "fefta": "8項", "confidence": "medium",
         "rationale": "航空機設計（軍用ステルス・高機動設計）"},
    ],
    "F02C": [
        {"eccn": "9A004", "fefta": "9項", "confidence": "high",
         "rationale": "ガスタービンエンジン（航空機・ミサイル用タービン）"},
        {"eccn": "9E001", "fefta": "9項", "confidence": "medium",
         "rationale": "推進技術（9E001: ガスタービン開発・生産技術）"},
    ],
    "G01M": [
        {"eccn": "9B001", "fefta": "9項", "confidence": "medium",
         "rationale": "航空機・エンジン試験装置（風洞・エンジンテストベンチ）"},
    ],

    # ── Category 4/5 追加補完 ──────────────────────────────────────────────
    "G06N": [
        {"eccn": "4E001", "fefta": "4項", "confidence": "high",
         "rationale": "AI/機械学習技術（4E001: ニューラルネット・エキスパートシステム技術）"},
        {"eccn": "4A003", "fefta": "4項", "confidence": "medium",
         "rationale": "AI加速器・推論チップ（4A003: コンピュータ性能規制）"},
    ],
    "G06F": [
        {"eccn": "4A003", "fefta": "4項", "confidence": "medium",
         "rationale": "デジタルコンピュータ（4A003: APP規制対象）"},
        {"eccn": "4D001", "fefta": "4項", "confidence": "medium",
         "rationale": "組み込みソフトウェア・OS（セキュリティ強化OS含む）"},
    ],
    "H04L": [
        {"eccn": "5A002", "fefta": "5の2項", "confidence": "high",
         "rationale": "暗号通信（5A002.a: 鍵長・暗号アルゴリズム規制）"},
        {"eccn": "5E002", "fefta": "5の2項", "confidence": "high",
         "rationale": "暗号技術（5E002: 暗号設備の開発・生産技術）"},
    ],
    "H04W": [
        {"eccn": "5A001", "fefta": "5項", "confidence": "medium",
         "rationale": "無線通信システム（5G/軍用SDR: 5A001.b 周波数・帯域規制）"},
        {"eccn": "5A002", "fefta": "5の2項", "confidence": "medium",
         "rationale": "無線暗号通信（エンドツーエンド暗号を含む無線システム）"},
    ],
    "H04B": [
        {"eccn": "5A001", "fefta": "5項", "confidence": "high",
         "rationale": "通信機器（衛星通信・拡散スペクトラム: 5A001.b）"},
    ],
    "H01Q": [
        {"eccn": "6A008", "fefta": "6項", "confidence": "medium",
         "rationale": "アンテナ（フェーズドアレイ・合成開口レーダー用アンテナ）"},
        {"eccn": "5A001", "fefta": "5項", "confidence": "medium",
         "rationale": "通信用アンテナシステム（5A001.b.3: 相関干渉除去）"},
    ],
    "5J500": [
        {"eccn": "5A001", "fefta": "5項", "confidence": "high",
         "rationale": "電気通信総合（5A001: デジタル交換機・SDH/SONET）"},
    ],
    "5K001": [
        {"eccn": "5A001", "fefta": "5項", "confidence": "medium",
         "rationale": "データ通信（5A001: ネットワーク機器・ルーター規制）"},
    ],

    # ── 制御システム（横断的ECCN）───────────────────────────────────────────
    "G05B": [
        {"eccn": "2D001", "fefta": "10項", "confidence": "medium",
         "rationale": "数値制御（NC）システム（2D001: 工作機械制御ソフトウェア）"},
        {"eccn": "7D003", "fefta": "7項", "confidence": "medium",
         "rationale": "誘導・制御ソフトウェア（7D003: 飛翔体誘導制御）"},
    ],
    "G05F": [
        {"eccn": "3A002", "fefta": "7項", "confidence": "low",
         "rationale": "電力制御・電源装置（パルスパワー・高電圧装置含む）"},
    ],

    # ── 精密測定・試験（横断的）──────────────────────────────────────────────
    "G01B": [
        {"eccn": "2B001", "fefta": "10項", "confidence": "medium",
         "rationale": "精密測長・表面粗さ測定（工作機械精度確認装置）"},
    ],
    "G01R": [
        {"eccn": "3A002", "fefta": "7項", "confidence": "medium",
         "rationale": "電気測定装置（高周波ベクトルネットワークアナライザ・スペアナ）"},
    ],
    "G01K": [
        {"eccn": "2B004", "fefta": "14項", "confidence": "low",
         "rationale": "温度測定（高温炉・プラズマ温度計測）"},
    ],
    "G01L": [
        {"eccn": "2B004", "fefta": "10項", "confidence": "medium",
         "rationale": "圧力測定（高圧プレス・等方圧加圧装置の計測）"},
    ],

    # ── 半導体製造装置 Category 3B ────────────────────────────────────────
    "H01L021": [
        {"eccn": "3B001", "fefta": "7項", "confidence": "high",
         "rationale": "半導体製造装置（CVD/PVD/ALD/エピタキシャル成長装置: 3B001.a/b）"},
        {"eccn": "3B002", "fefta": "7項", "confidence": "high",
         "rationale": "マスク製造装置・フォトリソ機器（3B002: 解像度規制）"},
    ],
    "G03F": [
        {"eccn": "3B002", "fefta": "7項", "confidence": "high",
         "rationale": "フォトリソグラフィ（EUV/DUV露光装置: 3B002.g 解像度閾値）"},
    ],
    "H01J": [
        {"eccn": "3B001", "fefta": "7項", "confidence": "high",
         "rationale": "電子ビーム蒸着・イオンビーム装置（3B001.c: 電子ビーム成膜）"},
        {"eccn": "6A005", "fefta": "6項", "confidence": "medium",
         "rationale": "電子管・イオン管（マグネトロン・クライストロン）"},
    ],

    # ── 先進材料 Category 1/3C ────────────────────────────────────────────
    "C22C": [
        {"eccn": "1C002", "fefta": "11項", "confidence": "high",
         "rationale": "超合金（ニッケル/コバルト系: 1C002.b タービンブレード向け）"},
        {"eccn": "1C010", "fefta": "11項", "confidence": "medium",
         "rationale": "繊維強化金属複合材（MMC）"},
    ],
    "C04B": [
        {"eccn": "1C007", "fefta": "11項", "confidence": "high",
         "rationale": "セラミック・ガラス系複合材（SiC繊維/SiC基複合材: 1C007.c）"},
        {"eccn": "1A002", "fefta": "11項", "confidence": "medium",
         "rationale": "炭素/炭素複合材（再突入体・ブレーキ材: 1A002.b）"},
    ],
    "C23C": [
        {"eccn": "2E001", "fefta": "11項", "confidence": "medium",
         "rationale": "表面コーティング技術（CVD/PVD: 超硬コーティング・耐熱コーティング）"},
        {"eccn": "1C002", "fefta": "11項", "confidence": "medium",
         "rationale": "高温コーティング（TBC: タービンブレード遮熱コート）"},
    ],
    "H01M": [
        {"eccn": "3A001", "fefta": "7項", "confidence": "low",
         "rationale": "電池・燃料電池（高エネルギー密度蓄電池: 宇宙・軍用電源）"},
    ],

    # ── 量子・先端技術 ─────────────────────────────────────────────────────
    "G06N010": [
        {"eccn": "3E001", "fefta": "7項", "confidence": "medium",
         "rationale": "量子コンピュータ技術（3E001: 量子デバイス開発・生産技術）"},
        {"eccn": "5E002", "fefta": "5の2項", "confidence": "medium",
         "rationale": "量子暗号通信技術（QKD: 量子鍵配送システム）"},
    ],
    "H04B010": [
        {"eccn": "5A002", "fefta": "5の2項", "confidence": "medium",
         "rationale": "光通信・量子通信（QKDシステム: 5A002.a.1 暗号機器）"},
    ],
    "B82Y010": [
        {"eccn": "3C001", "fefta": "7項", "confidence": "medium",
         "rationale": "ナノ材料（炭素ナノチューブ・グラフェン: 半導体・複合材応用）"},
    ],
    "B82Y030": [
        {"eccn": "1C010", "fefta": "11項", "confidence": "medium",
         "rationale": "ナノ繊維・ナノチューブ複合材（1C010: 比強度規制対象の可能性）"},
    ],

    # ── ソフトウェア（EAR Category 4D/5D/7D）────────────────────────────────
    "G06F021": [
        {"eccn": "5D002", "fefta": "5の2項", "confidence": "high",
         "rationale": "情報セキュリティソフトウェア（5D002: 暗号ソフト・アクセス制御）"},
    ],
    "G06F013": [
        {"eccn": "4D001", "fefta": "4項", "confidence": "medium",
         "rationale": "バス制御・メモリ管理（高性能コンピュータのシステムソフト）"},
    ],
    "G06T": [
        {"eccn": "4A003", "fefta": "4項", "confidence": "medium",
         "rationale": "画像処理（AI認識: GPU/NPU搭載システム。4A003 APP規制対象）"},
    ],

    # ── 化学・バイオテクノロジー補完 ─────────────────────────────────────
    "C12M": [
        {"eccn": "2B352", "fefta": "13項", "confidence": "high",
         "rationale": "発酵・細胞培養装置（2B352.a: バイオリアクター。生物剤生産設備）"},
    ],
    "C12P": [
        {"eccn": "2B352", "fefta": "13項", "confidence": "high",
         "rationale": "バイオプロセス（2B352.b: 凍結乾燥装置・エアロゾル生成）"},
    ],
    "A61K": [
        {"eccn": "1C351", "fefta": "13項", "confidence": "medium",
         "rationale": "医薬品・生物学的製剤（生物剤: ワクチン原液・毒素含む）"},
    ],

    # ── 高エネルギー物理・磁石技術 ──────────────────────────────────────
    "H01F": [
        {"eccn": "1C003", "fefta": "3項", "confidence": "high",
         "rationale": "超伝導磁石（1C003.a: Nb3Sn/NbTi 超伝導線材）"},
        {"eccn": "3A001", "fefta": "7項", "confidence": "low",
         "rationale": "磁性部品（フェライトコア・磁気メモリ素子）"},
    ],
    "H02N": [
        {"eccn": "3A002", "fefta": "3項", "confidence": "medium",
         "rationale": "電磁石・圧電素子（精密アクチュエーター: レーダー走査機構等）"},
    ],

    # ── 推進・空力・熱システム追加 ─────────────────────────────────────────
    "F02B": [
        {"eccn": "9E001", "fefta": "9項", "confidence": "high",
         "rationale": "往復動エンジン（小型UAV用エンジン技術: 9E001推進技術）"},
    ],
    "F15D": [
        {"eccn": "9B001", "fefta": "9項", "confidence": "medium",
         "rationale": "流体力学試験（風洞設備・空力試験装置: 9B001.a）"},
    ],
    "F28F": [
        {"eccn": "9A004", "fefta": "9項", "confidence": "medium",
         "rationale": "熱管理システム（宇宙機・ミサイルの冷却構造）"},
    ],

    # ── 核・放射線追加 ─────────────────────────────────────────────────────
    "G21D": [
        {"eccn": "0A001", "fefta": "14項", "confidence": "high",
         "rationale": "核融合炉・プラズマ装置（ITER関連：0A001.f）"},
    ],
    "C01G": [
        {"eccn": "0C001", "fefta": "14項", "confidence": "high",
         "rationale": "核燃料物質含む無機化合物（UF6・UO2・硝酸ウラニル）"},
    ],

    # ── 暗号・セキュリティ追加 ─────────────────────────────────────────────
    "H04K": [
        {"eccn": "5A002", "fefta": "5の2項", "confidence": "high",
         "rationale": "妨害装置・秘匿通信（5A002.a: 軍用ジャマー・拡散スペクトル通信）"},
    ],
    "G09C": [
        {"eccn": "5E002", "fefta": "5の2項", "confidence": "high",
         "rationale": "暗号・スクランブル装置（5E002: 暗号設計・生産技術）"},
    ],

    # ── 先進製造・3D プリンティング ────────────────────────────────────────
    "B33Y": [
        {"eccn": "2B004", "fefta": "10項", "confidence": "high",
         "rationale": "付加製造（3Dプリンティング）。金属積層造形（DMLS/EBM）は2B004対象"},
        {"eccn": "1A002", "fefta": "11項", "confidence": "medium",
         "rationale": "複合材3Dプリンティング（CFRPアーム・構造部品）"},
    ],
    "B29C": [
        {"eccn": "1A002", "fefta": "11項", "confidence": "medium",
         "rationale": "高分子複合材成形（RTM/オートクレーブ成形: CFRP成形）"},
    ],

    # ── 生物・医療デュアルユース追加 ──────────────────────────────────────
    "C12G": [
        {"eccn": "2B352", "fefta": "13項", "confidence": "medium",
         "rationale": "発酵プロセス（アルコール発酵設備: 生物兵器転用の可能性）"},
    ],
    "A61P": [
        {"eccn": "1C351", "fefta": "13項", "confidence": "low",
         "rationale": "薬効分類（生物毒素を含む薬剤化合物の分類体系）"},
    ],

    # ── 空間技術・リモートセンシング ─────────────────────────────────────
    "G01W": [
        {"eccn": "6A008", "fefta": "6項", "confidence": "medium",
         "rationale": "環境計測（気象レーダー・合成開口レーダーとの技術的重複）"},
    ],
    "G09B": [
        {"eccn": "7A994", "fefta": "7項", "confidence": "low",
         "rationale": "シミュレーター・訓練装置（航空機・航法シミュレーター）"},
    ],

    # ── 光学・電気光学 追加 ───────────────────────────────────────────────
    "G02C": [
        {"eccn": "6A004", "fefta": "6項", "confidence": "medium",
         "rationale": "光学ゴーグル・眼鏡系（ナイトビジョン・戦術光学装置）"},
    ],
    "G11B": [
        {"eccn": "4A003", "fefta": "4項", "confidence": "low",
         "rationale": "光記録・磁気記録装置（高密度ストレージ: コンピュータ性能規制補完）"},
    ],
    "H05H": [
        {"eccn": "0B001", "fefta": "14項", "confidence": "medium",
         "rationale": "プラズマ装置（粒子加速器: ウラン濃縮・同位体分離への応用）"},
        {"eccn": "6A005", "fefta": "6項", "confidence": "medium",
         "rationale": "プラズマレーザー（高エネルギーレーザー: 6A005.d Xレーザー）"},
    ],
    "B63C": [
        {"eccn": "8A002", "fefta": "8項", "confidence": "high",
         "rationale": "水中作業機器・潜水装置（8A002.c: AUV・潜水艇補助機器）"},
    ],
}


def load_and_expand() -> dict:
    existing = json.loads(_OUT.read_text(encoding="utf-8"))
    fti = existing.get("fterm_to_eccn_index", {})
    rev = existing.get("eccn_to_fterm_index", {})

    added = 0
    for fterm, entries in NEW_ENTRIES.items():
        if fterm not in fti:
            fti[fterm] = entries
            added += 1
        else:
            # 既存エントリに同一 ECCN がなければ追加
            existing_eccns = {e["eccn"] for e in fti[fterm]}
            for entry in entries:
                if entry["eccn"] not in existing_eccns:
                    fti[fterm].append(entry)

        # 逆引きインデックスも更新
        for entry in entries:
            eccn = entry["eccn"]
            if eccn not in rev:
                rev[eccn] = []
            if not any(r["fterm"] == fterm for r in rev[eccn]):
                rev[eccn].append({
                    "fterm": fterm,
                    "confidence": entry["confidence"],
                    "rationale": entry.get("rationale", ""),
                })

    existing["fterm_to_eccn_index"] = fti
    existing["eccn_to_fterm_index"] = rev
    existing["total"] = len(fti)
    existing["_updated"] = __import__("datetime").datetime.now().strftime("%Y-%m-%d")

    return existing, added


if __name__ == "__main__":
    data, added = load_and_expand()
    total = data["total"]
    rev_total = len(data["eccn_to_fterm_index"])

    _OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Expanded: +{added} new F-term groups")
    print(f"  fterm_to_eccn_index: {total} entries")
    print(f"  eccn_to_fterm_index: {rev_total} ECCNs covered")
    print(f"  Saved: {_OUT}")
