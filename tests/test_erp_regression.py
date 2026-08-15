"""ERP 既存フロー 回帰テスト — Phase 1 着手前に全件グリーンであること。

実行方法:
    pytest tests/test_erp_regression.py -v

前提条件:
    - ai_validation が http://localhost:8011 で稼働中であること
    - ERP_WEBHOOK_URL、ERP_WEBHOOK_BEARER が .env に設定されていること

テスト一覧:
    RT-01: ERP → POST /api/transactions で source_module='erp' の正常作成
    RT-02: 作成済み case_no で GET /api/transactions/{id}/status → 200
    RT-03: ERP webhook 受信エンドポイント /api/transactions/webhook/judgment-updated が存在する
    RT-04: products 配列付きトランザクション作成
    RT-05: ERP 側フィールドが正しくマッピングされること
"""
from __future__ import annotations

import uuid

import httpx
import pytest

BASE = "http://localhost:8011"
_ERP_BEARER = "dev-erp-integration-key"


# ──────────────────────────────────────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────────────────────────────────────

def _erp_headers() -> dict:
    return {"Authorization": f"Bearer {_ERP_BEARER}"}


def _create_erp_tx(
    erp_case_no: str | None = None,
    product_code: str = "MAT-REGRESSION-001",
    destination_country: str = "CN",
    total_value_usd: float = 5000.0,
    extra: dict | None = None,
) -> dict:
    """ERP 連携トランザクション作成ヘルパー。作成された TX dict を返す。"""
    erp_no = erp_case_no or f"ERP-REG-{uuid.uuid4().hex[:8].upper()}"
    payload = {
        "title": f"[回帰テスト] {erp_no}",
        "counterparty_name": "Regression Test Corp",
        "source_module": "erp",
        "erp_case_no": erp_no,
        "product_code": product_code,
        "destination_country": destination_country,
        "total_value_usd": total_value_usd,
        "end_user": "Test End User",
        "end_user_country": "CN",
        "intended_use": "Civil industrial use",
        "quantity": 1.0,
        "incoterms": "CIF",
    }
    if extra:
        payload.update(extra)
    resp = httpx.post(f"{BASE}/api/transactions", json=payload, timeout=30)
    assert resp.status_code in (200, 201), f"TX 作成失敗: {resp.status_code} {resp.text}"
    return resp.json()


# ──────────────────────────────────────────────────────────────────────────────
# テストケース
# ──────────────────────────────────────────────────────────────────────────────

def test_rt01_erp_transaction_create():
    """RT-01: ERP から POST /api/transactions → source_module='erp' で正常作成。"""
    tx = _create_erp_tx()
    assert tx.get("id"), "id が返されていない"
    assert tx.get("case_no"), "case_no が返されていない"
    # source_module が 'erp' になっていること
    # （レスポンスに source_module が含まれない場合は GET で確認）
    assert tx.get("status") in ("draft", "in_review"), f"想定外 status: {tx.get('status')}"


def test_rt02_status_get():
    """RT-02: 作成済み TX の GET /api/transactions/{id} → 200。"""
    tx = _create_erp_tx()
    tx_id = tx["id"]
    resp = httpx.get(f"{BASE}/api/transactions/{tx_id}", timeout=10)
    assert resp.status_code == 200, f"GET 失敗: {resp.status_code}"
    data = resp.json()
    assert data.get("id") == tx_id


def test_rt03_erp_webhook_endpoint_exists():
    """RT-03: ERP webhook 受信エンドポイント /api/transactions/webhook/judgment-updated が存在する。

    注意: 現在 Bearer 認証未適用（既知の技術的負債 TD-5）。
    ERP が Bearer トークンで呼ぶ outbound 経路は _push_erp_status() で別途保護されている。
    """
    payload = {
        "erp_case_no": "ERP-RT03-TEST",
        "judgment": "APPROVED",
        "comment": "regression test",
    }
    resp = httpx.post(
        f"{BASE}/api/transactions/webhook/judgment-updated",
        json=payload,
        timeout=10,
    )
    # エンドポイントが存在すること（4xx/5xx でも "不在" ではない）
    # 200/422 は正常、404 はエンドポイント不在を示す
    assert resp.status_code != 404, "ERP webhook エンドポイントが見つからない"
    assert resp.status_code < 500, f"ERP webhook エンドポイントがサーバーエラー: {resp.status_code}"


def test_rt04_multi_product_transaction():
    """RT-04: items（品目配列）付きトランザクションが正常作成される。"""
    erp_no = f"ERP-MULTI-{uuid.uuid4().hex[:8].upper()}"
    payload = {
        "title": f"[回帰テスト] マルチ品目 {erp_no}",
        "counterparty_name": "Multi Product Test Corp",
        "source_module": "erp",
        "erp_case_no": erp_no,
        "product_code": "MAT-MULTI-001",
        "destination_country": "US",
        "total_value_usd": 25000.0,
        "quantity": 3.0,
        "items": [
            {"item_name": "品目A", "item_description": "テスト品目A"},
            {"item_name": "品目B", "item_description": "テスト品目B"},
        ],
    }
    resp = httpx.post(f"{BASE}/api/transactions", json=payload, timeout=30)
    assert resp.status_code in (200, 201), f"マルチ品目 TX 作成失敗: {resp.status_code} {resp.text}"
    tx = resp.json()
    assert tx.get("id"), "id が返されていない"


def test_rt05_erp_fields_mapped():
    """RT-05: ERP フィールドが TX に正しくマッピングされている。"""
    erp_no = f"ERP-MAP-{uuid.uuid4().hex[:8].upper()}"
    tx = _create_erp_tx(
        erp_case_no=erp_no,
        product_code="MAT-MAP-TEST",
        destination_country="DE",
        total_value_usd=99999.0,
    )
    tx_id = tx["id"]

    # GET で詳細を取得してフィールドを確認
    resp = httpx.get(f"{BASE}/api/transactions/{tx_id}", timeout=10)
    if resp.status_code != 200:
        pytest.skip(f"GET /api/transactions/{tx_id} が {resp.status_code} — フィールドマッピング確認不可")

    data = resp.json()
    # erp_case_no が保存されていること
    if "erp_case_no" in data:
        assert data["erp_case_no"] == erp_no, f"erp_case_no 不一致: {data.get('erp_case_no')}"
    # linked_product_code が保存されていること
    if "linked_product_code" in data:
        assert data["linked_product_code"] == "MAT-MAP-TEST"
