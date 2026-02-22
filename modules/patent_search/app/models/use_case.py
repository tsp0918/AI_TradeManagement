from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class PatentUseCase(Base):
    """Patent use cases extracted by LLM."""

    __tablename__ = "patent_use_cases"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    patent_id = Column(Integer, ForeignKey("favorite_patents.id", ondelete="CASCADE"), nullable=False, index=True)
    use_case_text = Column(Text, nullable=False)
    extraction_model = Column(String(50), nullable=True)  # Which Ollama model was used
    confidence_score = Column(Float, nullable=True)  # Optional confidence metric
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationship to patent
    patent = relationship("FavoritePatent", back_populates="use_cases")

    def __repr__(self):
        return f"<PatentUseCase(id={self.id}, patent_id={self.patent_id}, model='{self.extraction_model}')>"
