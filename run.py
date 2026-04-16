#!/usr/bin/env python3
"""
Entry point for the Arlington Apartment Finder.

Usage
-----
# Install dependencies first (once):
#   pip install -r requirements.txt
#   playwright install chromium

# Copy and fill in your settings:
#   cp .env.example .env

# Start the app:
#   python run.py

# The dashboard will be available at http://localhost:8000
# The scraper runs automatically every SCRAPE_INTERVAL_MINUTES minutes.
# You can also trigger a manual scrape via the "↻ Scrape Now" button.

# To run a one-shot scrape without the web UI:
#   python scheduler.py
"""
import logging
import sys

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("apartment_finder.log"),
    ],
)

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
