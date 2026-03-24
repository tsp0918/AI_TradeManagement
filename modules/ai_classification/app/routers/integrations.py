# app/routers/integrations.py
from __future__ import annotations

import json
import pathlib
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, List, Optional, Union

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Product
from ..settings import settings
from ..services.external_app_client import ExternalAppClient

router = APIRouter(prefix="/integrations", tags=["integrations"])

# ─────────────────────────────────────────────────────────────────────
#  HS → ECCN / FEFTA 付加マッピング
# ─────────────────────────────────────────────────────────────────────

# HS heading (4桁) → 推定ECCN (US EAR CCL) 対照表
# 主要な二重用途品目のみ収録。完全ではなく参考用。
_HS_HEADING_TO_ECCN: dict[str, str] = {
    # Cat 0 Nuclear
    "2844": "0C001",  # 放射性元素
    # Cat 1 Materials
    "2818": "1C010",  # アルミナ
    "3802": "1C117",  # 活性炭
    "3818": "3C001",  # ドープシリコン（半導体基板）
    "7228": "1C117",  # 特殊鋼
    # Cat 2 Materials processing
    "8418": "2B350",  # 冷凍装置
    "8419": "2B350",  # 処理装置
    "8421": "2B352",  # 遠心分離機
    "8458": "2B001",  # 旋盤（CNC）
    "8459": "2B001",  # フライス盤
    "8461": "2B001",  # 形削り盤
    "8462": "2B001",  # 鍛造・プレス機
    "8466": "2B001",  # 工作機械部品
    "8481": "2A226",  # バルブ
    # Cat 3 Electronics
    "8486": "3B001",  # 半導体製造装置
    "8541": "3A001",  # 半導体デバイス（ダイオード/トランジスタ）
    "8542": "3A001",  # 電子集積回路（IC）
    "8543": "3A001",  # その他電気機械
    "8536": "3A001",  # 電気回路部品
    # Cat 4 Computers
    "8471": "4A003",  # コンピュータ
    "8473": "4A003",  # コンピュータ部品
    # Cat 5 Telecom
    "8517": "5A002",  # 通信機器
    "8526": "6A008",  # レーダー
    "8529": "6A008",  # レーダー部品
    # Cat 6 Sensors/Lasers
    "9001": "6A002",  # 光ファイバー
    "9002": "6A002",  # 光学レンズ
    "9003": "6A002",  # メガネフレーム（光学用途）
    "9005": "6A003",  # 双眼鏡/望遠鏡
    "9012": "6A001",  # 顕微鏡
    "9013": "6A005",  # レーザー / 液晶
    "9014": "7A001",  # 航法計器
    "9015": "7A002",  # 測量機器
    # Cat 7 Navigation
    "8802": "9A610",  # 航空機
    "8804": "1C111",  # パラシュート
    # Cat 9 Aerospace
    "8801": "9A610",  # 気球
    "8807": "9A610",  # 航空機部品
    # Chemicals
    "2903": "1C350",  # ハロゲン化炭化水素
    "3002": "1C351",  # 血液/ワクチン
    "3824": "1C350",  # 化学調製品
}

# HS chapter (2桁) → ECCN フォールバック（heading でヒットしない場合）
_HS_CHAPTER_TO_ECCN: dict[str, str] = {
    "28": "1C350",   # 無機化学品
    "29": "1C350",   # 有機化学品
    "30": "1C351",   # 医薬品
    "38": "1C117",   # 各種化学工業製品
    "84": "2B001",   # 機械（汎用）
    "85": "3A001",   # 電気機器（汎用）
    "90": "6A002",   # 光学・精密機器（汎用）
    "93": "USML",    # 武器（USML 対象）
}

_STAGING_DIR = pathlib.Path(__file__).resolve().parents[4] / "data" / "staging"
_HS_FEFTA_REVERSE: dict[str, list] = {}


@lru_cache(maxsize=1)
def _load_hs_fefta_reverse() -> dict[str, list]:
    """HS → FEFTA 逆引きインデックスをロード（起動後初回のみ）。"""
    path = _STAGING_DIR / "hs_fefta_mapping_v2.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("reverse_index", {})


def _lookup_eccn(hs_code: str) -> str:
    """HS コードから推定 ECCN を返す。マッチしない場合は 'EAR99'。"""
    heading = hs_code[:4]
    chapter = hs_code[:2]
    return (
        _HS_HEADING_TO_ECCN.get(heading)
        or _HS_CHAPTER_TO_ECCN.get(chapter)
        or "EAR99"
    )


def _lookup_fefta(hs_code: str) -> list[dict]:
    """HS コードから外為法別表第一の紐付け情報を返す。"""
    rev = _load_hs_fefta_reverse()
    # exact match
    if hs_code in rev:
        return rev[hs_code]
    # try heading (4-digit prefix)
    heading = hs_code[:4]
    if heading in rev:
        return rev[heading]
    return []


def _enrich_candidates(candidates: list[dict]) -> list[dict]:
    """FAISS 結果候補に ECCN と FEFTA 情報を付加する。"""
    enriched = []
    for c in candidates:
        hs = c.get("hs_code", "")
        eccn = _lookup_eccn(hs)
        fefta_items = _lookup_fefta(hs)
        fefta_ref = ""
        if fefta_items:
            refs = [f"EL-{fi['fefta_item_no']}({fi['fefta_category_label']})" for fi in fefta_items]
            fefta_ref = " / ".join(refs)
        enriched.append({
            **c,
            "eccn":      eccn,
            "fefta_ref": fefta_ref,
            "fefta_items": fefta_items,
        })
    return enriched


def _rerank_by_heading(candidates: list[dict], top_n: int = 5) -> list[dict]:
    """同一 heading (HS 4桁) 単位でクラスタリング再ランキングする。

    FAISS の embed_text に日本語 FEFTA ラベルが含まれるため、
    heading を跨いで重複ヒットが起きる問題を軽減する。
    同一 heading で複数ヒットした場合はカウントボーナスを加算し、
    各 heading の代表候補（最高スコア）だけを上位 top_n 件に絞る。
    """
    from collections import defaultdict
    heading_data: dict[str, list] = defaultdict(list)
    for c in candidates:
        heading = c.get("heading") or c.get("hs_code", "")[:4]
        heading_data[heading].append(c)

    ranked_headings: list[tuple[float, str]] = []
    for heading, items in heading_data.items():
        best_score = max(i.get("similarity_score", 0) for i in items)
        ranked_headings.append((best_score, heading))

    ranked_headings.sort(key=lambda x: x[0], reverse=True)

    result = []
    for rank, (_, heading) in enumerate(ranked_headings[:top_n], start=1):
        best = max(heading_data[heading], key=lambda i: i.get("similarity_score", 0))
        result.append({**best, "rank": rank})
    return result


class _HsFormValues(BaseModel):
    """モーダルから送られる現在のフォーム値（未保存でも最新値を使う）。"""
    name:        Optional[str] = None
    description: Optional[str] = None
    item_class:  Optional[str] = None


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _normalize_request_id(v: Any) -> Optional[Union[int, str]]:
    """
    AI側は request_id を int で返す例があるため、型揺れを吸収する。
    破壊的変換はしない（int化できればint、無理ならstr、空ならNone）。
    """
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        # "10" のような文字列なら int に寄せる
        if s.isdigit():
            try:
                return int(s)
            except Exception:
                return s
        return s
    # その他の型は文字列化
    return str(v)


def _wrap_payload(request_id: Any, payload_obj: Any) -> str:
    """
    UI側の external_eval_payload は常にこのwrapper形式に揃える。
    {
      "request_id": <int|str|null>,
      "payload": <dict|null>
    }
    """
    return _json_dumps(
        {
            "request_id": _normalize_request_id(request_id),
            "payload": payload_obj,
        }
    )


@router.post("/export-control/request/{product_id}")
async def request_export_control(product_id: int, db: Session = Depends(get_db)):
    """
    UI -> 外部AIへ判定依頼をPOSTする。
    外部AIが落ちていても UI が落ちないように queued を記録して返す。
    """
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # 外部AI仕様（AI側申し送りに合わせる）
    payload: Dict[str, Any] = {
        "product_id": product.id,
        "code": product.code,
        "name": product.name,
        # description が最重要。NoneだとAI側が困るので空文字に寄せる。
        "description": product.description or "",
        "usage_summary": product.usage_summary or "",
        "hs_code": product.hs_code,
        "eccn": product.eccn,
        "item_class": product.item_class,
        "bom_json": product.bom_json,
        "regulation_ai_raw": product.regulation_ai_raw,
        "callback_webhook": settings.PUBLIC_WEBHOOK_URL,  # UI側Webhook URL
        # 審査連鎖フィールド（R&D 由来の場合に自動付与）
        "source_module": "ai_classification",
        "rnd_case_id": product.source_rnd_case_id,
        "rnd_transaction_id": product.source_rnd_transaction_id,
    }

    client = ExternalAppClient()

    try:
        resp = await client.request_export_control(payload)  # 202(JSON)を想定

        product.external_eval_requested_at = datetime.utcnow()
        product.external_eval_status = (resp.get("status") or "queued")
        # この時点でreasonは返らない想定なので、軽い説明のみ
        product.external_eval_reason = "External request accepted (queued)."

        # request_id をDB追加なしで保持する（payload wrapper で統一）
        product.external_eval_payload = _wrap_payload(
            resp.get("request_id"),
            {
                "status": resp.get("status", "queued"),
                "received_at": resp.get("received_at"),
            },
        )

        db.commit()
        return {"ok": True, "sent": True, "external_response": resp}

    except httpx.ConnectError:
        product.external_eval_requested_at = datetime.utcnow()
        product.external_eval_status = "queued"
        product.external_eval_reason = "External app is not reachable (connection failed)."
        # payloadも「wrapper形式」で統一（UI表示がブレない）
        product.external_eval_payload = _wrap_payload(None, None)
        db.commit()
        return {
            "ok": True,
            "sent": False,
            "status": "queued",
            "detail": "External app not reachable. Please start external app or fix EXTERNAL_APP_BASE_URL.",
        }

    except httpx.HTTPStatusError as e:
        # 外部アプリが非2xxを返した場合（テーブル未作成など）
        status_code = e.response.status_code
        try:
            body_text = e.response.text[:500]
        except Exception:
            body_text = "(unable to read response body)"
        reason = f"External app returned HTTP {status_code}: {body_text}"
        product.external_eval_requested_at = datetime.utcnow()
        product.external_eval_status = "error"
        product.external_eval_reason = reason
        product.external_eval_payload = _wrap_payload(None, None)
        db.commit()
        return {
            "ok": False,
            "sent": True,
            "status": "error",
            "detail": f"External app returned HTTP {status_code}",
            "external_error": body_text,
        }

    except httpx.HTTPError as e:
        product.external_eval_requested_at = datetime.utcnow()
        product.external_eval_status = "error"
        product.external_eval_reason = f"External app HTTP error: {type(e).__name__}: {e}"
        product.external_eval_payload = _wrap_payload(None, None)
        db.commit()
        return {
            "ok": False,
            "sent": False,
            "status": "error",
            "detail": f"External app HTTP error: {type(e).__name__}",
        }


@router.post("/export-control/webhook")
async def export_control_webhook(body: dict, db: Session = Depends(get_db)):
    """
    外部AI -> UI(Webhook) に判定結果をPOSTしてもらう。

    仕様（AI側確定版）:
    {
      "product_id": 123,
      "request_id": 10,
      "status": "needs_review",
      "reason": "...",
      "payload": {...}
    }

    冪等性:
    - 同じ request_id の webhook が複数回届く可能性あり
    - UI側は「最新の内容で上書き」する（Product行を更新）
    """
    product_id = body.get("product_id")
    if product_id is None:
        raise HTTPException(status_code=400, detail="product_id is required")

    try:
        pid = int(product_id)
    except Exception:
        raise HTTPException(status_code=400, detail="product_id must be an integer")

    product = db.get(Product, pid)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    status = body.get("status")
    reason = body.get("reason")
    payload_obj = body.get("payload")
    request_id = body.get("request_id")

    # 保存ルール（外部仕様に完全一致）
    if status is not None:
        product.external_eval_status = str(status)
    product.external_eval_reason = (str(reason) if reason is not None else None)

    # external_eval_payload は wrapper 形式で統一（request_id + payload）
    product.external_eval_payload = _wrap_payload(request_id, payload_obj)

    product.external_eval_received_at = datetime.utcnow()

    # ai_validation で作成された transaction_id を保存（証跡連鎖用）
    tx_id = (payload_obj or {}).get("transaction_id") if isinstance(payload_obj, dict) else None
    if tx_id is not None:
        try:
            product.ui_validation_transaction_id = int(tx_id)
        except (ValueError, TypeError):
            pass

    db.commit()
    return {"ok": True}


class ProductFromRndIn(BaseModel):
    case_id:            str
    case_title:         str
    rnd_transaction_id: Optional[int] = None
    usage_summary:      str = ""
    product_code:       Optional[str] = None  # 省略時は RND-{case_id[:8]} で自動生成


class ProductFromRndOut(BaseModel):
    product_id:        int
    product_code:      str
    export_request_id: Optional[int] = None


@router.post("/products/from-rnd", response_model=ProductFromRndOut)
async def create_product_from_rnd(req: ProductFromRndIn, db: Session = Depends(get_db)):
    """R&D案件（rnd_assessment）からワンクリックで品目を登録し、即座に外部AI判定を発火する。"""
    code = (req.product_code or f"RND-{req.case_id[:8]}").strip()

    # 重複コードの場合はサフィックスを付ける
    existing = db.query(Product).filter(Product.code == code).first()
    if existing:
        from datetime import datetime as _dt
        code = f"{code}-{_dt.utcnow().strftime('%H%M%S')}"

    product = Product(
        code=code,
        name=req.case_title,
        usage_summary=req.usage_summary or "",
        source_rnd_case_id=req.case_id,
        source_rnd_transaction_id=req.rnd_transaction_id,
        export_control_status="not_evaluated",
    )
    db.add(product)
    db.commit()
    db.refresh(product)

    # 即時 export-control request を発火
    client = ExternalAppClient()
    payload: Dict[str, Any] = {
        "product_id": product.id,
        "code": product.code,
        "name": product.name,
        "description": "",
        "usage_summary": product.usage_summary or "",
        "callback_webhook": settings.PUBLIC_WEBHOOK_URL,
        "source_module": "ai_classification",
        "rnd_case_id": req.case_id,
        "rnd_transaction_id": req.rnd_transaction_id,
    }
    export_request_id: Optional[int] = None
    try:
        resp = await client.request_export_control(payload)
        export_request_id = resp.get("request_id")
        product.external_eval_requested_at = datetime.utcnow()
        product.external_eval_status = resp.get("status") or "queued"
        product.external_eval_payload = _wrap_payload(export_request_id, {"status": "queued"})
        db.commit()
    except Exception:
        pass  # 外部AI発火失敗は非致命的 — 品目登録は成功扱い

    return ProductFromRndOut(
        product_id=product.id,
        product_code=product.code,
        export_request_id=export_request_id,
    )


@router.get("/export-control/result/{product_id}")
def get_export_control_result(product_id: int, db: Session = Depends(get_db)):
    """
    UI側に保存されている外部AI判定結果をGET
    （画面でJSON確認する用途）
    """
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return {
        "product_id": product.id,
        "code": product.code,
        "external_eval_status": product.external_eval_status,
        "external_eval_reason": product.external_eval_reason,
        "external_eval_payload": product.external_eval_payload,
        "requested_at": product.external_eval_requested_at,
        "received_at": product.external_eval_received_at,
        "ui_validation_transaction_id": product.ui_validation_transaction_id,
    }


# ─────────────────────────────────────────────────────────────────────
#  HSコード判定 連携
# ─────────────────────────────────────────────────────────────────────

@router.post("/hs-classifier/request/{product_id}")
async def request_hs_classification(
    product_id: int,
    form_values: _HsFormValues = Body(default_factory=_HsFormValues),
    db: Session = Depends(get_db),
):
    """品目詳細画面から hs_classifier モジュールへ HSコード判定を依頼する（同期検索）。

    form_values が送られた場合はその値を優先（未保存の入力にも対応）。
    /classify (Webhook 非同期) ではなく /search (同期) を呼び出して
    即座に結果をDBに保存する。
    """
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # フォーム値を優先、なければDB値にフォールバック
    name  = (form_values.name        or "").strip() or (product.name        or "")
    desc  = (form_values.description or "").strip() or (product.description or "")
    cls   = (form_values.item_class  or "").strip() or (product.item_class  or "")

    # 検索クエリ: 品目名 + 説明 + 品目クラスを組み合わせる
    parts = [p for p in [name, desc, cls] if p]
    query = " ".join(parts).strip()
    if not query:
        return {
            "ok":     False,
            "detail": "品目名と説明が未入力です。入力してから判定を依頼してください。",
        }

    hs_url = settings.HS_CLASSIFIER_BASE_URL.rstrip("/") + "/search"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(hs_url, params={"q": query, "top_k": 15})
            r.raise_for_status()

        data = r.json()
        results = _rerank_by_heading(_enrich_candidates(data.get("results", [])), top_n=5)

        product.hs_classification_status = "completed"
        product.hs_classification_result = _json_dumps(results)
        product.hs_classified_at         = datetime.utcnow()
        product.hs_request_id            = None
        db.commit()
        return {"ok": True, "count": len(results)}

    except httpx.ConnectError:
        return {
            "ok":     False,
            "detail": f"HSコード判定モジュールに接続できません ({settings.HS_CLASSIFIER_BASE_URL})。起動しているか確認してください。",
        }
    except httpx.HTTPStatusError as e:
        return {"ok": False, "detail": f"HSコード判定モジュールがエラーを返しました: HTTP {e.response.status_code}"}
    except httpx.HTTPError as e:
        return {"ok": False, "detail": f"HTTPエラー: {type(e).__name__}"}


@router.post("/hs-classifier/webhook")
async def hs_classifier_webhook(body: dict, db: Session = Depends(get_db)):
    """hs_classifier モジュールから結果を受け取る Webhook エンドポイント。

    hs_classifier が POST する ClassifyResult 形式:
    {
      "request_id": "...",
      "item_id":    "123",         ← product_id (文字列)
      "status":     "completed",
      "classified_at": "...",
      "results":    [...],         ← list[HSCandidate]
      "error":      null
    }
    """
    item_id = body.get("item_id")
    if item_id is None:
        raise HTTPException(status_code=400, detail="item_id is required")

    try:
        product_id = int(item_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="item_id must be an integer string")

    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    status = body.get("status", "completed")
    product.hs_classification_status = status

    if status == "completed":
        results = _enrich_candidates(body.get("results") or [])
        product.hs_classification_result = _json_dumps(results)

        classified_at_raw = body.get("classified_at")
        if classified_at_raw:
            from datetime import datetime as _dt
            try:
                product.hs_classified_at = _dt.fromisoformat(classified_at_raw.replace("Z", "+00:00"))
            except Exception:
                product.hs_classified_at = datetime.utcnow()
        else:
            product.hs_classified_at = datetime.utcnow()

        # HSコードの最上位候補を hs_code カラムに反映するかはユーザー判断なので、
        # ここでは自動更新しない（UIから手動確定）
    else:
        # failed
        product.hs_classified_at = datetime.utcnow()

    db.commit()
    return {"ok": True}
