"""
Fetches Google Places reviews for apartment buildings and flags bad management.

Uses the free Google Places Text Search + Details API.
Set GOOGLE_PLACES_API_KEY in .env to enable. Without a key, this is skipped.

Bad management keywords (from 1-star patterns):
  "mold", "roaches", "mice", "rats", "no heat", "no hot water", "ignored",
  "unresponsive", "unsafe", "black mold", "bedbugs", "bed bugs", "flooding"
"""
import asyncio
import logging
import os
import re

import httpx

from models import Listing, get_session

logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")

BAD_KEYWORDS = [
    "mold", "roach", "mice", "rats", "rat ", "bed bug", "bedbug",
    "no heat", "no hot water", "unresponsive", "ignor", "unsafe",
    "flooding", "flooded", "broken heat", "broken ac", "slumlord",
    "pest", "infest",
]


async def fetch_reviews_for_listing(listing: Listing, client: httpx.AsyncClient) -> None:
    """Query Google Places for the building and update review fields."""
    if not GOOGLE_API_KEY:
        return
    if not listing.address:
        return

    query = f"{listing.address} Arlington VA apartment"
    try:
        # Text search to get place_id
        r = await client.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": query, "key": GOOGLE_API_KEY},
            timeout=10,
        )
        data = r.json()
        results = data.get("results", [])
        if not results:
            return
        place_id = results[0].get("place_id")
        if not place_id:
            return

        # Place details for rating + reviews
        r2 = await client.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={
                "place_id": place_id,
                "fields": "rating,user_ratings_total,reviews",
                "key": GOOGLE_API_KEY,
            },
            timeout=10,
        )
        detail = r2.json().get("result", {})
        listing.review_rating = detail.get("rating")
        listing.review_count = detail.get("user_ratings_total")

        # Check 1-star and 2-star reviews for bad keywords
        reviews = detail.get("reviews", [])
        bad = False
        for rev in reviews:
            if rev.get("rating", 5) <= 2:
                text = (rev.get("text") or "").lower()
                if any(kw in text for kw in BAD_KEYWORDS):
                    bad = True
                    break
        listing.bad_management = bad

    except Exception as exc:
        logger.debug("Review fetch failed for %s: %s", listing.address, exc)


async def enrich_reviews(listing_ids: list[int] | None = None) -> None:
    """Fetch reviews for all listings (or a subset by id)."""
    if not GOOGLE_API_KEY:
        logger.info("Reviews: GOOGLE_PLACES_API_KEY not set, skipping")
        return

    with get_session() as session:
        q = session.query(Listing).filter(Listing.review_rating.is_(None))
        if listing_ids:
            q = q.filter(Listing.id.in_(listing_ids))
        listings = q.limit(50).all()

        if not listings:
            return

        logger.info("Reviews: fetching for %d listings", len(listings))
        async with httpx.AsyncClient() as client:
            tasks = [fetch_reviews_for_listing(l, client) for l in listings]
            await asyncio.gather(*tasks, return_exceptions=True)

        session.commit()
        flagged = sum(1 for l in listings if l.bad_management)
        logger.info("Reviews: done. %d flagged for bad management", flagged)
