#!/usr/bin/env python3
"""
Entry point for the Arlington Apartment Finder.

Local usage
-----------
    pip install -r requirements.txt
    playwright install chromium      # only needed for Apts.com / Zillow scrapers
    cp .env.example .env             # fill in RENTCAST_API_KEY at minimum
    python run.py                    # dashboard at http://localhost:8000

One-shot scrape (no web server):
    python scheduler.py

Seed 40 demo listings instantly:
    python seed.py
"""
import logging
import os
import sys

import uvicorn

# Cloud platforms (Railway, Render, Fly.io) inject PORT at runtime
PORT = int(os.getenv("PORT", "8000"))

# Log to stdout only when running in a container (LOG_FILE=false)
handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
if os.getenv("LOG_FILE", "true").lower() != "false":
    try:
        handlers.append(logging.FileHandler("apartment_finder.log"))
    except OSError:
        pass  # read-only filesystem (some containers)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=handlers,
)

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
        log_level="info",
    )
