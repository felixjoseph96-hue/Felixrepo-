"""
RentCast API scraper — recommended primary data source.

RentCast provides a clean REST API for rental listings with no browser or
bot-detection issues. Free tier: 50 requests/month.
Sign up at https://app.rentcast.io → API Keys.

Set RENTCAST_API_KEY in your .env file to enable this scraper.
When the API key is present, RentCast results supplement or replace
the Playwright scrapers depending on network availability.

API docs: https://developers.rentcast.io/reference/rental-listings
"""
import logging
from datetime import datetime
from typing import List, Optional

import httpx

from config import Config
from filters import parse_sqft
from models import Listing

logger = logging.getLogger(__name__)

_BASE = "https://api.rentcast.io/v1"
_LISTINGS_URL = f"{_BASE}/listings/rental/long-term"


class RentCastScraper:
    """HTTP-only scraper — no Playwright required."""

    SOURCE = "rentcast"

    def __init__(self, api_key: str = "", **_):
        self.api_key = api_key or Config.RENTCAST_API_KEY

    async def scrape(self, known_ids: set[str]) -> List[Listing]:
        if not self.api_key:
            logger.info("RentCast: no API key configured, skipping")
            return []

        listings: List[Listing] = []
        # Paginate through up to 3 pages (150 results)
        for offset in range(0, 150, 50):
            batch = await self._fetch_page(offset)
            if not batch:
                break
            listings.extend(batch)
            if len(batch) < 50:
                break

        logger.info("RentCast: fetched %d listings total", len(listings))
        return listings

    async def _fetch_page(self, offset: int) -> List[Listing]:
        params = {
            "city":       "Arlington",
            "state":      "VA",
            "bedrooms":   int(Config.MIN_BEDROOMS),
            "bathrooms":  Config.MIN_BATHROOMS,
            "minRent":    1000,
            "maxRent":    Config.MAX_PRICE,
            "status":     "Active",
            "limit":      50,
            "offset":     offset,
        }
        headers = {
            "X-Api-Key": self.api_key,
            "Accept":    "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(_LISTINGS_URL, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                logger.error("RentCast: invalid API key")
            elif exc.response.status_code == 429:
                logger.warning("RentCast: rate limit reached")
            else:
                logger.error("RentCast API error %s: %s", exc.response.status_code, exc)
            return []
        except Exception as exc:
            logger.error("RentCast request failed: %s", exc)
            return []

        items = data if isinstance(data, list) else data.get("listings", [])
        return [self._item_to_listing(item) for item in items if item.get("id")]

    def _item_to_listing(self, item: dict) -> Listing:
        ext_id = str(item["id"])
        address_parts = [
            item.get("addressLine1", ""),
            item.get("city", ""),
            item.get("state", ""),
            item.get("zipCode", ""),
        ]
        address = ", ".join(p for p in address_parts if p)

        # RentCast returns price as a number
        price = item.get("price") or item.get("rent")
        price_min = int(price) if price else None

        # Sqft
        sqft = item.get("squareFootage") or item.get("livingArea")
        sqft_min = int(sqft) if sqft else None

        # Photos
        photos = item.get("photos", []) or []
        photo_urls = [
            p.get("url") or p if isinstance(p, dict) else p
            for p in photos[:10]
            if p
        ]

        # Amenities / features
        features = item.get("features", {}) or {}
        amenities: list[str] = []
        if features.get("cooling"):   amenities.append("Central A/C")
        if features.get("heating"):   amenities.append("Heating")
        if features.get("pool"):      amenities.append("Pool")
        if features.get("garage"):    amenities.append("Garage Parking")
        if features.get("dishwasher"):amenities.append("Dishwasher")
        if features.get("laundry"):   amenities.append("In-Unit Laundry")
        # Amenities can also be a flat list
        raw_amen = item.get("amenities", []) or []
        if isinstance(raw_amen, list):
            amenities += [a for a in raw_amen if isinstance(a, str)]

        # Build URL — RentCast doesn't always provide one; construct from address
        url = (
            item.get("listingUrl")
            or item.get("url")
            or f"https://app.rentcast.io/app?address={address.replace(' ', '+')}"
        )

        listing = Listing(
            source=self.SOURCE,
            external_id=ext_id,
            url=url,
            title=item.get("formattedAddress") or item.get("addressLine1") or address,
            address=address,
            neighborhood=item.get("neighborhood") or item.get("county"),
            city=item.get("city", "Arlington"),
            state=item.get("state", "VA"),
            latitude=item.get("latitude") or item.get("lat"),
            longitude=item.get("longitude") or item.get("lon"),
            price_min=price_min,
            price_max=price_min,
            bedrooms=float(item.get("bedrooms", 2)),
            bathrooms=float(item.get("bathrooms", 2)),
            sqft_min=sqft_min,
            description=item.get("description") or item.get("remarks"),
            detail_fetched=True,
            scraped_at=datetime.utcnow(),
        )
        listing.amenities = amenities
        listing.photos = photo_urls
        return listing

    # Compatibility shims so scheduler can treat this like the Playwright scrapers
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass
