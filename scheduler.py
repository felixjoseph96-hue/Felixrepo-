"""
Scraper scheduler.

Runs both scrapers on a configurable interval, upserts results into the
database, and triggers email notifications for newly found listings that
pass all hard filters.
"""
import asyncio
import logging
from datetime import datetime
from typing import List

from sqlalchemy.exc import IntegrityError

from config import Config
from filters import enrich_listing, passes_hard_filters, compute_score
from models import Listing, get_session, init_db
from notifier import send_notification
from scraper.apartments_com import ApartmentsComScraper
from scraper.zillow import ZillowScraper

logger = logging.getLogger(__name__)


# ─── Core scrape-and-store logic ──────────────────────────────────────────────

async def run_scraper_job() -> None:
    """
    Run one full scrape cycle:
      1. Fetch known listing IDs from DB (so we skip re-visiting detail pages).
      2. Scrape apartments.com and Zillow concurrently.
      3. Enrich each listing (metro score, gym, natural light).
      4. Upsert into DB, flagging brand-new listings.
      5. Send email digest for any newly found listings.
    """
    logger.info("=== Scrape job started ===")

    with get_session() as session:
        known = {
            (r.source, r.external_id)
            for r in session.query(Listing.source, Listing.external_id).all()
        }
        known_ids_apts = {eid for (src, eid) in known if src == "apartments_com"}
        known_ids_zillow = {eid for (src, eid) in known if src == "zillow"}

    # ── Scrape both sources concurrently ──────────────────────────────────────
    async def _run(scraper_cls, known_ids):
        async with scraper_cls(
            headless=Config.HEADLESS,
            max_detail_pages=Config.MAX_DETAIL_PAGES,
        ) as scraper:
            return await scraper.scrape(known_ids)

    results = await asyncio.gather(
        _run(ApartmentsComScraper, known_ids_apts),
        _run(ZillowScraper, known_ids_zillow),
        return_exceptions=True,
    )

    all_listings: List[Listing] = []
    for i, result in enumerate(results):
        source = ["apartments_com", "zillow"][i]
        if isinstance(result, Exception):
            logger.error("Scraper %s failed: %s", source, result)
        else:
            all_listings.extend(result)
            logger.info("Scraper %s returned %d listings", source, len(result))

    # ── Enrich + filter + persist ─────────────────────────────────────────────
    newly_added: List[Listing] = []

    with get_session() as session:
        for listing in all_listings:
            # Enrich with scoring data
            enrich_listing(listing)
            listing.scraped_at = datetime.utcnow()

            # Check if already in DB
            existing = (
                session.query(Listing)
                .filter_by(source=listing.source, external_id=listing.external_id)
                .first()
            )

            if existing:
                # Update mutable fields (price may change, detail may be richer)
                _update_existing(existing, listing)
                existing.is_new = False
            else:
                listing.is_new = True
                listing.first_seen_at = datetime.utcnow()
                session.add(listing)
                if passes_hard_filters(listing):
                    newly_added.append(listing)

        session.commit()

    logger.info(
        "Job complete: %d total scraped, %d new qualifying listings",
        len(all_listings), len(newly_added),
    )

    # ── Notify ────────────────────────────────────────────────────────────────
    if newly_added:
        # Sort by desirability score descending
        newly_added.sort(key=compute_score, reverse=True)
        await send_notification(newly_added)

        # Mark as notified
        with get_session() as session:
            for l in newly_added:
                obj = session.query(Listing).filter_by(
                    source=l.source, external_id=l.external_id
                ).first()
                if obj:
                    obj.is_notified = True
            session.commit()

    logger.info("=== Scrape job finished ===")


def _update_existing(existing: Listing, fresh: Listing) -> None:
    """Update only fields that may have changed or are now more complete."""
    if fresh.price_min is not None:
        existing.price_min = fresh.price_min
    if fresh.price_max is not None:
        existing.price_max = fresh.price_max
    if fresh.sqft_min is not None and existing.sqft_min is None:
        existing.sqft_min = fresh.sqft_min
        existing.sqft_max = fresh.sqft_max
    if fresh.detail_fetched and not existing.detail_fetched:
        existing.has_gym = fresh.has_gym
        existing.natural_light_score = fresh.natural_light_score
        existing.amenities = fresh.amenities
        existing.description = fresh.description
        existing.photos = fresh.photos or existing.photos
        existing.detail_fetched = True
    if fresh.latitude is not None and existing.latitude is None:
        existing.latitude = fresh.latitude
        existing.longitude = fresh.longitude
        existing.nearest_metro = fresh.nearest_metro
        existing.metro_distance_miles = fresh.metro_distance_miles
        existing.is_walkable = fresh.is_walkable
    existing.scraped_at = fresh.scraped_at


# ─── APScheduler wrapper ──────────────────────────────────────────────────────

def start_scheduler(app_instance=None):
    """
    Create and start an APScheduler AsyncIOScheduler.
    Runs run_scraper_job() immediately on start, then every SCRAPE_INTERVAL_MINUTES.
    Returns the scheduler so the caller can shut it down.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler(timezone="America/New_York")
    scheduler.add_job(
        run_scraper_job,
        trigger="interval",
        minutes=Config.SCRAPE_INTERVAL_MINUTES,
        id="scrape_job",
        name="Apartment scraper",
        max_instances=1,
        replace_existing=True,
        next_run_time=datetime.now(),  # run immediately on start
    )
    scheduler.start()
    logger.info(
        "Scheduler started — running every %d minutes", Config.SCRAPE_INTERVAL_MINUTES
    )
    return scheduler


# ─── CLI mode ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    init_db()
    asyncio.run(run_scraper_job())
