"""
Zillow scraper.

Strategy
--------
1. Navigate to Zillow rentals search with filters encoded in the URL
   (searchQueryState JSON).
2. Extract listing data from the __NEXT_DATA__ script tag embedded in the page
   (Next.js server-side data).
3. Fall back to intercepting the /async-create-search-page-state XHR response.
4. For each new listing visit its detail page to enrich sqft, amenities,
   description, and coordinates.
"""
import json
import logging
import re
import urllib.parse
from typing import List, Optional

from playwright.async_api import Page, Response

from config import Config
from filters import parse_beds, parse_baths, parse_price, parse_sqft
from models import Listing
from scraper.base import BaseScraper

logger = logging.getLogger(__name__)

# ── Search URL ────────────────────────────────────────────────────────────────
# Encodes: for_rent, 2+ beds, 2+ baths, 1000+ sqft, ≤$3500/mo,
# map centred on the Rosslyn→Ballston corridor.
_SEARCH_STATE = {
    "pagination": {},
    "isMapVisible": False,
    "mapBounds": {
        "west": -77.130,
        "east": -77.050,
        "south": 38.870,
        "north": 38.910,
    },
    "filterState": {
        "fr":   {"value": True},   # for_rent
        "fsba": {"value": False},
        "fsbo": {"value": False},
        "nc":   {"value": False},
        "cmsn": {"value": False},
        "auc":  {"value": False},
        "fore": {"value": False},
        "beds": {"min": 2},
        "baths":{"min": 2},
        "sqft": {"min": 1000},
        "price":{"max": Config.MAX_PRICE},
        "mp":   {"max": Config.MAX_PRICE},
    },
    "isListVisible": True,
}

_SEARCH_URL = (
    "https://www.zillow.com/arlington-va/rentals/"
    "?searchQueryState=" + urllib.parse.quote(json.dumps(_SEARCH_STATE))
)


class ZillowScraper(BaseScraper):
    SOURCE = "zillow"

    # ── Search page ───────────────────────────────────────────────────────────

    async def _scrape_search(self, page: Page) -> List[Listing]:
        captured_json: list[dict] = []

        async def _intercept(response: Response):
            if ("zillow.com" in response.url and response.status == 200 and
                    any(k in response.url for k in ["GetSearchPageState", "searchQueryState"])):
                try:
                    data = await response.json()
                    captured_json.append(data)
                except Exception:
                    pass

        page.on("response", _intercept)

        logger.info("Zillow: navigating to search URL")
        await self._goto(page, _SEARCH_URL, wait="networkidle")
        await self._random_delay(2, 4)

        # Scroll to ensure all listing cards render
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await self._random_delay(1, 2)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self._random_delay(1, 2)

        # ── Primary: parse __NEXT_DATA__ ─────────────────────────────────────
        listings = await self._parse_next_data(page)
        if listings:
            logger.info("Zillow: parsed %d listings from __NEXT_DATA__", len(listings))
            return listings

        # ── Secondary: captured XHR JSON ─────────────────────────────────────
        if captured_json:
            for payload in captured_json:
                listings.extend(self._parse_api_payload(payload))
            if listings:
                logger.info("Zillow: parsed %d listings from XHR", len(listings))
                return listings

        # ── Fallback: DOM cards ───────────────────────────────────────────────
        logger.info("Zillow: falling back to DOM parsing")
        listings = await self._parse_dom(page)
        logger.info("Zillow: parsed %d listings from DOM", len(listings))
        return listings

    # ── __NEXT_DATA__ parser ──────────────────────────────────────────────────

    async def _parse_next_data(self, page: Page) -> List[Listing]:
        try:
            raw = await page.eval_on_selector(
                "script#__NEXT_DATA__", "el => el.textContent"
            )
            data = json.loads(raw)
        except Exception:
            return []

        # Navigate the Next.js props tree (structure varies by Zillow deployment)
        search_state = (
            data.get("props", {})
                .get("pageProps", {})
                .get("searchPageState")
            or data.get("props", {})
                .get("pageProps", {})
                .get("gdpClientCache")
        )

        if not search_state:
            return []

        return self._extract_from_search_state(search_state)

    def _extract_from_search_state(self, state: dict) -> List[Listing]:
        # Try multiple known paths inside the state object
        items = (
            state.get("cat1", {}).get("searchResults", {}).get("listResults", [])
            or state.get("searchResults", {}).get("listResults", [])
            or state.get("listResults", [])
            or []
        )

        listings: List[Listing] = []
        for item in items:
            try:
                listing = self._result_to_listing(item)
                if listing:
                    listings.append(listing)
            except Exception as exc:
                logger.debug("Skipping Zillow item: %s", exc)
        return listings

    def _result_to_listing(self, item: dict) -> Optional[Listing]:
        zpid = str(item.get("zpid") or item.get("id") or "")
        url = item.get("detailUrl") or item.get("url") or ""
        if not zpid or not url:
            return None

        if not url.startswith("http"):
            url = "https://www.zillow.com" + url

        raw_price = str(item.get("price") or item.get("unformattedPrice") or "")
        price_min, price_max = parse_price(raw_price)

        lat_long = item.get("latLong") or {}
        lat = lat_long.get("latitude") or item.get("latitude")
        lon = lat_long.get("longitude") or item.get("longitude")

        listing = Listing(
            source=self.SOURCE,
            external_id=zpid,
            url=url,
            title=item.get("address") or item.get("streetAddress") or "",
            address=item.get("address") or "",
            neighborhood=item.get("neighborhood") or item.get("hdpData", {}).get("homeInfo", {}).get("city"),
            latitude=float(lat) if lat else None,
            longitude=float(lon) if lon else None,
            price_min=price_min,
            price_max=price_max,
            bedrooms=_safe_float(item.get("beds")),
            bathrooms=_safe_float(item.get("baths")),
        )

        # Sqft may be available on search card
        sqft = item.get("area") or item.get("livingArea")
        if sqft:
            listing.sqft_min = int(sqft)

        # Photo
        img = item.get("imgSrc") or item.get("carouselPhotos", [{}])[0].get("url", "")
        if img:
            listing.photos = [img]

        return listing

    # ── XHR JSON parser ───────────────────────────────────────────────────────

    def _parse_api_payload(self, data: dict) -> List[Listing]:
        # The XHR response mirrors the __NEXT_DATA__ structure
        search_state = (
            data.get("searchPageState")
            or data.get("cat1", {}).get("searchResults", {})
            or data
        )
        return self._extract_from_search_state(search_state)

    # ── DOM parser ────────────────────────────────────────────────────────────

    async def _parse_dom(self, page: Page) -> List[Listing]:
        listings: List[Listing] = []

        # Zillow renders listing cards as <article> or <li> with data-test="property-card"
        cards = await page.query_selector_all(
            "[data-test='property-card'], article[class*='ListItem']"
        )

        for card in cards:
            try:
                listing = await self._parse_card(card)
                if listing:
                    listings.append(listing)
            except Exception as exc:
                logger.debug("Failed to parse Zillow card: %s", exc)

        return listings

    async def _parse_card(self, card) -> Optional[Listing]:
        link_el = await card.query_selector("a[href*='/homedetails/'], a[href*='zillow.com']")
        url = await link_el.get_attribute("href") if link_el else ""
        if not url:
            return None
        if not url.startswith("http"):
            url = "https://www.zillow.com" + url

        # Extract zpid from URL
        m = re.search(r"/(\d+)_zpid", url)
        ext_id = m.group(1) if m else url.split("/")[-2]

        listing = Listing(source=self.SOURCE, external_id=ext_id, url=url)

        addr_el = await card.query_selector("[data-test='property-card-addr'], address")
        listing.address = (await addr_el.inner_text()).strip() if addr_el else ""
        listing.title = listing.address

        price_el = await card.query_selector(
            "[data-test='property-card-price'], .list-card-price, [class*='price']"
        )
        raw_price = (await price_el.inner_text()).strip() if price_el else ""
        listing.price_min, listing.price_max = parse_price(raw_price)

        details_el = await card.query_selector(
            "[data-test='property-card-details'], .list-card-details"
        )
        if details_el:
            raw = await details_el.inner_text()
            listing.bedrooms = parse_beds(raw)
            listing.bathrooms = parse_baths(raw)
            listing.sqft_min, listing.sqft_max = parse_sqft(raw)

        img_el = await card.query_selector("img[src]")
        src = await img_el.get_attribute("src") if img_el else None
        listing.photos = [src] if src else []

        return listing

    # ── Detail page ───────────────────────────────────────────────────────────

    async def _fetch_detail(self, page: Page, listing: Listing) -> None:
        logger.debug("Zillow detail: %s", listing.url)
        await self._goto(page, listing.url, wait="domcontentloaded")
        await self._random_delay(2, 4)

        # ── Sqft ──────────────────────────────────────────────────────────────
        sqft_el = await page.query_selector(
            "[data-testid='bed-bath-sqft-fact-container'] [class*='sqft'], "
            "span:has-text('sqft'), [class*='sqft']"
        )
        if sqft_el and listing.sqft_min is None:
            listing.sqft_min, listing.sqft_max = parse_sqft(await sqft_el.inner_text())

        # ── Beds / baths from fact list ───────────────────────────────────────
        fact_els = await page.query_selector_all("[data-testid*='fact'], .fact-group-container li")
        for el in fact_els:
            text = (await el.inner_text()).lower()
            if "bed" in text and listing.bedrooms is None:
                listing.bedrooms = parse_beds(text)
            if "bath" in text and listing.bathrooms is None:
                listing.bathrooms = parse_baths(text)
            if "sqft" in text and listing.sqft_min is None:
                listing.sqft_min, listing.sqft_max = parse_sqft(text)

        # ── Amenities ─────────────────────────────────────────────────────────
        amenity_els = await page.query_selector_all(
            ".amenity-toggle-row li, [class*='HomeFeatures'] li, "
            "[data-testid='facts-features'] li"
        )
        amenities = [(await el.inner_text()).strip() for el in amenity_els if await el.inner_text()]
        if amenities:
            listing.amenities = amenities

        # ── Description ───────────────────────────────────────────────────────
        desc_el = await page.query_selector(
            "[data-testid='description'], .listing-description, "
            "[class*='description'], section:has-text('About This Home')"
        )
        if desc_el:
            listing.description = (await desc_el.inner_text()).strip()

        # ── Photos ────────────────────────────────────────────────────────────
        photo_els = await page.query_selector_all(
            "[class*='media-gallery'] img[src], "
            "[class*='photo-carousel'] img[src]"
        )
        photos = []
        for el in photo_els:
            src = await el.get_attribute("src")
            if src and src.startswith("http") and "placeholder" not in src:
                photos.append(src)
        if photos:
            listing.photos = photos[:12]

        # ── Coordinates from __NEXT_DATA__ ────────────────────────────────────
        if listing.latitude is None:
            try:
                raw = await page.eval_on_selector(
                    "script#__NEXT_DATA__", "el => el.textContent"
                )
                data = json.loads(raw)
                home_info = (
                    data.get("props", {})
                        .get("pageProps", {})
                        .get("componentProps", {})
                        .get("gdpClientCache", {})
                )
                for v in home_info.values() if isinstance(home_info, dict) else []:
                    if isinstance(v, dict):
                        lat = v.get("property", {}).get("latitude")
                        lon = v.get("property", {}).get("longitude")
                        if lat and lon:
                            listing.latitude = lat
                            listing.longitude = lon
                            break
            except Exception:
                pass

        listing.detail_fetched = True


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _safe_float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None
