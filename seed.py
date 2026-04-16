"""
Demo data seed script.

Populates the database with realistic Arlington, VA apartment listings
so the dashboard can be used immediately without waiting for a live scrape.

Usage:
    python seed.py
    python seed.py --clear    # clear existing data first
"""
import argparse
import random
import sys
from datetime import datetime, timedelta

from filters import enrich_listing
from models import Listing, PriceHistory, get_session, init_db

# ── Sample data pools ─────────────────────────────────────────────────────────

_PROPERTIES = [
    # (name, address, neighborhood, lat, lon)
    ("The Rosslyn",         "1900 N Fort Myer Dr, Arlington, VA 22209",  "Rosslyn",        38.8951, -77.0716),
    ("Rosslyn Gateway",     "1530 Key Blvd, Arlington, VA 22209",        "Rosslyn",        38.8971, -77.0731),
    ("Key & Nash",          "1800 Key Blvd, Arlington, VA 22201",        "Rosslyn",        38.8964, -77.0724),
    ("Court House 201",     "2001 15th St N, Arlington, VA 22201",       "Court House",    38.8901, -77.0838),
    ("North Highland",      "1515 N Queen St, Arlington, VA 22209",      "Court House",    38.8888, -77.0862),
    ("The Clarendon",       "3001 Fairfax Dr, Arlington, VA 22201",      "Clarendon",      38.8872, -77.0951),
    ("Clarendon Commons",   "3400 Washington Blvd, Arlington, VA 22201", "Clarendon",      38.8855, -77.0977),
    ("Market Common",       "2700 Clarendon Blvd, Arlington, VA 22201",  "Clarendon",      38.8862, -77.0966),
    ("Virginia Square",     "3750 S Four Mile Run Dr, Arlington, VA",    "Virginia Square",38.8848, -77.1041),
    ("Ballston Metro",      "4040 Wilson Blvd, Arlington, VA 22203",     "Ballston",       38.8829, -77.1107),
    ("The Ordway",          "650 N Randolph St, Arlington, VA 22203",    "Ballston",       38.8839, -77.1095),
    ("Ballston Quarter",    "4238 Wilson Blvd, Arlington, VA 22203",     "Ballston",       38.8821, -77.1121),
    ("Pentagon Row",        "1201 S Joyce St, Arlington, VA 22202",      "Pentagon City",  38.8625, -77.0589),
    ("Liberty Center",      "825 N Kirkwood Rd, Arlington, VA 22201",    "Clarendon",      38.8860, -77.0955),
    ("The Alcove",          "2525 Wilson Blvd, Arlington, VA 22201",     "Court House",    38.8895, -77.0849),
    ("Lyon Place",          "3025 N Veitch St, Arlington, VA 22201",     "Clarendon",      38.8878, -77.0941),
    ("Radnor/Ft Myer Hts",  "1650 N Pierce St, Arlington, VA 22209",     "Rosslyn",        38.8982, -77.0745),
    ("The Henry",           "3255 Wilson Blvd, Arlington, VA 22201",     "Clarendon",      38.8868, -77.0972),
    ("Ballston Air Rights", "4500 Fairfax Dr, Arlington, VA 22203",      "Ballston",       38.8814, -77.1136),
    ("Courthouse Square",   "2001 N Adams St, Arlington, VA 22201",      "Court House",    38.8907, -77.0853),
]

_GYM_AMENITIES = [
    "Fitness Center", "State-of-the-Art Gym", "24/7 Fitness Room",
    "Yoga Studio", "Spin Studio", "Weight Room", "CrossFit-style Gym",
]
_STANDARD_AMENITIES = [
    "Rooftop Deck", "Pool", "Concierge", "Package Lockers", "Bike Storage",
    "Dog Run", "Co-working Space", "Game Room", "Sky Lounge", "EV Charging",
    "In-unit W/D", "Stainless Appliances", "Quartz Countertops", "Hardwood Floors",
    "Walk-in Closet", "Private Balcony", "Pet Friendly", "Garage Parking",
    "Guest Suite", "Business Center",
]
_LIGHT_DESCRIPTIONS = [
    "Floor-to-ceiling windows flood the living space with natural light and panoramic views.",
    "South-facing unit with oversized windows and a sun-drenched open floor plan.",
    "Bright and airy two-bedroom with large windows and 9-foot ceilings.",
    "Wall-to-wall windows offer spectacular views and abundant natural light throughout.",
    "East-facing unit gets beautiful morning sun. The kitchen and living area are very bright.",
    "Natural light pours through the large windows, creating a warm and inviting atmosphere.",
    "Sunlit corner unit with windows on two sides for all-day natural light.",
    "Expansive windows with unobstructed views allow for incredible natural light.",
]
_PLAIN_DESCRIPTIONS = [
    "Well-appointed two-bedroom apartment with modern finishes and an updated kitchen.",
    "Spacious unit featuring an open floor plan, in-unit washer/dryer, and quality appliances.",
    "Two-bedroom apartment with granite countertops, hardwood floors, and ample storage.",
    "Renovated unit with updated kitchen and bathrooms. Great closet space throughout.",
    "Modern apartment with stainless appliances, subway tile backsplash, and city views.",
    "Open concept two-bedroom featuring a large island kitchen and private outdoor space.",
]
_SOURCES = ["apartments_com", "zillow", "craigslist"]


def _make_listing(i: int) -> Listing:
    prop = _PROPERTIES[i % len(_PROPERTIES)]
    source = _SOURCES[i % len(_SOURCES)]
    ext_id = f"demo_{source}_{i:04d}"

    bedrooms = 2.0
    bathrooms = random.choice([2.0, 2.0, 2.0, 2.5])
    sqft = random.randint(980, 1550)

    price_base = random.randint(2200, 3450)
    price_min = price_base - (price_base % 50)
    price_max = price_min + random.choice([0, 50, 100, 150])

    has_gym = random.random() < 0.65
    high_light = random.random() < 0.45

    amenities = []
    if has_gym:
        amenities.append(random.choice(_GYM_AMENITIES))
    amenities += random.sample(_STANDARD_AMENITIES, k=random.randint(4, 9))

    description = (
        random.choice(_LIGHT_DESCRIPTIONS) if high_light
        else random.choice(_PLAIN_DESCRIPTIONS)
    )

    first_seen = datetime.utcnow() - timedelta(
        days=random.randint(0, 14),
        hours=random.randint(0, 23),
    )

    listing = Listing(
        source=source,
        external_id=ext_id,
        url=f"https://www.{source.replace('_com', '.com').replace('_','')}.com/listing/{ext_id}",
        title=prop[0],
        address=prop[1],
        neighborhood=prop[2],
        city="Arlington",
        state="VA",
        latitude=prop[3] + random.uniform(-0.001, 0.001),
        longitude=prop[4] + random.uniform(-0.001, 0.001),
        price_min=price_min,
        price_max=price_max if price_max != price_min else None,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        sqft_min=sqft,
        sqft_max=sqft + random.randint(0, 80) if random.random() < 0.3 else None,
        description=description,
        is_new=first_seen > datetime.utcnow() - timedelta(days=1),
        is_favorited=False,
        first_seen_at=first_seen,
        scraped_at=first_seen,
        detail_fetched=True,
    )
    listing.amenities = amenities
    listing.photos = []  # No real photos in seed data

    enrich_listing(listing)
    return listing


def seed(clear: bool = False) -> None:
    init_db()
    with get_session() as session:
        if clear:
            session.query(PriceHistory).delete()
            session.query(Listing).delete()
            session.commit()
            print("Cleared existing listings.")

        existing = {
            r.external_id
            for r in session.query(Listing.external_id).all()
        }

        added = 0
        for i in range(40):
            listing = _make_listing(i)
            if listing.external_id in existing:
                continue
            session.add(listing)
            session.flush()
            if listing.price_min:
                session.add(PriceHistory(
                    listing_id=listing.id,
                    price_min=listing.price_min,
                    price_max=listing.price_max,
                    recorded_at=listing.first_seen_at,
                ))
            # Add a fake prior price for some listings
            if random.random() < 0.4 and listing.price_min:
                old_price = listing.price_min + random.choice([50, 100, 150, -50])
                session.add(PriceHistory(
                    listing_id=listing.id,
                    price_min=old_price,
                    price_max=old_price,
                    recorded_at=listing.first_seen_at - timedelta(days=random.randint(3, 10)),
                ))
            added += 1

        session.commit()
        print(f"Seeded {added} demo listings.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed demo listings")
    parser.add_argument("--clear", action="store_true", help="Clear existing data first")
    args = parser.parse_args()
    seed(clear=args.clear)
