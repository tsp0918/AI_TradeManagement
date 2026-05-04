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


def ensure_new_apps(db: Session) -> None:
    """seed_if_empty() 後に呼ぶ。新モジュールのアプリを冪等的に追加する。"""
    _ensure_ai_validation(db)
    _ensure_screening(db)
    _ensure_rnd_assessment(db)


def _ensure_ai_validation(db: Session) -> None:
    if db.query(DapApp).filter(DapApp.app_key == "ai_validation_local").first():
        return

    app = DapApp(app_key="ai_validation_local", name="AI該非判定（ローカル）")
    db.add(app)
    db.flush()

    # Page: 新規案件作成フォーム
    page_new = DapPage(
        app_id=app.id,
        page_key="transaction_new",
        name="案件新規作成",
        url_regex=r".*(localhost|127\.0\.0\.1):8011/ui/transactions/new",
        must_have={},
    )
    db.add(page_new)
    db.flush()

    db.add_all([
        DapTarget(page_id=page_new.id, target_key="t_title", description="案件タイトル", anchors=[
            {"strategy": "css", "value": "input[name='title']"},
            {"strategy": "label_text", "value": "案件タイトル"},
        ]),
        DapTarget(page_id=page_new.id, target_key="t_counterparty", description="取引先企業名", anchors=[
            {"strategy": "css", "value": "input[name='counterparty_name']"},
            {"strategy": "label_text", "value": "取引先企業名"},
        ]),
        DapTarget(page_id=page_new.id, target_key="t_submit", description="案件登録ボタン", anchors=[
            {"strategy": "css", "value": "button[type='submit']"},
            {"strategy": "text_button", "value": "案件を登録"},
        ]),
    ])

    db.add_all([
        DapRule(
            app_id=app.id,
            rule_key="qr_title_quality",
            name="案件タイトル：具体化",
            rule_type="text_quality",
            severity="medium",
            params={
                "min_chars": 10,
                "forbidden_phrases": ["テスト", "test", "案件", "新規"],
                "field_target_id": "t_title",
            },
        ),
        DapRule(
            app_id=app.id,
            rule_key="qr_counterparty_missing",
            name="取引先企業名：未入力",
            rule_type="missing_or_generic",
            severity="high",
            params={
                "generic_values": ["不明", "未定", "TBD"],
                "field_target_id": "t_counterparty",
            },
        ),
        DapRule(
            app_id=app.id,
            rule_key="qr_new_min_gate",
            name="案件登録前：最低入力ゲート",
            rule_type="any_of_targets_missing",
            severity="high",
            params={"required_target_ids": ["t_title", "t_counterparty"]},
        ),
    ])

    db.add_all([
        DapIntervention(
            page_id=page_new.id,
            intervention_key="iv_title_coach",
            name="タイトルが抽象なら具体化テンプレ提示",
            trigger={"type": "field_blur", "target_id": "t_title"},
            rule_key="qr_title_quality",
            block_action=0,
            coach={"tone": "senior_supportive", "opening": "タイトルは「品目名 + 取引先 + 年度」のフォーマットにすると後で検索しやすくなります。"},
            actions=[
                {"type": "tooltip", "params": {"target_id": "t_title", "content": "例：「ArFフォトレジスト TS-4620 輸出審査 2025-Q1」のように具体的に。"}},
            ],
        ),
        DapIntervention(
            page_id=page_new.id,
            intervention_key="iv_counterparty_coach",
            name="取引先未入力なら入力促進",
            trigger={"type": "field_blur", "target_id": "t_counterparty"},
            rule_key="qr_counterparty_missing",
            block_action=0,
            coach={"tone": "strict_risk", "opening": "取引先企業名を入力するとスクリーニング（制裁リストチェック）を自動で実行できます。"},
            actions=[
                {"type": "tooltip", "params": {"target_id": "t_counterparty", "content": "正式な英語法人名を入力。例: Beijing Tech Co., Ltd."}},
                {"type": "highlight", "params": {"target_id": "t_counterparty"}},
            ],
        ),
        DapIntervention(
            page_id=page_new.id,
            intervention_key="iv_gate_before_submit",
            name="案件登録前：最低入力ゲート",
            trigger={"type": "attempt_action", "target_id": "t_submit"},
            rule_key="qr_new_min_gate",
            block_action=1,
            coach={"tone": "strict_risk", "opening": "タイトルと取引先企業名を入力してから登録してください。"},
            actions=[
                {"type": "checklist", "params": {"title": "登録前の確認", "items": [
                    "案件タイトルが具体的に記入されている",
                    "取引先企業名（英語法人名）が入力されている",
                ]}},
                {"type": "block_action", "params": {"target_id": "t_submit", "ttl_ms": 10000, "reason": "タイトルまたは取引先企業名が未入力です。"}},
            ],
        ),
    ])

    # Page: 案件詳細
    page_detail = DapPage(
        app_id=app.id,
        page_key="transaction_detail",
        name="案件詳細",
        url_regex=r".*(localhost|127\.0\.0\.1):8011/ui/transactions/\d+$",
        must_have={},
    )
    db.add(page_detail)
    db.flush()

    db.add_all([
        DapTarget(page_id=page_detail.id, target_key="t_run_ai", description="AI判定実行ボタン", anchors=[
            {"strategy": "css", "value": "a[href*='run']"},
            {"strategy": "text_button", "value": "AI判定を実行"},
        ]),
        DapTarget(page_id=page_detail.id, target_key="t_screening_btn", description="スクリーニング実行ボタン", anchors=[
            {"strategy": "css", "value": "button[type='submit']"},
            {"strategy": "text_button", "value": "スクリーニングを実行"},
        ]),
    ])

    db.commit()

    cfg = build_runtime_config(db, "ai_validation_local")
    rel = DapRelease(app_id=app.id, env="local", version="0.1.0", status="published", runtime_config=cfg)
    db.add(rel)
    db.commit()


def _ensure_screening(db: Session) -> None:
    if db.query(DapApp).filter(DapApp.app_key == "screening_local").first():
        return

    app = DapApp(app_key="screening_local", name="スクリーニング（ローカル）")
    db.add(app)
    db.flush()

    page = DapPage(
        app_id=app.id,
        page_key="screen_form",
        name="スクリーニング実行",
        url_regex=r".*(localhost|127\.0\.0\.1):8005/ui/screen",
        must_have={},
    )
    db.add(page)
    db.flush()

    db.add_all([
        DapTarget(page_id=page.id, target_key="t_company_name", description="企業名入力欄", anchors=[
            {"strategy": "css", "value": "input[name='company_name']"},
            {"strategy": "label_text", "value": "企業名"},
        ]),
    ])

    db.add_all([
        DapRule(
            app_id=app.id,
            rule_key="qr_company_name_quality",
            name="企業名：具体化",
            rule_type="text_quality",
            severity="medium",
            params={
                "min_chars": 5,
                "forbidden_phrases": ["不明", "未定", "企業"],
                "field_target_id": "t_company_name",
            },
        ),
    ])

    db.add_all([
        DapIntervention(
            page_id=page.id,
            intervention_key="iv_company_name_coach",
            name="企業名が抽象なら正式名称を促す",
            trigger={"type": "field_blur", "target_id": "t_company_name"},
            rule_key="qr_company_name_quality",
            block_action=0,
            coach={"tone": "senior_supportive", "opening": "正式な英語法人名を入力するとスクリーニング精度が上がります。"},
            actions=[
                {"type": "tooltip", "params": {"target_id": "t_company_name", "content": "例: Beijing Technology Co., Ltd. / Huawei Technologies Co., Ltd."}},
            ],
        ),
    ])

    db.commit()

    cfg = build_runtime_config(db, "screening_local")
    rel = DapRelease(app_id=app.id, env="local", version="0.1.0", status="published", runtime_config=cfg)
    db.add(rel)
    db.commit()


def _ensure_rnd_assessment(db: Session) -> None:
    if db.query(DapApp).filter(DapApp.app_key == "rnd_assessment_local").first():
        return

    app = DapApp(app_key="rnd_assessment_local", name="R&D審査（ローカル）")
    db.add(app)
    db.flush()

    # ─── ページ: 新規ケース作成（http://localhost:8003/ui/cases/new）
    page_new = DapPage(
        app_id=app.id,
        page_key="cases_new",
        name="新規ケース作成",
        url_regex=r".*(localhost|127\.0\.0\.1):8003/ui/cases/new",
        must_have={},
    )
    db.add(page_new)
    db.flush()

    # ─── ターゲット
    db.add_all([
        DapTarget(
            page_id=page_new.id,
            target_key="t_description",
            description="説明テキストエリア",
            anchors=[
                # 最も確実なセレクタを優先
                {"strategy": "css",        "value": "textarea[name='description']"},
                {"strategy": "label_text", "value": "説明"},
                # ユーザー指定セレクタ（フォールバック）
                {"strategy": "css",        "value": "body > main > div.card > form > div:nth-child(3) > textarea"},
            ],
        ),
        DapTarget(
            page_id=page_new.id,
            target_key="t_title",
            description="ケースタイトル入力欄",
            anchors=[
                {"strategy": "css",        "value": "input[name='title']"},
                {"strategy": "label_text", "value": "ケースタイトル"},
            ],
        ),
        DapTarget(
            page_id=page_new.id,
            target_key="t_submit",
            description="ケース作成ボタン",
            anchors=[
                {"strategy": "css",        "value": "button[type='submit']"},
                {"strategy": "text_button","value": "ケースを作成して入力へ"},
            ],
        ),
    ])

    # ─── ルール
    db.add_all([
        DapRule(
            app_id=app.id,
            rule_key="qr_case_title_quality",
            name="ケースタイトル：具体性チェック",
            rule_type="text_quality",
            severity="medium",
            params={
                "min_chars": 10,
                "forbidden_phrases": ["テスト", "test", "ケース", "新規", "未定"],
                "field_target_id": "t_title",
            },
        ),
        DapRule(
            app_id=app.id,
            rule_key="qr_case_min_gate",
            name="ケース作成前：最低入力ゲート",
            rule_type="any_of_targets_missing",
            severity="high",
            params={"required_target_ids": ["t_title"]},
        ),
    ])

    # ─── 介入
    db.add_all([
        # ① ページ読込チュートリアル: 説明欄をハイライト + 記入ポイントをポップアップ
        DapIntervention(
            page_id=page_new.id,
            intervention_key="iv_cases_new_tutorial",
            name="新規ケース作成チュートリアル（ページ読込時）",
            trigger={"type": "page_load", "delay_ms": 700},
            rule_key=None,   # 常に表示（always）
            block_action=0,
            coach={
                "tone": "senior_supportive",
                "opening": "記入内容が具体的なほど審査精度が上がります。最低限この5点を押さえましょう。",
            },
            actions=[
                # 説明欄をウォームブラウンのパルスリングで強調
                {"type": "highlight", "params": {"target_id": "t_description"}},
                # 説明欄の横に記入例を吹き出しで表示
                {
                    "type": "tooltip",
                    "params": {
                        "target_id": "t_description",
                        "content": (
                            "【記入例】\n"
                            "研究目的：次世代ArF液浸フォトレジストの解像度向上\n"
                            "対象品目：NA≥1.35 対応 193nm フォトレジスト\n"
                            "最終用途：自社ラボでのリソグラフィプロセス評価\n"
                            "使用地：日本国内（海外移転・第三者提供なし）\n"
                            "軍事関連：なし"
                        ),
                        "primaryText": "入力してみる",
                    },
                },
                # 右上パネルに5点チェックリスト
                {
                    "type": "checklist",
                    "params": {
                        "title": "📝 説明欄に記載すべき5点",
                        "items": [
                            "① 研究目的・技術的背景（何の問題を解くか）",
                            "② 対象品目・材料のスペック（波長・NA など）",
                            "③ 最終用途（工程名・装置・性能条件）",
                            "④ 最終使用地・第三国移転の有無",
                            "⑤ 軍事・政府機関との関連（なければ「なし」と明記）",
                        ],
                    },
                },
            ],
        ),
        # ② タイトルフォーカスアウト時：具体的なタイトルを促す
        DapIntervention(
            page_id=page_new.id,
            intervention_key="iv_case_title_coach",
            name="ケースタイトル：具体化コーチ",
            trigger={"type": "field_blur", "target_id": "t_title"},
            rule_key="qr_case_title_quality",
            block_action=0,
            coach={
                "tone": "senior_supportive",
                "opening": "タイトルは「品目名 ＋ 目的 ＋ 年度」の形式にするとあとで検索しやすくなります。",
            },
            actions=[
                {
                    "type": "tooltip",
                    "params": {
                        "target_id": "t_title",
                        "content": "例：「ArFフォトレジスト TS-4620 リソグラフィ適用評価 2026-Q1」",
                    },
                },
            ],
        ),
        # ③ 作成ボタン押下前：タイトル未入力ならブロック
        DapIntervention(
            page_id=page_new.id,
            intervention_key="iv_case_submit_gate",
            name="ケース作成前：タイトル入力ゲート",
            trigger={"type": "attempt_action", "target_id": "t_submit"},
            rule_key="qr_case_min_gate",
            block_action=1,
            coach={
                "tone": "strict_risk",
                "opening": "ケースタイトルが入力されていません。タイトルは審査の基準情報になります。",
            },
            actions=[
                {
                    "type": "checklist",
                    "params": {
                        "title": "作成前の確認",
                        "items": [
                            "ケースタイトルが具体的に記入されている",
                            "説明欄に研究目的・品目・用途が記載されている",
                        ],
                    },
                },
                {
                    "type": "block_action",
                    "params": {
                        "target_id": "t_submit",
                        "ttl_ms": 10000,
                        "reason": "ケースタイトルを入力してから作成してください。",
                    },
                },
            ],
        ),
    ])

    db.commit()

    cfg = build_runtime_config(db, "rnd_assessment_local")
    rel = DapRelease(app_id=app.id, env="local", version="0.1.0", status="published", runtime_config=cfg)
    db.add(rel)
    db.commit()
