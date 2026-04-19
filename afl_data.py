"""
afl_data.py
===========
Data layer for The Form Analyst AFL section.

Strategy:
- Historical advanced stats: Fryzigg RDS (same source fitzRoy uses)
- Current-season player stats: official AFL API fallback (same source family fitzRoy uses by default)
- Fixtures / ladder / tips: Squiggle
- Optional props: The Odds API
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import pyreadr
import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

FRYZIGG_RDS_URL = "http://www.fryziggafl.net/static/fryziggafl.rds"

SQUIGGLE_BASE = "https://api.squiggle.com.au"
AFLTABLES_BASE = "https://afltables.com/afl"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

AFL_API_BASE = "https://api.afl.com.au"
AFL_META_BASE = "https://aflapi.afl.com.au"

HEADERS = {
    "User-Agent": (
        "TheFormAnalyst/1.0 "
        "(https://theformanalyst.com; contact: admin@theformanalyst.com)"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-AU,en;q=0.9",
    "Referer": "https://www.afl.com.au/",
}

CURRENT_YEAR = datetime.now().year

SQUIGGLE_TEAM_IDS = {
    "Adelaide": 1,
    "Brisbane Lions": 2,
    "Carlton": 3,
    "Collingwood": 4,
    "Essendon": 5,
    "Fremantle": 6,
    "Geelong": 7,
    "Gold Coast": 8,
    "GWS Giants": 9,
    "Hawthorn": 10,
    "Melbourne": 11,
    "North Melbourne": 12,
    "Port Adelaide": 13,
    "Richmond": 14,
    "St Kilda": 15,
    "Sydney": 16,
    "West Coast": 17,
    "Western Bulldogs": 18,
}

_FRYZIGG_CACHE = {
    "df": None,
    "loaded": False,
}

_AFL_TOKEN_CACHE = {
    "token": None,
    "loaded_at": None,
}

# ─────────────────────────────────────────────
# GENERIC HELPERS
# ─────────────────────────────────────────────

def _get(url: str, params: Optional[dict] = None, retries: int = 3) -> Optional[Any]:
    """HTTP GET with retries and rate-limit handling."""
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=30)

            if response.status_code == 429:
                logger.warning("Rate limited from %s — sleeping 10s", url)
                time.sleep(10)
                continue

            response.raise_for_status()

            if not response.text or not response.text.strip():
                logger.warning("Empty response from %s (params=%s)", url, params)
                return None

            return response.json()

        except json.JSONDecodeError as exc:
            body = response.text[:300] if "response" in locals() else ""
            logger.error("JSON decode error from %s: %s — body=%r", url, exc, body)
            return None
        except requests.RequestException as exc:
            logger.error("Request failed (%s/%s) for %s: %s", attempt + 1, retries, url, exc)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)

    return None


def _coerce_int(value, default=0):
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    try:
        return int(float(value))
    except Exception:
        return default
def _coerce_match_id(value, default=0):
    """
    Convert AFL match ids like 'CD_M20260140601' into a stable integer.
    """
    if value is None:
        return default

    try:
        if pd.isna(value):
            return default
    except Exception:
        pass

    s = str(value).strip()
    digits = "".join(ch for ch in s if ch.isdigit())

    if not digits:
        return default

    try:
        return int(digits)
    except Exception:
        return default

def _coerce_float(value, default=0.0):
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return default


def _coerce_str(value, default=""):
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    return str(value).strip()


def _coerce_bool(value, default=False):
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def _coerce_date(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


def _coerce_datetime(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return pd.to_datetime(value)
    except Exception:
        return None


def _season_start_end(season: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    return pd.Timestamp(f"{season}-01-01"), pd.Timestamp(f"{season}-12-31")


def _first_existing(columns: set[str], *names: str) -> Optional[str]:
    for name in names:
        if name in columns:
            return name
    return None


# ─────────────────────────────────────────────
# SQUIGGLE API
# ─────────────────────────────────────────────

def fetch_squiggle_games(year: int, round_number: int = None) -> list[dict]:
    """Fetch fixtures/results from Squiggle."""
    params = {"q": "games", "year": year}
    if round_number is not None:
        params["round"] = round_number

    data = _get(SQUIGGLE_BASE, params)
    if not data or not isinstance(data, dict):
        return []

    return data.get("games", [])


def fetch_squiggle_standings(year: int, round_number: int = None) -> list[dict]:
    """Fetch ladder standings from Squiggle."""
    params = {"q": "standings", "year": year}
    if round_number is not None:
        params["round"] = round_number

    data = _get(SQUIGGLE_BASE, params)
    if not data or not isinstance(data, dict):
        return []

    return data.get("standings", [])


def fetch_squiggle_tips(
    year: int,
    round_number: int = None,
    source_id: int = 8,
) -> list[dict]:
    """Fetch model tips from Squiggle."""
    params = {"q": "tips", "year": year, "source": source_id}
    if round_number is not None:
        params["round"] = round_number

    data = _get(SQUIGGLE_BASE, params)
    if not data or not isinstance(data, dict):
        return []

    return data.get("tips", [])


def fetch_squiggle_current_round(year: int = None) -> int:
    """Get the current round from incomplete games."""
    y = year or CURRENT_YEAR
    params = {"q": "games", "year": y, "complete": "!100"}

    data = _get(SQUIGGLE_BASE, params)
    if not data or not isinstance(data, dict):
        return 1

    games = data.get("games", [])
    if not games:
        return 1

    rounds = [g.get("round", 1) for g in games if g.get("round") is not None]
    return min(rounds) if rounds else 1


def fetch_squiggle_upcoming_games(year: int = None) -> list[dict]:
    """Fetch all unplayed games for the season."""
    y = year or CURRENT_YEAR
    params = {"q": "games", "year": y, "complete": "0"}

    data = _get(SQUIGGLE_BASE, params)
    if not data or not isinstance(data, dict):
        return []

    return data.get("games", [])


# ─────────────────────────────────────────────
# AFL OFFICIAL API (current-season path)
# ─────────────────────────────────────────────

def _afl_parse_response(response: requests.Response) -> Optional[dict]:
    """Parse AFL API JSON safely."""
    try:
        response.raise_for_status()
        if "application/json" not in response.headers.get("Content-Type", ""):
            logger.error("AFL API did not return JSON: %s", response.headers.get("Content-Type"))
            return None
        return response.json()
    except Exception as exc:
        body = response.text[:300] if response is not None else ""
        logger.error("AFL API parse failed: %s — body=%r", exc, body)
        return None


def _get_afl_cookie(force_refresh: bool = False) -> Optional[str]:
    """
    Get AFL token used in x-media-mis-token header.
    Cached for a short period to avoid unnecessary token requests.
    """
    now = time.time()
    cached = _AFL_TOKEN_CACHE.get("token")
    loaded_at = _AFL_TOKEN_CACHE.get("loaded_at") or 0

    if cached and not force_refresh and (now - loaded_at) < 900:
        return cached

    try:
        response = requests.post(
            f"{AFL_API_BASE}/cfs/afl/WMCTok",
            headers=HEADERS,
            timeout=30,
        )
        data = _afl_parse_response(response)
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            logger.error("AFL token endpoint returned no token")
            return None

        _AFL_TOKEN_CACHE["token"] = token
        _AFL_TOKEN_CACHE["loaded_at"] = now
        return token
    except Exception as exc:
        logger.error("Failed to fetch AFL token: %s", exc)
        return None


def _afl_get(url: str, params: Optional[dict] = None, token: Optional[str] = None, retries: int = 2) -> Optional[dict]:
    """GET helper for AFL APIs."""
    for attempt in range(retries):
        try:
            headers = dict(HEADERS)
            if token:
                headers["x-media-mis-token"] = token

            response = requests.get(url, params=params, headers=headers, timeout=40)

            if response.status_code == 401 and token and attempt == 0:
                token = _get_afl_cookie(force_refresh=True)
                continue

            return _afl_parse_response(response)
        except requests.RequestException as exc:
            logger.error("AFL GET failed (%s/%s) for %s: %s", attempt + 1, retries, url, exc)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)

    return None


def _find_comp_id(comp: str = "AFLM") -> Optional[int]:
    """Find AFL competition id."""
    comp_code = "AFL" if comp == "AFLM" else comp
    data = _afl_get(f"{AFL_META_BASE}/afl/v2/competitions", params={"pageSize": 50})
    if not data or "competitions" not in data:
        return None

    comps = data.get("competitions") or []
    matches = [c for c in comps if "Legacy" not in _coerce_str(c.get("name")) and c.get("code") == comp_code]
    ids = [c.get("id") for c in matches if c.get("id") is not None]

    return min(ids) if ids else None


def _find_season_id(season: int, comp: str = "AFLM") -> Optional[int]:
    """Find AFL compSeason id."""
    import re

    comp_id = _find_comp_id(comp)
    if not comp_id:
        logger.error("Could not find comp id for %s", comp)
        return None

    data = _afl_get(
        f"{AFL_META_BASE}/afl/v2/competitions/{comp_id}/compseasons",
        params={"pageSize": 100},
    )
    if not data or "compSeasons" not in data:
        return None

    candidates = []
    for comp_season in data.get("compSeasons") or []:
        name = _coerce_str(comp_season.get("name"))
        if "Legacy" in name:
            continue

        match = re.search(r"([0-9]{4})", name)
        parsed_season = int(match.group(1)) if match else None
        if parsed_season == season and comp_season.get("id") is not None:
            candidates.append(int(comp_season["id"]))

    if not candidates:
        logger.warning("Could not find AFL season id for %s", season)
        return None

    return min(candidates)


def _find_round_ids(
    season: int,
    round_number: int = None,
    comp: str = "AFLM",
    future_rounds: bool = True,
) -> list[int]:
    """Find AFL round ids (NOT providerIds)."""
    season_id = _find_season_id(season, comp)
    if not season_id:
        return []

    data = _afl_get(
        f"{AFL_META_BASE}/afl/v2/compseasons/{season_id}/rounds",
        params={"pageSize": 30},
    )
    if not data or "rounds" not in data:
        return []

    rounds = data.get("rounds") or []

    if not future_rounds:
        today = pd.Timestamp.utcnow().tz_localize(None)
        filtered = []
        for r in rounds:
            utc_start = _coerce_datetime(r.get("utcStartTime"))
            if utc_start is not None and getattr(utc_start, "tzinfo", None) is not None:
                utc_start = utc_start.tz_convert(None)
            if utc_start is not None and utc_start < today:
                filtered.append(r)
        rounds = filtered

    if round_number is None:
        ids = [r.get("id") for r in rounds if r.get("id") is not None]
    else:
        ids = [
            r.get("id")
            for r in rounds
            if r.get("id") is not None and r.get("roundNumber") == round_number
        ]

    return [int(x) for x in ids if x is not None]


def _fetch_round_matches_afl(round_id: int, token: Optional[str] = None) -> list[dict]:
    """Fetch all match items for a round id."""
    token = token or _get_afl_cookie()
    if not token:
        return []

    data = _afl_get(
        f"{AFL_API_BASE}/cfs/afl/matchItems/round/{round_id}",
        token=token,
    )
    if not data:
        return []

    items = data.get("items") or []
    return items if isinstance(items, list) else []


def fetch_fixture_afl(season: int, round_number: int = None, comp: str = "AFLM") -> list[dict]:
    """
    Fetch AFL official fixture using the same endpoint pattern fitzRoy uses:
    https://aflapi.afl.com.au/afl/v2/matches

    Returns match rows that include providerId, which is then used to fetch
    current-season player stats from the AFL API.
    """
    comp_season_id = _find_season_id(season, comp)
    if comp_season_id is None:
        logger.warning("AFL fixture: no compSeasonId for season=%s comp=%s", season, comp)
        return []

    comp_id = _find_comp_id(comp)
    if comp_id is None:
        logger.warning("AFL fixture: no competitionId for comp=%s", comp)
        return []

    params = {
        "competitionId": comp_id,
        "compSeasonId": comp_season_id,
        "pageSize": 1000,
    }
    if round_number not in (None, "", 0):
        params["roundNumber"] = round_number

    data = _afl_get(f"{AFL_META_BASE}/afl/v2/matches", params=params, token=None)
    if not data or "matches" not in data:
        logger.warning("AFL fixture: no matches returned for season=%s round=%s", season, round_number)
        return []

    matches = data.get("matches") or []
    if not isinstance(matches, list):
        return []

    # fitzRoy filters back down to the requested season as a safety check
    filtered = []
    for match in matches:
        comp_season = match.get("compSeason") or {}
        comp_season_name = _coerce_str(comp_season.get("name"))
        if str(season) in comp_season_name:
            filtered.append(match)

    logger.info(
        "AFL fixture: %s matches for season %s round=%s via afl/v2/matches",
        len(filtered), season, round_number
    )
    return filtered


def _clean_names_playerstats_afl(df: pd.DataFrame) -> pd.DataFrame:
    """Mirror fitzRoy AFL player stats name cleaning."""
    if df is None or df.empty:
        return df

    cleaned = df.copy()
    cleaned.columns = [str(c) for c in cleaned.columns]
    cleaned.columns = [c.replace("playerStats.", "") for c in cleaned.columns]
    cleaned.columns = [c.replace("stats.", "") for c in cleaned.columns]
    cleaned.columns = [c.replace("playerName.", "") for c in cleaned.columns]
    return cleaned


def _fetch_match_stats_afl(match_provider_id: int, token: Optional[str] = None) -> pd.DataFrame:
    """Fetch AFL official player stats for one match."""
    token = token or _get_afl_cookie()
    if not token:
        return pd.DataFrame()

    data = _afl_get(
        f"{AFL_API_BASE}/cfs/afl/playerStats/match/{match_provider_id}",
        token=token,
    )
    if not data:
        return pd.DataFrame()

    # DEBUG: log the top-level structure once so we can see what AFL is actually returning
    try:
        logger.info("AFL stats debug match %s top-level keys: %s", match_provider_id, list(data.keys())[:50])

        for key in [
            "homeTeamPlayerStats",
            "awayTeamPlayerStats",
            "playerStats",
            "players",
            "items",
            "home",
            "away",
            "match",
            "teamStats",
        ]:
            value = data.get(key)
            if isinstance(value, list):
                logger.info("AFL stats debug match %s key=%s list_len=%s", match_provider_id, key, len(value))
                if value:
                    first = value[0]
                    if isinstance(first, dict):
                        logger.info(
                            "AFL stats debug match %s key=%s first_item_keys=%s",
                            match_provider_id,
                            key,
                            list(first.keys())[:50],
                        )
            elif isinstance(value, dict):
                logger.info(
                    "AFL stats debug match %s key=%s dict_keys=%s",
                    match_provider_id,
                    key,
                    list(value.keys())[:50],
                )
    except Exception as exc:
        logger.warning("AFL stats debug logging failed for %s: %s", match_provider_id, exc)

    home_stats = data.get("homeTeamPlayerStats") or []
    away_stats = data.get("awayTeamPlayerStats") or []

    def _to_df(rows: list[dict], team_status: str) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame()

        df = pd.json_normalize(rows)
        for bad_col in ("teamId", "playerStats.lastUpdated"):
            if bad_col in df.columns:
                df = df.drop(columns=[bad_col])

        df = _clean_names_playerstats_afl(df)
        df["teamStatus"] = team_status
        df["providerId"] = match_provider_id
        return df

    home_df = _to_df(home_stats, "home")
    away_df = _to_df(away_stats, "away")

    if home_df.empty and away_df.empty:
        logger.warning("AFL stats debug match %s produced no home/away player rows", match_provider_id)
        return pd.DataFrame()

    combined = pd.concat([home_df, away_df], ignore_index=True)
    logger.info("AFL stats debug match %s dataframe columns: %s", match_provider_id, list(combined.columns)[:100])
    logger.info("AFL stats debug match %s dataframe rowcount: %s", match_provider_id, len(combined))
    return combined


def _normalise_afl_fixture_match(match: dict) -> dict:
    """Normalise one AFL official match row into something we can merge against player stats."""
    home_name = (
        (((match.get("home") or {}).get("team") or {}).get("name"))
        or (((match.get("home") or {}).get("team") or {}).get("club", {}) or {}).get("name")
        or ""
    )
    away_name = (
        (((match.get("away") or {}).get("team") or {}).get("name"))
        or (((match.get("away") or {}).get("team") or {}).get("club", {}) or {}).get("name")
        or ""
    )

    venue_name = ((match.get("venue") or {}).get("name")) or ""
    round_obj = match.get("round") or {}
    comp_season = match.get("compSeason") or {}

    return {
        "providerId": match.get("providerId"),
        "utcStartTime": match.get("utcStartTime"),
        "status": match.get("status"),
        "compSeason.shortName": comp_season.get("shortName") or comp_season.get("name") or "",
        "round.name": round_obj.get("name") or "",
        "round.roundNumber": round_obj.get("roundNumber"),
        "venue.name": venue_name,
        "home.team.name": home_name,
        "away.team.name": away_name,
    }


def fetch_afl_player_stats_current_season(season: int, round_number: int = None, comp: str = "AFLM") -> list[dict]:
    """
    Fetch current-season player stats using AFL official API,
    but only for rounds/matches that should already have stats available.
    """
    # If no round provided, only go up to the current round from Squiggle
    if round_number is None:
        try:
            round_number = fetch_squiggle_current_round(season)
        except Exception:
            round_number = None

    matches = fetch_fixture_afl(season, round_number=round_number, comp=comp)
    if not matches:
        logger.warning("AFL current-season stats: no fixtures found for season %s round=%s", season, round_number)
        return []

    # Keep only matches that have already started / are not future scheduled matches
    now_utc = pd.Timestamp.utcnow()

    filtered_matches = []
    for match in matches:
        utc_start = _coerce_datetime(match.get("utcStartTime"))
        status = _coerce_str(match.get("status")).lower()

        include = False

        # AFL statuses vary, so allow several non-future cases
        if status in {"concluded", "complete", "finished", "closed", "final"}:
            include = True
        elif utc_start is not None and utc_start <= now_utc:
            include = True

        if include:
            filtered_matches.append(match)

    if not filtered_matches:
        logger.warning(
            "AFL current-season stats: no completed/started matches found for season %s round=%s",
            season, round_number
        )
        return []

    match_provider_ids = [m.get("providerId") for m in filtered_matches if m.get("providerId")]
    if not match_provider_ids:
        logger.warning("AFL current-season stats: no providerIds found for season %s", season)
        return []

    token = _get_afl_cookie()
    if not token:
        return []

    match_details_map = {
        m.get("providerId"): _normalise_afl_fixture_match(m)
        for m in filtered_matches
        if m.get("providerId")
    }

    all_rows: list[dict] = []

    for match_provider_id in match_provider_ids:
        try:
            stats_df = _fetch_match_stats_afl(match_provider_id, token=token)
            if stats_df.empty:
                continue

            details = match_details_map.get(match_provider_id, {})

            for _, row in stats_df.iterrows():
                all_rows.append(_build_afl_current_row(row, details, season, match_provider_id))

            time.sleep(0.1)

        except Exception as exc:
            logger.warning("AFL match stats fetch failed for match %s: %s", match_provider_id, exc)

    all_rows = [r for r in all_rows if r.get("match_id") and r.get("player_id")]

    logger.info(
        "AFL current-season stats: prepared %s rows for season %s round=%s",
        len(all_rows), season, round_number
    )
    return all_rows


def _build_afl_current_row(row: pd.Series, details: dict, season: int, match_id: int) -> dict:
    """Map AFL current-season row into your DB shape."""
    row_dict = row.to_dict() if hasattr(row, "to_dict") else dict(row)
    
    def pick(*keys, default=None):
        for key in keys:
            if key in row_dict and row_dict.get(key) not in (None, "", []):
                return row_dict.get(key)
        return default
 
    first_name = _coerce_str(
        pick(
            "player.givenName",
            "player.player.givenName",
            "player.player.player.givenName",
            "givenName",
            "firstName",
        )
    )
    last_name = _coerce_str(
        pick(
            "player.surname",
            "player.player.surname",
            "player.player.player.surname",
            "surname",
            "lastName",
        )
    )
    full_name = _coerce_str(
        pick(
            "player.displayName",
            "player.player.displayName",
            "displayName",
        )
    )
 
    if not first_name and full_name:
        parts = full_name.split()
        first_name = parts[0] if parts else ""
        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
 
    home_team = _coerce_str(details.get("home.team.name"))
    away_team = _coerce_str(details.get("away.team.name"))
 
    player_team = _coerce_str(pick("team.name"))
    if not player_team:
        player_team = home_team if _coerce_str(row_dict.get("teamStatus")) == "home" else away_team
 
    home_score = _coerce_int(
        pick(
            "homeTeamScore",
            "match.homeTeamScore",
            "home.score",
        )
    )
    away_score = _coerce_int(
        pick(
            "awayTeamScore",
            "match.awayTeamScore",
            "away.score",
        )
    )
 
    winner = ""
    margin = 0
    if home_score or away_score:
        if home_score > away_score:
            winner = home_team
            margin = home_score - away_score
        elif away_score > home_score:
            winner = away_team
            margin = away_score - home_score
 player_id_raw = pick(
    "player.playerId",
    "player.player.playerId",
    "player.player.player.playerId",
    "playerId",
    "id",
)
player_id_digits = ''.join(c for c in str(player_id_raw) if c.isdigit())
    return {
    "match_id": _coerce_match_id(match_id),
    "match_date": _coerce_date(details.get("utcStartTime")),
    "match_round": _coerce_str(details.get("round.roundNumber") or details.get("round.name")),
    "match_home_team": home_team,
    "match_away_team": away_team,
    "match_home_team_score": home_score,
    "match_away_team_score": away_score,
    "match_margin": margin,
    "match_winner": winner,
    "match_weather_temp_c": 0,
    "match_weather_type": "",
    "match_attendance": 0,
    "venue_name": _coerce_str(details.get("venue.name")),
    "season": season,
    "player_id": _coerce_int(
        ''.join(filter(str.isdigit, str(pick(
            "player.playerId",
            "player.player.playerId",
            "player.player.player.playerId",
            "playerId",
            "id",
        )))) or "0"
    ),
    "player_first_name": first_name,
    "player_last_name": last_name,
    "player_team": player_team,
    "guernsey_number": _coerce_int(
        pick(
            "player.playerJumperNumber",
            "player.player.playerJumperNumber",
            "player.player.player.playerJumperNumber",
            "player.jumperNumber",
            "guernseyNumber",
            "jumperNumber",
        )
    ),
        "player_height_cm": _coerce_int(pick("heightCm", "height")),
        "player_weight_kg": _coerce_int(pick("weightKg", "weight")),
        "player_is_retired": False,
        "kicks": _coerce_int(pick("kicks")),
        "marks": _coerce_int(pick("marks")),
        "handballs": _coerce_int(pick("handballs")),
        "disposals": _coerce_int(pick("disposals")),
        "effective_disposals": _coerce_int(pick("extendedStats.effectiveDisposals", "effectiveDisposals")),
        "disposal_efficiency_percentage": _coerce_int(pick("disposalEfficiency")),
        "goals": _coerce_int(pick("goals")),
        "behinds": _coerce_int(pick("behinds")),
        "hitouts": _coerce_int(pick("hitouts")),
        "tackles": _coerce_int(pick("tackles")),
        "rebounds": _coerce_int(pick("rebound50s", "rebounds")),
        "inside_fifties": _coerce_int(pick("inside50s", "insideFifties")),
        "clearances": _coerce_int(pick("clearances.totalClearances", "clearances")),
        "clangers": _coerce_int(pick("clangers")),
        "free_kicks_for": _coerce_int(pick("freesFor", "freeKicksFor")),
        "free_kicks_against": _coerce_int(pick("freesAgainst", "freeKicksAgainst")),
        "brownlow_votes": 0,
        "contested_possessions": _coerce_int(pick("contestedPossessions")),
        "uncontested_possessions": _coerce_int(pick("uncontestedPossessions")),
        "contested_marks": _coerce_int(pick("contestedMarks")),
        "marks_inside_fifty": _coerce_int(pick("marksInside50")),
        "one_percenters": _coerce_int(pick("onePercenters")),
        "bounces": _coerce_int(pick("bounces")),
        "goal_assists": _coerce_int(pick("goalAssists")),
        "time_on_ground_percentage": _coerce_int(pick("timeOnGroundPercentage", "timeOnGroundPct")),
        "afl_fantasy_score": _coerce_int(pick("dreamTeamPoints", "aflFantasyScore", "fantasyScore")),
        "supercoach_score": _coerce_int(pick("supercoachScore")),
        "centre_clearances": _coerce_int(pick("clearances.centreClearances", "centreClearances")),
        "stoppage_clearances": _coerce_int(pick("clearances.stoppageClearances", "stoppageClearances")),
        "score_involvements": _coerce_int(pick("scoreInvolvements")),
        "metres_gained": _coerce_int(pick("metresGained")),
        "turnovers": _coerce_int(pick("turnovers")),
        "intercepts": _coerce_int(pick("intercepts")),
        "tackles_inside_fifty": _coerce_int(pick("tacklesInside50")),
    }


# ─────────────────────────────────────────────
# FRYZIGG DATA (historical path)
# ─────────────────────────────────────────────

def _download_fryzigg_rds() -> pd.DataFrame:
    """Download and cache Fryzigg RDS as DataFrame."""
    global _FRYZIGG_CACHE

    if _FRYZIGG_CACHE["loaded"] and _FRYZIGG_CACHE["df"] is not None:
        logger.info("Fryzigg: using cached DataFrame (%s rows)", len(_FRYZIGG_CACHE["df"]))
        return _FRYZIGG_CACHE["df"]

    logger.info("Fryzigg: downloading %s ...", FRYZIGG_RDS_URL)
    response = requests.get(FRYZIGG_RDS_URL, timeout=120)
    response.raise_for_status()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".rds") as tmp:
        tmp.write(response.content)
        tmp_path = tmp.name

    try:
        result = pyreadr.read_r(tmp_path)
        if not result:
            raise ValueError("No objects found in Fryzigg RDS")

        df = next(iter(result.values()))
        if df is None or df.empty:
            raise ValueError("Fryzigg RDS loaded but DataFrame is empty")

        df.columns = [str(c).strip().lower() for c in df.columns]
        _FRYZIGG_CACHE["df"] = df
        _FRYZIGG_CACHE["loaded"] = True

        logger.info("Fryzigg: loaded %s rows, %s columns", len(df), len(df.columns))
        return df
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _fetch_fryzigg_player_stats_from_rds(season: int) -> list[dict]:
    """
    Load Fryzigg player stats for one season from the .rds file.
    Mirrors fitzRoy logic: filter by date range, not by assuming a season column.
    """
    try:
        df = _download_fryzigg_rds()
    except Exception as exc:
        logger.error("Fryzigg: failed loading RDS: %s", exc)
        return []

    cols = set(df.columns)
    match_date_col = _first_existing(cols, "match_date", "date")
    if match_date_col is None:
        logger.error("Fryzigg: no match_date/date column found. Sample cols: %s", list(df.columns)[:30])
        return []

    temp_dates = pd.to_datetime(df[match_date_col], errors="coerce")
    start_date, end_date = _season_start_end(season)
    season_df = df[(temp_dates >= start_date) & (temp_dates <= end_date)].copy()

    if season_df.empty:
        logger.warning("Fryzigg: no rows for season %s", season)
        return []

    logger.info("Fryzigg: %s rows for season %s", len(season_df), season)

    c_match_id = _first_existing(cols, "match_id", "game_id", "id")
    c_match_round = _first_existing(cols, "match_round", "round")
    c_home_team = _first_existing(cols, "match_home_team", "home_team")
    c_away_team = _first_existing(cols, "match_away_team", "away_team")
    c_home_score = _first_existing(cols, "match_home_team_score", "home_score")
    c_away_score = _first_existing(cols, "match_away_team_score", "away_score")
    c_match_margin = _first_existing(cols, "match_margin", "margin")
    c_match_winner = _first_existing(cols, "match_winner", "winner")
    c_weather_temp = _first_existing(cols, "match_weather_temp_c", "weather_temp_c")
    c_weather_type = _first_existing(cols, "match_weather_type", "weather_type")
    c_attendance = _first_existing(cols, "match_attendance", "attendance")
    c_venue = _first_existing(cols, "venue_name", "venue")

    c_player_id = _first_existing(cols, "player_id")
    c_first = _first_existing(cols, "player_first_name", "first_name")
    c_last = _first_existing(cols, "player_last_name", "last_name")
    c_team = _first_existing(cols, "player_team", "team")
    c_guernsey = _first_existing(cols, "guernsey_number", "guernsey")
    c_height = _first_existing(cols, "player_height_cm", "height_cm")
    c_weight = _first_existing(cols, "player_weight_kg", "weight_kg")
    c_retired = _first_existing(cols, "player_is_retired", "is_retired")

    def build_row(row) -> dict:
        def g(col):
            return row[col] if col in row.index and col is not None else None

        return {
            "match_id": _coerce_int(g(c_match_id)),
            "match_date": _coerce_date(g(match_date_col)),
            "match_round": _coerce_str(g(c_match_round)),
            "match_home_team": _coerce_str(g(c_home_team)),
            "match_away_team": _coerce_str(g(c_away_team)),
            "match_home_team_score": _coerce_int(g(c_home_score)),
            "match_away_team_score": _coerce_int(g(c_away_score)),
            "match_margin": _coerce_int(g(c_match_margin)),
            "match_winner": _coerce_str(g(c_match_winner)),
            "match_weather_temp_c": _coerce_int(g(c_weather_temp)),
            "match_weather_type": _coerce_str(g(c_weather_type)),
            "match_attendance": _coerce_int(g(c_attendance)),
            "venue_name": _coerce_str(g(c_venue)),
            "season": season,
            "player_id": _coerce_int(g(c_player_id)),
            "player_first_name": _coerce_str(g(c_first)),
            "player_last_name": _coerce_str(g(c_last)),
            "player_team": _coerce_str(g(c_team)),
            "guernsey_number": _coerce_int(g(c_guernsey)),
            "player_height_cm": _coerce_int(g(c_height)),
            "player_weight_kg": _coerce_int(g(c_weight)),
            "player_is_retired": _coerce_bool(g(c_retired), False),

            "kicks": _coerce_int(g(_first_existing(cols, "kicks"))),
            "marks": _coerce_int(g(_first_existing(cols, "marks"))),
            "handballs": _coerce_int(g(_first_existing(cols, "handballs"))),
            "disposals": _coerce_int(g(_first_existing(cols, "disposals"))),
            "effective_disposals": _coerce_int(g(_first_existing(cols, "effective_disposals"))),
            "disposal_efficiency_percentage": _coerce_int(g(_first_existing(cols, "disposal_efficiency_percentage"))),
            "goals": _coerce_int(g(_first_existing(cols, "goals"))),
            "behinds": _coerce_int(g(_first_existing(cols, "behinds"))),
            "hitouts": _coerce_int(g(_first_existing(cols, "hitouts"))),
            "tackles": _coerce_int(g(_first_existing(cols, "tackles"))),
            "rebounds": _coerce_int(g(_first_existing(cols, "rebounds"))),
            "inside_fifties": _coerce_int(g(_first_existing(cols, "inside_fifties"))),
            "clearances": _coerce_int(g(_first_existing(cols, "clearances"))),
            "clangers": _coerce_int(g(_first_existing(cols, "clangers"))),
            "free_kicks_for": _coerce_int(g(_first_existing(cols, "free_kicks_for"))),
            "free_kicks_against": _coerce_int(g(_first_existing(cols, "free_kicks_against"))),
            "brownlow_votes": _coerce_int(g(_first_existing(cols, "brownlow_votes"))),
            "contested_possessions": _coerce_int(g(_first_existing(cols, "contested_possessions"))),
            "uncontested_possessions": _coerce_int(g(_first_existing(cols, "uncontested_possessions"))),
            "contested_marks": _coerce_int(g(_first_existing(cols, "contested_marks"))),
            "marks_inside_fifty": _coerce_int(g(_first_existing(cols, "marks_inside_fifty"))),
            "one_percenters": _coerce_int(g(_first_existing(cols, "one_percenters"))),
            "bounces": _coerce_int(g(_first_existing(cols, "bounces"))),
            "goal_assists": _coerce_int(g(_first_existing(cols, "goal_assists"))),
            "time_on_ground_percentage": _coerce_int(g(_first_existing(cols, "time_on_ground_percentage"))),
            "afl_fantasy_score": _coerce_int(g(_first_existing(cols, "afl_fantasy_score"))),
            "supercoach_score": _coerce_int(g(_first_existing(cols, "supercoach_score"))),
            "centre_clearances": _coerce_int(g(_first_existing(cols, "centre_clearances"))),
            "stoppage_clearances": _coerce_int(g(_first_existing(cols, "stoppage_clearances"))),
            "score_involvements": _coerce_int(g(_first_existing(cols, "score_involvements"))),
            "metres_gained": _coerce_int(g(_first_existing(cols, "metres_gained"))),
            "turnovers": _coerce_int(g(_first_existing(cols, "turnovers"))),
            "intercepts": _coerce_int(g(_first_existing(cols, "intercepts"))),
            "tackles_inside_fifty": _coerce_int(g(_first_existing(cols, "tackles_inside_fifty"))),
        }

    rows = [build_row(row) for _, row in season_df.iterrows()]
    rows = [r for r in rows if r["player_id"] and r["match_id"]]

    logger.info("Fryzigg: prepared %s rows for season %s", len(rows), season)
    return rows


def fetch_fryzigg_player_stats(season: int) -> list[dict]:
    """
    Unified player stats fetch.

    Rules:
    - For current season: try AFL official current-season stats first.
    - If AFL official returns nothing, fall back to Fryzigg RDS.
    - For past seasons: use Fryzigg RDS directly.
    """
    if season >= CURRENT_YEAR:
        logger.info("Player stats: trying AFL official current-season source for %s", season)
        current_rows = fetch_afl_player_stats_current_season(
    season,
    round_number=fetch_squiggle_current_round(season)
)
        if current_rows:
            return current_rows

        logger.warning("Player stats: AFL current-season source returned no rows for %s, falling back to Fryzigg", season)

    return _fetch_fryzigg_player_stats_from_rds(season)


def fetch_fryzigg_player_stats_range(start_year: int, end_year: int) -> list[dict]:
    """Fetch multiple seasons."""
    all_stats: list[dict] = []

    for year in range(start_year, end_year + 1):
        logger.info("Fetching player stats for %s...", year)
        stats = fetch_fryzigg_player_stats(year)
        all_stats.extend(stats)
        time.sleep(1)

    return all_stats


# ─────────────────────────────────────────────
# AFL TABLES
# ─────────────────────────────────────────────

def fetch_afltables_results(year: int) -> list[dict]:
    """Scrape basic season results from AFL Tables."""
    from bs4 import BeautifulSoup

    url = f"{AFLTABLES_BASE}/seas/{year}.html"

    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("AFL Tables fetch failed for %s: %s", year, exc)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    results: list[dict] = []

    tables = soup.find_all("table", class_="sortable")
    for table in tables:
        rows = table.find_all("tr")[1:]
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 8:
                continue

            try:
                results.append(
                    {
                        "year": year,
                        "date": cells[0].text.strip(),
                        "round": cells[1].text.strip(),
                        "home_team": cells[2].text.strip(),
                        "home_score": cells[3].text.strip(),
                        "away_team": cells[4].text.strip(),
                        "away_score": cells[5].text.strip(),
                        "venue": cells[6].text.strip(),
                        "crowd": cells[7].text.strip() if len(cells) > 7 else "",
                    }
                )
            except (IndexError, AttributeError):
                continue

    return results


# ─────────────────────────────────────────────
# THE ODDS API
# ─────────────────────────────────────────────

def fetch_afl_player_props(api_key: str, market: str = "player_disposals") -> list[dict]:
    """Fetch live AFL player prop lines from The Odds API."""
    if not api_key:
        logger.warning("No Odds API key configured — skipping prop fetch")
        return []

    events_url = f"{ODDS_API_BASE}/sports/aussierules_afl/events"
    events_data = _get(events_url, {"apiKey": api_key, "regions": "au"})

    if not events_data or not isinstance(events_data, list):
        return []

    props: list[dict] = []

    for event in events_data[:9]:
        event_id = event.get("id")
        if not event_id:
            continue

        odds_url = f"{ODDS_API_BASE}/sports/aussierules_afl/events/{event_id}/odds"
        params = {
            "apiKey": api_key,
            "regions": "au",
            "markets": market,
            "oddsFormat": "decimal",
        }

        odds_data = _get(odds_url, params)
        if not odds_data or not isinstance(odds_data, dict):
            continue

        home = odds_data.get("home_team", "")
        away = odds_data.get("away_team", "")
        commence = odds_data.get("commence_time", "")

        for bookmaker in odds_data.get("bookmakers", []):
            bk_name = bookmaker.get("title", "")

            for mkt in bookmaker.get("markets", []):
                if mkt.get("key") != market:
                    continue

                for outcome in mkt.get("outcomes", []):
                    props.append(
                        {
                            "event_id": event_id,
                            "home_team": home,
                            "away_team": away,
                            "commence_time": commence,
                            "bookmaker": bk_name,
                            "market": market,
                            "player": outcome.get("description", ""),
                            "name": outcome.get("name", ""),
                            "line": outcome.get("point", 0),
                            "odds": outcome.get("price", 0),
                        }
                    )

        time.sleep(0.5)

    return props


# ─────────────────────────────────────────────
# CONVENIENCE AGGREGATORS
# ─────────────────────────────────────────────

def get_player_season_averages(player_stats: list[dict]) -> dict:
    """Calculate season averages across key stats."""
    if not player_stats:
        return {}

    stats_to_avg = [
        "disposals",
        "effective_disposals",
        "disposal_efficiency_percentage",
        "kicks",
        "marks",
        "handballs",
        "goals",
        "behinds",
        "tackles",
        "hitouts",
        "rebounds",
        "inside_fifties",
        "clearances",
        "contested_possessions",
        "uncontested_possessions",
        "marks_inside_fifty",
        "score_involvements",
        "metres_gained",
        "afl_fantasy_score",
        "supercoach_score",
        "time_on_ground_percentage",
    ]

    totals = {stat: 0 for stat in stats_to_avg}
    count = len(player_stats)

    for game in player_stats:
        for stat in stats_to_avg:
            totals[stat] += game.get(stat, 0) or 0

    return {stat: round(totals[stat] / count, 1) for stat in stats_to_avg}


def get_player_vs_opponent(player_stats: list[dict], opponent_team: str) -> dict:
    """Filter a player's game log to games against one opponent."""
    opp_games = [
        g for g in player_stats
        if g.get("match_home_team") == opponent_team or g.get("match_away_team") == opponent_team
    ]

    if not opp_games:
        return {"games": 0, "averages": {}, "hit_rates": {}}

    averages = get_player_season_averages(opp_games)

    disp_lines = [15, 20, 25, 30, 35]
    hit_rates = {}
    for line in disp_lines:
        hits = sum(1 for g in opp_games if (g.get("disposals") or 0) >= line)
        hit_rates[f"disp_{line}+"] = round(hits / len(opp_games) * 100, 1)

    return {
        "games": len(opp_games),
        "averages": averages,
        "hit_rates": hit_rates,
        "last_5": sorted(
            opp_games,
            key=lambda x: x.get("match_date", ""),
            reverse=True,
        )[:5],
    }


def get_player_last_n_games(player_stats: list[dict], n: int = 5) -> list[dict]:
    """Return the n most recent games for a player."""
    sorted_games = sorted(
        player_stats,
        key=lambda x: x.get("match_date", ""),
        reverse=True,
    )
    return sorted_games[:n]


def calculate_disposal_edge(
    player_avg: float,
    book_line: float,
    vs_opp_avg: float = None,
    last5_avg: float = None,
) -> dict:
    """Calculate edge between model prediction and book line."""
    model_pred = player_avg

    if vs_opp_avg and vs_opp_avg > 0:
        model_pred = player_avg * 0.50 + vs_opp_avg * 0.30 + model_pred * 0.20

    if last5_avg and last5_avg > 0:
        model_pred = model_pred * 0.80 + last5_avg * 0.20

    edge = round(model_pred - book_line, 1)

    return {
        "model_prediction": round(model_pred, 1),
        "book_line": book_line,
        "edge": edge,
        "edge_positive": edge > 0,
        "edge_pct": round(abs(edge) / book_line * 100, 1) if book_line else 0,
        "recommendation": "value" if edge >= 2.0 else "skip",
    }
