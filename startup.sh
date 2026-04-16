#!/usr/bin/env bash
# Startup script — runs inside the container / on the server before uvicorn.
set -e

echo "=== Arlington Apartment Finder ==="

# 1. Initialise / migrate the database
echo "[startup] Initialising database..."
python -c "from models import init_db; init_db(); print('[startup] Database ready.')"

# 2. If the database is empty, seed 40 demo listings so the dashboard isn't blank
LISTING_COUNT=$(python -c "
from models import get_session, Listing
with get_session() as s:
    print(s.query(Listing).count())
")

if [ "$LISTING_COUNT" -eq "0" ]; then
  echo "[startup] Database is empty — seeding demo listings..."
  python seed.py
fi

# 3. Start the web server
echo "[startup] Starting web server on port ${PORT:-8000}..."
exec python run.py
