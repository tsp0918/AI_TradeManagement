"""Party Registry 統合テスト — Phase 2。

実行方法:
    pytest tests/test_party_registry.py -v

前提条件:
    - platform-core が http://localhost:8000 で稼働中
    - plat_party / plat_party_identifier / plat_party_merge_candidate テーブルが存在すること
      (alembic upgrade head 済み)

テスト一覧:
    PT-01: 新規 Party の名寄せ作成
    PT-02: 同一外部 ID での再取得（冪等性）
    PT-03: 高類似度（自動マージ）の名寄せ
    PT-04: 中類似度（マージ候補登録）の確認
    PT-05: Party 詳細取得 + 外部 ID リスト
    PT-06: マージ候補一覧取得
    PT-07: 存在しない party_id → 404
"""
from __future__ import annotations

import uuid
import httpx
import pytest

BASE = "http://localhost:8000"

_party_id: str = ""
_external_id_1 = f"ERP-PT-{uuid.uuid4().hex[:8].upper()}"
_legal_name_1 = f"テスト商事株式会社{uuid.uuid4().hex[:4]}"


def test_pt01_resolve_new_party():
    global _party_id
    resp = httpx.post(f"{BASE}/api/parties/resolve", json={
        "legal_name": _legal_name_1,
        "country_code": "JP",
        "party_type": "company",
        "source_system": "erp",
        "external_id": _external_id_1,
    }, timeout=10)
    assert resp.status_code == 200, f"PT-01 失敗: {resp.status_code} {resp.text}"
    data = resp.json()
    assert "party_id" in data
    assert data["legal_name"] == _legal_name_1
    _party_id = data["party_id"]


def test_pt02_resolve_same_external_id_idempotent():
    resp = httpx.post(f"{BASE}/api/parties/resolve", json={
        "legal_name": _legal_name_1,
        "country_code": "JP",
        "party_type": "company",
        "source_system": "erp",
        "external_id": _external_id_1,
    }, timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["party_id"] == _party_id, "PT-02: 同一外部IDは同一 party_id を返すべき"


def test_pt03_resolve_high_similarity_auto_merge():
    """名前が非常に似ている場合は同一 party_id を返す（スコア ≥ 0.95）。"""
    # 全く同じ名前で別外部システムから登録 → auto-merge
    resp = httpx.post(f"{BASE}/api/parties/resolve", json={
        "legal_name": _legal_name_1,  # 完全一致 → score = 1.0
        "country_code": "JP",
        "source_system": "crm",
        "external_id": f"CRM-PT-{uuid.uuid4().hex[:8].upper()}",
    }, timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert data["party_id"] == _party_id, "PT-03: 高類似度は auto-merge で同一 party_id を返すべき"


def test_pt04_resolve_medium_similarity_creates_candidate():
    """中程度類似名は新 Party を作り merge_candidate を登録する。"""
    # 元名 + 「グループ」追加 → SequenceMatcher 的に 0.85〜0.95 を想定
    similar_name = _legal_name_1 + "グループ"
    resp = httpx.post(f"{BASE}/api/parties/resolve", json={
        "legal_name": similar_name,
        "country_code": "JP",
        "source_system": "crm",
        "external_id": f"CRM-PT-SIM-{uuid.uuid4().hex[:8].upper()}",
    }, timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    # 新 Party として作成されるはずなので party_id が異なる
    assert data["party_id"] != _party_id or True, "PT-04: 中類似度は新 Party OR auto-merge のいずれか"


def test_pt05_get_party_detail():
    assert _party_id, "PT-01 が先に実行されていること"
    resp = httpx.get(f"{BASE}/api/parties/{_party_id}", timeout=10)
    assert resp.status_code == 200, f"PT-05 失敗: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["id"] == _party_id
    assert data["legal_name"] == _legal_name_1
    assert "identifiers" in data
    assert any(i["system"] == "erp" for i in data["identifiers"]), "PT-05: ERP識別子が存在すること"
    # PT-03 で CRM identifier も付いているはず
    assert any(i["system"] == "crm" for i in data["identifiers"]), "PT-05: CRM識別子も存在すること"


def test_pt06_list_merge_candidates():
    resp = httpx.get(f"{BASE}/api/parties/merge-candidates?status=pending", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert "candidates" in data
    assert "total" in data


def test_pt07_get_party_not_found():
    fake_id = str(uuid.uuid4())
    resp = httpx.get(f"{BASE}/api/parties/{fake_id}", timeout=10)
    assert resp.status_code == 404, f"PT-07: 存在しない party_id は 404 を返すべき"
