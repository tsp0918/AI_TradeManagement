"""review_key_hash 生成ユーティリティ。

同一内容の見積改訂に対して正式審査を省略（仮審査結果を継承）するために使用する。
同一ハッシュ + valid_until 未到来 → 正式審査で仮審査結果を自動継承。
"""
from __future__ import annotations

import hashlib
import json

# 取引金額のバケット分類（ハッシュに含めるのはバケットのみ、正確な金額は不要）
_VALUE_BUCKETS: list[tuple[float, str]] = [
    (10_000, "XS"),       # ~$10K
    (100_000, "S"),       # ~$100K
    (500_000, "M"),       # ~$500K
    (5_000_000, "L"),     # ~$5M
]


def _value_bucket(total_value_usd: float | None) -> str:
    """金額をバケット文字列に変換する。"""
    if total_value_usd is None:
        return "UNKNOWN"
    for threshold, label in _VALUE_BUCKETS:
        if total_value_usd < threshold:
            return label
    return "XL"


def compute_review_key_hash(
    product_codes: list[str],
    quantities: list[float],
    destination_country: str,
    end_user_party_id: str | None,
    end_use: str,
    total_value_usd: float | None,
) -> str:
    """審査キーの SHA256 ハッシュを生成して返す。

    以下のフィールドが全て同一であれば同一ハッシュを返す:
    - 品目コード（ソート済み）× 数量ペア
    - 仕向国
    - エンドユーザー（party_id、未設定は空文字）
    - 最終用途（正規化済み）
    - 取引金額バケット

    Args:
        product_codes: 品目コードのリスト
        quantities:    各品目の数量（product_codes と同順）
        destination_country: 仕向国 ISO alpha-2
        end_user_party_id:   エンドユーザー party ID（未設定 None）
        end_use:             最終用途説明
        total_value_usd:     取引総額 USD（None 可）

    Returns:
        64文字の SHA256 hex ダイジェスト
    """
    # 品目コード順でソートして (code, qty) ペアを正規化
    sorted_pairs = sorted(zip(product_codes, quantities), key=lambda p: p[0])

    key = json.dumps(
        {
            "products": [[code, qty] for code, qty in sorted_pairs],
            "dest": destination_country.upper() if destination_country else "",
            "end_user": end_user_party_id or "",
            "end_use": (end_use or "").strip().lower(),
            "value": _value_bucket(total_value_usd),
        },
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(key.encode()).hexdigest()
