from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.db.base import PlatformBase


class HanteiRecord(PlatformBase):
    """モジュール横断 該非判定レコード。R&D → 品目管理 → 取引審査 の判定データ継承。"""

    __tablename__ = "plat_hantei_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 全モジュール共通キー: ai_classification = product.code, rnd = "RND-{case_id}", ai_validation = linked_product_code
    product_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # 判定を行ったモジュール: rnd | classification | validation
    source_module: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # モジュール内部参照ID（任意）
    rnd_case_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # FAISS で特定された規制項番情報
    item_no: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    item_label: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    regulation_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Ollama LLM 判定結果
    llm_verdict: Mapped[str] = mapped_column(String(32), nullable=False, default="")       # APPLICABLE/REVIEW_NEEDED/NOT_APPLICABLE
    llm_confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="")    # HIGH/MEDIUM/LOW
    llm_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_key_question: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 転記済み最終判定
    decision: Mapped[str] = mapped_column(String(32), nullable=False, default="")  # controlled/needs_review/non_controlled
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
