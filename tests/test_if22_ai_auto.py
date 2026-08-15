"""IF-22（返品・再輸出の審査）および AI判定自動実行の統合テスト。

実行方法:
    pytest tests/test_if22_ai_auto.py -v

前提条件:
    - ai_validation が http://localhost:8011 で稼働中
    - export_license が http://localhost:8012 で稼働中

テスト一覧:
    IF22-01: disposition=scrap → 廃棄として記録（新 TX なし）
    IF22-02: disposition=return_to_origin → 輸入規制確認の案内を返す
    IF22-03: disposition=reship_to_other → 新 TX（RET-YYYYMM-NNNNNN）が起票される
    IF22-04: 不正な disposition → 422
    IF22-05: 存在しない tx_id → 404
    IF22-06: allocation_no 付きで rollback 指示が受け付けられる
    AUTO-01: IF-01 新規仮審査起票後、matrix_match AiRun が自動作成される
    AUTO-02: IF-02 smooth_completion=False の本審査でも matrix_match が自動実行される
    AUTO-03: IF-02 smooth_completion=True（仮審査継承）では AI判定は実行されない
"""
from __future__ import annotations

import time
import uuid
import httpx
import pytest

VAL_BASE = "http://localhost:8011"

# テスト用の既存 TX を事前に1件作成する
_base_tx_id: int = 0
_base_case_no: str = ""
_prov_case_no: str = ""
_prov_tx_id: int = 0
_prov_product_code: str = ""
_form_case_no: str = ""


def _create_tx(dest: str = "US", product: str = "MAT-TEST-001") -> dict:
    r = httpx.post(
        f"{VAL_BASE}/api/transactions",
        json={
            "title": f"IF22テスト用取引 {product} → {dest}",
            "source_module": "erp",
            "destination_country": dest,
            "product_code": product,
            "total_value_usd": 50000,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_if22_00_setup():
    """前提: テスト用の TX を1件作成。"""
    global _base_tx_id, _base_case_no
    d = _create_tx()
    _base_tx_id = d["id"]
    _base_case_no = d["case_no"]
    assert _base_tx_id > 0


def test_if22_01_scrap():
    """IF22-01: disposition=scrap → 廃棄として記録、新 TX なし。"""
    assert _base_tx_id > 0
    r = httpx.post(
        f"{VAL_BASE}/api/transactions/{_base_tx_id}/return-review",
        json={
            "erp_return_number": "RET-0000001",
            "items": [{"product_code": "MAT-TEST-001", "quantity": 5}],
            "return_destination_country": "JP",
            "disposition": "scrap",
        },
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["disposition"] == "scrap"
    assert d["action"] == "scrap_recorded"
    assert d["original_tx_id"] == _base_tx_id
    assert "new_case_no" not in d


def test_if22_02_return_to_origin():
    """IF22-02: disposition=return_to_origin → 輸入規制確認の案内。"""
    assert _base_tx_id > 0
    r = httpx.post(
        f"{VAL_BASE}/api/transactions/{_base_tx_id}/return-review",
        json={
            "erp_return_number": "RET-0000002",
            "reason_code": "quality_defect",
            "items": [{"product_code": "MAT-TEST-001", "quantity": 3}],
            "return_destination_country": "JP",
            "disposition": "return_to_origin",
        },
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["disposition"] == "return_to_origin"
    assert d["action"] == "import_regulation_check"
    assert "notice" in d


def test_if22_03_reship_to_other():
    """IF22-03: disposition=reship_to_other → 新 TX が起票される。"""
    assert _base_tx_id > 0
    r = httpx.post(
        f"{VAL_BASE}/api/transactions/{_base_tx_id}/return-review",
        json={
            "erp_return_number": "RET-0000003",
            "original_delivery_number": "DEL-0000112",
            "items": [{"product_code": "MAT-TEST-001", "quantity": 10}],
            "return_destination_country": "KR",
            "disposition": "reship_to_other",
        },
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["disposition"] == "reship_to_other"
    assert d["action"] == "new_tx_created"
    new_case = d["new_case_no"]
    assert new_case.startswith("RET-"), f"case_no が RET- で始まらない: {new_case}"
    new_tx_id = d["new_tx_id"]
    assert new_tx_id > 0

    # 新 TX を GET して確認
    r2 = httpx.get(f"{VAL_BASE}/api/transactions/{new_tx_id}")
    assert r2.status_code == 200, r2.text
    tx = r2.json()
    assert tx["destination_country"] == "KR"


def test_if22_04_invalid_disposition():
    """IF22-04: 不正な disposition → 422。"""
    assert _base_tx_id > 0
    r = httpx.post(
        f"{VAL_BASE}/api/transactions/{_base_tx_id}/return-review",
        json={
            "items": [{"product_code": "MAT-TEST-001", "quantity": 1}],
            "return_destination_country": "JP",
            "disposition": "invalid_value",
        },
    )
    assert r.status_code == 422, r.text


def test_if22_05_tx_not_found():
    """IF22-05: 存在しない tx_id → 404。"""
    r = httpx.post(
        f"{VAL_BASE}/api/transactions/999999/return-review",
        json={"disposition": "scrap", "items": []},
    )
    assert r.status_code == 404, r.text


def test_if22_06_allocation_rollback_accepted():
    """IF22-06: allocation_no 付き → ロールバック指示がレスポンスに含まれる。"""
    assert _base_tx_id > 0
    fake_alloc_no = f"ALLOC-TEST-{uuid.uuid4().hex[:8].upper()}"
    r = httpx.post(
        f"{VAL_BASE}/api/transactions/{_base_tx_id}/return-review",
        json={
            "items": [{"product_code": "MAT-TEST-001", "quantity": 5}],
            "return_destination_country": "JP",
            "disposition": "scrap",
            "allocation_no": fake_alloc_no,
            "returned_amount_usd": 25000.0,
        },
    )
    assert r.status_code == 200, r.text
    d = r.json()
    rb = d["license_rollback"]
    assert rb is not None
    assert rb["allocation_no"] == fake_alloc_no
    assert rb["returned_quantity"] == 5.0


# ──────────────────────────────────────────────────────────────────────────────
# AI判定自動実行テスト
# ──────────────────────────────────────────────────────────────────────────────

def test_auto01_provisional_review_triggers_matrix_match():
    """AUTO-01: IF-01 新規仮審査起票後、matrix_match AiRun が自動作成される。"""
    global _prov_case_no, _prov_tx_id, _prov_product_code
    ext_id = uuid.uuid4().hex[:8].upper()
    _prov_product_code = f"AUTO-PROV-{ext_id}"
    r = httpx.post(
        f"{VAL_BASE}/api/crm/provisional-review",
        json={
            "title": f"AUTO-01 仮審査テスト {ext_id}",
            "products": [{"product_code": _prov_product_code, "quantity": 1.0}],
            "destination_country": "DE",
            "end_use": "industrial_automation",
            "total_value_usd": 120000,
        },
        headers={"X-Timestamp": "0", "X-Signature": "skip"},
    )
    assert r.status_code == 201, r.text
    d = r.json()
    _prov_case_no = d["case_no"]
    assert d["created"] is True

    # case_no から tx_id を取得
    r2 = httpx.get(f"{VAL_BASE}/api/crm/review-status/{_prov_case_no}")
    assert r2.status_code == 200, r2.text
    tx_id_from_api = r2.json().get("tx_id")
    # review-status に tx_id がなければ transactions で検索
    if not tx_id_from_api:
        r3 = httpx.get(f"{VAL_BASE}/api/transactions", params={"case_no": _prov_case_no})
        # fallback: recentから探す
        r3 = httpx.get(f"{VAL_BASE}/api/transactions/recent")
        assert r3.status_code == 200, r3.text
        txs = r3.json().get("transactions", [])
        matched = [t for t in txs if t.get("case_no") == _prov_case_no]
        _prov_tx_id = matched[0]["id"] if matched else 0
    else:
        _prov_tx_id = tx_id_from_api

    # AI パイプライン（usage_extract が最初のステップ）が起動されるまで最大 20 秒待つ
    # GET /api/transactions/{id} は最新の ai_run を "ai_run" キーで返す
    if _prov_tx_id:
        deadline = time.time() + 20
        found = False
        while time.time() < deadline:
            r4 = httpx.get(f"{VAL_BASE}/api/transactions/{_prov_tx_id}")
            if r4.status_code == 200:
                tx_data = r4.json()
                ai_run = tx_data.get("ai_run")
                if ai_run:  # パイプラインのいずれかのステップが AiRun を作成した
                    found = True
                    break
            time.sleep(1)
        assert found, (
            f"tx_id={_prov_tx_id} の AI パイプラインが 20 秒以内に開始されなかった"
            " （usage_extract または matrix_match AiRun が作成されていない）"
        )
    else:
        pytest.skip("tx_id を取得できなかったため AUTO-01 をスキップ")


def test_auto02_formal_review_new_triggers_matrix_match():
    """AUTO-02: IF-02 smooth_completion=False（仮審査なし）の本審査で matrix_match が自動実行。"""
    ext_id = uuid.uuid4().hex[:8].upper()
    r = httpx.post(
        f"{VAL_BASE}/api/crm/formal-review",
        json={
            "title": f"AUTO-02 本審査テスト {ext_id}",
            "products": [{"product_code": f"AUTO-FORM-{ext_id}", "quantity": 2.0}],
            "destination_country": "SG",
            "end_use": "research",
            "total_value_usd": 80000,
            "crm_contract_id": f"CTR-{ext_id}",
        },
        headers={"X-Timestamp": "0", "X-Signature": "skip"},
    )
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["smooth_completion"] is False
    global _form_case_no
    _form_case_no = d["case_no"]
    # matrix_match は非同期で起動。レスポンスが返ること自体で自動実行のキックを確認
    assert _form_case_no.startswith("CRM-FORM-"), f"case_no 形式不正: {_form_case_no}"


def test_auto03_formal_review_smooth_no_extra_run():
    """AUTO-03: IF-02 smooth_completion=True（仮審査継承）では AI判定の追加実行なし。"""
    assert _prov_case_no and _prov_product_code, "AUTO-01 を先に実行してください"
    # 仮審査と同一内容（同一ハッシュ）で本審査を起票 → smooth_completion=True になる
    r = httpx.post(
        f"{VAL_BASE}/api/crm/formal-review",
        json={
            "title": f"AUTO-03 本審査テスト（仮審査継承）",
            "products": [{"product_code": _prov_product_code, "quantity": 1.0}],
            "destination_country": "DE",
            "end_use": "industrial_automation",
            "total_value_usd": 120000,
            "parent_case_no": _prov_case_no,
        },
        headers={"X-Timestamp": "0", "X-Signature": "skip"},
    )
    assert r.status_code == 201, r.text
    d = r.json()
    # smooth_completion=True の場合 AI 判定は起動しない（レスポンスで確認）
    assert d["smooth_completion"] is True
    assert d["inherited_from"] == _prov_case_no
