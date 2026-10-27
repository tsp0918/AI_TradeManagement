#!/usr/bin/env python3
"""CRM連携用 webhook_endpoint 5件を platform-core に登録する。

使い方:
    python scripts/register_crm_webhooks.py

必要な環境変数 (.env から自動読み込み):
    CRM_BASE_URL                  — CRM ベース URL（例: http://localhost:7500）
    AITM_REVIEW_WEBHOOK_SECRET    — review.judgment.completed 署名鍵
    AITM_REVIEW_WEBHOOK_BEARER    — review.judgment.completed Bearer トークン
    AITM_OVERRIDE_WEBHOOK_SECRET  — override.applied 署名鍵
    AITM_OVERRIDE_WEBHOOK_BEARER  — override.applied Bearer トークン
    AITM_PARTY_WEBHOOK_SECRET     — party.linked / screening.alert 署名鍵
    AITM_PARTY_WEBHOOK_BEARER     — party.linked / screening.alert Bearer トークン
    AITM_SANCTIONS_WEBHOOK_SECRET — sanctions.sync.completed 署名鍵
    AITM_SANCTIONS_WEBHOOK_BEARER — sanctions.sync.completed Bearer トークン

鍵のローテーション:
    鍵を変えるときは DELETE /api/webhooks/endpoints/<id> で既存行を削除してから
    このスクリプトを再実行してください（冪等チェックは URL + event_type のみで、
    bearer / signing_secret は含まれません）。

値の正:
    CRM 側の simulation/src/webhook_secrets.py が正なので、片方だけ変更しないこと。
"""
import os
import sys
import httpx
from pathlib import Path

# .env を自動読み込み
env_path = Path(__file__).parents[1] / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

PLATFORM_BASE = os.environ.get("MODULE_PLATFORM_URL", "http://localhost:8000")
CRM_BASE = os.environ.get("CRM_BASE_URL", "")

ENDPOINTS = [
    {
        "event_type": "review.judgment.completed",
        "url_path": "/webhooks/aitm/review-result",
        "secret_env": "AITM_REVIEW_WEBHOOK_SECRET",
        "bearer_env": "AITM_REVIEW_WEBHOOK_BEARER",
    },
    {
        "event_type": "override.applied",
        "url_path": "/webhooks/override-applied",
        "secret_env": "AITM_OVERRIDE_WEBHOOK_SECRET",
        "bearer_env": "AITM_OVERRIDE_WEBHOOK_BEARER",
    },
    {
        "event_type": "party.linked",
        "url_path": "/webhooks/aitm/party-event",
        "secret_env": "AITM_PARTY_WEBHOOK_SECRET",
        "bearer_env": "AITM_PARTY_WEBHOOK_BEARER",
    },
    {
        "event_type": "screening.alert",
        "url_path": "/webhooks/aitm/party-event",
        "secret_env": "AITM_PARTY_WEBHOOK_SECRET",
        "bearer_env": "AITM_PARTY_WEBHOOK_BEARER",
    },
    {
        "event_type": "sanctions.sync.completed",
        "url_path": "/webhooks/sanctions-sync-completed",
        "secret_env": "AITM_SANCTIONS_WEBHOOK_SECRET",
        "bearer_env": "AITM_SANCTIONS_WEBHOOK_BEARER",
    },
]


def main() -> None:
    if not CRM_BASE:
        print("ERROR: CRM_BASE_URL が未設定です。.env に CRM_BASE_URL=https://... を追加してください。")
        sys.exit(1)

    success = 0
    with httpx.Client(base_url=PLATFORM_BASE, timeout=10) as client:
        for ep in ENDPOINTS:
            signing_secret = os.environ.get(ep["secret_env"], "")
            bearer = os.environ.get(ep["bearer_env"], "")
            url = CRM_BASE.rstrip("/") + ep["url_path"]

            if not signing_secret and not bearer:
                print(f"WARN  {ep['event_type']:35s} → signing_secret / bearer がどちらも未設定です")

            body: dict = {
                "url": url,
                "event_type": ep["event_type"],
                "target_system": "crm",
                "signing_secret": signing_secret or "placeholder",
            }
            if bearer:
                body["bearer"] = bearer

            resp = client.post("/api/webhooks/endpoints", json=body)
            if resp.status_code in (200, 201):
                data = resp.json()
                print(f"  OK  {ep['event_type']:35s} → {url}  (id={data.get('id', '?')})")
                success += 1
            elif resp.status_code == 409:
                print(f"SKIP  {ep['event_type']:35s} → 既存レコードあり（409）")
                success += 1
            else:
                print(f"FAIL  {ep['event_type']:35s} → {resp.status_code} {resp.text}")

    print(f"\n{success}/{len(ENDPOINTS)} 件登録完了")
    if success < len(ENDPOINTS):
        sys.exit(1)


if __name__ == "__main__":
    main()
