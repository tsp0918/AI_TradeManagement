"""Phase 4 ライセンスクォータ統合テスト。

実行方法:
    pytest tests/test_quota.py -v

前提条件:
    - export_license が http://localhost:8012 で稼働中
    - ai_validation が http://localhost:8011 で稼働中

テスト一覧:
    QT-01: クォータ登録（管理用）
    QT-02: クォータ一覧取得
    QT-03: クォータ詳細取得
    QT-04: IF-06 残枠照会 — 残枠十分
    QT-05: IF-06 残枠照会 — 対象品目なし（ライセンス不要）
    QT-06: IF-07 仮引当作成
    QT-07: IF-07 仮引当一覧確認
    QT-08: IF-07 仮引当解放
    QT-09: IF-21 消費確定
    QT-10: IF-08 審査取下げ（ai_validation）
    QT-11: IF-08 取下げ済み案件の再取下げは 409
"""
from __future__ import annotations

import uuid
import httpx
import pytest

EL_BASE = "http://localhost:8012"
VAL_BASE = "http://localhost:8011"

_PRODUCT_CODE = f"QT-PROD-{uuid.uuid4().hex[:6].upper()}"
_LICENSE_NO = f"J-QT-{uuid.uuid4().hex[:6].upper()}"
_quota_id: str = ""
_alloc_no: str = ""


def test_qt01_register_quota():
    global _quota_id
    resp = httpx.post(f"{EL_BASE}/api/licenses/quotas/register", json={
        "license_no": _LICENSE_NO,
        "license_type": "EAR",
        "product_code": _PRODUCT_CODE,
        "eccn": "3B001",
        "destination_country": "CN",
        "total_value_usd": 500000,
        "total_unit": 500,
        "valid_from": "2026-01-01",
        "valid_until": "2027-12-31",
    }, timeout=10)
    assert resp.status_code == 201, f"QT-01 失敗: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["license_no"] == _LICENSE_NO
    assert data["total_value_usd"] == 500000.0
    assert data["available_value_usd"] == 500000.0
    _quota_id = data["id"]


def test_qt01b_register_quota_duplicate():
    resp = httpx.post(f"{EL_BASE}/api/licenses/quotas/register", json={
        "license_no": _LICENSE_NO,
        "license_type": "EAR",
        "product_code": _PRODUCT_CODE,
        "total_value_usd": 999,
    }, timeout=10)
    assert resp.status_code == 409, "QT-01b: 重複登録は 409 を返すべき"


def test_qt02_list_quotas():
    resp = httpx.get(f"{EL_BASE}/api/licenses/quotas?product_code={_PRODUCT_CODE}", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(q["license_no"] == _LICENSE_NO for q in data["quotas"])


def test_qt03_get_quota_detail():
    assert _quota_id, "QT-01 が先に実行されていること"
    resp = httpx.get(f"{EL_BASE}/api/licenses/quotas/{_quota_id}", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["license_no"] == _LICENSE_NO
    assert "allocations" in data


def test_qt04_quota_check_sufficient():
    resp = httpx.post(f"{EL_BASE}/api/licenses/quota-check", json={
        "items": [{"product_code": _PRODUCT_CODE, "quantity": 100, "amount_usd": 100000}],
        "destination_country": "CN",
        "contract_end_date": "2027-06-30",
    }, timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall"] in ("sufficient", "expiring"), f"QT-04: {data}"
    item = data["items"][0]
    assert item["license_required"] is True
    assert item["sufficient"] is True


def test_qt05_quota_check_no_license():
    """登録されていない品目 → license_required=False。"""
    unknown_code = f"UNKNOWN-{uuid.uuid4().hex[:6]}"
    resp = httpx.post(f"{EL_BASE}/api/licenses/quota-check", json={
        "items": [{"product_code": unknown_code, "quantity": 10}],
    }, timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall"] == "not_required"
    assert data["items"][0]["license_required"] is False


def test_qt06_allocate():
    global _alloc_no
    resp = httpx.post(f"{EL_BASE}/api/licenses/allocations", json={
        "transaction_id": f"TXN-QT-{uuid.uuid4().hex[:6]}",
        "case_no": f"CRM-FORM-{uuid.uuid4().hex[:6]}",
        "items": [{"product_code": _PRODUCT_CODE, "quantity": 100, "amount_usd": 80000}],
        "destination_country": "CN",
    }, timeout=10)
    assert resp.status_code == 201, f"QT-06 失敗: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["status"] == "allocated"
    assert data["allocation_id"] is not None
    _alloc_no = data["allocation_id"]

    # 残枠が減っていること
    quota_resp = httpx.get(f"{EL_BASE}/api/licenses/quotas/{_quota_id}", timeout=10)
    q = quota_resp.json()
    assert q["allocated_value_usd"] == 80000.0
    assert q["available_value_usd"] == 420000.0


def test_qt07_list_allocations():
    resp = httpx.get(f"{EL_BASE}/api/licenses/allocations?status=allocated", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(a["allocation_no"] == _alloc_no for a in data["allocations"])


def test_qt08_release_and_consume():
    """別途新規引当を作成して消費確定 (IF-21) を確認する。"""
    alloc_resp = httpx.post(f"{EL_BASE}/api/licenses/allocations", json={
        "transaction_id": f"TXN-QT-CONSUME-{uuid.uuid4().hex[:6]}",
        "items": [{"product_code": _PRODUCT_CODE, "quantity": 50, "amount_usd": 40000}],
    }, timeout=10)
    assert alloc_resp.status_code == 201
    consume_no = alloc_resp.json()["allocation_id"]

    # 消費確定
    consume_resp = httpx.post(
        f"{EL_BASE}/api/licenses/allocations/{consume_no}/consume",
        json={"consumed_quantity": 50, "consumed_amount_usd": 40000},
        timeout=10,
    )
    assert consume_resp.status_code == 200, f"QT-08 消費 失敗: {consume_resp.status_code} {consume_resp.text}"
    assert consume_resp.json()["result"] == "consumed"

    # consumed_value_usd が増加していること
    quota_resp = httpx.get(f"{EL_BASE}/api/licenses/quotas/{_quota_id}", timeout=10)
    q = quota_resp.json()
    assert q["consumed_value_usd"] == 40000.0


def test_qt09_release_allocation():
    """仮引当を解放する。"""
    release_resp = httpx.delete(f"{EL_BASE}/api/licenses/allocations/{_alloc_no}", timeout=10)
    assert release_resp.status_code == 200, f"QT-09 解放 失敗: {release_resp.status_code} {release_resp.text}"
    assert release_resp.json()["result"] == "released"


def test_qt10_if08_withdraw():
    """IF-08: 審査取下げ。"""
    # まず取引を作成
    create_resp = httpx.post(f"{VAL_BASE}/api/transactions", json={
        "title": "QT-10 取下げテスト",
        "destination_country": "CN",
        "products": [{"product_code": f"QT-PROD-{uuid.uuid4().hex[:6]}", "quantity": 1.0}],
        "source_module": "crm",
    }, timeout=10)
    assert create_resp.status_code == 201
    tx_id = create_resp.json()["id"]

    # 取下げ
    withdraw_resp = httpx.post(f"{VAL_BASE}/api/transactions/{tx_id}/withdraw", json={
        "reason_code": "opportunity_lost",
        "reason": "競合他社に決定したため失注",
        "withdrawn_by": "QTテスト担当者",
    }, timeout=10)
    assert withdraw_resp.status_code == 200, f"QT-10 失敗: {withdraw_resp.status_code} {withdraw_resp.text}"
    data = withdraw_resp.json()
    assert data["status"] == "withdrawn"
    assert data["reason_code"] == "opportunity_lost"


def test_qt11_withdraw_already_withdrawn_409():
    """IF-08: 取下げ済みの案件を再取下げ → 409。"""
    create_resp = httpx.post(f"{VAL_BASE}/api/transactions", json={
        "title": "QT-11 二重取下げテスト",
        "destination_country": "US",
        "products": [{"product_code": f"QT-PROD-{uuid.uuid4().hex[:6]}", "quantity": 1.0}],
        "source_module": "crm",
    }, timeout=10)
    assert create_resp.status_code == 201
    tx_id = create_resp.json()["id"]

    body = {"reason_code": "opportunity_lost"}
    httpx.post(f"{VAL_BASE}/api/transactions/{tx_id}/withdraw", json=body, timeout=10)
    resp2 = httpx.post(f"{VAL_BASE}/api/transactions/{tx_id}/withdraw", json=body, timeout=10)
    assert resp2.status_code == 409, "QT-11: 二重取下げは 409 を返すべき"
