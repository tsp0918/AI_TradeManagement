from __future__ import annotations

from sqlalchemy.orm import Session

from .models import DapApp, DapPage, DapRule, DapIntervention, DapTarget, DapRelease
from .runtime_builder import build_runtime_config


def seed_if_empty(db: Session) -> None:
    if db.query(DapApp).first():
        return

    # App
    app = DapApp(app_key="rd_risk_local", name="R&Dプロジェクト審査（ローカル）")
    db.add(app)
    db.flush()

    # Page: broad regex (adjust later for your actual paths)
    page = DapPage(
        app_id=app.id,
        page_key="rd_case_form",
        name="R&D審査フォーム",
        url_regex=r".*(localhost|127\.0\.0\.1):8000/.*",
        must_have={},
    )
    db.add(page)
    db.flush()

    # Targets: placeholders (Recorderで実要素へ上書き推奨)
    db.add_all([
        DapTarget(page_id=page.id, target_key="t_enduse", description="用途要件（End-use）", anchors=[
            {"strategy":"attr","value":"data-dap-field=end_use_description"},
            {"strategy":"label_text","value":"用途要件"},
            {"strategy":"label_text","value":"用途"},
            {"strategy":"aria","value":"End-use"},
            {"strategy":"css","value":"textarea[name='end_use_description']"},
        ]),
        DapTarget(page_id=page.id, target_key="t_enduser", description="需要者要件（End-user）", anchors=[
            {"strategy":"attr","value":"data-dap-field=end_user_name_or_type"},
            {"strategy":"label_text","value":"需要者要件"},
            {"strategy":"label_text","value":"最終需要者"},
            {"strategy":"css","value":"input[name='end_user_name_or_type']"},
        ]),
        DapTarget(page_id=page.id, target_key="t_destination", description="仕向国（Destination）", anchors=[
            {"strategy":"attr","value":"data-dap-field=destination_country"},
            {"strategy":"label_text","value":"仕向国"},
            {"strategy":"css","value":"select[name='destination_country']"},
        ]),
        DapTarget(page_id=page.id, target_key="t_run_assessment", description="審査実行ボタン", anchors=[
            {"strategy":"attr","value":"data-dap-action=run_assessment"},
            {"strategy":"text_button","value":"審査実行"},
            {"strategy":"text_button","value":"Run assessment"},
            {"strategy":"css","value":"button[type='submit']"},
        ]),
    ])

    # Rules
    db.add_all([
        DapRule(
            app_id=app.id,
            rule_key="qr_enduse_quality",
            name="用途要件：抽象NG（具体化）",
            rule_type="text_quality",
            severity="high",
            params={
                "min_chars": 90,
                "forbidden_phrases": ["研究用途", "一般用途", "顧客要望", "未定"],
                "must_include_any": ["工程", "装置", "性能", "最終", "使用地"],
                "field_target_id": "t_enduse",
            },
        ),
        DapRule(
            app_id=app.id,
            rule_key="qr_enduser_missing",
            name="需要者要件：不明/抽象NG",
            rule_type="missing_or_generic",
            severity="high",
            params={
                "generic_values": ["顧客", "不明", "未定", "TBD"],
                "field_target_id": "t_enduser",
            },
        ),
        # 審査実行前に「用途 or 需要者」どちらか欠けていたら止める
        DapRule(
            app_id=app.id,
            rule_key="qr_minimum_gate",
            name="審査実行前：最低限の入力ゲート",
            rule_type="any_of_targets_missing",
            severity="high",
            params={
                "required_target_ids": ["t_enduse", "t_enduser"]
            },
        ),
    ])

    # Interventions
    db.add_all([
        # 用途が抽象ならテンプレ挿入
        DapIntervention(
            page_id=page.id,
            intervention_key="iv_enduse_coach",
            name="用途が抽象なら具体化テンプレで伴走",
            trigger={"type":"field_blur", "target_id":"t_enduse"},
            rule_key="qr_enduse_quality",
            block_action=0,
            coach={"tone":"senior_supportive", "opening":"この書き方だと審査が割れやすい。30秒で“判断できる用途”に整えよう。"},
            actions=[
                {"type":"tooltip", "params":{"target_id":"t_enduse", "content":"次の4点を1行ずつ。\n①工程/装置 ②対象/最終成果物 ③性能/条件 ④最終使用地/第三国移転 + 軍事/研究の関与"}},
                {"type":"insert_template", "params":{"target_id":"t_enduse", "template":"【工程/装置】\n【対象/最終成果物】\n【性能/条件】\n【最終使用地/第三国移転】\n【軍事/研究の関与】"}},
            ],
        ),
        # 需要者が不明ならチェックリスト
        DapIntervention(
            page_id=page.id,
            intervention_key="iv_enduser_coach",
            name="需要者が抽象なら確認観点を提示",
            trigger={"type":"field_blur", "target_id":"t_enduser"},
            rule_key="qr_enduser_missing",
            block_action=0,
            coach={"tone":"strict_risk", "opening":"需要者が曖昧だと、要件判定の根拠が崩れる。最低限ここだけ埋めよう。"},
            actions=[
                {"type":"checklist","params":{"title":"需要者要件：最低限の確認","items":[
                    "法人名 or 組織種別（大学/研究機関/政府/軍関連/民生企業）",
                    "使用場所（国/拠点）",
                    "第三者提供・再販・共同研究の有無"
                ]}},
                {"type":"highlight","params":{"target_id":"t_enduser"}},
            ],
        ),
        # 審査実行前にゲート（用途/需要者）
        DapIntervention(
            page_id=page.id,
            intervention_key="iv_gate_before_run",
            name="審査実行前：最低入力が揃っていなければ止める",
            trigger={"type":"attempt_action", "target_id":"t_run_assessment"},
            rule_key="qr_minimum_gate",
            block_action=1,
            coach={"tone":"strict_risk", "opening":"そのまま実行すると誤判定の確率が上がる。先に“審査に必要な最低入力”を揃えよう。"},
            actions=[
                {"type":"checklist","params":{"title":"実行前の最低要件","items":[
                    "用途要件が具体化されている（工程/装置/条件/最終使用地）",
                    "需要者要件が“誰が使うか”の粒度になっている"
                ]}},
                {"type":"highlight","params":{"target_id":"t_enduse"}},
                {"type":"highlight","params":{"target_id":"t_enduser"}},
                {"type":"block_action","params":{"target_id":"t_run_assessment","ttl_ms":12000,"reason":"用途要件/需要者要件が不足しています。埋めてから審査実行してください。"}},
            ],
        ),
    ])

    db.commit()

    cfg = build_runtime_config(db, "rd_risk_local")
    rel = DapRelease(app_id=app.id, env="local", version="0.1.0", status="published", runtime_config=cfg)
    db.add(rel)
    db.commit()
