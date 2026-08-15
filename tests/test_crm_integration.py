"""CRM 統合テスト — Phase 1/Phase 3 機能確認。

実行方法:
    pytest tests/test_crm_integration.py -v

前提条件:
    - ai_validation が http://localhost:8011 で稼働中
    - CRM_SKIP_HMAC=1 で起動していること（HMAC 署名省略）

テスト一覧:
    CT-01: IF-01 仮審査起票 — 新規作成（created: true）
    CT-02: IF-01 仮審査起票 — 同一ハッシュで再利用（created: false）
    CT-03: IF-02 本審査起票 — ハッシュ一致でスムーズ完了（smooth_completion: true）
    CT-04: IF-02 本審査起票 — 新規品目（smooth_completion: false）
    CT-05: IF-03 ステータス照会 — 仮審査 case_no で取得
    CT-06: IF-03 ステータス照会 — 存在しない case_no → 404
    CT-07: Phase3 override 申請 — 正常作成
    CT-08: Phase3 override バリデーション — 理由が短すぎる → 422
    CT-09: Phase3 override 一覧取得
"""
from __future__ import annotations

import uuid
import httpx
import pytest

BASE = "http://localhost:8011"

_COMMON = {
    "destination_country": "CN",
    "end_user_party_id": f"EU-CT-{uuid.uuid4().hex[:6].upper()}",
    "end_use": "civil industrial use",
    "total_value_usd": 75000,
    "products": [{"product_code": f"MAT-CT-{uuid.uuid4().hex[:6].upper()}", "quantity": 5.0}],
}

_prov_case_no: str = ""


def test_ct01_provisional_create():
    global _prov_case_no
    resp = httpx.post(f"{BASE}/api/crm/provisional-review", json={
        "title": "CT-01 仮審査テスト",
        **_COMMON,
        "crm_quote_id": "QUOTE-CT-001",
    }, timeout=10)
    assert resp.status_code == 201, f"CT-01 失敗: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["created"] is True, "CT-01: created should be True for new TX"
    assert data["review_type"] == "provisional"
    assert data["case_no"].startswith("CRM-PROV-")
    assert data["valid_until"]
    _prov_case_no = data["case_no"]


def test_ct02_provisional_idempotent():
    resp = httpx.post(f"{BASE}/api/crm/provisional-review", json={
        "title": "CT-02 重複テスト（タイトルが違っても同一内容）",
        **_COMMON,
    }, timeout=10)
    assert resp.status_code == 201
    data = resp.json()
    assert data["created"] is False, "CT-02: created should be False for hash match"
    assert data["case_no"] == _prov_case_no, "CT-02: should return existing case_no"


def test_ct03_formal_smooth_completion():
    resp = httpx.post(f"{BASE}/api/crm/formal-review", json={
        "title": "CT-03 本審査（スムーズ完了）",
        **_COMMON,
        "parent_case_no": _prov_case_no,
        "crm_contract_id": "CONTRACT-CT-003",
    }, timeout=10)
    assert resp.status_code == 201, f"CT-03 失敗: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["smooth_completion"] is True, "CT-03: smooth_completion should be True"
    assert data["inherited_from"] == _prov_case_no
    assert data["case_no"].startswith("CRM-FORM-")


def test_ct04_formal_new_product():
    unique = uuid.uuid4().hex[:8].upper()
    resp = httpx.post(f"{BASE}/api/crm/formal-review", json={
        "title": f"CT-04 本審査（新品目・継承なし）{unique}",
        "destination_country": "US",
        "end_use": "military simulation",
        "total_value_usd": 1_200_000,
        "products": [{"product_code": f"MAT-NEW-{unique}", "quantity": 1.0}],
        "crm_contract_id": f"CONTRACT-CT-004-{unique}",
    }, timeout=10)
    assert resp.status_code == 201
    data = resp.json()
    assert data["smooth_completion"] is False, "CT-04: smooth_completion should be False for new product"
    assert data["inherited_from"] is None


def test_ct05_review_status():
    assert _prov_case_no, "CT-01 が先に実行されていること"
    resp = httpx.get(f"{BASE}/api/crm/review-status/{_prov_case_no}", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["case_no"] == _prov_case_no
    assert data["review_type"] == "provisional"
    assert data["is_expired"] is False
    assert data["crm_quote_id"] == "QUOTE-CT-001"


def test_ct06_review_status_not_found():
    resp = httpx.get(f"{BASE}/api/crm/review-status/NONEXISTENT-XXXXX", timeout=10)
    assert resp.status_code == 404


def test_ct07_override_create():
    txs = httpx.get(f"{BASE}/api/transactions/recent?limit=5&all_orgs=true", timeout=10).json().get("transactions", [])
    assert txs, "テスト対象の TX が存在しません"
    tx_id = txs[0]["id"]

    resp = httpx.post(f"{BASE}/api/transactions/{tx_id}/overrides", json={
        "overridden_by": "テスト申請者",
        "approver_name": "テスト承認者",
        "approver_title": "コンプライアンス部長",
        "approver_email": "approver@test.example.com",
        "reason": "テスト用オーバーライド申請です。当該品目は規制対象ですが、最終用途証明書と政府機関による確認が完了しており、再輸出リスクが極めて低いと判断されました。",
        "scope": "transaction",
        "valid_until": "2027-03-31",
        "department_head_approval": False,
    }, timeout=10)
    assert resp.status_code == 201, f"CT-07 失敗: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["scope"] == "transaction"
    assert data["valid_until"] == "2027-03-31"


def test_ct08_override_reason_too_short():
    txs = httpx.get(f"{BASE}/api/transactions/recent?limit=5&all_orgs=true", timeout=10).json().get("transactions", [])
    assert txs
    tx_id = txs[0]["id"]

    resp = httpx.post(f"{BASE}/api/transactions/{tx_id}/overrides", json={
        "overridden_by": "A",
        "approver_name": "B",
        "approver_title": "C",
        "approver_email": "a@b.com",
        "reason": "短すぎる理由",
        "scope": "transaction",
        "valid_until": "2027-01-01",
    }, timeout=10)
    assert resp.status_code == 422, f"CT-08: 短い理由で 422 が返るべき。実際: {resp.status_code}"


def test_ct09_override_list():
    txs = httpx.get(f"{BASE}/api/transactions/recent?limit=5&all_orgs=true", timeout=10).json().get("transactions", [])
    assert txs
    tx_id = txs[0]["id"]

    resp = httpx.get(f"{BASE}/api/transactions/{tx_id}/overrides", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert "overrides" in data
    assert "total" in data
