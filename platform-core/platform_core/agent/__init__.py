"""
platform_core.agent
====================
NeuroSymbolic Platform Orchestrator Agent 基盤。

BaseAgent が `required - known = missing` パターンを実装し、
各モジュール API を「ツール」として呼び出しながら業務フローを横断する。
"""
from .base_agent import (
    MissingAttr,
    AgentResponse,
    AgentTool,
    BaseContext,
    BaseOntology,
    BaseLLMBridge,
    BaseAgent,
)

__all__ = [
    "MissingAttr",
    "AgentResponse",
    "AgentTool",
    "BaseContext",
    "BaseOntology",
    "BaseLLMBridge",
    "BaseAgent",
]
