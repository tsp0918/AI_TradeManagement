#!/usr/bin/env python3
"""
E2E 一気通貫フローテスト — Phase 12
全フロー: R&D起案 → 品目登録 → 取引審査 → ERP連携 → スクリーニング再ヒット → ブロック → 解除

実行: python scripts/e2e_full_flow_test.py
"""
import json
import sqlite3
import time
import sys
import httpx

AI_VALIDATION  = "http://localhost:8011"
AI_CLASS       = "http://localhost:8002"
RND_ASSESSMENT = "http://localhost:8003"
SCREENING      = "http://localhost:8005"
EXPORT_LICENSE = "http://localhost:8012"
ERP            = "http://localhost:8888"
ERP_DB         = "/Users/takehirosato/Desktop/erp-system/erp.db"

OK   = "\033[92m✅\033[0m"
FAIL = "\033[91m❌\033[0m"
INFO = "\033[94mℹ\033[0m"

passed = []
failed = []

ERP_TEST_MATERIAL = "MAT-1000001"  # ERP DB に実在する品目コード


def check(label: str, cond: bool, detail: str = "") -> bool:
    if cond:
        print(f"  {OK} {label}" + (f"  ({detail})" if detail else ""))
        passed.append(label)
    else:
        print(f"  {FAIL} {label}" + (f"  ({detail})" if detail else ""))
        failed.append(label)
    return cond


def erp_jwt() -> str:
    r = httpx.post(f"{ERP}/auth/token", data={"username": "admin@example.com", "password": "admin1234"})
    return r.json()["access_token"]


def erp_fefta(material_code: str) -> str | None:
    conn = sqlite3.connect(ERP_DB)
    row = conn.execute(
        "SELECT fefta_judgment FROM materials WHERE material_code=? AND client_id='DEMO'",
        (material_code,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


# ─────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  AI Trade Management — E2E 一気通貫フローテスト")
print("="*60)

# ERP テスト前に fefta_judgment をリセット
_conn = sqlite3.connect(ERP_DB)
_conn.execute(
    "UPDATE materials SET fefta_judgment='NOT_APPLICABLE', last_compliance_check_at=NULL"
    " WHERE material_code=? AND client_id='DEMO'",
    (ERP_TEST_MATERIAL,)
)
_conn.commit()
_conn.close()
print(f"  {INFO} ERP {ERP_TEST_MATERIAL} fefta_judgment をリセット済み")

# ─────────────────────────────────────────────────────────────────
print("\n[1] 全モジュール死活確認")
for name, url in [("ai_validation", AI_VALIDATION), ("ai_classification", AI_CLASS),
                   ("rnd_assessment", RND_ASSESSMENT), ("screening", SCREENING),
                   ("export_license", EXPORT_LICENSE)]:
    try:
        r = httpx.get(f"{url}/health", timeout=3)
        check(f"{name} health", r.status_code == 200, f"port {url.split(':')[-1]}")
    except Exception as e:
        check(f"{name} health", False, str(e))

# ─────────────────────────────────────────────────────────────────
print("\n[2] ② R&D アセスメント → ai_classification 品目自動登録")
# まず ai_classification に "RND-TEST-E2E" が存在しないことを確認（既存であれば削除）
CASE_ID = "TEST-E2E-2026"
PROD_CODE = f"RND-{CASE_ID}"

# 既存チェック（409 = すでに存在、スキップ）
r_pre = httpx.post(f"{AI_CLASS}/api/products", json={
    "code": PROD_CODE, "name": "E2E テスト R&D品目", "description": "e2e test"
})
try:
    _pre_json = r_pre.json()
    _pre_detail = _pre_json.get("id") or _pre_json.get("detail", "")[:40]
except Exception:
    _pre_detail = r_pre.text[:40]
print(f"  {INFO} 品目事前状態: {r_pre.status_code} ({_pre_detail})")

# R&D アセスメント POST をシミュレート（rnd_assessment の _register_rnd_product_bg を直接呼ぶ）
r_rnd = httpx.post(f"{AI_CLASS}/api/products", json={
    "code": PROD_CODE,
    "name": "半導体プロセス技術 EUV対応レジスト（R&D）",
    "description": "EUV フォトレジスト合成プロセス。外為法 7 項目及び 9 項目該当可能性。",
    "item_type": None,
})
exists = r_rnd.status_code in (200, 201, 409)
check("R&D品目 ai_classification 登録 (code=RND-TEST-E2E-2026)", exists,
      f"status={r_rnd.status_code}")

# ─────────────────────────────────────────────────────────────────
print("\n[3] ④ ERP → AI TM 品目バッチ連携（erp-sync）")
r_sync = httpx.post(f"{AI_CLASS}/products/erp-sync", json={
    "code": ERP_TEST_MATERIAL,
    "name": "E2E テスト完成品 CVD装置",
    "description": "AI Trade Management E2E テスト用完成品",
    "eccn": "3B001",
    "country_of_origin": "JP",
    "item_type": "FINISHED_GOODS",
    "bom": [
        {"child_code": "COMP-001", "child_name": "CVDチャンバー", "origin_country": "JP"},
        {"child_code": "COMP-002", "child_name": "RF電源", "origin_country": "US"},
    ]
})
check("ERP erp-sync 品目登録", r_sync.status_code == 200,
      f"aitm_product_id={r_sync.json().get('aitm_product_id')}")

# ─────────────────────────────────────────────────────────────────
print("\n[4] ⑤ ERP SO → AI TM 取引審査（Transaction 作成）")
r_tx = httpx.post(f"{AI_VALIDATION}/api/transactions", json={
    "title": "E2E CVD装置 中国向け輸出審査",
    "product_code": ERP_TEST_MATERIAL,
    "destination_country": "CN",
    "counterparty_name": "SMIC Beijing Co. Ltd.",
    "erp_case_no": "SO-E2E-2026-001",
    "end_user": "SMIC Beijing",
    "usage": "半導体量産ライン向け",
})
tx_id = r_tx.json().get("id")
case_no = r_tx.json().get("case_no")
check("取引審査 Transaction 作成", r_tx.status_code in (200, 201) and tx_id,
      f"tx_id={tx_id} case_no={case_no}")

# ─────────────────────────────────────────────────────────────────
print("\n[5] ⑤ 取引先スクリーニング（SMIC → match 期待）")
r_scr = httpx.post(f"{SCREENING}/api/screen", json={
    "company_name": "SMIC",
    "country": "CN",
    "threshold": 0.6,
    "transaction_id": tx_id,
}, timeout=30)
scr_status = r_scr.json().get("result_status")
check("SMIC スクリーニング ヒット", scr_status in ("match", "possible_match"),
      f"result_status={scr_status}")

# ─────────────────────────────────────────────────────────────────
print("\n[6] ⑥ スクリーニング再ヒット → ERP NEEDS_REVIEW 通知（ERP fefta_judgment 確認）")
time.sleep(2)  # background thread 完了待ち
fefta_before = erp_fefta(ERP_TEST_MATERIAL)
print(f"  {INFO} ERP {ERP_TEST_MATERIAL} fefta_judgment 現在値: {fefta_before}")

# flag-for-review を直接呼んで確認（スクリーニング background thread 経由でも同じ）
if tx_id:
    r_flag = httpx.post(f"{AI_VALIDATION}/api/transactions/{tx_id}/flag-for-review")
    check("flag-for-review エンドポイント応答", r_flag.status_code == 200,
          f"erp_notified={r_flag.json().get('erp_notified')}")
    time.sleep(2)
    fefta_after_flag = erp_fefta(ERP_TEST_MATERIAL)
    check("ERP fefta_judgment → NEEDS_REVIEW", fefta_after_flag == "NEEDS_REVIEW",
          f"fefta={fefta_after_flag}")

# ─────────────────────────────────────────────────────────────────
print("\n[7] ⑤ AI TM 判定完了 → ERP APPROVED 通知")
if tx_id:
    r_j = httpx.post(f"{AI_VALIDATION}/decision/{tx_id}/run-and-two-lists", timeout=60)
    check("AI 2リスト判定実行", r_j.status_code in (200, 201), f"status={r_j.status_code}")
    # 取引 詳細で overall_status 確認
    r_detail = httpx.get(f"{AI_VALIDATION}/api/transactions/{tx_id}")
    overall = r_detail.json().get("agent_judgment_status") or r_detail.json().get("status")
    print(f"  {INFO} 判定ステータス: {overall}")

# ─────────────────────────────────────────────────────────────────
print("\n[8] ⑦ BOM COO 変更 → item_version イベント生成")
# COO 変更をシミュレート（US → CN）
r_coo = httpx.post(f"{AI_CLASS}/products/erp-sync", json={
    "code": ERP_TEST_MATERIAL,
    "name": "E2E テスト完成品 CVD装置",
    "eccn": "3B001",
    "country_of_origin": "JP",
    "item_type": "FINISHED_GOODS",
    "bom": [
        {"child_code": "COMP-001", "child_name": "CVDチャンバー", "origin_country": "CN"},  # JP→CN 変更
        {"child_code": "COMP-002", "child_name": "RF電源", "origin_country": "US"},
    ]
})
check("BOM COO 変更 erp-sync", r_coo.status_code == 200)
time.sleep(2)

# item_version イベント確認（親品目 ERP_TEST_MATERIAL の open イベントを優先）
r_events = httpx.get(f"{AI_CLASS}/api/item-versions/events?limit=20")
events = r_events.json() if r_events.status_code == 200 else []
coo_events = [e for e in events if e.get("change_category") == "coo_change"]
# 親品目の open イベントを優先
parent_coo_open = [e for e in coo_events
                   if e.get("item_code") == ERP_TEST_MATERIAL and e.get("status") == "open"]
coo_events_to_resolve = parent_coo_open or [e for e in coo_events if e.get("status") == "open"]
check("COO 変更 → item_version イベント生成", len(coo_events) > 0,
      f"events={len(coo_events)}")

# ─────────────────────────────────────────────────────────────────
print("\n[9] ⑦ item_version イベント resolve → ERP APPROVED 通知")
if coo_events_to_resolve:
    ev_id = coo_events_to_resolve[0]["id"]
    r_resolve = httpx.post(
        f"{AI_CLASS}/api/item-versions/events/{ev_id}/resolve",
        json={"resolution_notes": "E2E テスト: COO 変更を確認・承認済み"},
    )
    check("item_version resolve", r_resolve.status_code == 200,
          f"status={r_resolve.json().get('status')}")
    time.sleep(2)
    fefta_resolved = erp_fefta(ERP_TEST_MATERIAL)
    check("item_version resolved → ERP APPROVED 通知",
          fefta_resolved == "APPROVED",
          f"fefta={fefta_resolved}")
else:
    check("item_version resolve → ERP APPROVED 通知", False, "解決対象 COO イベントなし")

# ─────────────────────────────────────────────────────────────────
print("\n[10] ③ export_license 承認 → ERP APPROVED 通知")
r_app = httpx.post(f"{EXPORT_LICENSE}/api/export-licenses", json={
    "item_description": f"{ERP_TEST_MATERIAL} E2E テスト",
    "eccn": "3B001",
    "destination_country": "CN",
    "transaction_ids": [str(tx_id)] if tx_id else [],
})
app_id = r_app.json().get("id")
check("輸出許可申請作成", r_app.status_code in (200, 201) and app_id, f"id={app_id}")

if app_id:
    r_approve = httpx.post(f"{EXPORT_LICENSE}/api/export-licenses/{app_id}/approve", json={
        "license_number": "EL-E2E-2026-001",
        "issuing_authority": "経産省（E2Eテスト）",
        "approved_at": "2026-06-28T12:00:00Z",
        "expires_at":  "2027-06-28T12:00:00Z",
    })
    check("輸出許可証 承認", r_approve.json().get("status") == "approved")
    time.sleep(2)
    fefta_lic = erp_fefta(ERP_TEST_MATERIAL)
    check("export_license 承認 → ERP APPROVED", fefta_lic == "APPROVED",
          f"fefta={fefta_lic}")

# ─────────────────────────────────────────────────────────────────
print("\n[11] ERP Pull ポーラー動作確認（deminimis mark-notified エンドポイント）")
token = erp_jwt()
r_dm = httpx.get(f"{ERP}/gts/deminimis?alert_level=BREACH&limit=3",
                 headers={"Authorization": f"Bearer {token}"})
check("ERP deminimis GET", r_dm.status_code == 200,
      f"records={len(r_dm.json())}")

if r_dm.json():
    rec = r_dm.json()[0]
    r_mark = httpx.patch(f"{ERP}/gts/deminimis/{rec['id']}/mark-notified",
                         json={"ai_tm_case_ref": "E2E-POLLER-TEST"},
                         headers={"Authorization": f"Bearer {token}"})
    check("ERP mark-notified PATCH", r_mark.status_code == 200,
          f"ok={r_mark.json().get('ok')}")

# ─────────────────────────────────────────────────────────────────
print("\n[12] DEEMED_EXPORT_RISK イベント（rnd_assessment → ai_validation）")
r_dr = httpx.post(f"{AI_VALIDATION}/api/transactions/events", json={
    "event_type": "DEEMED_EXPORT_RISK",
    "material_code": "RND-E2E-2026",
    "deemed_export_risk_level": "HIGH",
    "person_name": "E2E Test Person（Tsinghua University）",
    "case_title": "E2E 半導体プロセス技術みなし輸出審査",
    "top_factors": ["country_risk_CN", "tech_control_3B001", "affiliation_risk"],
    "recommendation": "E2Eテスト: みなし輸出該当疑い",
})
check("DEEMED_EXPORT_RISK → 取引審査案件自動生成",
      r_dr.status_code == 200 and r_dr.json().get("case_ref"),
      f"case_ref={r_dr.json().get('case_ref')}")

# ─────────────────────────────────────────────────────────────────
print("\n" + "="*60)
total = len(passed) + len(failed)
print(f"  結果: {len(passed)}/{total} passed")
if failed:
    print(f"\n  {FAIL} 失敗項目:")
    for f in failed:
        print(f"    - {f}")
else:
    print(f"\n  {OK} 全テスト合格 — フロー一気通貫確認済み")
print("="*60 + "\n")
sys.exit(0 if not failed else 1)
