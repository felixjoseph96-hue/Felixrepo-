"""
Filtering and scoring logic for apartment listings.

Handles:
  - Hard-filter validation (price, beds, baths, sqft)
  - Metro walkability via Haversine distance
  - Gym / fitness detection from amenities + description
  - Natural-light scoring from description keywords
"""
import math
import re
from typing import Optional

from config import Config
from models import Listing


# ─── Haversine distance ───────────────────────────────────────────────────────

def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance between two lat/lon points in miles."""
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ─── Metro proximity ──────────────────────────────────────────────────────────

def score_metro(listing: Listing) -> Listing:
    """
    Attach nearest_metro, metro_distance_miles, and is_walkable to the listing.
    Requires listing.latitude / listing.longitude to be set.
    """
    if listing.latitude is None or listing.longitude is None:
        # Try neighbourhood heuristic when we don't have coordinates
        _apply_neighborhood_metro_heuristic(listing)
        return listing

    best_name = None
    best_dist = float("inf")
    for station in Config.METRO_STATIONS:
        dist = _haversine_miles(listing.latitude, listing.longitude,
                                station["lat"], station["lon"])
        if dist < best_dist:
            best_dist = dist
            best_name = station["name"]

    listing.nearest_metro = best_name
    listing.metro_distance_miles = best_dist
    listing.is_walkable = best_dist <= Config.MAX_METRO_WALK_MILES
    return listing


def _apply_neighborhood_metro_heuristic(listing: Listing) -> None:
    """
    Fall back: infer walkability from address / neighbourhood text.
    Marks walkable if the address mentions a known walkable neighbourhood.
    """
    text = " ".join(filter(None, [listing.address, listing.neighborhood, listing.title])).lower()
    walkable_terms = ["rosslyn", "clarendon", "court house", "courthouse",
                      "virginia square", "ballston"]
    if any(t in text for t in walkable_terms):
        listing.is_walkable = True
        # Assign nearest station by keyword
        for station in Config.METRO_STATIONS:
            if station["name"].lower().replace("-mu", "").replace(" ", "") in text.replace(" ", ""):
                listing.nearest_metro = station["name"]
                listing.metro_distance_miles = 0.25  # assumed walkable
                return
        listing.nearest_metro = "Arlington Metro"
        listing.metro_distance_miles = 0.30


# ─── Gym detection ────────────────────────────────────────────────────────────

def detect_gym(listing: Listing) -> bool:
    """Return True if any gym/fitness keyword is found in amenities or description."""
    corpus = " ".join(listing.amenities).lower()
    if listing.description:
        corpus += " " + listing.description.lower()
    return any(kw in corpus for kw in Config.GYM_KEYWORDS)


# ─── Natural-light scoring ────────────────────────────────────────────────────

def score_natural_light(listing: Listing) -> int:
    """
    Return a score 0–5 indicating how much natural light the listing likely has.
    Based on keyword frequency and weight in title + description + amenities.
    """
    corpus = " ".join(filter(None, [
        listing.title or "",
        listing.description or "",
        " ".join(listing.amenities),
    ])).lower()

    total = 0
    for keyword, weight in Config.NATURAL_LIGHT_KEYWORDS:
        if keyword in corpus:
            total += weight

    return min(total, 5)


# ─── Hard filters ─────────────────────────────────────────────────────────────

def passes_hard_filters(listing: Listing) -> bool:
    """
    Return True only if the listing satisfies every non-negotiable requirement.
    We are lenient when data is missing (None) to avoid false negatives from
    incomplete scraping.
    """
    # Price: must not exceed max (skip if unknown)
    if listing.price_min is not None and listing.price_min > Config.MAX_PRICE:
        return False

    # Bedrooms: must be at least MIN_BEDROOMS
    if listing.bedrooms is not None and listing.bedrooms < Config.MIN_BEDROOMS:
        return False

    # Bathrooms: must be at least MIN_BATHROOMS
    if listing.bathrooms is not None and listing.bathrooms < Config.MIN_BATHROOMS:
        return False

    # Sqft: must be at least MIN_SQFT (only filter if we have the data)
    if listing.sqft_min is not None and listing.sqft_min < Config.MIN_SQFT:
        return False

    return True


# ─── Composite scoring ────────────────────────────────────────────────────────

def compute_score(listing: Listing) -> int:
    """
    Return a 0–100 desirability score (higher = better match).
    Used to sort listings on the dashboard.
    """
    score = 0

    # Metro walkability (0–30 pts)
    if listing.is_walkable:
        if listing.metro_distance_miles is not None:
            score += max(0, 30 - int(listing.metro_distance_miles / Config.MAX_METRO_WALK_MILES * 30))
        else:
            score += 20

    # Gym (0–20 pts)
    if listing.has_gym:
        score += 20

    # Natural light (0–20 pts, scaled from 0–5)
    score += min(20, listing.natural_light_score * 4)

    # Price value (0–20 pts): cheaper relative to max = better
    if listing.price_min is not None and listing.price_min > 0:
        price_pct = listing.price_min / Config.MAX_PRICE
        score += max(0, int((1 - price_pct) * 20))

    # Sqft bonus (0–10 pts): extra space above minimum
    if listing.sqft_min is not None and listing.sqft_min >= Config.MIN_SQFT:
        extra = listing.sqft_min - Config.MIN_SQFT
        score += min(10, extra // 100)

    return min(score, 100)


# ─── Apply all scoring to a listing ──────────────────────────────────────────

def enrich_listing(listing: Listing) -> Listing:
    """Run all scoring functions and update listing fields in-place."""
    score_metro(listing)
    listing.has_gym = detect_gym(listing)
    listing.natural_light_score = score_natural_light(listing)
    return listing


# ─── Price parsing helpers ────────────────────────────────────────────────────

def parse_price(text: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """
    Parse a price string like '$2,800', '$2,800–$3,200', 'From $2,500', etc.
    Returns (price_min, price_max).
    """
    if not text:
        return None, None
    digits = re.findall(r"\d[\d,]*", text.replace(",", ""))
    nums = [int(d.replace(",", "")) for d in digits if 500 < int(d.replace(",", "")) < 20000]
    if not nums:
        return None, None
    return min(nums), max(nums)


def parse_sqft(text: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """
    Parse sqft strings like '1,050 sq ft', '950–1,200 sqft', etc.
    Returns (sqft_min, sqft_max).
    """
    if not text:
        return None, None
    digits = re.findall(r"\d[\d,]*", text.replace(",", ""))
    nums = [int(d) for d in digits if 300 < int(d) < 10000]
    if not nums:
        return None, None
    return min(nums), max(nums)


def parse_beds(text: Optional[str]) -> Optional[float]:
    """Parse '2 bd', '2 Beds', 'Studio', etc."""
    if not text:
        return None
    text = text.lower()
    if "studio" in text:
        return 0.0
    m = re.search(r"(\d+\.?\d*)\s*(bed|br|bd)", text)
    if m:
        return float(m.group(1))
    return None


def parse_baths(text: Optional[str]) -> Optional[float]:
    """Parse '2 ba', '2.5 Baths', etc."""
    if not text:
        return None
    m = re.search(r"(\d+\.?\d*)\s*(bath|ba\b)", text.lower())
    if m:
        return float(m.group(1))
    return None
