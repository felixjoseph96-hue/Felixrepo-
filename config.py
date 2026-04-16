"""Central configuration loaded from environment / .env file."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///apartments.db")

    # Email
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    NOTIFY_EMAIL: str = os.getenv("NOTIFY_EMAIL", "")

    # Scraper behaviour
    SCRAPE_INTERVAL_MINUTES: int = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "30"))
    HEADLESS: bool = os.getenv("HEADLESS", "true").lower() == "true"
    MAX_DETAIL_PAGES: int = int(os.getenv("MAX_DETAIL_PAGES", "20"))

    # Search criteria
    MAX_PRICE: int = int(os.getenv("MAX_PRICE", "3500"))
    MIN_BEDROOMS: float = float(os.getenv("MIN_BEDROOMS", "2"))
    MIN_BATHROOMS: float = float(os.getenv("MIN_BATHROOMS", "2"))
    MIN_SQFT: int = int(os.getenv("MIN_SQFT", "1000"))
    MAX_METRO_WALK_MILES: float = float(os.getenv("MAX_METRO_WALK_MILES", "0.5"))

    # Target Metro stations (Orange / Blue / Silver line – Arlington corridor)
    METRO_STATIONS: list[dict] = [
        {"name": "Rosslyn",        "lat": 38.8960, "lon": -77.0706},
        {"name": "Court House",    "lat": 38.8893, "lon": -77.0854},
        {"name": "Clarendon",      "lat": 38.8863, "lon": -77.0963},
        {"name": "Virginia Square","lat": 38.8842, "lon": -77.1036},
        {"name": "Ballston-MU",    "lat": 38.8822, "lon": -77.1118},
    ]

    # Bounding box for the Rosslyn → Ballston corridor
    SEARCH_BOUNDS = {
        "north": 38.910,
        "south": 38.870,
        "east":  -77.050,
        "west":  -77.130,
    }

    # Natural-light keyword weights
    NATURAL_LIGHT_KEYWORDS: list[tuple[str, int]] = [
        ("floor-to-ceiling windows", 3),
        ("wall-to-wall windows", 3),
        ("panoramic windows", 3),
        ("natural light", 2),
        ("natural lighting", 2),
        ("sun-drenched", 2),
        ("sun drenched", 2),
        ("sunlit", 2),
        ("bright and airy", 2),
        ("south-facing", 2),
        ("east-facing", 1),
        ("west-facing", 1),
        ("large windows", 1),
        ("oversized windows", 1),
        ("bright", 1),
    ]

    # Gym / fitness keywords
    GYM_KEYWORDS: list[str] = [
        "fitness center",
        "fitness room",
        "fitness studio",
        "gym",
        "weight room",
        "workout room",
        "exercise room",
        "health club",
    ]
