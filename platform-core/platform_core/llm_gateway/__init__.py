"""
platform_core.llm_gateway
===========================
Claude Haiku / Sonnet への統一アクセス層。
BaseLLMBridge の実装を提供する。
"""
from .client import ClaudeLLMBridge

__all__ = ["ClaudeLLMBridge"]
