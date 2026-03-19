"""
platform_core.ontology
======================
NeuroSymbolic ナレッジベース層。

Symbolic（記号的）推論とNeural（統計的）FAISS検索を統合し、
「何が判定に必要で、今何が不明か」を構造的に把握する。

核心パターン:  required - known = missing  → 質問戦略を駆動

モジュール:
    models/     - Pydantic ドメインモデル (HanteiKubanbang, Threshold, ...)
    db/         - SQLAlchemy ORM スキーマ & リポジトリ
    rules/      - 推論エンジン (OntologyReasoningEngine)
    seed/       - 初期知識データ (JSON)
"""
