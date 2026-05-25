"""
dap.ab_test_intent
==================
Stage 1 AB テスト: Qwen2.5-14B Local vs Claude Haiku API

使用方法:
    # DAP モジュールのルートから実行
    cd /Users/takehirosato/Desktop/AI_TradeManagement/modules/dap
    python ab_test_intent.py

    # API 比較なしでローカルのみ計測 (API キー不要)
    python ab_test_intent.py --local-only

移行判定基準:
    - Local 正解率 >= 90%
    - Local-API 一致率 >= 85% (--local-only 時はスキップ)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

# プロジェクトルートを sys.path に追加
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.WARNING)  # テスト中はWARN以上のみ表示

# ── テストケース ──────────────────────────────────────────────────────────────
# expected_intent: 正解ラベル
# expected_simple: True = ローカル処理すべき / False = Sonnet に流すべき
# context: モジュール・ページ情報

TEST_CASES = [
    # ── NAVIGATE_UI (シンプル) ──────────────────────────────────────────────
    {
        "input": "取引一覧ページに移動したい",
        "expected_intent": "NAVIGATE_UI",
        "expected_simple": True,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/dashboard"},
    },
    {
        "input": "品目管理に戻りたい",
        "expected_intent": "NAVIGATE_UI",
        "expected_simple": True,
        "context": {"module_name": "品目管理", "page_path": "/ui/products"},
    },
    {
        "input": "ダッシュボードに移動してください",
        "expected_intent": "NAVIGATE_UI",
        "expected_simple": True,
        "context": {"module_name": "R&Dリスク管理", "page_path": "/ui/cases"},
    },
    {
        "input": "スクリーニングのページはどこですか",
        "expected_intent": "NAVIGATE_UI",
        "expected_simple": True,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/1"},
    },
    {
        "input": "前のページに戻る方法を教えてください",
        "expected_intent": "NAVIGATE_UI",
        "expected_simple": True,
        "context": {"module_name": "品目管理", "page_path": "/ui/products/1"},
    },

    # ── EXPLAIN_UI (シンプル) ──────────────────────────────────────────────
    {
        "input": "この画面の操作方法を教えてください",
        "expected_intent": "EXPLAIN_UI",
        "expected_simple": True,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/new"},
    },
    {
        "input": "保存ボタンはどこにありますか",
        "expected_intent": "EXPLAIN_UI",
        "expected_simple": True,
        "context": {"module_name": "品目管理", "page_path": "/ui/products/edit/1"},
    },
    {
        "input": "この画面で何ができますか",
        "expected_intent": "EXPLAIN_UI",
        "expected_simple": True,
        "context": {"module_name": "スクリーニング", "page_path": "/ui/screening"},
    },
    {
        "input": "入力必須項目を教えてください",
        "expected_intent": "EXPLAIN_UI",
        "expected_simple": True,
        "context": {"module_name": "R&Dリスク管理", "page_path": "/ui/cases/new"},
    },
    {
        "input": "次に何をすればいいですか",
        "expected_intent": "EXPLAIN_UI",
        "expected_simple": True,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/1"},
    },

    # ── SAVE_TRANSACTION (シンプル) ────────────────────────────────────────
    {
        "input": "この取引を保存してください",
        "expected_intent": "SAVE_TRANSACTION",
        "expected_simple": True,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/new"},
    },
    {
        "input": "案件を登録したい",
        "expected_intent": "SAVE_TRANSACTION",
        "expected_simple": True,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/new"},
    },
    {
        "input": "入力した内容を保存する方法を教えて",
        "expected_intent": "SAVE_TRANSACTION",
        "expected_simple": True,
        "context": {"module_name": "品目管理", "page_path": "/ui/products/edit/1"},
    },

    # ── CLASSIFY_ITEM (複雑・Sonnet 必須) ─────────────────────────────────
    {
        "input": "この品目は規制対象ですか",
        "expected_intent": "CLASSIFY_ITEM",
        "expected_simple": False,
        "context": {"module_name": "品目管理", "page_path": "/ui/products/1"},
    },
    {
        "input": "ECCNコードを確認したい",
        "expected_intent": "CLASSIFY_ITEM",
        "expected_simple": False,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/1"},
    },
    {
        "input": "この半導体はリスト規制に該当しますか",
        "expected_intent": "CLASSIFY_ITEM",
        "expected_simple": False,
        "context": {"module_name": "品目管理", "page_path": "/ui/products/1"},
    },
    {
        "input": "光ファイバーの外為法項番を調べてほしい",
        "expected_intent": "CLASSIFY_ITEM",
        "expected_simple": False,
        "context": {"module_name": "品目管理", "page_path": "/ui/products/2"},
    },

    # ── CHECK_COMPLIANCE (複雑・Sonnet 必須) ──────────────────────────────
    {
        "input": "輸出許可が必要かどうか調べてほしい",
        "expected_intent": "CHECK_COMPLIANCE",
        "expected_simple": False,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/1"},
    },
    {
        "input": "中国向けの輸出でキャッチオール規制は適用されますか",
        "expected_intent": "CHECK_COMPLIANCE",
        "expected_simple": False,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/1"},
    },
    {
        "input": "半導体製造装置をシンガポール法人経由で輸出する場合のリスクを教えてください",
        "expected_intent": "CHECK_COMPLIANCE",
        "expected_simple": False,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/2"},
    },
    {
        "input": "この取引に輸出許可申請は必要ですか",
        "expected_intent": "CHECK_COMPLIANCE",
        "expected_simple": False,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/1"},
    },

    # ── EXPLAIN_REGULATION (複雑・Sonnet 必須) ────────────────────────────
    {
        "input": "外為法第25条の内容を教えて",
        "expected_intent": "EXPLAIN_REGULATION",
        "expected_simple": False,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/dashboard"},
    },
    {
        "input": "みなし輸出とはどういう意味ですか",
        "expected_intent": "EXPLAIN_REGULATION",
        "expected_simple": False,
        "context": {"module_name": "R&Dリスク管理", "page_path": "/ui/cases/1"},
    },
    {
        "input": "EAR99とは何ですか",
        "expected_intent": "EXPLAIN_REGULATION",
        "expected_simple": False,
        "context": {"module_name": "品目管理", "page_path": "/ui/products/1"},
    },
    {
        "input": "キャッチオール規制の判定手順を詳しく教えてください",
        "expected_intent": "EXPLAIN_REGULATION",
        "expected_simple": False,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/dashboard"},
    },

    # ── SEARCH_ECCN (複雑・Sonnet 必須) ───────────────────────────────────
    {
        "input": "3E001に該当するかどうか確認したい",
        "expected_intent": "SEARCH_ECCN",
        "expected_simple": False,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/1"},
    },
    {
        "input": "このECCNコードの意味を教えてください",
        "expected_intent": "SEARCH_ECCN",
        "expected_simple": False,
        "context": {"module_name": "品目管理", "page_path": "/ui/products/1"},
    },

    # ── 境界ケース ──────────────────────────────────────────────────────────
    {
        "input": "AI判定を実行したい",
        "expected_intent": "CLASSIFY_ITEM",
        "expected_simple": False,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/1"},
    },
    {
        "input": "スクリーニングを実行するにはどうすればいいですか",
        "expected_intent": "EXPLAIN_UI",
        "expected_simple": True,
        "context": {"module_name": "スクリーニング", "page_path": "/ui/screening"},
    },
    {
        "input": "対話形式の該非判定エージェントを起動してください",
        "expected_intent": "AGENT_START",
        "expected_simple": False,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/1"},
    },
    {
        "input": "ロシア向け案件のリスクはどのくらいですか",
        "expected_intent": "CHECK_COMPLIANCE",
        "expected_simple": False,
        "context": {"module_name": "AI取引審査", "page_path": "/ui/transactions/3"},
    },
]


# ── ローカル分類 ───────────────────────────────────────────────────────────

async def classify_local(case: dict) -> dict:
    """ローカル Qwen で意図分類を実行する"""
    from app.llm_router import classify_intent, init_ollama, is_local_llm_available
    if not is_local_llm_available():
        init_ollama()
    ctx = {
        "module_name":  case["context"].get("module_name", ""),
        "page_path":    case["context"].get("page_path", ""),
        "persona_level": "unknown",
    }
    t0 = time.perf_counter()
    result = await classify_intent(case["input"], ctx)
    elapsed = time.perf_counter() - t0
    result["_elapsed_ms"] = round(elapsed * 1000)
    return result


# ── API 分類 (Haiku) ──────────────────────────────────────────────────────

async def classify_api(case: dict) -> dict:
    """Claude Haiku API で意図分類を実行する (比較用)"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"intent": "API_SKIP", "confidence": 0.0, "_elapsed_ms": 0}

    try:
        import anthropic
        from app.llm_router import _INTENT_SYSTEM_PROMPT

        client = anthropic.Anthropic(api_key=api_key)
        ctx = case["context"]
        user_msg = (
            f"現在のモジュール: {ctx.get('module_name', '')} ({ctx.get('page_path', '')})\n"
            f"ユーザー習熟度: unknown\n"
            f"発話: {case['input']}"
        )
        t0 = time.perf_counter()
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=128,
            system=_INTENT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        elapsed = time.perf_counter() - t0
        raw = resp.content[0].text.strip()
        result = json.loads(raw)
        result["_elapsed_ms"] = round(elapsed * 1000)
        return result
    except Exception as e:
        return {"intent": "API_ERROR", "confidence": 0.0, "_elapsed_ms": 0, "_error": str(e)}


# ── メイン ────────────────────────────────────────────────────────────────

async def run_ab_test(local_only: bool = False) -> dict:
    total   = len(TEST_CASES)
    results = []

    print(f"\n{'='*60}")
    print(f"DAP Intent Classification AB テスト ({total}件)")
    print(f"Local モデル: {os.environ.get('OLLAMA_MODEL', 'qwen2.5:14b')}")
    print(f"モード: {'Local のみ' if local_only else 'Local + Haiku API 比較'}")
    print(f"{'='*60}\n")

    for i, case in enumerate(TEST_CASES, 1):
        print(f"[{i:02d}/{total}] {case['input'][:50]}")

        # ローカル分類
        local = await classify_local(case)

        # API 分類 (比較用)
        api = {} if local_only else await classify_api(case)

        expected        = case["expected_intent"]
        expected_simple = case["expected_simple"]
        local_intent    = local.get("intent", "?")
        local_conf      = local.get("confidence", 0.0)
        local_complex   = local.get("requires_complex_reasoning", True)

        # 正解判定
        correct_local   = local_intent == expected
        # ルーティング正確性: should_use_local の判定が expected_simple と一致するか
        from app.llm_router import CONFIDENCE_THRESHOLD, SIMPLE_INTENTS
        local_would_route_simple = (
            local_conf >= CONFIDENCE_THRESHOLD
            and local_intent in SIMPLE_INTENTS
            and not local_complex
        )
        routing_correct = local_would_route_simple == expected_simple

        result = {
            "input":            case["input"],
            "expected":         expected,
            "expected_simple":  expected_simple,
            "local_intent":     local_intent,
            "local_confidence": local_conf,
            "local_complex":    local_complex,
            "local_elapsed_ms": local.get("_elapsed_ms", 0),
            "correct_local":    correct_local,
            "routing_correct":  routing_correct,
        }

        if not local_only and api:
            api_intent = api.get("intent", "?")
            result.update({
                "api_intent":     api_intent,
                "api_elapsed_ms": api.get("_elapsed_ms", 0),
                "correct_api":    api_intent == expected,
                "agreement":      local_intent == api_intent,
            })

        results.append(result)

        # 簡易表示
        local_mark   = "✅" if correct_local  else "❌"
        routing_mark = "✅" if routing_correct else "⚠️ "
        api_str = f"  API={result.get('api_intent','—')}({result.get('api_elapsed_ms','—')}ms)" if not local_only and api else ""
        print(
            f"  {local_mark} Local={local_intent}({local_conf:.2f}, {local.get('_elapsed_ms')}ms)"
            f"{api_str}"
            f"  {routing_mark} routing={'simple' if local_would_route_simple else 'sonnet'}"
            f"  (expected={'simple' if expected_simple else 'sonnet'})"
        )

    # ── サマリ ──────────────────────────────────────────────────────────────
    local_acc    = sum(r["correct_local"]   for r in results) / total
    routing_acc  = sum(r["routing_correct"] for r in results) / total
    avg_local_ms = sum(r["local_elapsed_ms"] for r in results) / total

    print(f"\n{'='*60}")
    print(f"=== 結果サマリ ===")
    print(f"Local 正解率:      {local_acc:.1%}  ({sum(r['correct_local'] for r in results)}/{total})")
    print(f"ルーティング正確率: {routing_acc:.1%}  ({sum(r['routing_correct'] for r in results)}/{total})")
    print(f"Local 平均レイテンシ: {avg_local_ms:.0f}ms")

    if not local_only:
        api_results = [r for r in results if "correct_api" in r]
        if api_results:
            api_acc   = sum(r["correct_api"] for r in api_results) / len(api_results)
            agreement = sum(r["agreement"]   for r in api_results) / len(api_results)
            avg_api   = sum(r["api_elapsed_ms"] for r in api_results) / len(api_results)
            print(f"API 正解率:        {api_acc:.1%}  ({sum(r['correct_api'] for r in api_results)}/{len(api_results)})")
            print(f"Local-API 一致率:  {agreement:.1%}")
            print(f"API 平均レイテンシ: {avg_api:.0f}ms  (Local比: {avg_local_ms/avg_api:.1f}x)")

    # ルーティング別詳細
    simple_cases  = [r for r in results if r["expected_simple"]]
    complex_cases = [r for r in results if not r["expected_simple"]]
    if simple_cases:
        simple_routing_acc = sum(r["routing_correct"] for r in simple_cases) / len(simple_cases)
        print(f"\nシンプルケース ルーティング精度: {simple_routing_acc:.1%} ({len(simple_cases)}件中)")
    if complex_cases:
        complex_routing_acc = sum(r["routing_correct"] for r in complex_cases) / len(complex_cases)
        print(f"複雑ケース ルーティング精度:     {complex_routing_acc:.1%} ({len(complex_cases)}件中)")

    # NG ケース詳細
    ng = [r for r in results if not r["correct_local"]]
    if ng:
        print(f"\n--- NG ケース詳細 ({len(ng)}件) ---")
        for r in ng:
            print(f"  ❌ 期待={r['expected']} / Local={r['local_intent']}({r['local_confidence']:.2f})")
            print(f"     「{r['input'][:60]}」")

    routing_ng = [r for r in results if not r["routing_correct"]]
    if routing_ng:
        print(f"\n--- ルーティング誤り ({len(routing_ng)}件) ---")
        for r in routing_ng:
            got    = "simple" if (r["local_confidence"] >= CONFIDENCE_THRESHOLD and r["local_intent"] in SIMPLE_INTENTS and not r["local_complex"]) else "sonnet"
            expect = "simple" if r["expected_simple"] else "sonnet"
            print(f"  ⚠️  期待={expect} / 実際={got}  intent={r['local_intent']}({r['local_confidence']:.2f})")
            print(f"     「{r['input'][:60]}」")

    # ── 移行判定 ────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"=== 移行判定 ===")

    # 重大リスク: 複雑ケースが誤ってローカルに流れる
    false_simple = [r for r in results if not r["expected_simple"] and
                    r["local_confidence"] >= CONFIDENCE_THRESHOLD and
                    r["local_intent"] in SIMPLE_INTENTS and not r["local_complex"]]
    if false_simple:
        print(f"🚨 危険: 複雑ケース {len(false_simple)}件がローカルに誤ルーティングされます")
        for r in false_simple:
            print(f"   「{r['input'][:60]}」")

    ok_conditions = [
        ("Local 正解率 ≥ 90%",     local_acc >= 0.90),
        ("ルーティング正確率 ≥ 90%", routing_acc >= 0.90),
        ("複雑ケース誤ルーティングなし", len(false_simple) == 0),
    ]
    if not local_only:
        api_results_ok = [r for r in results if "agreement" in r]
        if api_results_ok:
            agreement_ok = sum(r["agreement"] for r in api_results_ok) / len(api_results_ok) >= 0.85
            ok_conditions.append(("Local-API 一致率 ≥ 85%", agreement_ok))

    all_ok = all(c[1] for c in ok_conditions)
    for label, ok in ok_conditions:
        print(f"  {'✅' if ok else '❌'} {label}")

    if all_ok:
        print("\n✅ 移行OK: 全条件クリア。シャドーモードへ進めます。")
    else:
        print("\n⚠️  要調整: CONFIDENCE_THRESHOLD またはプロンプトの見直しを推奨。")
        print("   対処案: SIMPLE_INTENTS の絞り込み / Few-shot 例の追加 / 閾値を 0.90 に引き上げ")

    return {
        "local_accuracy":    local_acc,
        "routing_accuracy":  routing_acc,
        "avg_local_ms":      avg_local_ms,
        "false_simple_count": len(false_simple),
        "all_ok":            all_ok,
        "results":           results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DAP Intent AB テスト")
    parser.add_argument("--local-only", action="store_true", help="Haiku API との比較を省略")
    parser.add_argument("--save", type=str, default="", help="結果を JSON ファイルに保存")
    args = parser.parse_args()

    summary = asyncio.run(run_ab_test(local_only=args.local_only))

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"\n結果を {args.save} に保存しました。")
