"""
Ladbrokes Affiliate API utility
Docs: https://nedscode.github.io/affiliate-feeds/

REST polling only — no WebSocket.
Two endpoints used:
  GET /racing/meetings  → find today's AUS thoroughbred meetings + race UUIDs
  GET /racing/events/<uuid> → live runner odds for a specific race
"""

import time
import logging
import requests

logger = logging.getLogger(__name__)

LB_BASE_URL = "https://api-affiliates.ladbrokes.com.au/affiliates/v1"

LB_HEADERS = {
    "From": "j.partington13@hotmail.com",
    "X-Partner": "www.theformanalyst.com",
}

# ── Simple in-memory caches ────────────────────────────────────────────────
# { date_str: (timestamp, meetings_list) }
_meetings_cache: dict = {}
MEETINGS_CACHE_TTL = 600  # 10 minutes

# { race_uuid: (timestamp, odds_dict) }
_odds_cache: dict = {}
ODDS_CACHE_TTL = 30  # 30 seconds


# ── Name normalisation (mirrors normalize_runner_name in app.py) ───────────
def _norm(name: str) -> str:
    """Lowercase, strip punctuation, collapse spaces."""
    import re
    if not name:
        return ""
    s = str(name).lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ── Venue name fuzzy match ─────────────────────────────────────────────────
def _venues_match(lb_name: str, pf_name: str) -> bool:
    """
    Returns True if the Ladbrokes meeting name matches the PuntingForm track name.
    Handles minor spelling differences and partial matches.
    """
    import re
    def _clean(s):
        s = s.lower().strip()
        s = re.sub(r"['\-]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    lb = _clean(lb_name)
    pf = _clean(pf_name)
    return lb == pf or lb.startswith(pf) or pf.startswith(lb) or pf in lb or lb in pf


# ── 1. Fetch today's meetings (cached 10 min) ──────────────────────────────
def fetch_todays_meetings(date_str: str) -> list:
    """
    Fetch AUS thoroughbred meetings from Ladbrokes for a given date.

    Args:
        date_str: ISO date string "YYYY-MM-DD"

    Returns:
        List of meeting dicts from the Ladbrokes API, or [] on failure.
        Each dict has: meeting (uuid), name, date, category, races[]
        Each race has: id (uuid), race_number, start_time
    """
    now = time.time()
    cached = _meetings_cache.get(date_str)
    if cached and (now - cached[0]) < MEETINGS_CACHE_TTL:
        return cached[1]

    try:
        url = f"{LB_BASE_URL}/racing/meetings"
        params = {
            "category": "T",       # Thoroughbred only
            "country": "AUS",
            "date_from": date_str,
            "date_to": date_str,
            "limit": 50,
        }
        resp = requests.get(url, headers=LB_HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        meetings = data.get("data", {}).get("meetings", [])
        _meetings_cache[date_str] = (now, meetings)
        logger.info(f"Ladbrokes: fetched {len(meetings)} meetings for {date_str}")
        return meetings

    except Exception as e:
        logger.warning(f"Ladbrokes meetings fetch failed for {date_str}: {e}")
        return []


# ── 2. Match a race to its Ladbrokes UUID ─────────────────────────────────
def match_race_uuid(track_name: str, date_str: str, race_number: int) -> str | None:
    """
    Find the Ladbrokes event UUID for a specific race.

    Args:
        track_name:  Venue name e.g. "Flemington", "Caulfield"
        date_str:    ISO date string "YYYY-MM-DD"
        race_number: Integer race number

    Returns:
        Ladbrokes event UUID string, or None if not found.
    """
    if not track_name:
        return None

    meetings = fetch_todays_meetings(date_str)
    if not meetings:
        return None

    # Find matching meeting
    matched_meeting = None
    for m in meetings:
        if _venues_match(m.get("name", ""), track_name):
            matched_meeting = m
            break

    if not matched_meeting:
        logger.debug(f"Ladbrokes: no meeting match for '{track_name}' on {date_str}")
        return None

    # Find matching race by race_number
    for race in matched_meeting.get("races", []):
        if race.get("race_number") == race_number:
            uuid = race.get("id")
            logger.debug(f"Ladbrokes: matched R{race_number} at {track_name} → {uuid}")
            return uuid

    logger.debug(f"Ladbrokes: meeting found for {track_name} but no R{race_number}")
    return None


# ── 3. Fetch race odds (cached 30 sec) ────────────────────────────────────
def fetch_race_odds(race_uuid: str) -> dict:
    """
    Fetch live fixed-win odds for all runners in a race.

    Args:
        race_uuid: Ladbrokes event UUID

    Returns:
        Dict with keys:
          "status":  race status string ("Open", "Closed", "Live", "Final", etc.)
          "odds":    { normalised_horse_name: { win, place, favourite, mover } }
        Returns {"status": "error", "odds": {}} on any failure.
    """
    now = time.time()
    cached = _odds_cache.get(race_uuid)
    if cached and (now - cached[0]) < ODDS_CACHE_TTL:
        return cached[1]

    try:
        url = f"{LB_BASE_URL}/racing/events/{race_uuid}"
        resp = requests.get(url, headers=LB_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        race_data = data.get("data", {})
        status = race_data.get("race", {}).get("status", "Unknown")
        runners = race_data.get("runners", [])

        odds = {}
        for runner in runners:
            if runner.get("is_scratched"):
                continue
            name_norm = _norm(runner.get("name", ""))
            if not name_norm:
                continue
            runner_odds = runner.get("odds", {})
            odds[name_norm] = {
                "win":       runner_odds.get("fixed_win"),
                "place":     runner_odds.get("fixed_place"),
                "favourite": runner.get("favourite", False),
                "mover":     runner.get("mover", False),
                "flucs":     runner.get("flucs", []),
            }

        result = {"status": status, "odds": odds}
        _odds_cache[race_uuid] = (now, result)
        logger.debug(f"Ladbrokes: fetched odds for {race_uuid} — status={status}, runners={len(odds)}")
        return result

    except Exception as e:
        logger.warning(f"Ladbrokes odds fetch failed for {race_uuid}: {e}")
        return {"status": "error", "odds": {}}
