"""
afl_weather.py
==============
Weather backfill for AFL games (fills the gap left by the 2026 fallback
source, which has no match_weather_type).

Writes to its own table, afl_game_weather, so the nightly Fryzigg /
player-stats upsert never overwrites it.

Source: Open-Meteo historical archive (archive-api.open-meteo.com, no key).

Game window:
  - afl_games has a bounce time  -> 3 hours from bounce
  - date only (time missing)     -> 12:00 to 22:00 local

Tagging:
  - Indoor venue (Docklands / Marvel)      -> DRY
  - rain_mm >= AFL_RAIN_THRESHOLD_MM       -> RAIN
  - otherwise                              -> DRY
RAIN matches Fryzigg's label, so `weather_type = 'RAIN'` works across seasons.

One-off backfill:
    python afl_weather.py 2026            # only games not yet tagged
    python afl_weather.py 2026 --force    # re-fetch every 2026 game
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import requests

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# CONFIG (override via env vars)
# ─────────────────────────────────────────────

RAIN_THRESHOLD_MM = float(os.environ.get("AFL_RAIN_THRESHOLD_MM", "1.0"))
REQUEST_DELAY_SECONDS = float(os.environ.get("AFL_WEATHER_REQUEST_DELAY", "1.0"))
MAX_RETRIES = int(os.environ.get("AFL_WEATHER_MAX_RETRIES", "4"))

GAME_WINDOW_HOURS = 3
DATE_ONLY_WINDOW = (12, 22)  # local hours, used when bounce time is unknown

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_SOURCE = "open-meteo-archive"
INDOOR_SOURCE = "indoor-venue"

WEATHER_RAIN = "RAIN"
WEATHER_DRY = "DRY"


# ─────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────

AFL_GAME_WEATHER_SCHEMA = """
CREATE TABLE IF NOT EXISTS afl_game_weather (
    game_id         INTEGER PRIMARY KEY,
    game_date       DATE NOT NULL,
    home_team       TEXT NOT NULL,
    away_team       TEXT NOT NULL,
    venue           TEXT,
    window_start    TIMESTAMP,
    window_end      TIMESTAMP,
    rain_mm         FLOAT,
    temp_c          FLOAT,
    weather_type    TEXT NOT NULL,
    is_indoor       BOOLEAN DEFAULT FALSE,
    source          TEXT NOT NULL,
    fetched_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE (game_date, home_team, away_team)
)
"""


# ─────────────────────────────────────────────
# VENUES
# Keys are afl_games.venue values (Squiggle names), plus common aliases.
# ─────────────────────────────────────────────

AFL_VENUES: dict[str, dict] = {
    "Adelaide Oval":     {"lat": -34.9156, "lon": 138.5961, "tz": "Australia/Adelaide"},
    "Adelaide Hills":    {"lat": -35.0776, "lon": 138.8494, "tz": "Australia/Adelaide"},
    "Barossa Park":      {"lat": -34.6010, "lon": 138.8880, "tz": "Australia/Adelaide"},
    "Norwood Oval":      {"lat": -34.9205, "lon": 138.6315, "tz": "Australia/Adelaide"},
    "Bellerive Oval":    {"lat": -42.8773, "lon": 147.3736, "tz": "Australia/Hobart"},
    "York Park":         {"lat": -41.4260, "lon": 147.1390, "tz": "Australia/Hobart"},
    "Carrara":           {"lat": -28.0063, "lon": 153.3667, "tz": "Australia/Brisbane"},
    "Gabba":             {"lat": -27.4858, "lon": 153.0381, "tz": "Australia/Brisbane"},
    "Cazaly's Stadium":  {"lat": -16.9357, "lon": 145.7497, "tz": "Australia/Brisbane"},
    "Docklands":         {"lat": -37.8165, "lon": 144.9475, "tz": "Australia/Melbourne", "indoor": True},
    "M.C.G.":            {"lat": -37.8200, "lon": 144.9834, "tz": "Australia/Melbourne"},
    "Kardinia Park":     {"lat": -38.1580, "lon": 144.3547, "tz": "Australia/Melbourne"},
    "Eureka Stadium":    {"lat": -37.5395, "lon": 143.8481, "tz": "Australia/Melbourne"},
    "S.C.G.":            {"lat": -33.8917, "lon": 151.2247, "tz": "Australia/Sydney"},
    "Sydney Showground": {"lat": -33.8433, "lon": 151.0674, "tz": "Australia/Sydney"},
    "Stadium Australia": {"lat": -33.8472, "lon": 151.0634, "tz": "Australia/Sydney"},
    "Manuka Oval":       {"lat": -35.3181, "lon": 149.1350, "tz": "Australia/Sydney"},
    "Perth Stadium":     {"lat": -31.9512, "lon": 115.8890, "tz": "Australia/Perth"},
    "Hands Oval":        {"lat": -33.3380, "lon": 115.6386, "tz": "Australia/Perth"},
    "Marrara Oval":      {"lat": -12.3990, "lon": 130.8870, "tz": "Australia/Darwin"},
    "Traeger Park":      {"lat": -23.7075, "lon": 133.8760, "tz": "Australia/Darwin"},
}

VENUE_ALIASES = {
    "Marvel Stadium": "Docklands",
    "Etihad Stadium": "Docklands",
    "MCG": "M.C.G.",
    "SCG": "S.C.G.",
    "Optus Stadium": "Perth Stadium",
    "GMHBA Stadium": "Kardinia Park",
    "Metricon Stadium": "Carrara",
    "People First Stadium": "Carrara",
    "Heritage Bank Stadium": "Carrara",
    "Blundstone Arena": "Bellerive Oval",
    "Ninja Stadium": "Bellerive Oval",
    "University of Tasmania Stadium": "York Park",
    "UTAS Stadium": "York Park",
    "GIANTS Stadium": "Sydney Showground",
    "ENGIE Stadium": "Sydney Showground",
    "UNSW Canberra Oval": "Manuka Oval",
    "Mars Stadium": "Eureka Stadium",
    "TIO Stadium": "Marrara Oval",
    "TIO Traeger Park": "Traeger Park",
    "ANZ Stadium": "Stadium Australia",
    "Accor Stadium": "Stadium Australia",
}


def get_venue(name: str | None) -> dict | None:
    if not name:
        return None
    name = name.strip()
    return AFL_VENUES.get(name) or AFL_VENUES.get(VENUE_ALIASES.get(name, ""))


# ─────────────────────────────────────────────
# PURE HELPERS
# ─────────────────────────────────────────────

def classify_weather(rain_mm: float | None, indoor: bool = False,
                     threshold: float | None = None) -> str:
    if indoor:
        return WEATHER_DRY
    limit = RAIN_THRESHOLD_MM if threshold is None else threshold
    if rain_mm is not None and rain_mm >= limit:
        return WEATHER_RAIN
    return WEATHER_DRY


def _parse_bounce(game_dt, localtime_text) -> datetime | None:
    """Return the local bounce datetime, or None if only a date is known."""
    for value in (game_dt, localtime_text):
        if isinstance(value, datetime):
            if (value.hour, value.minute) != (0, 0):
                return value.replace(tzinfo=None)
        elif isinstance(value, str) and value.strip():
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
                try:
                    parsed = datetime.strptime(value.strip(), fmt)
                except ValueError:
                    continue
                if (parsed.hour, parsed.minute) != (0, 0):
                    return parsed
    return None


def game_window(game_date: date, bounce: datetime | None) -> tuple[datetime, datetime]:
    """Local start/end of the period to sum rain over."""
    if bounce is not None:
        return bounce, bounce + timedelta(hours=GAME_WINDOW_HOURS)
    start = datetime.combine(game_date, datetime.min.time())
    return (start + timedelta(hours=DATE_ONLY_WINDOW[0]),
            start + timedelta(hours=DATE_ONLY_WINDOW[1]))


def summarise_window(hourly: dict, start: datetime, end: datetime) -> tuple[float | None, float | None]:
    """
    Sum rain and average temperature over [start, end].

    Open-Meteo hourly precipitation at time T is the total for the hour
    ending at T, so the hour covering start..start+1h is labelled start+1h.
    Includes every hour that overlaps the window.
    Returns (None, None) when the archive has no data yet.
    """
    first_label = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    last_label = end.replace(minute=0, second=0, microsecond=0)
    if end > last_label:
        last_label += timedelta(hours=1)

    times = hourly.get("time") or []
    rain = hourly.get("precipitation") or []
    temps = hourly.get("temperature_2m") or []

    rain_vals, temp_vals = [], []
    for i, t in enumerate(times):
        ts = datetime.fromisoformat(t)
        if first_label <= ts <= last_label:
            if i < len(rain) and rain[i] is not None:
                rain_vals.append(float(rain[i]))
            # Temperature is instantaneous — use readings inside the window.
            if start <= ts <= end and i < len(temps) and temps[i] is not None:
                temp_vals.append(float(temps[i]))

    rain_mm = round(sum(rain_vals), 2) if rain_vals else None
    temp_c = round(sum(temp_vals) / len(temp_vals), 1) if temp_vals else None
    return rain_mm, temp_c


# ─────────────────────────────────────────────
# OPEN-METEO
# ─────────────────────────────────────────────

def fetch_hourly_weather(lat: float, lon: float, tz: str,
                         start_date: date, end_date: date) -> dict | None:
    """Fetch hourly rain + temp in local time. Retries with backoff."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "hourly": "precipitation,temperature_2m",
        "timezone": tz,
    }
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=30)
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.RequestException(f"HTTP {response.status_code}")
            if response.status_code >= 400:
                logger.warning("Open-Meteo client error %s: %s",
                               response.status_code, response.text[:200])
                return None
            return response.json().get("hourly") or {}
        except (requests.RequestException, ValueError) as exc:
            wait = 2 ** (attempt + 1)
            logger.warning("Open-Meteo request failed (%s/%s): %s",
                           attempt + 1, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)
    return None


# ─────────────────────────────────────────────
# DB
# ─────────────────────────────────────────────

def ensure_weather_table(db) -> None:
    with db.engine.begin() as conn:
        conn.execute(db.text(AFL_GAME_WEATHER_SCHEMA))


def _games_needing_weather(db, season: int, force: bool) -> list[dict]:
    sql = """
        SELECT g.id, g.date, g."localtime", g.venue, g.hteam, g.ateam
        FROM afl_games g
        LEFT JOIN afl_game_weather w ON w.game_id = g.id
        WHERE g.year = :season
          AND g.complete = 100
          AND g.date IS NOT NULL
    """
    if not force:
        sql += " AND w.game_id IS NULL"
    sql += " ORDER BY g.date"
    with db.engine.connect() as conn:
        rows = conn.execute(db.text(sql), {"season": season}).mappings().all()
    return [dict(r) for r in rows]


_UPSERT_SQL = """
    INSERT INTO afl_game_weather (
        game_id, game_date, home_team, away_team, venue,
        window_start, window_end, rain_mm, temp_c,
        weather_type, is_indoor, source, fetched_at
    ) VALUES (
        :game_id, :game_date, :home_team, :away_team, :venue,
        :window_start, :window_end, :rain_mm, :temp_c,
        :weather_type, :is_indoor, :source, NOW()
    )
    ON CONFLICT (game_id) DO UPDATE SET
        game_date    = EXCLUDED.game_date,
        home_team    = EXCLUDED.home_team,
        away_team    = EXCLUDED.away_team,
        venue        = EXCLUDED.venue,
        window_start = EXCLUDED.window_start,
        window_end   = EXCLUDED.window_end,
        rain_mm      = EXCLUDED.rain_mm,
        temp_c       = EXCLUDED.temp_c,
        weather_type = EXCLUDED.weather_type,
        is_indoor    = EXCLUDED.is_indoor,
        source       = EXCLUDED.source,
        fetched_at   = NOW()
"""


def sync_game_weather(db, season: int, force: bool = False) -> int:
    """
    Tag completed games in `season` with rain/temp. Returns rows written.
    Games already tagged are skipped unless force=True.
    Games the archive has no data for yet are skipped and retried next run.
    """
    ensure_weather_table(db)
    games = _games_needing_weather(db, season, force)
    logger.info("AFL weather: %s game(s) to tag for %s", len(games), season)

    written = 0
    cache: dict[tuple, dict | None] = {}
    for game in games:
        venue = get_venue(game["venue"])
        if venue is None:
            logger.warning("AFL weather: unknown venue %r (game %s) — add it to AFL_VENUES",
                           game["venue"], game["id"])
            continue

        game_dt = game["date"]
        if isinstance(game_dt, str):
            game_dt = datetime.fromisoformat(game_dt)
        game_date = game_dt.date() if isinstance(game_dt, datetime) else game_dt
        bounce = _parse_bounce(game["date"], game.get("localtime"))
        start, end = game_window(game_date, bounce)
        indoor = bool(venue.get("indoor"))

        rain_mm, temp_c = None, None
        key = (venue["lat"], venue["lon"], game_date)
        if key not in cache:
            cache[key] = fetch_hourly_weather(
                venue["lat"], venue["lon"], venue["tz"],
                game_date, end.date(),
            )
            time.sleep(REQUEST_DELAY_SECONDS)
        hourly = cache[key]
        if hourly:
            rain_mm, temp_c = summarise_window(hourly, start, end)

        if rain_mm is None and not indoor:
            logger.info("AFL weather: no archive data yet for game %s (%s) — will retry",
                        game["id"], game_date)
            continue

        params = {
            "game_id": game["id"],
            "game_date": game_date,
            "home_team": game["hteam"],
            "away_team": game["ateam"],
            "venue": game["venue"],
            "window_start": start,
            "window_end": end,
            "rain_mm": rain_mm,
            "temp_c": temp_c,
            "weather_type": classify_weather(rain_mm, indoor),
            "is_indoor": indoor,
            "source": INDOOR_SOURCE if indoor and rain_mm is None else WEATHER_SOURCE,
        }
        with db.engine.begin() as conn:
            conn.execute(db.text(_UPSERT_SQL), params)
        written += 1

    logger.info("AFL weather: %s game(s) tagged for %s", written, season)
    return written


def _make_db():
    from sqlalchemy import create_engine, text

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL not set")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    engine = create_engine(db_url, pool_pre_ping=True)
    return SimpleNamespace(engine=engine, text=text)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    season_arg = int(args[0]) if args else datetime.now().year
    sync_game_weather(_make_db(), season_arg, force="--force" in sys.argv)
