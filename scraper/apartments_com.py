"""
Apartments.com scraper.

Strategy
--------
1. Navigate to the search URL (pre-filtered for Arlington, VA / 2 beds / ≤$3500).
2. Intercept the POST to /services/search/ which returns JSON listing data.
3. Fall back to DOM parsing of <article class="placard"> elements.
4. For each new listing visit its detail page to get sqft, amenities,
   full description, and photos.
"""
import json
import logging
import re
import urllib.parse
from typing import List, Optional

from playwright.async_api import Page, Request, Response

from config import Config
from filters import parse_beds, parse_baths, parse_price, parse_sqft
from models import Listing
from scraper.base import BaseScraper

logger = logging.getLogger(__name__)

# ── Search URL ────────────────────────────────────────────────────────────────
# apartments.com path encoding: /{city-state}/{beds}/{max-price}/
_SEARCH_URL = (
    "https://www.apartments.com/arlington-va/2-bedrooms/under-3500/"
    "?bb=upkzlAx_ytDmC~nvsE"   # bounding box for Rosslyn→Ballston corridor
)


class ApartmentsComScraper(BaseScraper):
    SOURCE = "apartments_com"

    # ── Search page ───────────────────────────────────────────────────────────

    async def _scrape_search(self, page: Page) -> List[Listing]:
        captured: list[dict] = []

        async def _intercept_response(response: Response):
            if "apartments.com/services/search" in response.url and response.status == 200:
                try:
                    data = await response.json()
                    captured.append(data)
                except Exception:
                    pass

        page.on("response", _intercept_response)

        logger.info("Apartments.com: navigating to search URL")
        await self._goto(page, _SEARCH_URL, wait="networkidle")
        await self._random_delay(2, 4)

        # Scroll to trigger lazy-loaded listings
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await self._random_delay(1, 2)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self._random_delay(1, 2)

        listings: List[Listing] = []

        # ── Primary: parse intercepted JSON ──────────────────────────────────
        if captured:
            for payload in captured:
                listings.extend(self._parse_json_payload(payload))
            logger.info("Apartments.com: parsed %d listings from JSON API", len(listings))

        # ── Fallback: DOM parsing ─────────────────────────────────────────────
        if not listings:
            logger.info("Apartments.com: falling back to DOM parsing")
            listings = await self._parse_dom(page)
            logger.info("Apartments.com: parsed %d listings from DOM", len(listings))

        return listings

    # ── JSON API parser ───────────────────────────────────────────────────────

    def _parse_json_payload(self, data: dict) -> List[Listing]:
        listings: List[Listing] = []

        # The API response structure has changed over time; try both known shapes
        items = (
            data.get("PlacardState", {}).get("Items", [])
            or data.get("searchResults", {}).get("listResults", [])
            or []
        )

        for item in items:
            try:
                listing = self._json_item_to_listing(item)
                if listing:
                    listings.append(listing)
            except Exception as exc:
                logger.debug("Skipping malformed JSON item: %s", exc)

        return listings

    def _json_item_to_listing(self, item: dict) -> Optional[Listing]:
        ext_id = str(item.get("PropertyID") or item.get("zpid") or item.get("id") or "")
        url = item.get("Url") or item.get("detailUrl") or item.get("url") or ""
        if not ext_id or not url:
            return None

        if not url.startswith("http"):
            url = "https://www.apartments.com" + url

        listing = Listing(
            source=self.SOURCE,
            external_id=ext_id,
            url=url,
            title=item.get("PropertyName") or item.get("name") or "",
        )

        # Address
        addr = item.get("Address") or {}
        if isinstance(addr, dict):
            listing.address = ", ".join(
                filter(None, [addr.get("AddressLine1"), addr.get("City"),
                              addr.get("State"), addr.get("PostalCode")])
            )
            listing.neighborhood = addr.get("Neighborhood") or addr.get("NeighborhoodName")
            listing.latitude = addr.get("Latitude")
            listing.longitude = addr.get("Longitude")
        else:
            listing.address = str(addr)

        # Price
        rent = item.get("Rent") or {}
        if isinstance(rent, dict):
            listing.price_min = rent.get("Min") or rent.get("min")
            listing.price_max = rent.get("Max") or rent.get("max")
        else:
            raw_price = str(rent or item.get("price") or "")
            listing.price_min, listing.price_max = parse_price(raw_price)

        # Beds / baths
        beds = item.get("Beds") or item.get("beds") or {}
        if isinstance(beds, dict):
            listing.bedrooms = beds.get("Min") or beds.get("Max") or beds.get("max")
        else:
            listing.bedrooms = parse_beds(str(beds))

        baths = item.get("Baths") or item.get("baths") or {}
        if isinstance(baths, dict):
            listing.bathrooms = baths.get("Min") or baths.get("Max") or baths.get("max")
        else:
            listing.bathrooms = parse_baths(str(baths))

        # Photos
        photos = item.get("Photos") or item.get("photos") or []
        listing.photos = [p.get("Url") or p if isinstance(p, dict) else p for p in photos[:10]]

        return listing

    # ── DOM parser ────────────────────────────────────────────────────────────

    async def _parse_dom(self, page: Page) -> List[Listing]:
        listings: List[Listing] = []

        article_handles = await page.query_selector_all("article[data-listingid]")
        if not article_handles:
            article_handles = await page.query_selector_all("article.placard")

        for article in article_handles:
            try:
                listing = await self._parse_placard(page, article)
                if listing:
                    listings.append(listing)
            except Exception as exc:
                logger.debug("Failed to parse placard: %s", exc)

        return listings

    async def _parse_placard(self, page: Page, article) -> Optional[Listing]:
        ext_id = await article.get_attribute("data-listingid") or ""
        if not ext_id:
            return None

        link_el = await article.query_selector("a.property-link, a[data-listingid]")
        url = await link_el.get_attribute("href") if link_el else ""
        if not url:
            return None
        if not url.startswith("http"):
            url = "https://www.apartments.com" + url

        listing = Listing(source=self.SOURCE, external_id=ext_id, url=url)

        # Title
        title_el = await article.query_selector(
            ".property-name h2, .js-placardTitle, .property-title"
        )
        listing.title = (await title_el.inner_text()).strip() if title_el else ""

        # Address
        addr_el = await article.query_selector(".property-address, address")
        listing.address = (await addr_el.inner_text()).strip() if addr_el else ""

        # Price
        price_el = await article.query_selector(
            ".price-range, .rent-pricing, [class*='price']"
        )
        raw_price = (await price_el.inner_text()).strip() if price_el else ""
        listing.price_min, listing.price_max = parse_price(raw_price)

        # Beds / baths
        beds_el = await article.query_selector(".property-beds, [class*='bed']")
        raw_beds = (await beds_el.inner_text()).strip() if beds_el else ""
        listing.bedrooms = parse_beds(raw_beds)
        listing.bathrooms = parse_baths(raw_beds)

        # Photo
        img_el = await article.query_selector("img[src]")
        src = await img_el.get_attribute("src") if img_el else None
        listing.photos = [src] if src else []

        return listing

    # ── Detail page ───────────────────────────────────────────────────────────

    async def _fetch_detail(self, page: Page, listing: Listing) -> None:
        logger.debug("Apartments.com detail: %s", listing.url)
        await self._goto(page, listing.url, wait="domcontentloaded")
        await self._random_delay(1.5, 3)

        # ── Sqft ──────────────────────────────────────────────────────────────
        sqft_el = await page.query_selector(
            "[class*='sqft'], [class*='square'], .property-size, "
            ".unit-details li:has-text('sq ft')"
        )
        if sqft_el:
            listing.sqft_min, listing.sqft_max = parse_sqft(await sqft_el.inner_text())

        # ── Beds / baths (authoritative from detail page) ─────────────────────
        detail_beds_el = await page.query_selector(
            ".unit-details li:has-text('Bed'), .priceBedRangeInfo, [class*='bedsDetails']"
        )
        if detail_beds_el:
            raw = await detail_beds_el.inner_text()
            listing.bedrooms = listing.bedrooms or parse_beds(raw)
            listing.bathrooms = listing.bathrooms or parse_baths(raw)

        # ── Amenities ─────────────────────────────────────────────────────────
        amenity_els = await page.query_selector_all(
            ".amenityItem, li.specInfo, [class*='amenity'] li, .feature-list li"
        )
        amenities = []
        for el in amenity_els:
            text = (await el.inner_text()).strip()
            if text:
                amenities.append(text)
        listing.amenities = amenities

        # ── Description ───────────────────────────────────────────────────────
        desc_el = await page.query_selector(
            ".property-description, #descriptionSection, [class*='description']"
        )
        if desc_el:
            listing.description = (await desc_el.inner_text()).strip()

        # ── Photos ────────────────────────────────────────────────────────────
        photo_els = await page.query_selector_all(
            ".photos-overview img[src], .carousel img[src], [class*='photo'] img[src]"
        )
        photos = []
        for el in photo_els:
            src = await el.get_attribute("src")
            if src and src.startswith("http"):
                photos.append(src)
        if photos:
            listing.photos = photos[:12]

        # ── Coordinates ───────────────────────────────────────────────────────
        if listing.latitude is None:
            lat, lon = await self._extract_coords(page)
            listing.latitude = lat
            listing.longitude = lon

        listing.detail_fetched = True

    async def _extract_coords(self, page: Page) -> tuple[Optional[float], Optional[float]]:
        """Try to read lat/lon from embedded JSON-LD or meta tags."""
        try:
            json_ld = await page.query_selector("script[type='application/ld+json']")
            if json_ld:
                raw = await json_ld.inner_text()
                data = json.loads(raw)
                geo = data.get("geo") or {}
                return geo.get("latitude"), geo.get("longitude")
        except Exception:
            pass
        return None, None
