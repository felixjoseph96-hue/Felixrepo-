"""SQLAlchemy models and database initialisation."""
import json
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, create_engine, text,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship
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
    source = Column(String(50), nullable=False)       # "apartments_com" | "zillow" | "craigslist"
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
    price_min = Column(Integer)
    price_max = Column(Integer)
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

    # ── User interaction ──────────────────────────────────────────────────────
    is_favorited = Column(Boolean, default=False)
    notes = Column(Text, default="")

    # ── Workflow ──────────────────────────────────────────────────────────────
    listed_at = Column(DateTime)
    scraped_at = Column(DateTime, default=datetime.utcnow)
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    date_available = Column(DateTime)
    is_new = Column(Boolean, default=True)
    is_notified = Column(Boolean, default=False)
    detail_fetched = Column(Boolean, default=False)

    # ── Relationships ─────────────────────────────────────────────────────────
    price_history = relationship(
        "PriceHistory", back_populates="listing",
        order_by="PriceHistory.recorded_at", cascade="all, delete-orphan"
    )

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
        return self.price_min or self.price_max

    @property
    def sqft(self) -> Optional[int]:
        return self.sqft_min or self.sqft_max

    def to_dict(self, include_detail: bool = False) -> dict:
        d = {
            "id": self.id,
            "source": self.source,
            "url": self.url,
            "title": self.title,
            "address": self.address,
            "neighborhood": self.neighborhood,
            "latitude": self.latitude,
            "longitude": self.longitude,
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
            "amenities": self.amenities[:6],
            "is_new": self.is_new,
            "is_favorited": self.is_favorited,
            "scraped_at": self.scraped_at.isoformat() if self.scraped_at else None,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "date_available": self.date_available.isoformat() if self.date_available else None,
            "score": None,  # injected by API layer
        }
        if include_detail:
            d.update({
                "photos": self.photos,
                "amenities": self.amenities,
                "description": self.description,
                "notes": self.notes or "",
                "price_history": [
                    {
                        "price_min": h.price_min,
                        "price_max": h.price_max,
                        "recorded_at": h.recorded_at.isoformat(),
                    }
                    for h in self.price_history
                ],
            })
        return d


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    price_min = Column(Integer)
    price_max = Column(Integer)
    recorded_at = Column(DateTime, default=datetime.utcnow)

    listing = relationship("Listing", back_populates="price_history")


# ─── Engine / session helpers ─────────────────────────────────────────────────

engine = create_engine(
    Config.DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """Add columns introduced after the initial schema without dropping data."""
    new_columns = [
        ("listings", "is_favorited",   "BOOLEAN DEFAULT 0"),
        ("listings", "notes",          "TEXT DEFAULT ''"),
        ("listings", "date_available", "DATETIME"),
    ]
    with engine.connect() as conn:
        for table, col, col_def in new_columns:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"))
                conn.commit()
            except Exception:
                pass  # column already exists


def get_session() -> Session:
    return Session(engine)
