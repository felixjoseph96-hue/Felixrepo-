"""
Craigslist housing scraper.

Craigslist uses server-side rendered HTML (no heavy JS), so pages are
scraped with plain httpx rather than Playwright. This is faster and
avoids the bot-detection challenges of the other scrapers.

Search area: Washington DC / NoVA (washingtondc.craigslist.org), sub-area nva
Filters applied via URL: 2 bedrooms, max $3500/mo, min 1000 sqft
"""
import hashlib
import logging
import re
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlencode, urljoin

import httpx
from bs4 import BeautifulSoup

from config import Config
from filters import parse_baths, parse_beds, parse_price, parse_sqft
from models import Listing

logger = logging.getLogger(__name__)

_BASE_URL = "https://washingtondc.craigslist.org"
_SEARCH_PATH = "/search/nva/apa"
_SEARCH_PARAMS = {
    "min_bedrooms": 2,
    "max_bedrooms": 3,
    "min_price": 1200,
    "max_price": Config.MAX_PRICE,
    "minSqft": Config.MIN_SQFT,
    "availabilityMode": 0,
    "sale_date": "all+dates",
    # Centre on Rosslyn/Clarendon corridor
    "lat": 38.889,
    "lon": -77.091,
    "search_distance": 3,  # miles
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Re-use the base scraper's lifecycle only for interface compatibility;
# Craigslist doesn't need Playwright so we override everything.
from scraper.base import BaseScraper
from playwright.async_api import Page


class CraigslistScraper(BaseScraper):
    SOURCE = "craigslist"

    # Override: no Playwright browser needed
    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=20,
            verify=False,  # handle TLS inspection proxies
        )
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()

    async def scrape(self, known_ids: set[str]) -> List[Listing]:
        search_url = _BASE_URL + _SEARCH_PATH + "?" + urlencode(_SEARCH_PARAMS)
        logger.info("Craigslist: fetching search page")
        try:
            resp = await self._client.get(search_url)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("Craigslist search failed: %s", exc)
            return []

        listings = self._parse_search_html(resp.text)
        logger.info("Craigslist: found %d listings on search page", len(listings))

        new_listings = [l for l in listings if l.external_id not in known_ids]
        logger.info("Craigslist: %d new → fetching detail pages (max %d)",
                    len(new_listings), self.max_detail_pages)

        for listing in new_listings[: self.max_detail_pages]:
            try:
                await self._fetch_detail_http(listing)
            except Exception as exc:
                logger.warning("Craigslist detail failed %s: %s", listing.url, exc)
            import asyncio
            await asyncio.sleep(0.8 + (hash(listing.external_id) % 10) / 10)

        return listings

    # ── Search page parser ────────────────────────────────────────────────────

    def _parse_search_html(self, html: str) -> List[Listing]:
        soup = BeautifulSoup(html, "lxml")
        listings: List[Listing] = []

        # Craigslist uses both old (.result-row) and new (.cl-static-search-result) layouts
        items = (
            soup.select("li.cl-static-search-result")
            or soup.select("li.result-row")
        )

        for item in items:
            try:
                listing = self._parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception as exc:
                logger.debug("Craigslist: skipping item: %s", exc)

        return listings

    def _parse_item(self, item) -> Optional[Listing]:
        # URL and ID
        link = item.select_one("a[href]")
        if not link:
            return None
        url = link.get("href", "")
        if not url.startswith("http"):
            url = urljoin(_BASE_URL, url)

        # Use the post ID from URL as external_id
        m = re.search(r"/(\d{10,})\.html", url)
        if not m:
            ext_id = hashlib.md5(url.encode()).hexdigest()[:16]
        else:
            ext_id = m.group(1)

        title_el = item.select_one(".title, .titlestring, a.posting-title, a.cl-app-anchor")
        title = title_el.get_text(strip=True) if title_el else ""

        price_el = item.select_one(".price, .result-price, .priceinfo")
        raw_price = price_el.get_text(strip=True) if price_el else ""
        price_min, price_max = parse_price(raw_price)

        housing_el = item.select_one(".housing, .result-meta .housing")
        raw_housing = housing_el.get_text(strip=True) if housing_el else ""
        bedrooms = parse_beds(raw_housing)
        sqft_min, sqft_max = parse_sqft(raw_housing)

        listing = Listing(
            source=self.SOURCE,
            external_id=ext_id,
            url=url,
            title=title,
            price_min=price_min,
            price_max=price_max,
            bedrooms=bedrooms or 2.0,
            sqft_min=sqft_min,
            sqft_max=sqft_max,
        )
        return listing

    # ── Detail page ───────────────────────────────────────────────────────────

    async def _fetch_detail_http(self, listing: Listing) -> None:
        resp = await self._client.get(listing.url)
        if resp.status_code != 200:
            return

        soup = BeautifulSoup(resp.text, "lxml")

        # Address
        addr_el = soup.select_one(".mapaddress, #map")
        if addr_el:
            listing.address = addr_el.get_text(strip=True)

        # Title (authoritative)
        title_el = soup.select_one("#titletextonly, .postingtitle .titletextonly")
        if title_el:
            listing.title = title_el.get_text(strip=True)

        # Price
        price_el = soup.select_one(".price")
        if price_el:
            price_min, price_max = parse_price(price_el.get_text(strip=True))
            listing.price_min = price_min or listing.price_min
            listing.price_max = price_max or listing.price_max

        # Attributes (beds, baths, sqft)
        for attr in soup.select(".attrgroup span, .mapAndAttrs .attrgroup span"):
            text = attr.get_text(strip=True).lower()
            if "br" in text or "bed" in text:
                listing.bedrooms = parse_beds(text) or listing.bedrooms
            if "ba" in text or "bath" in text:
                listing.bathrooms = parse_baths(text)
            if "ft" in text or "sqft" in text:
                mn, mx = parse_sqft(text)
                listing.sqft_min = mn or listing.sqft_min

        # Description
        body_el = soup.select_one("#postingbody, section.userbody")
        if body_el:
            # Remove "QR Code Link to This Post" boilerplate
            for el in body_el.select(".print-qrcode-container"):
                el.decompose()
            listing.description = body_el.get_text(separator=" ", strip=True)

        # Lat / lon from map data attribute
        map_el = soup.select_one("#map")
        if map_el:
            lat = map_el.get("data-latitude")
            lon = map_el.get("data-longitude")
            if lat and lon:
                try:
                    listing.latitude = float(lat)
                    listing.longitude = float(lon)
                except ValueError:
                    pass

        # Photos
        imgs = soup.select("img.slide[src]")
        photos = [img["src"] for img in imgs if "https" in img.get("src", "")]
        if photos:
            listing.photos = photos[:10]

        # Neighborhood from breadcrumb
        bc = soup.select_one(".breadcrumbs a[href*='search']")
        if bc:
            listing.neighborhood = listing.neighborhood or bc.get_text(strip=True)

        listing.city = "Arlington"
        listing.state = "VA"
        listing.detail_fetched = True

    # ── Unused abstract methods (Craigslist uses httpx, not Playwright) ───────

    async def _scrape_search(self, page: Page):  # pragma: no cover
        return []

    async def _fetch_detail(self, page: Page, listing: Listing):  # pragma: no cover
        pass
