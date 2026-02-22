from sqlalchemy import Column, Integer, String, Text, Date, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class FavoritePatent(Base):
    """Favorite patents model for saving patents."""

    __tablename__ = "favorite_patents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    publication_number = Column(String(50), unique=True, nullable=False, index=True)
    title = Column(Text, nullable=False)
    abstract = Column(Text, nullable=True)
    description = Column(Text, nullable=True)  # Patent description
    claims = Column(Text, nullable=True)  # Patent claims
    inventors = Column(Text, nullable=True)  # JSON array of inventors
    applicants = Column(Text, nullable=True)  # JSON array of applicants
    filing_date = Column(Date, nullable=True)
    publication_date = Column(Date, nullable=True)
    url = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)  # User's personal notes
    added_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationship to use cases
    use_cases = relationship("PatentUseCase", back_populates="patent", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<FavoritePatent(id={self.id}, publication_number='{self.publication_number}', title='{self.title[:50]}...')>"
