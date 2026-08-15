"""継続モニタリング統合テスト — Phase 5。

実行方法:
    pytest tests/test_monitoring.py -v

前提条件:
    - platform-core が http://localhost:8000 で稼働中
    - monitoring_subscription テーブルが存在すること (alembic upgrade head 済み)

テスト一覧:
    MT-01: 購読を POST で作成 → 201
    MT-02: 購読一覧取得（アクティブフィルター）
    MT-03: 購読の非アクティブ化（DELETE）
    MT-04: enable_watch=True で party resolve → 購読が自動作成される
    MT-05: 同一対象×トリガーの重複購読は冪等（created=False が返る）
    MT-06: 存在しない購読 ID → 404
"""
from __future__ import annotations

import uuid
import httpx
import pytest

BASE = "http://localhost:8000"

_party_id: str = ""
_sub_id: str = ""
_ext_id = f"CRM-MON-{uuid.uuid4().hex[:8].upper()}"
_legal_name = f"モニタリングテスト株式会社{uuid.uuid4().hex[:4]}"


def test_mt01_create_subscription():
    """MT-01: 購読を POST で作成。"""
    global _party_id, _sub_id

    # まず Party を作成
    r = httpx.post(
        f"{BASE}/api/parties/resolve",
        json={
            "legal_name": _legal_name,
            "country_code": "JP",
            "party_type": "company",
            "source_system": "crm",
            "external_id": _ext_id,
        },
    )
    assert r.status_code == 200, r.text
    _party_id = r.json()["party_id"]

    # モニタリング購読を作成
    r = httpx.post(
        f"{BASE}/api/monitoring/subscriptions",
        json={
            "subject_type": "party",
            "subject_id": _party_id,
            "trigger_type": "sanction_change",
            "created_from_if": "IF-01",
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["created"] is True
    sub = data["subscription"]
    assert sub["subject_type"] == "party"
    assert sub["subject_id"] == _party_id
    assert sub["trigger_type"] == "sanction_change"
    assert sub["is_active"] is True
    assert sub["created_from_if"] == "IF-01"
    _sub_id = sub["id"]


def test_mt02_list_subscriptions():
    """MT-02: 購読一覧取得（subject_id で絞り込み）。"""
    assert _party_id, "MT-01 を先に実行してください"
    r = httpx.get(
        f"{BASE}/api/monitoring/subscriptions",
        params={"subject_id": _party_id, "is_active": "true"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] >= 1
    found = [s for s in data["subscriptions"] if s["subject_id"] == _party_id]
    assert found, "作成した party_id の購読が一覧に含まれていない"


def test_mt03_deactivate_subscription():
    """MT-03: 購読を非アクティブ化。"""
    assert _sub_id, "MT-01 を先に実行してください"
    r = httpx.delete(f"{BASE}/api/monitoring/subscriptions/{_sub_id}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["already_inactive"] is False

    # 再取得して is_active=False を確認
    r2 = httpx.get(
        f"{BASE}/api/monitoring/subscriptions",
        params={"subject_id": _party_id, "is_active": "false"},
    )
    assert r2.status_code == 200, r2.text
    inactive = [s for s in r2.json()["subscriptions"] if s["id"] == _sub_id]
    assert inactive, "非アクティブ化した購読が is_active=false の一覧に含まれていない"
    assert inactive[0]["is_active"] is False


def test_mt04_enable_watch_auto_creates_subscription():
    """MT-04: enable_watch=True で party resolve → 購読が自動作成される。"""
    ext_id_2 = f"CRM-MON-WATCH-{uuid.uuid4().hex[:8].upper()}"
    legal_name_2 = f"ウォッチテスト株式会社{uuid.uuid4().hex[:4]}"

    r = httpx.post(
        f"{BASE}/api/parties/resolve",
        json={
            "legal_name": legal_name_2,
            "country_code": "JP",
            "party_type": "company",
            "source_system": "crm",
            "external_id": ext_id_2,
            "enable_watch": True,
            "monitor_until": "2027-12-31",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    party_id_2 = data["party_id"]
    assert data["subscription_id"] is not None, "enable_watch=True なのに subscription_id が返らない"

    # 購読が実際に作成されていることを確認
    r2 = httpx.get(
        f"{BASE}/api/monitoring/subscriptions",
        params={"subject_id": party_id_2, "is_active": "true"},
    )
    assert r2.status_code == 200, r2.text
    subs = r2.json()["subscriptions"]
    assert len(subs) >= 1
    assert subs[0]["trigger_type"] == "sanction_change"
    assert subs[0]["monitor_until"] == "2027-12-31"
    assert subs[0]["created_from_if"] == "IF-01"


def test_mt05_duplicate_subscription_is_idempotent():
    """MT-05: 同一対象×トリガー種別の購読作成は冪等（created=False が返る）。"""
    party_id_for_dup = _party_id
    # MT-03 で非アクティブ化済みなので、新たな party で実施
    ext_id_3 = f"CRM-MON-DUP-{uuid.uuid4().hex[:8].upper()}"
    legal_name_3 = f"重複テスト株式会社{uuid.uuid4().hex[:4]}"

    r = httpx.post(
        f"{BASE}/api/parties/resolve",
        json={
            "legal_name": legal_name_3,
            "country_code": "US",
            "party_type": "company",
            "source_system": "crm",
            "external_id": ext_id_3,
        },
    )
    assert r.status_code == 200, r.text
    dup_party_id = r.json()["party_id"]

    payload = {
        "subject_type": "party",
        "subject_id": dup_party_id,
        "trigger_type": "sanction_change",
    }
    r1 = httpx.post(f"{BASE}/api/monitoring/subscriptions", json=payload)
    assert r1.status_code == 201, r1.text
    assert r1.json()["created"] is True

    # 同一内容で再作成 → 冪等
    r2 = httpx.post(f"{BASE}/api/monitoring/subscriptions", json=payload)
    assert r2.status_code == 201, r2.text
    data2 = r2.json()
    assert data2["created"] is False
    assert data2["subscription"]["subject_id"] == dup_party_id


def test_mt06_deactivate_nonexistent():
    """MT-06: 存在しない購読 ID への DELETE → 404。"""
    fake_id = str(uuid.uuid4())
    r = httpx.delete(f"{BASE}/api/monitoring/subscriptions/{fake_id}")
    assert r.status_code == 404, r.text
