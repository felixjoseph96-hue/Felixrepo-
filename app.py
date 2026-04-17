"""
FastAPI web dashboard for the apartment scraper.

Routes
------
GET  /                              HTML dashboard
GET  /api/listings                  JSON listing data
GET  /api/listings/{id}             Full listing detail + price history
POST /api/listings/{id}/favorite    Toggle favorite flag
POST /api/listings/{id}/notes       Save user notes
POST /api/listings/{id}/dismiss     Clear "new" flag
GET  /api/export.csv                Download all qualifying listings as CSV
GET  /api/map-data                  Listing coords + metro stations for map
GET  /api/stats                     Summary counts
POST /api/scrape                    Trigger a manual scrape run
"""
import asyncio
import csv
import io
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from config import Config
from filters import compute_score, passes_hard_filters
from models import Listing, PriceHistory, get_session, init_db
from scheduler import run_scraper_job, start_scheduler

logger = logging.getLogger(__name__)

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
templates.env.filters["compute_score"] = compute_score
templates.env.filters["format_num"] = lambda v: f"{int(v):,}" if v else "0"


def get_db():
    with get_session() as session:
        yield session


# ─── Shared query helper ──────────────────────────────────────────────────────

def _query_listings(
    db: DBSession,
    source: Optional[str] = None,
    max_price: Optional[int] = None,
    walkable_only: bool = False,
    gym_only: bool = False,
    new_only: bool = False,
    favorites_only: bool = False,
    min_light: int = 0,
    available_by: Optional[str] = None,
    hide_bad_mgmt: bool = False,
    sort: str = "score",
    limit: int = 50,
    offset: int = 0,
) -> list[Listing]:
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
    if favorites_only:
        q = q.filter(Listing.is_favorited == True)
    if min_light > 0:
        q = q.filter(Listing.natural_light_score >= min_light)
    if available_by:
        from datetime import datetime as _dt
        try:
            cutoff = _dt.fromisoformat(available_by)
            q = q.filter(
                (Listing.date_available <= cutoff) | (Listing.date_available.is_(None))
            )
        except ValueError:
            pass
    if hide_bad_mgmt:
        q = q.filter((Listing.bad_management == False) | (Listing.bad_management.is_(None)))

    if sort == "price":
        q = q.order_by(Listing.price_min.asc().nullslast())
    elif sort == "sqft":
        q = q.order_by(Listing.sqft_min.desc().nullslast())
    elif sort == "newest":
        q = q.order_by(Listing.first_seen_at.desc())
    else:
        q = q.order_by(Listing.first_seen_at.desc())

    rows = q.offset(offset).limit(limit * 3).all()

    if sort == "score":
        rows.sort(key=compute_score, reverse=True)

    return rows[:limit]


def _enrich(listing: Listing) -> dict:
    d = listing.to_dict()
    d["score"] = compute_score(listing)
    return d


# ─── HTML dashboard ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: DBSession = Depends(get_db)):
    listings = _query_listings(db, sort="score", limit=200)
    qualifying = [l for l in listings if passes_hard_filters(l)]

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    stats = {
        "total":      db.query(Listing).count(),
        "new_today":  db.query(Listing).filter(Listing.first_seen_at >= today).count(),
        "qualifying": len(qualifying),
        "walkable":   sum(1 for l in qualifying if l.is_walkable),
        "has_gym":    sum(1 for l in qualifying if l.has_gym),
        "favorites":  db.query(Listing).filter(Listing.is_favorited == True).count(),
        "last_scrape": max(
            (l.scraped_at for l in listings if l.scraped_at), default=None
        ),
        "next_scrape_minutes": Config.SCRAPE_INTERVAL_MINUTES,
    }

    return templates.TemplateResponse(request, "index.html", context={
        "listings": qualifying,
        "stats": stats,
        "config": {
            "max_price":     Config.MAX_PRICE,
            "min_sqft":      Config.MIN_SQFT,
            "min_beds":      int(Config.MIN_BEDROOMS),
            "min_baths":     Config.MIN_BATHROOMS,
            "max_metro_walk": Config.MAX_METRO_WALK_MILES,
        },
        "metro_stations": Config.METRO_STATIONS,
    })


# ─── Listing list (JSON) ─────────────────────────────────────────────────────

@app.get("/api/listings")
async def api_listings(
    db: DBSession = Depends(get_db),
    source: Optional[str] = Query(None),
    max_price: Optional[int] = Query(None),
    walkable: bool = Query(False),
    gym: bool = Query(False),
    new_only: bool = Query(False),
    favorites_only: bool = Query(False),
    min_light: int = Query(0),
    available_by: Optional[str] = Query(None),
    sort: str = Query("score", pattern="^(score|price|sqft|newest)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    listings = _query_listings(
        db,
        source=source,
        max_price=max_price,
        walkable_only=walkable,
        gym_only=gym,
        new_only=new_only,
        favorites_only=favorites_only,
        min_light=min_light,
        available_by=available_by,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    qualifying = [l for l in listings if passes_hard_filters(l)]
    return JSONResponse({
        "count": len(qualifying),
        "has_more": len(qualifying) == limit,
        "listings": [_enrich(l) for l in qualifying],
    })


# ─── Listing detail ───────────────────────────────────────────────────────────

@app.get("/api/listings/{listing_id}")
async def api_listing_detail(listing_id: int, db: DBSession = Depends(get_db)):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    d = listing.to_dict(include_detail=True)
    d["score"] = compute_score(listing)
    return JSONResponse(d)


# ─── Favorite toggle ─────────────────────────────────────────────────────────

@app.post("/api/listings/{listing_id}/favorite")
async def toggle_favorite(listing_id: int, db: DBSession = Depends(get_db)):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing.is_favorited = not listing.is_favorited
    db.commit()
    return JSONResponse({"is_favorited": listing.is_favorited})


# ─── Notes save ──────────────────────────────────────────────────────────────

@app.post("/api/listings/{listing_id}/notes")
async def save_notes(listing_id: int, request: Request, db: DBSession = Depends(get_db)):
    body = await request.json()
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing.notes = body.get("notes", "")
    db.commit()
    return JSONResponse({"status": "saved"})


# ─── Dismiss ─────────────────────────────────────────────────────────────────

@app.post("/api/listings/{listing_id}/dismiss")
async def dismiss_listing(listing_id: int, db: DBSession = Depends(get_db)):
    listing = db.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing.is_new = False
    db.commit()
    return JSONResponse({"status": "dismissed"})


# ─── CSV export ───────────────────────────────────────────────────────────────

@app.get("/api/export.csv")
async def export_csv(db: DBSession = Depends(get_db)):
    listings = _query_listings(db, sort="score", limit=500)
    qualifying = [l for l in listings if passes_hard_filters(l)]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Source", "Title", "Address", "Neighborhood",
        "Price Min", "Price Max", "Beds", "Baths", "Sqft Min",
        "Nearest Metro", "Metro Distance (mi)", "Walkable",
        "Has Gym", "Natural Light Score", "Score", "Favorited",
        "First Seen", "URL",
    ])
    for l in qualifying:
        writer.writerow([
            l.source, l.title or "", l.address or "", l.neighborhood or "",
            l.price_min or "", l.price_max or "",
            l.bedrooms or "", l.bathrooms or "", l.sqft_min or "",
            l.nearest_metro or "",
            f"{l.metro_distance_miles:.2f}" if l.metro_distance_miles else "",
            "Yes" if l.is_walkable else "No",
            "Yes" if l.has_gym else "No",
            l.natural_light_score,
            compute_score(l),
            "Yes" if l.is_favorited else "No",
            l.first_seen_at.strftime("%Y-%m-%d %H:%M") if l.first_seen_at else "",
            l.url,
        ])

    output.seek(0)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=arlington_apts_{ts}.csv"},
    )


# ─── Map data ─────────────────────────────────────────────────────────────────

@app.get("/api/map-data")
async def map_data(db: DBSession = Depends(get_db)):
    listings = _query_listings(db, sort="score", limit=200)
    qualifying = [l for l in listings if passes_hard_filters(l) and l.latitude and l.longitude]

    return JSONResponse({
        "listings": [
            {
                "id": l.id,
                "title": l.title or l.address or "",
                "address": l.address or "",
                "lat": l.latitude,
                "lon": l.longitude,
                "price_min": l.price_min,
                "beds": l.bedrooms,
                "baths": l.bathrooms,
                "sqft_min": l.sqft_min,
                "is_walkable": l.is_walkable,
                "has_gym": l.has_gym,
                "natural_light_score": l.natural_light_score,
                "score": compute_score(l),
                "is_favorited": l.is_favorited,
                "nearest_metro": l.nearest_metro,
                "metro_distance_miles": round(l.metro_distance_miles, 2) if l.metro_distance_miles else None,
            }
            for l in qualifying
        ],
        "metro_stations": Config.METRO_STATIONS,
    })


# ─── Stats ────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def api_stats(db: DBSession = Depends(get_db)):
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return JSONResponse({
        "total":     db.query(Listing).count(),
        "new_today": db.query(Listing).filter(Listing.first_seen_at >= today).count(),
        "favorites": db.query(Listing).filter(Listing.is_favorited == True).count(),
        "scrape_interval_minutes": Config.SCRAPE_INTERVAL_MINUTES,
    })


# ─── Manual scrape trigger ────────────────────────────────────────────────────

@app.post("/api/scrape")
async def trigger_scrape():
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
