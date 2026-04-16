"""Abstract base class shared by all site scrapers."""
import asyncio
import glob as _glob
import logging
import os
import random
from abc import ABC, abstractmethod
from typing import List, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from models import Listing


def _find_chromium_executable() -> Optional[str]:
    """
    Locate a usable Chromium binary.
    Checks PLAYWRIGHT_BROWSERS_PATH directories first, then falls back
    to system-installed Chromium.
    """
    # Playwright browser search paths
    search_roots = [
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
        os.path.expanduser("~/.cache/ms-playwright"),
        "/opt/pw-browsers",
        "/ms-playwright",
    ]
    for root in filter(None, search_roots):
        for exe in _glob.glob(os.path.join(root, "chromium-*/chrome-linux/chrome")):
            if os.access(exe, os.X_OK):
                return exe

    # System Chromium fallbacks
    for candidate in ["/usr/bin/chromium-browser", "/usr/bin/chromium",
                      "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]:
        if os.access(candidate, os.X_OK):
            return candidate

    return None

logger = logging.getLogger(__name__)

# Realistic desktop viewport sizes
_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
]

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]


class BaseScraper(ABC):
    """
    Async base scraper that manages a Playwright browser session.

    Subclasses implement:
      - search_url   (property) → str
      - _parse_search_results(page) → List[Listing]   (search-results page)
      - _parse_detail_page(page, listing) → Listing   (individual listing page)
    """

    SOURCE: str = "unknown"

    def __init__(self, headless: bool = True, max_detail_pages: int = 20):
        self.headless = headless
        self.max_detail_pages = max_detail_pages
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        launch_kwargs: dict = dict(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--ignore-certificate-errors",
                "--ignore-ssl-errors",
            ],
        )
        exe = _find_chromium_executable()
        if exe:
            logger.info("Using Chromium: %s", exe)
            launch_kwargs["executable_path"] = exe
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        ua = random.choice(_USER_AGENTS)
        vp = random.choice(_VIEWPORTS)
        self._context = await self._browser.new_context(
            user_agent=ua,
            viewport=vp,
            locale="en-US",
            timezone_id="America/New_York",
            ignore_https_errors=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
            },
        )
        # Patch navigator.webdriver to avoid easy bot detection
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return self

    async def __aexit__(self, *_):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        await self._playwright.stop()

    # ── Public interface ───────────────────────────────────────────────────────

    async def scrape(self, known_ids: set[str]) -> List[Listing]:
        """
        Run a full scrape cycle.

        Args:
            known_ids: set of external_id strings already in the DB so we
                       skip fetching their detail pages again.
        Returns:
            List of Listing objects (unsaved) ready to be upserted into DB.
        """
        page = await self._new_page()
        try:
            listings = await self._scrape_search(page)
        finally:
            await page.close()

        # Fetch detail pages for new listings only (rate-limit friendly)
        new_listings = [l for l in listings if l.external_id not in known_ids]
        logger.info(
            "%s: %d total, %d new -> fetching detail pages (max %d)",
            self.SOURCE, len(listings), len(new_listings), self.max_detail_pages,
        )

        for listing in new_listings[: self.max_detail_pages]:
            detail_page = await self._new_page()
            try:
                await self._fetch_detail(detail_page, listing)
            except Exception as exc:
                logger.warning("Detail page failed for %s: %s", listing.url, exc)
            finally:
                await detail_page.close()
            await self._random_delay(1.5, 3.5)

        return listings

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _new_page(self) -> Page:
        page = await self._context.new_page()
        # Block heavy assets to speed up loading & reduce fingerprint
        await page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("font", "media")
            else route.continue_(),
        )
        return page

    async def _goto(self, page: Page, url: str, wait: str = "domcontentloaded") -> None:
        """Navigate with retry logic."""
        for attempt in range(3):
            try:
                await page.goto(url, wait_until=wait, timeout=30_000)
                return
            except Exception as exc:
                if attempt == 2:
                    raise
                logger.warning("Navigation attempt %d failed: %s", attempt + 1, exc)
                await asyncio.sleep(2 ** attempt)

    @staticmethod
    async def _random_delay(lo: float = 1.0, hi: float = 3.0) -> None:
        await asyncio.sleep(random.uniform(lo, hi))

    # ── Abstract methods ───────────────────────────────────────────────────────

    @abstractmethod
    async def _scrape_search(self, page: Page) -> List[Listing]:
        """Navigate to the search URL and return a list of partially-filled Listing objects."""

    @abstractmethod
    async def _fetch_detail(self, page: Page, listing: Listing) -> None:
        """Visit listing.url and enrich the listing with sqft, amenities, description."""
