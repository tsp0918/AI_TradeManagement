"""
transform/mapping_builder.py
IPC ↔ ECCN および HS ↔ 外為法 のクロスマッピングテーブルを生成する。

生成ファイル:
  data/staging/mappings/ipc_eccn_mapping.json
  data/staging/mappings/hs_fefta_mapping.json
  data/staging/mappings/regulatory_keyword_dict.json

手法:
  - IPC↔ECCN: Claude Haiku API でバッチ生成（+既知ルール seed）
  - HS↔FEFTA: キーワード類似度 + 既存 control_nodes.json のエッジ情報から導出
  - キーワード辞書: ECCN/FEFTA ノードの label から規制固有用語を抽出
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── IPC ↔ ECCN マッピング ────────────────────────────────────────────────────

# 既知の確定マッピング（Seed）— 追加は手動でここに記載
IPC_ECCN_SEED: dict[str, list[str]] = {
    "C07C": ["1C350", "1C351"],   # 有機化合物 → 化学兵器前駆体
    "C07D": ["1C350"],
    "C12N": ["1C351", "1C352"],   # 微生物 → 生物剤
    "G03F": ["3C005"],            # フォトリソグラフィ → 半導体製造装置
    "H01L": ["3A001", "3E001"],   # 半導体デバイス
    "H01S": ["6A005"],            # レーザー
    "B64G": ["9A004", "9A515"],   # 宇宙機器
    "F42B": ["ML1", "ML2"],       # 弾薬
    "G21": ["0C001", "0D001"],    # 核材料
}


def build_ipc_eccn_mapping(
    control_nodes_path: Path,
    anthropic_api_key: str | None = None,
    output_path: Path | None = None,
    dry_run: bool = False,
    batch_size: int = 10,
    use_local: bool = False,
) -> dict[str, list[str]]:
    """
    IPC分類コード → ECCN番号 のマッピング辞書を生成する。

    処理:
      1. Seed マッピングを初期値として使用
      2. control_nodes.json の patent ノードの ipc_codes と eccn_explicit から
         データドリブンでマッピングを追加・拡充
      3. use_local=True: Ollama ローカル LLM で補完（API キー不要）
         anthropic_api_key 指定時: Claude Haiku で補完
    """
    mapping: dict[str, list[str]] = {k: list(v) for k, v in IPC_ECCN_SEED.items()}

    # control_nodes.json から patent ノードの IPC → ECCN 推定
    cn_data = json.loads(control_nodes_path.read_text(encoding="utf-8"))
    _extract_from_nodes(cn_data.get("nodes", []), mapping)

    logger.info("IPC↔ECCN mapping built: %d IPC classes covered", len(mapping))

    # LLM 補完（オプション）
    if use_local and not dry_run:
        mapping = _enrich_with_ollama(mapping, cn_data, batch_size)
    elif anthropic_api_key and not dry_run:
        mapping = _enrich_with_claude(mapping, cn_data, anthropic_api_key, batch_size)

    if output_path and not dry_run:
        _save(mapping, output_path, "_comment: IPC → ECCN クロスマッピング")
    elif dry_run:
        logger.info("[DRY_RUN] %d IPC entries would be saved.", len(mapping))

    return mapping


def _extract_from_nodes(nodes: list[dict], mapping: dict[str, list[str]]) -> None:
    """patent ノードの ipc_codes と regulation ノードの eccn_explicit からマッピングを抽出。"""
    # ECCN ノード ID セット
    eccn_ids = {n["id"] for n in nodes if n.get("regime") == "ear" or "C" in n.get("id", "")}

    for node in nodes:
        ipc_hints = node.get("ipc_hints", []) or []
        eccn_refs = node.get("eccn_explicit", []) or []
        for ipc in ipc_hints:
            ipc4 = ipc[:4] if len(ipc) >= 4 else ipc
            for eccn in eccn_refs:
                if ipc4 not in mapping:
                    mapping[ipc4] = []
                if eccn not in mapping[ipc4]:
                    mapping[ipc4].append(eccn)


def _enrich_with_claude(
    mapping: dict[str, list[str]],
    cn_data: dict,
    api_key: str,
    batch_size: int,
) -> dict[str, list[str]]:
    """未カバーの IPC クラスについて Claude API でECCN推定を補完する。"""
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic パッケージが未インストール。Claude 補完をスキップ。")
        return mapping

    # ECCN ノードのラベル情報を収集（コンテキスト用）
    eccn_nodes = [n for n in cn_data.get("nodes", []) if n.get("type") == "regulation"
                  and n.get("regime") == "ear"]

    client = anthropic.Anthropic(api_key=api_key)
    uncovered_ipcs = [ipc for ipc in _COMMON_IPC_CLASSES if ipc not in mapping]

    for i in range(0, len(uncovered_ipcs), batch_size):
        batch = uncovered_ipcs[i: i + batch_size]
        prompt = _build_ipc_eccn_prompt(batch, eccn_nodes)

        try:
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            result = json.loads(msg.content[0].text)
            for ipc, eccns in result.items():
                if ipc not in mapping:
                    mapping[ipc] = eccns
                else:
                    for eccn in eccns:
                        if eccn not in mapping[ipc]:
                            mapping[ipc].append(eccn)
            logger.info("Claude enriched %d IPC classes", len(batch))
        except Exception as exc:
            logger.warning("Claude API error: %s", exc)

        time.sleep(0.5)

    return mapping


def _enrich_with_ollama(
    mapping: dict[str, list[str]],
    cn_data: dict,
    batch_size: int,
) -> dict[str, list[str]]:
    """未カバーの IPC クラスについて Ollama ローカル LLM で ECCN 推定を補完する。"""
    try:
        import ollama
    except ImportError:
        logger.warning("ollama パッケージが未インストール。ローカル補完をスキップ。")
        return mapping

    ollama_url = _get_ollama_url()
    model = _get_ollama_model()
    eccn_nodes = [n for n in cn_data.get("nodes", []) if n.get("type") == "regulation"
                  and n.get("regime") == "ear"]

    client = ollama.Client(host=ollama_url)
    uncovered_ipcs = [ipc for ipc in _COMMON_IPC_CLASSES if ipc not in mapping]

    for i in range(0, len(uncovered_ipcs), batch_size):
        batch = uncovered_ipcs[i: i + batch_size]
        prompt = _build_ipc_eccn_prompt(batch, eccn_nodes)
        try:
            resp = client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.1, "num_predict": 512},
            )
            result = json.loads(resp.message.content or "{}")
            for ipc, eccns in result.items():
                if not isinstance(eccns, list):
                    continue
                if ipc not in mapping:
                    mapping[ipc] = eccns
                else:
                    for eccn in eccns:
                        if eccn not in mapping[ipc]:
                            mapping[ipc].append(eccn)
            logger.info("Ollama enriched %d IPC classes", len(batch))
        except Exception as exc:
            logger.warning("Ollama API error: %s", exc)

    return mapping


def _get_ollama_url() -> str:
    import os
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


def _get_ollama_model() -> str:
    import os
    return os.environ.get("OLLAMA_QUESTION_MODEL", "qwen2.5:7b")


def _build_ipc_eccn_prompt(ipcs: list[str], eccn_nodes: list[dict]) -> str:
    eccn_summary = "\n".join(
        f"- {n['id']}: {n.get('label', '')[:80]}" for n in eccn_nodes[:30]
    )
    ipc_list = ", ".join(ipcs)
    return f"""以下のIPC（国際特許分類）コードそれぞれに対し、該当する可能性が高いECCN番号を最大3つ推定してください。

IPC コード: {ipc_list}

参考ECCN一覧:
{eccn_summary}

回答は JSON 形式のみで返してください:
{{"H01L": ["3A001", "3E001"], "G03F": ["3C005"]}}
"""


# ── HS ↔ 外為法マッピング ────────────────────────────────────────────────────

def build_hs_fefta_mapping(
    control_nodes_path: Path,
    hs_codes_path: Path,
    output_path: Path | None = None,
    dry_run: bool = False,
    min_score: float = 0.3,
) -> dict[str, list[str]]:
    """
    HS コード → 外為法別表ノード ID のマッピングを生成する。

    手法: HS description と FEFTA node label のキーワードオーバーラップスコア。
    公式マッピングが入手できた場合は上書きで適用できるよう設計。
    """
    cn_data   = json.loads(control_nodes_path.read_text(encoding="utf-8"))
    hs_data   = json.loads(hs_codes_path.read_text(encoding="utf-8"))

    fefta_nodes = [n for n in cn_data.get("nodes", []) if n.get("regime") == "fefta"]
    mapping: dict[str, list[str]] = {}

    for hs in hs_data:
        hs_code = hs.get("hs_code", "")
        desc    = (hs.get("description_en", "") + " " + hs.get("atlas_short_name", "")).lower()
        if not hs_code or not desc:
            continue

        matches: list[tuple[float, str]] = []
        for node in fefta_nodes:
            score = _overlap_score(desc, node)
            if score >= min_score:
                matches.append((score, node["id"]))

        if matches:
            matches.sort(reverse=True)
            mapping[hs_code] = [m[1] for m in matches[:3]]

    logger.info("HS↔FEFTA mapping: %d HS codes matched", len(mapping))

    if output_path and not dry_run:
        _save(mapping, output_path, "_comment: HS → 外為法別表 近似マッピング (キーワードスコア)")
    elif dry_run:
        logger.info("[DRY_RUN] %d HS entries would be saved.", len(mapping))

    return mapping


def _overlap_score(hs_desc: str, fefta_node: dict) -> float:
    node_text = " ".join(
        filter(None, [fefta_node.get("label", ""), fefta_node.get("description", "")])
    ).lower()
    hs_words   = set(re.findall(r"\b\w{3,}\b", hs_desc))
    node_words = set(re.findall(r"\b\w{3,}\b", node_text))
    if not hs_words or not node_words:
        return 0.0
    return len(hs_words & node_words) / len(hs_words | node_words)


# ── 規制キーワード辞書 ────────────────────────────────────────────────────────

def build_regulatory_keyword_dict(
    control_nodes_path: Path,
    output_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """
    ECCN / FEFTA ノードの label + requirement_text から規制固有の技術用語辞書を生成する。
    FAISS 検索クエリの前処理（シノニム展開）に利用する。

    出力形式: { "レジスト": ["photoresist", "resist", "感光性樹脂", ...], ... }
    """
    cn_data = json.loads(control_nodes_path.read_text(encoding="utf-8"))
    nodes   = cn_data.get("nodes", [])

    keyword_dict: dict[str, list[str]] = {}

    for node in nodes:
        if node.get("regime") not in ("fefta", "ear", "wa"):
            continue
        label    = node.get("label", "")
        req_text = node.get("requirement_text", "") or ""
        # 括弧内の同義語パターン: "レジスト（photoresist）" を抽出
        for m in re.finditer(r"([^\s（(、,]+)[（(]([^）)]+)[）)]", label + " " + req_text):
            ja  = m.group(1).strip()
            en  = m.group(2).strip()
            if ja and en and len(ja) >= 2 and len(en) >= 3:
                if ja not in keyword_dict:
                    keyword_dict[ja] = []
                if en not in keyword_dict[ja]:
                    keyword_dict[ja].append(en)

    logger.info("Regulatory keyword dict: %d terms", len(keyword_dict))

    if output_path and not dry_run:
        _save(keyword_dict, output_path, "_comment: 規制固有キーワード辞書")
    elif dry_run:
        logger.info("[DRY_RUN] %d terms would be saved.", len(keyword_dict))

    return keyword_dict


# ── ユーティリティ ────────────────────────────────────────────────────────────

def _save(data: Any, path: Path, comment: str = "") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, dict) and comment:
        wrapped = {"_comment": comment, **data}
    else:
        wrapped = data
    path.write_text(json.dumps(wrapped, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved %s (%d entries)", path.name, len(data))


# IPC 大分類コード（主要50クラス）
_COMMON_IPC_CLASSES = [
    "A61K", "A61P", "B64G", "C01B", "C04B", "C06B", "C07C", "C07D", "C08F",
    "C12N", "C12Q", "C22C", "C23C", "C30B", "F02K", "F16K", "G01N", "G01S",
    "G02B", "G03F", "G06F", "G06N", "G21C", "G21Y", "H01J", "H01L", "H01S",
    "H03F", "H04B", "H04L", "H04W",
]
