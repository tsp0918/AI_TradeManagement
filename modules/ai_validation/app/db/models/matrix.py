# app/db/models/matrix.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.orm import relationship

from app.db.base import Base


class MatrixRule(Base):
    __tablename__ = "matrix_rules"

    id = Column(Integer, primary_key=True, index=True)

    regime = Column(String(32), nullable=False, index=True)          # 例: JP_FX
    list_name = Column(String(255), nullable=True, index=True)       # 例: "3項 化学兵器"
    item_no = Column(String(255), nullable=False, index=True)        # 例: "輸出令 第3項..."
    title = Column(Text, nullable=True)
    requirement_text = Column(Text, nullable=False)                 # NOT NULL
    usage_criteria_text = Column(Text, nullable=True)
    tech_criteria_text = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    version = Column(String(64), nullable=True)
    effective_date = Column(String(32), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # matrix_matches は ai_run.py 側の MatrixMatch.matrix_rule と対応
    matches = relationship("MatrixMatch", back_populates="matrix_rule")
