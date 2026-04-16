"""
FastAPI web dashboard for the apartment scraper.

Routes
------
GET /              — HTML listing dashboard
GET /api/listings  — JSON listing data (for AJAX refresh)
GET /api/stats     — Summary statistics
POST /api/scrape   — Trigger a manual scrape run (async)
POST /api/listings/{id}/dismiss — Hide a listing from the dashboard
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Query, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc
from sqlalchemy.orm import Session as DBSession

from config import Config
from filters import compute_score, passes_hard_filters
from models import Listing, get_session, init_db
from scheduler import run_scraper_job, start_scheduler

logger = logging.getLogger(__name__)

# ─── Lifespan: init DB + start scheduler ─────────────────────────────────────

_scheduler = None
_scrape_running = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    init_db()
    _scheduler = start_scheduler()
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(
    title="Arlington Apartment Finder",
    description="Real-time scraper for Rosslyn / Clarendon / Ballston rentals",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Register custom Jinja2 filters
templates.env.filters["compute_score"] = compute_score
templates.env.filters["format_num"] = lambda v: f"{int(v):,}" if v else "0"


# ─── DB dependency ────────────────────────────────────────────────────────────

def get_db():
    with get_session() as session:
        yield session


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _query_listings(
    db: DBSession,
    source: Optional[str] = None,
    max_price: Optional[int] = None,
    walkable_only: bool = False,
    gym_only: bool = False,
    new_only: bool = False,
    min_light: int = 0,
    sort: str = "score",
    limit: int = 100,
    offset: int = 0,
):
    q = db.query(Listing)

    if source:
        q = q.filter(Listing.source == source)
    if max_price:
        q = q.filter((Listing.price_min <= max_price) | (Listing.price_min.is_(None)))
    if walkable_only:
        q = q.filter(Listing.is_walkable == True)
    if gym_only:
        q = q.filter(Listing.has_gym == True)
    if new_only:
        q = q.filter(Listing.is_new == True)
    if min_light > 0:
        q = q.filter(Listing.natural_light_score >= min_light)

    if sort == "price":
        q = q.order_by(Listing.price_min.asc().nullslast())
    elif sort == "sqft":
        q = q.order_by(Listing.sqft_min.desc().nullslast())
    elif sort == "newest":
        q = q.order_by(Listing.first_seen_at.desc())
    else:
        # Default: sort by composite score (computed in Python after fetch)
        q = q.order_by(Listing.first_seen_at.desc())

    listings = q.offset(offset).limit(limit * 3).all()  # over-fetch for score sort

    # Apply score sort in Python (score is computed, not stored)
    if sort == "score":
        listings.sort(key=compute_score, reverse=True)

    return listings[:limit]


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: DBSession = Depends(get_db)):
    """Main dashboard page."""
    listings = _query_listings(db, sort="score", limit=200)
    qualifying = [l for l in listings if passes_hard_filters(l)]

    stats = {
        "total": db.query(Listing).count(),
        "new_today": db.query(Listing).filter(
            Listing.first_seen_at >= datetime.utcnow().replace(hour=0, minute=0, second=0)
        ).count(),
        "qualifying": len(qualifying),
        "walkable": sum(1 for l in qualifying if l.is_walkable),
        "has_gym": sum(1 for l in qualifying if l.has_gym),
        "last_scrape": max(
            (l.scraped_at for l in listings if l.scraped_at), default=None
        ),
        "next_scrape_minutes": Config.SCRAPE_INTERVAL_MINUTES,
    }

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "listings": qualifying,
            "all_listings": listings,
            "stats": stats,
            "config": {
                "max_price": Config.MAX_PRICE,
                "min_sqft": Config.MIN_SQFT,
                "min_beds": int(Config.MIN_BEDROOMS),
                "min_baths": Config.MIN_BATHROOMS,
                "max_metro_walk": Config.MAX_METRO_WALK_MILES,
            },
        },
    )


@app.get("/api/listings")
async def api_listings(
    db: DBSession = Depends(get_db),
    source: Optional[str] = Query(None, description="apartments_com | zillow"),
    max_price: Optional[int] = Query(None),
    walkable: bool = Query(False),
    gym: bool = Query(False),
    new_only: bool = Query(False),
    min_light: int = Query(0),
    sort: str = Query("score", regex="^(score|price|sqft|newest)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """JSON endpoint for listing data."""
    listings = _query_listings(
        db,
        source=source,
        max_price=max_price,
        walkable_only=walkable,
        gym_only=gym,
        new_only=new_only,
        min_light=min_light,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    qualifying = [l for l in listings if passes_hard_filters(l)]
    result = []
    for l in qualifying:
        d = l.to_dict()
        d["score"] = compute_score(l)
        result.append(d)
    return JSONResponse({"count": len(result), "listings": result})


@app.get("/api/stats")
async def api_stats(db: DBSession = Depends(get_db)):
    """Summary stats for the status bar."""
    total = db.query(Listing).count()
    new_today = db.query(Listing).filter(
        Listing.first_seen_at >= datetime.utcnow().replace(hour=0, minute=0, second=0)
    ).count()
    return JSONResponse({
        "total": total,
        "new_today": new_today,
        "scrape_interval_minutes": Config.SCRAPE_INTERVAL_MINUTES,
    })


@app.post("/api/scrape")
async def trigger_scrape():
    """Manually trigger a scrape run (non-blocking)."""
    global _scrape_running
    if _scrape_running:
        return JSONResponse({"status": "already_running"}, status_code=409)

    async def _run():
        global _scrape_running
        _scrape_running = True
        try:
            await run_scraper_job()
        except Exception as exc:
            logger.error("Manual scrape failed: %s", exc)
        finally:
            _scrape_running = False

    asyncio.create_task(_run())
    return JSONResponse({"status": "started"})


@app.post("/api/listings/{listing_id}/dismiss")
async def dismiss_listing(listing_id: int, db: DBSession = Depends(get_db)):
    """Mark a listing as no longer new (removes it from the 'new' filter)."""
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing.is_new = False
    db.commit()
    return JSONResponse({"status": "dismissed"})
