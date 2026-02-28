"""
usage_extract ステップ — 手動入力モード

役割:
  - DB に既存の UsageRequirement (source=core) を確認・集計して返す
  - 手動入力された用途テキストがあればそのまま利用（AIによる自動抽出の代替）
  - 将来は LLM で TransactionItem.spec_text から自動抽出する実装に置き換え可能

出力:
  {
    "step": "usage_extract",
    "mode": "manual",
    "core_count": N,      # source=core の usage_requirements 件数
    "total_count": N,     # 全 usage_requirements 件数
    "usage_ids": [...]    # id のリスト
  }
"""
from typing import Dict, Any

from sqlalchemy.orm import Session

from app.db.models.transaction import UsageRequirement, UsageSource


def step_usage_extract(
    db: Session,
    transaction_id: int,
    run_id: int,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    手動入力モード: DB に既存の UsageRequirement を確認して返す。

    将来の AI 自動抽出実装への移行パス:
      1. TransactionItem.spec_text を LLM に渡して用途要件を抽出
      2. 抽出結果を UsageRequirement (source=core, created_by=ai) として INSERT
      3. 既存の手動エントリと重複しない場合のみ追加
    """
    urs = (
        db.query(UsageRequirement)
        .filter(UsageRequirement.transaction_id == transaction_id)
        .all()
    )

    core_ids = [u.id for u in urs if u.source == UsageSource.core.value]
    all_ids  = [u.id for u in urs]

    return {
        "step":        "usage_extract",
        "mode":        "manual",
        "core_count":  len(core_ids),
        "total_count": len(all_ids),
        "usage_ids":   all_ids,
    }
