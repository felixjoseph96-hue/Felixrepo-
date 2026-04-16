# ── Arlington Apartment Finder ────────────────────────────────────────────────
# Uses the official Playwright Python image so Chromium is pre-installed and
# the Apartments.com / Zillow / Craigslist scrapers work out of the box.
# For RentCast-API-only mode (no browser), you can swap the base image to
# python:3.11-slim and remove the PLAYWRIGHT_BROWSERS_PATH line.

FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# ── System setup ─────────────────────────────────────────────────────────────
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    LOG_FILE=false \
    DATABASE_URL=sqlite:////data/apartments.db

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# ── Persistent data directory (mount a volume here) ──────────────────────────
RUN mkdir -p /data

# ── Port ─────────────────────────────────────────────────────────────────────
EXPOSE 8000

# ── Startup ───────────────────────────────────────────────────────────────────
CMD ["bash", "startup.sh"]
