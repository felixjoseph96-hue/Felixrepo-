"""SQLAlchemy models and database initialisation."""
import json
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Column, DateTime, Float, ForeignKey, Integer, String, Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship
from sqlalchemy.pool import StaticPool

from config import Config


class Base(DeclarativeBase):
    pass


class Interview(Base):
    __tablename__ = "interviews"

    id = Column(Integer, primary_key=True, autoincrement=True)

    filename = Column(String(500), nullable=False)
    source_type = Column(String(20), nullable=False, default="text")  # text | audio

    physician_name = Column(String(200))       # optional; leave blank to anonymize
    specialty = Column(String(200))
    tags = Column(String(500))                  # comma-separated, optional

    raw_text = Column(Text)                      # parsed/transcribed transcript
    summary = Column(Text)                       # Claude-generated interview summary
    top_insight = Column(Text)                   # single-sentence key takeaway

    status = Column(String(20), nullable=False, default="pending")  # pending|processing|done|error
    error_message = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)

    pain_points = relationship(
        "PainPoint", back_populates="interview", cascade="all, delete-orphan"
    )

    def tag_list(self) -> List[str]:
        return [t.strip() for t in (self.tags or "").split(",") if t.strip()]


class PainPoint(Base):
    __tablename__ = "pain_points"

    id = Column(Integer, primary_key=True, autoincrement=True)
    interview_id = Column(Integer, ForeignKey("interviews.id"), nullable=False)

    theme = Column(String(200), nullable=False)          # from controlled vocabulary
    custom_label = Column(String(300))                    # detail when theme == "Other"
    description = Column(Text, nullable=False)
    quote = Column(Text)                                   # verbatim supporting quote
    severity = Column(Integer, default=3)                  # 1-5
    frequency_signal = Column(String(10), default="medium")  # low|medium|high

    created_at = Column(DateTime, default=datetime.utcnow)

    interview = relationship("Interview", back_populates="pain_points")

    def display_theme(self) -> str:
        if self.theme == "Other" and self.custom_label:
            return f"Other: {self.custom_label}"
        return self.theme


class InsightsReport(Base):
    __tablename__ = "insights_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    generated_at = Column(DateTime, default=datetime.utcnow)
    interview_count = Column(Integer, default=0)
    content_markdown = Column(Text, nullable=False)


_engine = None
_SessionLocal = None


def init_db():
    global _engine, _SessionLocal
    engine_kwargs = {}
    connect_args = {}
    if Config.DATABASE_URL.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        if Config.DATABASE_URL == "sqlite:///:memory:":
            engine_kwargs["poolclass"] = StaticPool
    _engine = create_engine(Config.DATABASE_URL, connect_args=connect_args, **engine_kwargs)
    Base.metadata.create_all(_engine)
    _SessionLocal = lambda: Session(_engine)
    return _engine


def get_session() -> Session:
    if _SessionLocal is None:
        init_db()
    return _SessionLocal()
