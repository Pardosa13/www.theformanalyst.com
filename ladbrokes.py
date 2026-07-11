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
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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



NEXT_TO_GO_GRACE_SECONDS = 5 * 60
try:
    MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")
except ZoneInfoNotFoundError:
    logger.warning("Timezone data for Australia/Melbourne is unavailable; falling back to UTC for Next To Go display")
    MELBOURNE_TZ = timezone.utc
_FINAL_RACE_STATUSES = {"abandoned", "final", "finalised", "closed", "interim", "resulted", "live", "jumped"}
_ACTIVE_RACE_STATUSES = {"open", "delayed", "suspended"}
_NON_RACE_TERMS = ("jockey challenge", "futures", "future", "market", "special", "top jockey")

def _parse_ladbrokes_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        logger.debug("Ladbrokes: could not parse start_time %r", value)
        return None

def _race_field(race: dict, *names, default=None):
    for name in names:
        if name in race and race.get(name) is not None:
            return race.get(name)
    return default

def _is_genuine_race(meeting: dict, race: dict, now_utc: datetime) -> bool:
    try:
        race_number = int(_race_field(race, "race_number", "number", "raceNumber", default=0) or 0)
    except (TypeError, ValueError):
        return False
    if race_number <= 0:
        return False
    if str(meeting.get("category") or "T").upper() != "T":
        return False
    if str(meeting.get("country") or "AUS").upper() not in {"AUS", "AU"}:
        return False
    status = str(_race_field(race, "status", "race_status", default="") or "").strip().lower()
    if status in _FINAL_RACE_STATUSES:
        return False
    haystack = " ".join(str(x or "") for x in [meeting.get("name"), race.get("name"), race.get("race_name"), race.get("market_name")]).lower()
    if any(term in haystack for term in _NON_RACE_TERMS):
        return False
    start_utc = _parse_ladbrokes_utc(_race_field(race, "start_time", "startTime"))
    if not start_utc:
        return False
    if status in _ACTIVE_RACE_STATUSES:
        return True
    return (now_utc - start_utc).total_seconds() <= NEXT_TO_GO_GRACE_SECONDS

def build_next_to_go_races(date_str: str, limit: int | None = None) -> dict:
    now_utc = datetime.now(timezone.utc)
    races = []
    meetings = fetch_todays_meetings(date_str)
    for meeting in meetings:
        track = meeting.get("name") or meeting.get("meeting_name") or ""
        for race in meeting.get("races", []) or []:
            if not isinstance(race, dict) or not _is_genuine_race(meeting, race, now_utc):
                continue
            start_utc = _parse_ladbrokes_utc(_race_field(race, "start_time", "startTime"))
            race_number = int(_race_field(race, "race_number", "number", "raceNumber"))
            local_dt = start_utc.astimezone(MELBOURNE_TZ)
            races.append({
                "ladbrokes_event_id": _race_field(race, "id", "event_id", "eventId", "uuid"),
                "track": track,
                "race_number": race_number,
                "race_name": _race_field(race, "name", "race_name", "raceName", default="") or "",
                "status": _race_field(race, "status", "race_status", default="") or "",
                "start_time_utc": start_utc.isoformat().replace("+00:00", "Z"),
                "start_time_melbourne": local_dt.isoformat(),
                "start_time_melbourne_display": local_dt.strftime("%-I:%M %p"),
            })
    races.sort(key=lambda r: r["start_time_utc"])
    if limit:
        races = races[:limit]
    return {"status": "ok", "races": races, "fetched_at": now_utc.isoformat().replace("+00:00", "Z")}

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
        race_obj  = race_data.get("race", {})
        status    = race_obj.get("status", "Unknown")

        # Silk sprite URL — ensure https prefix
        silk_url = race_obj.get("silk_url", "")
        if silk_url and not silk_url.startswith("http"):
            silk_url = "https://" + silk_url

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
                "win":           runner_odds.get("fixed_win"),
                "place":         runner_odds.get("fixed_place"),
                "favourite":     runner.get("favourite", False),
                "mover":         runner.get("mover", False),
                "flucs":         runner.get("flucs", []),
                "runner_number": runner.get("runner_number", 0),
            }

        result = {"status": status, "odds": odds, "silk_url": silk_url}
        _odds_cache[race_uuid] = (now, result)
        logger.debug(f"Ladbrokes: fetched odds for {race_uuid} — status={status}, runners={len(odds)}")
        return result

    except Exception as e:
        logger.warning(f"Ladbrokes odds fetch failed for {race_uuid}: {e}")
        return {"status": "error", "odds": {}, "silk_url": ""}
