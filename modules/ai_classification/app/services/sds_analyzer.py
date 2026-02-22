# app/services/sds_analyzer.py
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from .ollama_client import OllamaClient

# --- 正規表現（SDSから拾う） ---
RE_SIGNAL = re.compile(r"注意喚起語[:：]?\s*(危険|警告)")
RE_GHS = re.compile(r"\bGHS\d{2}\b")          # 例: GHS02
RE_H = re.compile(r"\bH\d{3}\b")              # 例: H225
RE_P = re.compile(r"\bP\d{3}(?:\+\w+)*\b")    # 例: P210 / P305+P351+P338 等

# 法令っぽい文字列（雑に拾う）
RE_LAWS = [
    ("is_poison", re.compile(r"毒物及び劇物取締法.*毒物", re.DOTALL)),
    ("is_deleterious", re.compile(r"毒物及び劇物取締法.*劇物", re.DOTALL)),
    ("is_roudou_anzen_eisei", re.compile(r"労働安全衛生法")),
    ("is_prtr", re.compile(r"PRTR|化管法")),
    ("is_shoubouho", re.compile(r"消防法")),
    ("is_high_pressure_gas", re.compile(r"高圧ガス保安法")),
]


def _uniq_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _compact_lines(text: str, max_lines: int = 220) -> str:
    """
    ざっくり整形：空行を潰しつつ、上限行数で切る
    """
    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    return "\n".join(lines)


def _extract_rule_mapping(sds_text: str) -> Dict[str, Any]:
    """
    ルールベース抽出（まずはここで完結を狙う）
    """
    text = sds_text or ""

    m = RE_SIGNAL.search(text)
    signal = m.group(1).strip() if m else None

    pictos = _uniq_keep_order(RE_GHS.findall(text))
    hs = _uniq_keep_order(RE_H.findall(text))
    ps = _uniq_keep_order(RE_P.findall(text))

    flags: Dict[str, bool] = {
        "is_poison": False,
        "is_deleterious": False,
        "is_kashinho": False,
        "is_kashinho_class_I": False,
        "is_kashinho_class_II": False,
        "is_roudou_anzen_eisei": False,
        "is_prtr": False,
        "is_shoubouho": False,
        "is_high_pressure_gas": False,
    }
    for k, rx in RE_LAWS:
        flags[k] = bool(rx.search(text))

    # ghs_classes は重いので、現段階では空でOK（必要なら後で拡張）
    ghs_classes: List[str] = []

    return {
        "ghs_signal_word": signal,
        "ghs_pictograms": pictos,
        "ghs_h_statements": hs,
        "ghs_p_statements": ps,
        "ghs_classes": ghs_classes,
        **flags,
    }


def _is_sufficient(mapping: Dict[str, Any]) -> bool:
    """
    「Ollamaを呼ばない」判定を緩めにするのがポイント。
    SDSがまともなら Hコード or pictogram or signal のどれかは拾えることが多い。
    """
    if mapping.get("ghs_signal_word"):
        return True
    if mapping.get("ghs_h_statements"):
        return True
    if mapping.get("ghs_pictograms"):
        return True
    # 法令フラグが1つでも立っていれば十分扱いにしてよい
    for k in [
        "is_poison", "is_deleterious", "is_roudou_anzen_eisei",
        "is_prtr", "is_shoubouho", "is_high_pressure_gas",
    ]:
        if mapping.get(k) is True:
            return True
    return False


def _extract_relevant_snippets(sds_text: str, max_chars: int = 3500) -> str:
    """
    Ollamaに渡す“短文”を作る：H/P/GHS/法令/危険有害性に寄った行だけ抜き出す。
    """
    key_terms = [
        "注意喚起語", "危険", "警告", "GHS", "H", "P",
        "危険有害性", "ラベル", "絵表示", "区分", "分類",
        "毒物及び劇物取締法", "労働安全衛生法", "PRTR", "化管法", "消防法", "高圧ガス保安法",
        "第2章", "SECTION 2", "Section 2", "3. 組成", "SECTION 3", "Section 3",
    ]
    lines = (sds_text or "").splitlines()
    picked: List[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if any(t in s for t in key_terms) or RE_H.search(s) or RE_P.search(s) or RE_GHS.search(s):
            picked.append(s)

    # それでも空なら先頭だけ
    if not picked:
        picked = [ln.strip() for ln in lines if ln.strip()][:120]

    text = "\n".join(picked)
    text = _compact_lines(text, max_lines=260)
    return text[:max_chars]


def _chunk_text(text: str, chunk_chars: int = 2500, overlap: int = 250) -> List[str]:
    """
    文字数ベースでchunk化（PDF抽出は改行が多いのでこれが安定）
    """
    t = text or ""
    if len(t) <= chunk_chars:
        return [t]
    chunks: List[str] = []
    i = 0
    while i < len(t):
        j = min(len(t), i + chunk_chars)
        chunks.append(t[i:j])
        i = max(i + chunk_chars - overlap, i + 1)
    return chunks


def _ollama_prompt(snippet: str) -> str:
    return (
        "以下は化学品SDSから抜粋したテキストです。\n"
        "日本の法令（毒劇法、労安法、PRTR、消防法、高圧ガス保安法等）とGHS表記を手掛かりに、\n"
        "次のJSONだけを返してください（説明文禁止）。\n\n"
        "{\n"
        '  "ghs_signal_word": "危険 または 警告 または null",\n'
        '  "ghs_pictograms": ["GHS02","GHS07"],\n'
        '  "ghs_h_statements": ["H225","H315"],\n'
        '  "ghs_p_statements": ["P210","P280"],\n'
        '  "ghs_classes": ["引火性液体 区分2"],\n'
        '  "is_poison": false,\n'
        '  "is_deleterious": false,\n'
        '  "is_kashinho": false,\n'
        '  "is_kashinho_class_I": false,\n'
        '  "is_kashinho_class_II": false,\n'
        '  "is_roudou_anzen_eisei": false,\n'
        '  "is_prtr": false,\n'
        '  "is_shoubouho": false,\n'
        '  "is_high_pressure_gas": false\n'
        "}\n\n"
        "SDS抜粋:\n"
        f"{snippet}\n"
    )


def _merge_mapping(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """
    base を優先しつつ、空の項目だけ extra で補完
    """
    out = dict(base)

    def merge_list(key: str):
        b = out.get(key) or []
        e = extra.get(key) or []
        if isinstance(b, str):
            b = [b]
        if isinstance(e, str):
            e = [e]
        if not b and e:
            out[key] = e
        elif b and e:
            out[key] = _uniq_keep_order([*b, *e])

    for k in ["ghs_pictograms", "ghs_h_statements", "ghs_p_statements", "ghs_classes"]:
        merge_list(k)

    # signal word
    if not out.get("ghs_signal_word") and extra.get("ghs_signal_word"):
        out["ghs_signal_word"] = extra["ghs_signal_word"]

    # bool flags（Trueが優先）
    for k in [
        "is_poison", "is_deleterious", "is_kashinho", "is_kashinho_class_I", "is_kashinho_class_II",
        "is_roudou_anzen_eisei", "is_prtr", "is_shoubouho", "is_high_pressure_gas",
    ]:
        if bool(extra.get(k)) and not bool(out.get(k)):
            out[k] = True

    return out


async def analyze_sds_to_mapping(sds_text: str, ollama: OllamaClient) -> Dict[str, Any]:
    """
    3段階：
      1) ルール抽出で十分なら Ollamaを呼ばずに返す
      2) 足りない場合、短い抜粋だけ Ollamaへ（1回）
      3) それでも足りない場合、chunkで最大2回まで追加（CPU前提で抑える）
    """
    text = _compact_lines(sds_text, max_lines=2000)

    base = _extract_rule_mapping(text)
    debug = {
        "source": "rule_first_then_ollama_if_needed",
        "text_len": len(text),
        "rule": {
            "signal": base.get("ghs_signal_word"),
            "pictos": len(base.get("ghs_pictograms") or []),
            "h": len(base.get("ghs_h_statements") or []),
            "p": len(base.get("ghs_p_statements") or []),
        },
        "ollama_called": False,
        "ollama_steps": [],
    }

    # (1) ここで止まるのが理想
    if _is_sufficient(base):
        debug["decision"] = "rule_sufficient_no_ollama"
        return {"mapping": base, "raw": json.dumps(debug, ensure_ascii=False)}

    # (2) 抜粋だけで1回
    snippet = _extract_relevant_snippets(text, max_chars=3500)
    prompt = _ollama_prompt(snippet)

    debug["ollama_called"] = True
    try:
        resp = await ollama.chat_json(prompt, timeout_sec=240.0)
        extra = resp if isinstance(resp, dict) else {}
        merged = _merge_mapping(base, extra)
        debug["ollama_steps"].append({"mode": "snippet_once", "snippet_len": len(snippet)})
    except Exception as e:
        debug["ollama_steps"].append({"mode": "snippet_once", "error": repr(e)})
        # Ollama失敗でも落とさず、ruleの結果で返す
        debug["decision"] = "ollama_failed_return_rule"
        return {"mapping": base, "raw": json.dumps(debug, ensure_ascii=False)}

    if _is_sufficient(merged):
        debug["decision"] = "snippet_sufficient"
        return {"mapping": merged, "raw": json.dumps(debug, ensure_ascii=False)}

    # (3) chunkで最大2回だけ追加（CPU前提）
    chunks = _chunk_text(snippet, chunk_chars=2200, overlap=200)
    chunks = chunks[:2]  # 最大2回だけ

    cur = merged
    for idx, ch in enumerate(chunks, start=1):
        try:
            resp = await ollama.chat_json(_ollama_prompt(ch), timeout_sec=240.0)
            extra = resp if isinstance(resp, dict) else {}
            cur = _merge_mapping(cur, extra)
            debug["ollama_steps"].append({"mode": "chunk", "idx": idx, "chunk_len": len(ch)})
            if _is_sufficient(cur):
                debug["decision"] = "chunk_sufficient"
                return {"mapping": cur, "raw": json.dumps(debug, ensure_ascii=False)}
        except Exception as e:
            debug["ollama_steps"].append({"mode": "chunk", "idx": idx, "error": repr(e)})
            break

    debug["decision"] = "still_insufficient_return_best_effort"
    return {"mapping": cur, "raw": json.dumps(debug, ensure_ascii=False)}
