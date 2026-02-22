# scripts/seed_data.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.db.models.transaction import Transaction, TransactionItem, UsageRequirement
from app.db.models.patent import Patent, PatentUsecase
from app.db.models.matrix import MatrixRule

CORE = "core"
EXPANDED = "expanded"


def _safe_set(obj: Any, key: str, value: Any) -> None:
    if hasattr(obj, key):
        setattr(obj, key, value)


def _get_or_create_transaction(db: Session, case_no: str, title: str, status: str) -> Transaction:
    tx = db.query(Transaction).filter(Transaction.case_no == case_no).first()
    if not tx:
        tx = Transaction(case_no=case_no, title=title, status=status)
        db.add(tx)
        db.flush()
    else:
        _safe_set(tx, "title", title)
        _safe_set(tx, "status", status)
    return tx


def _get_or_create_item(
    db: Session,
    tx: Transaction,
    item_name: str,
    item_model: str,
    spec_text: str,
    attachments_meta: Dict[str, Any],
) -> TransactionItem:
    item = (
        db.query(TransactionItem)
        .filter(TransactionItem.transaction_id == tx.id,
                TransactionItem.item_name == item_name)
        .first()
    )
    if not item:
        item = TransactionItem(
            transaction_id=tx.id,
            item_name=item_name,
            item_model=item_model,
            spec_text=spec_text,
            attachments_meta=attachments_meta,
        )
        db.add(item)
        db.flush()
    else:
        _safe_set(item, "item_model", item_model)
        _safe_set(item, "spec_text", spec_text)
    return item


def _ensure_usage(
    db: Session,
    tx: Transaction,
    item: TransactionItem,
    source: str,
    text: str,
    risk_tags: List[str],
    confidence: Optional[float],
):
    text = text.strip()
    if not text:
        return

    exists = (
        db.query(UsageRequirement)
        .filter(
            UsageRequirement.transaction_id == tx.id,
            UsageRequirement.source == source,
            UsageRequirement.text == text,
        )
        .first()
    )
    if exists:
        return

    u = UsageRequirement(
        transaction_id=tx.id,
        source=source,
        text=text,
        risk_tags=risk_tags,
        created_by="user",
    )
    _safe_set(u, "transaction_item_id", item.id)
    _safe_set(u, "confidence", confidence)

    db.add(u)


def _get_or_create_patent(
    db: Session,
    pub: str,
    title: str,
    who: str,
    abstract: str,
    ipc: str,
    url: Optional[str],
) -> Patent:
    p = db.query(Patent).filter(Patent.publication_number == pub).first()
    if not p:
        p = Patent(publication_number=pub)
        db.add(p)
        db.flush()

    _safe_set(p, "title", title)
    _safe_set(p, "assignee", who)
    _safe_set(p, "applicant", who)
    _safe_set(p, "abstract", abstract)
    _safe_set(p, "usage_detail", abstract)
    _safe_set(p, "ipc_codes_raw", ipc)
    _safe_set(p, "source_url", url)

    return p


def _ensure_patent_usecase(db: Session, p: Patent, text: str) -> None:
    exists = (
        db.query(PatentUsecase)
        .filter(PatentUsecase.patent_id == p.id,
                PatentUsecase.usecase_text == text)
        .first()
    )
    if exists:
        return

    pu = PatentUsecase(
        patent_id=p.id,
        usecase_text=text,
        extraction_method="manual",
        quality_score=0.9,
    )
    db.add(pu)


def _ensure_rule(db: Session, regime: str, item_no: str, title: str, requirement_text: str):
    exists = (
        db.query(MatrixRule)
        .filter(MatrixRule.regime == regime,
                MatrixRule.item_no == item_no,
                MatrixRule.version.is_(None))
        .first()
    )
    if exists:
        return

    db.add(
        MatrixRule(
            regime=regime,
            list_name="SeedList",
            item_no=item_no,
            title=title,
            requirement_text=requirement_text,
        )
    )


def upsert_min_seed(db: Session) -> None:
    base_time = datetime.utcnow() - timedelta(days=1)

    tx_specs = [
        {
            "case_no": "TX-0001",
            "title": "Seed: Lithography material export review (KrF photoresist)",
            "item": ("Photoresist (KrF)", "KR-PR-100"),
            "spec": "KrF露光用フォトレジスト。微細加工用途。",
            "usages": [
                (CORE, "KrFエキシマレーザー露光を用いた半導体微細加工用レジスト材料として使用", ["semiconductor_mfg"], None),
                (EXPANDED, "微細加工向けリソグラフィ工程で使用される感光性樹脂", ["semiconductor_mfg"], 0.72),
            ],
        },
        {
            "case_no": "TX-0002",
            "title": "Seed: Semiconductor equipment export review (stage)",
            "item": ("Lithography wafer stage", "STG-200"),
            "spec": "半導体露光装置用ウェハステージ。",
            "usages": [
                (CORE, "半導体露光装置向けウェハ位置決め用の高精度ステージとして使用", ["semiconductor_equipment"], None),
            ],
        },
        {
            "case_no": "TX-0003",
            "title": "Seed: Device export review (MCU)",
            "item": ("Industrial MCU", "IMCU-40N"),
            "spec": "産業制御用マイクロコントローラ。",
            "usages": [
                (CORE, "産業用途制御機器に搭載されるマイクロコントローラとして使用", ["device_control"], None),
            ],
        },
        {
            "case_no": "TX-0004",
            "title": "Seed: Process chemical export review",
            "item": ("Lithography developer", "DEV-88"),
            "spec": "フォトリソ工程用現像液。",
            "usages": [
                (CORE, "フォトリソグラフィ工程の現像および洗浄用途に使用", ["process_chemical"], None),
            ],
        },
        {
            "case_no": "TX-0005",
            "title": "Seed: Lithography material export review (ArF)",
            "item": ("Photoresist (ArF)", "ARF-300"),
            "spec": "ArF露光用フォトレジスト。",
            "usages": [
                (CORE, "ArFエキシマレーザー露光を用いた微細パターン形成用感光材料として使用", ["semiconductor_mfg"], None),
            ],
        },
    ]

    for i, spec in enumerate(tx_specs):
        tx = _get_or_create_transaction(db, spec["case_no"], spec["title"], "draft")

        _safe_set(tx, "created_at", base_time + timedelta(minutes=i))
        _safe_set(tx, "updated_at", base_time + timedelta(minutes=i))

        item = _get_or_create_item(
            db,
            tx,
            spec["item"][0],
            spec["item"][1],
            spec["spec"],
            {"files": []},
        )

        for src, text, tags, conf in spec["usages"]:
            _ensure_usage(db, tx, item, src, text, tags, conf)

    db.flush()


def main():
    db = SessionLocal()
    try:
        upsert_min_seed(db)
        db.commit()
        print("Seed data inserted/updated successfully.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
