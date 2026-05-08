"""
platform_core.agent.tools
============================
AgentTool の具象実装群。

各ツールはモジュール API を呼び出してエージェントに情報を提供する。
エージェントはオントロジーが「自動取得可能」と判定した属性に対して
ユーザーへの質問の代わりにこれらのツールを実行する。

実装済み:
    RunValidationTool  - ai_validation パイプラインを実行し2リスト結果を取得
    ScreeningTool      - 取引先スクリーニング結果を取得
    GetTransactionTool - 既存 Transaction の情報を取得してContextに投入
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from platform_core.agent.base_agent import AgentTool, BaseContext


_AI_VALIDATION_URL = os.getenv("MODULE_AI_VALIDATION_URL", "http://localhost:8011")
_SCREENING_URL     = os.getenv("MODULE_SCREENING_URL",     "http://localhost:8005")
_INTERNAL_KEY      = os.getenv("INTERNAL_SERVICE_KEY",     "dev-internal-key")
_INTERNAL_HEADER   = {"X-Internal-Service-Key": _INTERNAL_KEY}


# ─────────────────────────────────────────────────
# RunValidationTool
# ─────────────────────────────────────────────────

class RunValidationTool(AgentTool):
    """
    ai_validation モジュールのパイプライン（run-and-two-lists）を実行し、
    2リスト判定結果を取得する。

    Context に transaction_id が必要。
    実行結果は context に "two_lists_result" として格納される。
    """
    name        = "run_validation"
    description = "ai_validation パイプラインを実行して2リスト判定結果を取得する"

    async def execute(self, context: BaseContext, **kwargs) -> Any:
        tx_id = getattr(context, "transaction_id", None)
        if not tx_id:
            return None

        url = f"{_AI_VALIDATION_URL}/decision/{tx_id}/run-and-two-lists"
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=_INTERNAL_HEADER)
            resp.raise_for_status()
            return resp.json()

    def get_result_attr_key(self) -> Optional[str]:
        return "two_lists_result"


# ─────────────────────────────────────────────────
# ScreeningTool
# ─────────────────────────────────────────────────

class ScreeningTool(AgentTool):
    """
    取引先スクリーニングモジュールを呼び出し、
    制裁リスト照合結果を取得する。

    Context に "counterparty_name" が必要。
    結果は "screening_result" として格納される。
    """
    name        = "screening_check"
    description = "取引先企業名で制裁リストスクリーニングを実行する"

    async def execute(self, context: BaseContext, **kwargs) -> Any:
        known = context.get_known_attributes()
        company_name = known.get("counterparty_name")
        if not company_name:
            return None

        url = f"{_SCREENING_URL}/api/screen"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json={"company_name": company_name},
                headers=_INTERNAL_HEADER,
            )
            resp.raise_for_status()
            result = resp.json()

        # 制裁対象フラグを context に直接更新
        if result.get("is_sanctioned"):
            context.update_attribute("is_sanctioned", True)
            context.update_attribute("sanction_lists", result.get("sanction_lists", []))

        return result

    def get_result_attr_key(self) -> Optional[str]:
        return "screening_result"


# ─────────────────────────────────────────────────
# GetTransactionTool
# ─────────────────────────────────────────────────

class GetTransactionTool(AgentTool):
    """
    既存 Transaction（ai_validation）から情報を取得し、
    Context の known_attributes を自動補完する。

    品目名・申告用途・取引先名・ステータスを取得して投入する。
    """
    name        = "get_transaction"
    description = "既存トランザクションの情報をContextに投入する"

    async def execute(self, context: BaseContext, **kwargs) -> Any:
        tx_id = getattr(context, "transaction_id", None)
        if not tx_id:
            return None

        url = f"{_AI_VALIDATION_URL}/api/transactions/{tx_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=_INTERNAL_HEADER)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            tx = resp.json()

        # Context に必要な情報を投入
        if tx.get("counterparty_name"):
            context.update_attribute("counterparty_name", tx["counterparty_name"])
        if tx.get("items"):
            first_item = tx["items"][0]
            context.update_attribute("item_name",  first_item.get("item_name", ""))
            context.update_attribute("item_model", first_item.get("item_model", ""))

        # usage_requirements から core 用途を取得
        for ur in tx.get("usage_requirements", []):
            if ur.get("source") == "core" and ur.get("text"):
                context.update_attribute("declared_usage", ur["text"])
                break

        return tx

    def get_result_attr_key(self) -> Optional[str]:
        return "transaction_data"


# ─────────────────────────────────────────────────
# CatchallDetailTool
# ─────────────────────────────────────────────────

class CatchallDetailTool(AgentTool):
    """
    指定トランザクションの最新キャッチオール判定結果を取得する。

    「なぜキャッチオール規制が適用されるのか」「どの EAR Country Chart 列が
    ヒットしているか」などのユーザー質問に答えるためにエージェントが使用する。

    Context に transaction_id が必要。
    結果は "catchall_result" として格納される。
    """
    name        = "get_catchall_detail"
    description = (
        "キャッチオール規制の判定結果詳細を取得する。"
        "仕向地・EARグループ・Country Chart列・Red Flag数・推奨アクションを含む。"
    )

    async def execute(self, context: BaseContext, **kwargs) -> Any:
        tx_id = getattr(context, "transaction_id", None)
        if not tx_id:
            return None

        url = f"{_AI_VALIDATION_URL}/decision/{tx_id}/catchall-result"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=_INTERNAL_HEADER)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    def get_result_attr_key(self) -> Optional[str]:
        return "catchall_result"


# ─────────────────────────────────────────────────
# デフォルトツールセット
# ─────────────────────────────────────────────────

def default_tools() -> list[AgentTool]:
    """HanteiAgent に標準登録するツールのリストを返す"""
    return [
        RunValidationTool(),
        ScreeningTool(),
        GetTransactionTool(),
        CatchallDetailTool(),
    ]
