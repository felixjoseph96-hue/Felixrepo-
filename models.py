"""SQLAlchemy models and database initialisation."""
import json
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, String, Text,
    UniqueConstraint, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import StaticPool

from config import Config


class Base(DeclarativeBase):
    pass


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_source_external_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    # ── Source ──────────────────────────────────────────────────────────────
    source = Column(String(50), nullable=False)       # "apartments_com" | "zillow"
    external_id = Column(String(300), nullable=False)
    url = Column(String(1000), nullable=False)

    # ── Location ─────────────────────────────────────────────────────────────
    address = Column(String(500))
    neighborhood = Column(String(200))
    city = Column(String(100), default="Arlington")
    state = Column(String(10), default="VA")
    latitude = Column(Float)
    longitude = Column(Float)
    nearest_metro = Column(String(100))
    metro_distance_miles = Column(Float)
    is_walkable = Column(Boolean, default=False)

    # ── Listing details ───────────────────────────────────────────────────────
    title = Column(String(500))
    price_min = Column(Integer)   # monthly rent (low end if range)
    price_max = Column(Integer)   # monthly rent (high end if range)
    bedrooms = Column(Float)
    bathrooms = Column(Float)
    sqft_min = Column(Integer)
    sqft_max = Column(Integer)
    description = Column(Text)
    _photos_json = Column("photos_json", Text, default="[]")
    _amenities_json = Column("amenities_json", Text, default="[]")

    # ── Scored attributes ─────────────────────────────────────────────────────
    has_gym = Column(Boolean, default=False)
    natural_light_score = Column(Integer, default=0)  # 0–5

    # ── Workflow ──────────────────────────────────────────────────────────────
    listed_at = Column(DateTime)                       # date site shows
    scraped_at = Column(DateTime, default=datetime.utcnow)
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    is_new = Column(Boolean, default=True)
    is_notified = Column(Boolean, default=False)
    detail_fetched = Column(Boolean, default=False)    # full detail page visited

    # ── Helpers ───────────────────────────────────────────────────────────────
    @property
    def photos(self) -> List[str]:
        return json.loads(self._photos_json or "[]")

    @photos.setter
    def photos(self, value: List[str]):
        self._photos_json = json.dumps(value)

    @property
    def amenities(self) -> List[str]:
        return json.loads(self._amenities_json or "[]")

    @amenities.setter
    def amenities(self, value: List[str]):
        self._amenities_json = json.dumps(value)

    @property
    def price(self) -> Optional[int]:
        """Return lowest price for display / filtering."""
        return self.price_min or self.price_max

    @property
    def sqft(self) -> Optional[int]:
        return self.sqft_min or self.sqft_max

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "url": self.url,
            "title": self.title,
            "address": self.address,
            "neighborhood": self.neighborhood,
            "price_min": self.price_min,
            "price_max": self.price_max,
            "bedrooms": self.bedrooms,
            "bathrooms": self.bathrooms,
            "sqft_min": self.sqft_min,
            "sqft_max": self.sqft_max,
            "nearest_metro": self.nearest_metro,
            "metro_distance_miles": round(self.metro_distance_miles, 2) if self.metro_distance_miles else None,
            "is_walkable": self.is_walkable,
            "has_gym": self.has_gym,
            "natural_light_score": self.natural_light_score,
            "photos": self.photos[:3],
            "amenities": self.amenities,
            "is_new": self.is_new,
            "scraped_at": self.scraped_at.isoformat() if self.scraped_at else None,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            # score is injected by the API layer to avoid circular import
            "score": None,
        }


# ─── Engine / session helpers ─────────────────────────────────────────────────

engine = create_engine(
    Config.DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
