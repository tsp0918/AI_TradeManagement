"""
platform_core.ontology.rules.engine
=====================================
OntologyReasoningEngine — Phase 1 の核心実装。

★ 核心パターン:
    required = 候補項番が「判定に必要とするattr_keyの集合」
    known    = TransactionContext が「現在判明している属性」
    missing  = required - known   ← この差分が次の質問を決定する

また BaseOntology を実装し、BaseAgent から利用可能にする。
"""
from __future__ import annotations

from typing import Any, Optional

from platform_core.agent.base_agent import BaseOntology, BaseContext, MissingAttr
from platform_core.ontology.models.regulation import HanteiKubanbang, Threshold, NonNumericAttribute
from platform_core.ontology.models.judgment import JudgmentResult, JudgmentReason, JudgmentStatus


class OntologyReasoningEngine(BaseOntology):
    """
    NeuroSymbolicエンジンのSymbolic（記号的）推論部分。

    HanteiKubanbang のリストを保持し:
    1. get_required_attributes()  — 候補項番が必要とするattr_keyを返す
    2. get_attribute_context()    — attr_key → MissingAttr に変換
    3. apply_rules()              — ユーザー回答で候補を絞り込む
    4. reason()                   — 全属性判明後に最終判定を生成

    初期化方法:
        # シードデータから（DB不要）
        from platform_core.ontology.db.repository import load_seed_ontology
        engine = OntologyReasoningEngine(load_seed_ontology())

        # DBから（非同期）
        kubanbang_list = await repo.get_all()
        engine = OntologyReasoningEngine(kubanbang_list)
    """

    def __init__(self, kubanbang_list: list[HanteiKubanbang]):
        # domain_id → HanteiKubanbang の辞書（O(1)検索用）
        self._registry: dict[str, HanteiKubanbang] = {
            k.domain_id: k for k in kubanbang_list
        }
        # attr_key → (label, reason, unit, example, priority, can_auto) の辞書
        # 複数の項番が同じattr_keyを要求する場合は最高優先度を採用
        self._attr_meta: dict[str, MissingAttr] = {}
        self._build_attr_meta()

    # ── 初期化ヘルパー ──────────────────────────────────────────────────────

    def _build_attr_meta(self) -> None:
        """全HanteiKubanbangからattr_keyの横断辞書を構築する"""
        for kb in self._registry.values():
            for t in kb.thresholds:
                existing = self._attr_meta.get(t.attr_key)
                if existing is None or t.priority > existing.priority:
                    self._attr_meta[t.attr_key] = MissingAttr(
                        attr_key=t.attr_key,
                        label=t.label,
                        reason=self._build_reason(t.attr_key),
                        priority=t.priority,
                        unit=t.unit,
                        example=t.example,
                    )
            for a in kb.non_numeric_attrs:
                existing = self._attr_meta.get(a.attr_key)
                if existing is None or a.priority > existing.priority:
                    self._attr_meta[a.attr_key] = MissingAttr(
                        attr_key=a.attr_key,
                        label=a.label,
                        reason=self._build_reason(a.attr_key),
                        priority=a.priority,
                        unit=None,
                        example=a.example,
                    )

    def _build_reason(self, attr_key: str) -> str:
        """この属性を必要とする判定項番を列挙してreason文を生成"""
        titles = [
            kb.short_label or kb.title
            for kb in self._registry.values()
            if attr_key in kb.get_required_attr_keys()
        ]
        if titles:
            return f"{', '.join(titles[:3])} の判定に必要"
        return "判定パラメータの確認"

    # ── BaseOntology 実装 ───────────────────────────────────────────────────

    def get_required_attributes(self, candidate_ids: list[str]) -> set[str]:
        """
        ★ 核心メソッド。
        候補 domain_id リストから「判定に必要なattr_keyの集合」を返す。
        これが required の源泉。
        """
        required: set[str] = set()
        for domain_id in candidate_ids:
            kb = self._registry.get(domain_id)
            if kb:
                required |= kb.get_required_attr_keys()
        return required

    def get_attribute_context(self, attr_key: str) -> Optional[MissingAttr]:
        """attr_key → MissingAttr（ラベル・理由・単位など）を返す"""
        return self._attr_meta.get(attr_key)

    def apply_rules(
        self, context: BaseContext, candidates: list[str]
    ) -> dict[str, Any]:
        """
        現在の Context の既知属性をもとに、候補項番を絞り込む。

        各HanteiKubanbangの閾値を評価し:
        - 「除外条件を満たす」→ 候補から除外
        - 「規制条件を満たす」→ remaining に残留（確度UP）
        - 「判定不能」→ remaining に残留（まだ情報不足）

        戻り値:
            remaining: 残存候補 domain_id リスト
            excluded:  除外された domain_id と除外理由
            flags:     懸念フラグ
        """
        known = context.get_known_attributes()
        remaining: list[str] = []
        excluded: dict[str, str] = {}
        flags: list[str] = []

        for domain_id in candidates:
            kb = self._registry.get(domain_id)
            if not kb:
                remaining.append(domain_id)
                continue

            exclude_this = False
            for t in kb.thresholds:
                if t.attr_key not in known:
                    continue
                try:
                    actual = float(known[t.attr_key])
                    is_ctrl = t.is_controlled(actual)
                    # 規制対象外（閾値を下回る）→ 除外
                    # 注意: 下回るは「!is_controlled」
                    # HANDOFF: "0.03 > 0.005（閾値）→ 2-7項を除外" の例は
                    # 閾値が「GTE 0.005」で actual=0.03 → is_controlled=True（除外ではない）
                    # 実際のユースケースでは「controlled_value以上なら規制対象」
                    # 閾値未満 = 非規制 = 除外する方向の判定は
                    # 運用によって異なるため、ここでは「明確に非規制」の場合のみ除外
                    if not is_ctrl:
                        # 数値閾値で「規制未満」が確定 → この項番は除外候補
                        # ただし他の閾値が残っている場合は慎重に
                        exclude_this = True
                        excluded[domain_id] = (
                            f"{t.label}={known[t.attr_key]}{t.unit or ''} が "
                            f"閾値({t.operator.value}{t.controlled_value}{t.unit or ''})を満たさない"
                        )
                        break
                except (TypeError, ValueError):
                    pass

            for a in kb.non_numeric_attrs:
                if a.attr_key not in known:
                    continue
                actual_str = str(known[a.attr_key])
                result = a.is_controlled(actual_str)
                if result is False:
                    exclude_this = True
                    excluded[domain_id] = (
                        f"{a.label}={actual_str} が除外条件に該当"
                    )
                    break

            if not exclude_this:
                remaining.append(domain_id)
            else:
                # 懸念フラグ: 規制対象が除外されていくことは安全方向
                pass

        # 懸念チェック（destination_country が懸念国リストに含まれるか等）
        dest = known.get("destination_country", "")
        if dest in {"KP", "IR", "SY", "CU"}:
            flags.append(f"仕向国 {dest} は懸念国リストに含まれます")

        return {
            "remaining": remaining,
            "excluded":  excluded,
            "flags":     flags,
        }

    # ── 最終推論 ─────────────────────────────────────────────────────────────

    def reason(
        self, context: BaseContext, candidates: list[str]
    ) -> JudgmentResult:
        """
        全属性が揃った（または情報収集が完了した）後に最終判定を実行する。

        各候補項番について:
        - 閾値を満たす → CONTROLLED（規制対象）
        - 閾値を満たさない → NOT_CONTROLLED
        - 判定不能 → REQUIRES_REVIEW
        """
        from platform_core.ontology.models.transaction import TransactionContext
        session_id = getattr(context, "session_id", "unknown")
        tx_id = getattr(context, "transaction_id", None)
        result = JudgmentResult(session_id=session_id, transaction_id=tx_id)
        known = context.get_known_attributes()

        for domain_id in candidates:
            kb = self._registry.get(domain_id)
            if not kb:
                result.add_reason(JudgmentReason(
                    domain_id=domain_id,
                    title=domain_id,
                    status=JudgmentStatus.REQUIRES_REVIEW,
                    explanation="オントロジー定義なし",
                ))
                continue

            status = self._evaluate_kubanbang(kb, known)
            explanation = self._build_explanation(kb, known, status)
            result.add_reason(JudgmentReason(
                domain_id=domain_id,
                title=kb.title,
                status=status,
                explanation=explanation,
            ))

        result.finalize()
        return result

    def _evaluate_kubanbang(
        self, kb: HanteiKubanbang, known: dict[str, Any]
    ) -> JudgmentStatus:
        """1件の項番に対して全閾値を評価し判定ステータスを返す"""
        has_unknown = False
        for t in kb.thresholds:
            if t.attr_key not in known:
                has_unknown = True
                continue
            try:
                actual = float(known[t.attr_key])
                if t.is_controlled(actual):
                    return JudgmentStatus.CONTROLLED
            except (TypeError, ValueError):
                has_unknown = True

        for a in kb.non_numeric_attrs:
            if a.attr_key not in known:
                has_unknown = True
                continue
            result = a.is_controlled(str(known[a.attr_key]))
            if result is True:
                return JudgmentStatus.CONTROLLED
            if result is None:
                has_unknown = True

        if has_unknown:
            return JudgmentStatus.REQUIRES_REVIEW
        return JudgmentStatus.NOT_CONTROLLED

    def _build_explanation(
        self,
        kb: HanteiKubanbang,
        known: dict[str, Any],
        status: JudgmentStatus,
    ) -> str:
        """判定根拠を自然言語で説明する文字列を生成する"""
        parts: list[str] = []
        for t in kb.thresholds:
            if t.attr_key in known:
                val = known[t.attr_key]
                try:
                    ctrl = "規制対象" if t.is_controlled(float(val)) else "閾値未満（非該当）"
                except (TypeError, ValueError):
                    ctrl = "要確認（値不正）"
                parts.append(
                    f"{t.label}={val}{t.unit or ''} ({t.operator.value}{t.controlled_value}{t.unit or ''}) → {ctrl}"
                )
        for a in kb.non_numeric_attrs:
            if a.attr_key in known:
                val = known[a.attr_key]
                r = a.is_controlled(str(val))
                ctrl = "規制対象" if r is True else ("非該当" if r is False else "要確認")
                parts.append(f"{a.label}={val} → {ctrl}")

        if not parts:
            return "確認済み属性なし。FAISSスコアによる参考ヒット。"
        return " / ".join(parts)

    # ── ユーティリティ ───────────────────────────────────────────────────────

    def get_missing_attributes(
        self,
        candidate_ids: list[str],
        known_attributes: dict[str, Any],
    ) -> list[MissingAttr]:
        """
        ★ Phase 1 の最初のマイルストーン検証用メソッド。

        候補 domain_id と既知属性を受け取り、
        「次に確認すべき属性」を優先度順で返す。

        使用例:
            engine = OntologyReasoningEngine(load_seed_ontology())
            missing = engine.get_missing_attributes(
                candidate_ids=["FEFTA::7::law::6"],
                known_attributes={"destination_country": "CN"},
            )
            # → [MissingAttr(attr_key="operating_frequency_ghz", ...),
            #    MissingAttr(attr_key="end_use_type", ...)]
        """
        required = self.get_required_attributes(candidate_ids)
        known    = set(known_attributes.keys())
        missing  = required - known   # ★ 核心の一行

        result: list[MissingAttr] = []
        for attr_key in missing:
            ma = self.get_attribute_context(attr_key)
            if ma:
                result.append(ma)

        return sorted(result, key=lambda a: -a.priority)

    @property
    def all_domain_ids(self) -> list[str]:
        """登録されている全domain_idのリスト"""
        return list(self._registry.keys())

    def get_kubanbang(self, domain_id: str) -> Optional[HanteiKubanbang]:
        return self._registry.get(domain_id)
