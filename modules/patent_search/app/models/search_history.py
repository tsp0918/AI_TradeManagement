from sqlalchemy import Column, Integer, String, Text, DateTime
from datetime import datetime
from app.database import Base


class SearchHistory(Base):
    """Search history model to track user searches."""

    __tablename__ = "search_history"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    query = Column(Text, nullable=False)
    filters = Column(Text, nullable=True)  # JSON string of filter parameters
    result_count = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<SearchHistory(id={self.id}, query='{self.query}', result_count={self.result_count})>"
