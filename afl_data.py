"""
afl_data.py
===========
Data layer for The Form Analyst AFL section.

Strategy:
- Historical advanced stats: Fryzigg RDS
- Current-season player stats: official AFL API first, Fryzigg fallback
- Fixtures / ladder / tips: Squiggle
- Optional props: The Odds API
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from typing import Any, Optional
from pathlib import Path
from urllib.parse import urlencode

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

_ODDS_API_ENV_VARS = ("ODDS_API_KEY", "THE_ODDS_API_KEY", "ODDSAPI_KEY")
_ODDS_API_KEY_MIN_LENGTH = 10

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
AFL_2026_CSV_PATH = Path("data/afl_2026_stats.csv")

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

_FRYZIGG_CACHE: dict[str, Any] = {
    "df": None,
    "loaded": False,
}

_AFL_TOKEN_CACHE: dict[str, Any] = {
    "token": None,
    "loaded_at": None,
}


# ─────────────────────────────────────────────
# GENERIC HELPERS
# ─────────────────────────────────────────────

def _get(url: str, params: Optional[dict] = None, retries: int = 3) -> Optional[Any]:
    """HTTP GET with retries and rate-limit handling."""
    for attempt in range(retries):
        response = None
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=30)

            if response.status_code == 429:
                logger.warning("Rate limited from %s — sleeping 10s", url)
                time.sleep(10)
                continue

            if 400 <= response.status_code < 500:
                logger.warning("Client error %s from %s — not retrying", response.status_code, url)
                return None

            response.raise_for_status()

            if not response.text or not response.text.strip():
                logger.warning("Empty response from %s", url)
                return None

            return response.json()

        except json.JSONDecodeError as exc:
            body = response.text[:300] if response is not None else ""
            logger.error("JSON decode error from %s: %s — body=%r", url, exc, body)
            return None
        except requests.RequestException as exc:
            logger.error("Request failed (%s/%s) for %s: %s", attempt + 1, retries, url, exc)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)

    return None


def _coerce_int(value: Any, default: int = 0) -> int:
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


def _coerce_match_id(value: Any, default: int = 0) -> int:
    """
    Convert ids like 'CD_M20260140601' into a stable integer.
    """
    if value is None:
        return default

    try:
        if pd.isna(value):
            return default
    except Exception:
        pass

    raw = str(value).strip()
    digits = "".join(ch for ch in raw if ch.isdigit())

    if not digits:
        return default

    try:
        return int(digits)
    except Exception:
        return default


def _hash_match_key_to_bigint(match_key: str) -> int:
    """
    Derive a stable, collision-resistant signed 63-bit integer from a match key.
    Uses SHA-1 of the key, keeping the first 8 bytes, clamped to signed BIGINT range.
    Deterministic across runs (same key always produces the same integer).
    """
    digest = hashlib.sha1(match_key.encode("utf-8")).digest()
    raw = int.from_bytes(digest[:8], "big")
    # Clamp to signed 63-bit maximum so it fits in a PostgreSQL BIGINT column
    return raw & 0x7FFFFFFFFFFFFFFF


def _coerce_float(value: Any, default: float = 0.0) -> float:
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


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    return str(value).strip()


def _coerce_bool(value: Any, default: bool = False) -> bool:
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


def _coerce_date(value: Any):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        ts = pd.to_datetime(value, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None


def _coerce_datetime(value: Any):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        ts = pd.to_datetime(value, errors="coerce", utc=True)
        if pd.isna(ts):
            return None
        return ts
    except Exception:
        return None


def _season_start_end(season: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    return pd.Timestamp(f"{season}-01-01"), pd.Timestamp(f"{season}-12-31")


def _first_existing(columns: set[str], *names: str) -> Optional[str]:
    for name in names:
        if name in columns:
            return name
    return None


def _normalise_team_name(name: Any) -> str:
    raw = _coerce_str(name)
    mapping = {
        # Legacy / short-name aliases
        "West Coast Eagles": "West Coast",
        "Greater Western Sydney": "GWS Giants",
        "GWS": "GWS Giants",
        "Footscray": "Western Bulldogs",
        "Brisbane": "Brisbane Lions",
        # Full names with mascots as returned by The Odds API
        "Adelaide Crows": "Adelaide",
        "Carlton Blues": "Carlton",
        "Collingwood Magpies": "Collingwood",
        "Essendon Bombers": "Essendon",
        "Fremantle Dockers": "Fremantle",
        "Geelong Cats": "Geelong",
        "Gold Coast Suns": "Gold Coast",
        "Hawthorn Hawks": "Hawthorn",
        "Melbourne Demons": "Melbourne",
        "North Melbourne Kangaroos": "North Melbourne",
        "Port Adelaide Power": "Port Adelaide",
        "Richmond Tigers": "Richmond",
        "St Kilda Saints": "St Kilda",
        "Sydney Swans": "Sydney",
    }
    return mapping.get(raw, raw)


def _safe_series_get(row: pd.Series, column_name: Optional[str]):
    if not column_name:
        return None
    return row[column_name] if column_name in row.index else None


def _pick_from_dict(data: dict, *keys: str, default=None):
    for key in keys:
        value = data.get(key)
        if value not in (None, "", []):
            return value
    return default


# ─────────────────────────────────────────────
# SQUIGGLE API
# ─────────────────────────────────────────────

def fetch_squiggle_teams() -> list[dict]:
    """Fetch all AFL teams from Squiggle, including logo URLs."""
    data = _get(SQUIGGLE_BASE, {"q": "teams"})
    if not data or not isinstance(data, dict):
        return []
    return data.get("teams", [])


def afl_player_headshot_url(player_id: int | None, first_name: str = "", last_name: str = "") -> str | None:
    """
    Return the public headshot URL for a player.

    For positive (Fryzigg) player IDs, returns a proxy URL via /api/afl/player-headshot/
    to avoid CORS/hotlink blocks on the CDN.
    For zero/negative IDs (2026 debuts), returns None so the UI falls back to initials.
    """
    if not player_id or player_id <= 0:
        return None
    params = ""
    if first_name and last_name:
        params = f"?first_name={first_name}&last_name={last_name}"
    return f"/api/afl/player-headshot/{player_id}{params}"


def fetch_squiggle_games(year: int, round_number: int = None) -> list[dict]:
    params = {"q": "games", "year": year}
    if round_number is not None:
        params["round"] = round_number

    data = _get(SQUIGGLE_BASE, params)
    if not data or not isinstance(data, dict):
        return []

    return data.get("games", [])


def fetch_squiggle_standings(year: int, round_number: int = None) -> list[dict]:
    params = {"q": "standings", "year": year}
    if round_number is not None:
        params["round"] = round_number

    data = _get(SQUIGGLE_BASE, params)
    if not data or not isinstance(data, dict):
        return []

    return data.get("standings", [])


def fetch_squiggle_tips(year: int, round_number: int = None, source_id: int = 8) -> list[dict]:
    params = {"q": "tips", "year": year, "source": source_id}
    if round_number is not None:
        params["round"] = round_number

    data = _get(SQUIGGLE_BASE, params)
    if not data or not isinstance(data, dict):
        return []

    return data.get("tips", [])


def fetch_squiggle_current_round(year: int = None) -> int:
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


def _afl_get(
    url: str,
    params: Optional[dict] = None,
    token: Optional[str] = None,
    retries: int = 2,
) -> Optional[dict]:
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
    comp_code = "AFL" if comp == "AFLM" else comp
    data = _afl_get(f"{AFL_META_BASE}/afl/v2/competitions", params={"pageSize": 50})
    if not data or "competitions" not in data:
        return None

    comps = data.get("competitions") or []
    matches = [
        comp_row
        for comp_row in comps
        if "Legacy" not in _coerce_str(comp_row.get("name"))
        and comp_row.get("code") == comp_code
    ]
    ids = [comp_row.get("id") for comp_row in matches if comp_row.get("id") is not None]

    return min(ids) if ids else None


def _find_season_id(season: int, comp: str = "AFLM") -> Optional[int]:
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
        now = pd.Timestamp.utcnow()
        filtered = []
        for round_row in rounds:
            utc_start = _coerce_datetime(round_row.get("utcStartTime"))
            if utc_start is not None and utc_start < now:
                filtered.append(round_row)
        rounds = filtered

    if round_number is None:
        ids = [round_row.get("id") for round_row in rounds if round_row.get("id") is not None]
    else:
        ids = [
            round_row.get("id")
            for round_row in rounds
            if round_row.get("id") is not None and round_row.get("roundNumber") == round_number
        ]

    return [int(x) for x in ids if x is not None]


def _fetch_round_matches_afl(round_id: int, token: Optional[str] = None) -> list[dict]:
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
    if round_number not in (None, ""):
        params["roundNumber"] = round_number

    data = _afl_get(f"{AFL_META_BASE}/afl/v2/matches", params=params, token=None)
    if not data or "matches" not in data:
        logger.warning("AFL fixture: no matches returned for season=%s round=%s", season, round_number)
        return []

    matches = data.get("matches") or []
    if not isinstance(matches, list):
        return []

    # We already constrained the query to compSeasonId (unique to this season),
    # so all returned matches should belong to the requested season.  The
    # original name-based filter (`str(season) in comp_season_name`) was too
    # strict: if the AFL API changes its season-name format (e.g. "AFL Season
    # 2026" → something without "2026") every match gets rejected, producing 0
    # rows even though data exists.  We now accept all matches from the API
    # response (they're already season-scoped) and emit a diagnostic warning
    # when no name evidence of the expected year is found.
    filtered = list(matches)
    mismatched = sum(
        1 for m in filtered
        if (s := _coerce_str((m.get("compSeason") or {}).get("shortName")
                             or (m.get("compSeason") or {}).get("name")))
        and str(season) not in s
    )

    if mismatched and mismatched == len(filtered):
        logger.warning(
            "AFL fixture: season %s — none of the %s matches have '%s' in "
            "compSeason name (API may have changed season-name format). "
            "Accepting all matches since compSeasonId=%s already scopes results.",
            season, len(filtered), season, comp_season_id,
        )

    logger.info(
        "AFL fixture: %s matches for season %s round=%s via afl/v2/matches",
        len(filtered),
        season,
        round_number,
    )
    return filtered


def _clean_names_playerstats_afl(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    cleaned = df.copy()
    cleaned.columns = [str(col) for col in cleaned.columns]
    cleaned.columns = [col.replace("playerStats.", "") for col in cleaned.columns]
    cleaned.columns = [col.replace("stats.", "") for col in cleaned.columns]
    cleaned.columns = [col.replace("playerName.", "") for col in cleaned.columns]
    return cleaned


def _fetch_match_stats_afl(match_provider_id: int, token: Optional[str] = None) -> pd.DataFrame:
    token = token or _get_afl_cookie()
    if not token:
        logger.warning(
            "AFL stats API diagnostics: missing auth token before player stats fetch for match providerId=%s",
            match_provider_id,
        )
        return pd.DataFrame()

    stats_url = f"{AFL_API_BASE}/cfs/afl/playerStats/match/{match_provider_id}"
    headers = dict(HEADERS)
    headers["x-media-mis-token"] = token

    logger.info(
        "AFL stats API diagnostics: GET %s (providerId=%s)",
        stats_url,
        match_provider_id,
    )

    try:
        response = requests.get(stats_url, headers=headers, timeout=40)
    except requests.RequestException as exc:
        logger.error(
            "AFL stats API diagnostics: request failed for providerId=%s url=%s error=%s",
            match_provider_id,
            stats_url,
            exc,
        )
        return pd.DataFrame()

    content_type = response.headers.get("Content-Type", "")
    logger.info(
        "AFL stats API diagnostics: providerId=%s status=%s content_type=%s headers=%s",
        match_provider_id,
        response.status_code,
        content_type,
        dict(response.headers),
    )

    raw_body_preview = (response.text or "")[:1000]
    logger.info(
        "AFL stats API diagnostics: providerId=%s raw_body_preview=%r",
        match_provider_id,
        raw_body_preview,
    )

    if response.status_code == 401:
        logger.warning(
            "AFL stats API diagnostics: providerId=%s returned 401 (likely token/auth issue)",
            match_provider_id,
        )

    data = _afl_parse_response(response)
    if not data:
        logger.warning(
            "AFL stats API diagnostics: empty/invalid parsed response for providerId=%s",
            match_provider_id,
        )
        return pd.DataFrame()

    if isinstance(data, dict):
        logger.info(
            "AFL stats API diagnostics: providerId=%s top_level_keys=%s",
            match_provider_id,
            list(data.keys()),
        )
    else:
        logger.warning(
            "AFL stats API diagnostics: providerId=%s parsed payload is %s not dict",
            match_provider_id,
            type(data).__name__,
        )
        return pd.DataFrame()

    home_stats = data.get("homeTeamPlayerStats") or []
    away_stats = data.get("awayTeamPlayerStats") or []

    # If the expected keys are absent the AFL API may have changed its response
    # structure.  Log the top-level keys so the cron log captures the new shape.
    if not home_stats and not away_stats:
        top_keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
        logger.warning(
            "AFL stats API: match %s returned no homeTeamPlayerStats/awayTeamPlayerStats. "
            "Top-level response keys: %s — API format may have changed.",
            match_provider_id, top_keys,
        )
        # Try common alternative key names used by the AFL API in past/future versions.
        found_alt = False
        for home_key, away_key in (
            ("homeTeamPlayersStats", "awayTeamPlayersStats"),
            ("home", "away"),
            ("homeTeam", "awayTeam"),
        ):
            candidate_home = data.get(home_key)
            candidate_away = data.get(away_key)
            if isinstance(candidate_home, list) and candidate_home:
                home_stats = candidate_home
                away_stats = candidate_away or []
                logger.info(
                    "AFL stats API: match %s — found stats under '%s'/'%s'",
                    match_provider_id, home_key, away_key,
                )
                found_alt = True
                break
            # handle {"home": {"players": [...]}} shape
            if isinstance(candidate_home, dict):
                for sub in ("players", "playerStats", "stats"):
                    if isinstance(candidate_home.get(sub), list) and candidate_home[sub]:
                        home_stats = candidate_home[sub]
                        away_stats = (candidate_away or {}).get(sub) or []
                        logger.info(
                            "AFL stats API: match %s — found stats under '%s.%s'/'%s.%s'",
                            match_provider_id, home_key, sub, away_key, sub,
                        )
                        found_alt = True
                        break
                if found_alt:
                    break

    if not home_stats and not away_stats:
        logger.warning(
            "AFL stats API diagnostics: providerId=%s response contains zero player stat rows (expected home/away player stats keys missing or empty)",
            match_provider_id,
        )

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

    detected_records = len(home_stats) + len(away_stats)
    logger.info(
        "AFL stats API diagnostics: providerId=%s detected home_records=%s away_records=%s total_records=%s",
        match_provider_id,
        len(home_stats),
        len(away_stats),
        detected_records,
    )

    if home_df.empty and away_df.empty:
        logger.warning(
            "AFL stats API diagnostics: providerId=%s parser produced 0 dataframe rows from detected_records=%s",
            match_provider_id,
            detected_records,
        )
        return pd.DataFrame()

    return pd.concat([home_df, away_df], ignore_index=True)


def _normalise_afl_fixture_match(match: dict) -> dict:
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
        "home.team.name": _normalise_team_name(home_name),
        "away.team.name": _normalise_team_name(away_name),
    }


def fetch_afl_player_stats_current_season(season: int, round_number: int = None, comp: str = "AFLM") -> list[dict]:
    """
    Fetch current-season player stats using AFL official API
    for ALL completed rounds in the current season.
    """
    try:
        current_round = round_number or fetch_squiggle_current_round(season)
    except Exception:
        current_round = round_number or 1

    # current_round is the first round with any incomplete game (from Squiggle complete!=100).
    # Only rounds before it are considered fully completed.
    # Round 0 is the Opening Round (used in seasons like 2026 where an Opening Round
    # precedes the officially-numbered rounds). Always include it — fetch_fixture_afl
    # will return an empty list if the season has no Opening Round.
    completed_rounds = [0] + list(range(1, current_round))
    if current_round <= 1:
        logger.warning(
            "AFL current-season stats: no completed rounds yet for season %s "
            "(current_round=%s — season may not have started)",
            season, current_round,
        )
        return []

    all_matches = []
    for rnd in completed_rounds:
        round_matches = fetch_fixture_afl(season, round_number=rnd, comp=comp)
        if round_matches:
            all_matches.extend(round_matches)
        time.sleep(0.1)

    if not all_matches:
        logger.warning(
            "AFL current-season stats: no fixtures found for completed rounds in season %s",
            season
        )
        return []

    # Deduplicate by providerId
    deduped = {}
    for match in all_matches:
        provider_id = match.get("providerId")
        if provider_id:
            deduped[provider_id] = match

    filtered_matches = list(deduped.values())
    if not filtered_matches:
        logger.warning("AFL current-season stats: no providerIds found for season %s", season)
        return []

    token = _get_afl_cookie()
    if not token:
        logger.warning(
            "AFL current-season stats: could not obtain auth token — "
            "stats API is unavailable (WMCTok endpoint may be down or changed)."
        )
        return []

    match_details_map = {
        m.get("providerId"): _normalise_afl_fixture_match(m)
        for m in filtered_matches
        if m.get("providerId")
    }

    all_rows: list[dict] = []

    for match_provider_id, details in match_details_map.items():
        try:
            stats_df = _fetch_match_stats_afl(match_provider_id, token=token)
            if stats_df.empty:
                continue

            for _, row in stats_df.iterrows():
                all_rows.append(_build_afl_current_row(row, details, season, match_provider_id))

            time.sleep(0.1)

        except Exception as exc:
            logger.warning("AFL match stats fetch failed for match %s: %s", match_provider_id, exc)

    all_rows = [r for r in all_rows if r.get("match_id") and r.get("player_id")]

    logger.info(
        "AFL current-season stats: prepared %s rows for season %s across rounds %s",
        len(all_rows), season, completed_rounds
    )
    return all_rows


def _build_afl_current_row(row: pd.Series, details: dict, season: int, match_id: int) -> dict:
    """Map AFL current-season row into DB shape."""
    row_dict = row.to_dict() if hasattr(row, "to_dict") else dict(row)

    def pick(*keys, default=None):
        return _pick_from_dict(row_dict, *keys, default=default)

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
            "playerName",
        )
    )

    if not first_name and full_name:
        parts = full_name.split()
        first_name = parts[0] if parts else ""
        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    home_team = _normalise_team_name(details.get("home.team.name"))
    away_team = _normalise_team_name(details.get("away.team.name"))

    player_team = _normalise_team_name(pick("team.name", "team.teamName", "teamName"))
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
    player_id = _coerce_int(player_id_raw)

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
        "player_id": player_id,
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
    if not os.environ.get("AFL_CRON_MODE"):
        # Allow in local dev or when explicitly permitted
        if not (
            os.environ.get("FLASK_ENV") == "development"
            or os.environ.get("ALLOW_FRYZIGG_RDS") == "1"
        ):
            raise RuntimeError(
                "Fryzigg RDS blocked outside cron. "
                "Set AFL_CRON_MODE=1 (cron), FLASK_ENV=development (local dev), "
                "or ALLOW_FRYZIGG_RDS=1 to allow."
            )

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

        df.columns = [str(col).strip().lower() for col in df.columns]
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
    Filter by date range.
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

    def build_row(row: pd.Series) -> dict:
        def g(column_name: Optional[str]):
            return _safe_series_get(row, column_name)

        return {
            "match_id": _coerce_match_id(g(c_match_id)),
            "match_date": _coerce_date(g(match_date_col)),
            "match_round": _coerce_str(g(c_match_round)),
            "match_home_team": _normalise_team_name(g(c_home_team)),
            "match_away_team": _normalise_team_name(g(c_away_team)),
            "match_home_team_score": _coerce_int(g(c_home_score)),
            "match_away_team_score": _coerce_int(g(c_away_score)),
            "match_margin": _coerce_int(g(c_match_margin)),
            "match_winner": _normalise_team_name(g(c_match_winner)),
            "match_weather_temp_c": _coerce_int(g(c_weather_temp)),
            "match_weather_type": _coerce_str(g(c_weather_type)),
            "match_attendance": _coerce_int(g(c_attendance)),
            "venue_name": _coerce_str(g(c_venue)),
            "season": season,
            "player_id": _coerce_int(g(c_player_id)),
            "player_first_name": _coerce_str(g(c_first)),
            "player_last_name": _coerce_str(g(c_last)),
            "player_team": _normalise_team_name(g(c_team)),
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
    rows = [row for row in rows if row["player_id"] and row["match_id"]]

    logger.info("Fryzigg: prepared %s rows for season %s", len(rows), season)
    return rows

def fetch_afltables_player_stats_rpy2(season: int) -> list[dict]:
    """
    Load pre-fetched player stats CSV from repo.
    """
    try:
        csv_path = f"data/afl_{season}_stats.csv"
        
        df = pd.read_csv(csv_path)
        logger.info("Loaded %s rows from %s", len(df), csv_path)
        
        rows = []
        for _, row in df.iterrows():
            rows.append({
                "match_id": _coerce_match_id(row.get("match_id")),
                "match_date": _coerce_date(row.get("Date")),
                "match_round": _coerce_str(row.get("Round")),
                "match_home_team": _normalise_team_name(row.get("Home.Team")),
                "match_away_team": _normalise_team_name(row.get("Away.Team")),
                "season": season,
                "player_id": _coerce_int(row.get("ID")),
                "player_first_name": _coerce_str(row.get("First.Name")),
                "player_last_name": _coerce_str(row.get("Last.Name")),
                "player_team": _normalise_team_name(row.get("Team")),
                "guernsey_number": _coerce_int(row.get("Number")),
                "kicks": _coerce_int(row.get("KI")),
                "marks": _coerce_int(row.get("MK")),
                "handballs": _coerce_int(row.get("HB")),
                "disposals": _coerce_int(row.get("DI")),
                "effective_disposals": _coerce_int(row.get("DA")),
                "goals": _coerce_int(row.get("GL")),
                "behinds": _coerce_int(row.get("BH")),
                "hitouts": _coerce_int(row.get("HO")),
                "tackles": _coerce_int(row.get("TK")),
                "rebounds": _coerce_int(row.get("RB")),
                "inside_fifties": _coerce_int(row.get("IF")),
                "clearances": _coerce_int(row.get("CL")),
                "clangers": _coerce_int(row.get("CG")),
                "free_kicks_for": _coerce_int(row.get("FF")),
                "free_kicks_against": _coerce_int(row.get("FA")),
                "contested_possessions": _coerce_int(row.get("CP")),
                "uncontested_possessions": _coerce_int(row.get("UP")),
                "contested_marks": _coerce_int(row.get("CM")),
                "one_percenters": _coerce_int(row.get("1%")),
                "bounces": _coerce_int(row.get("BO")),
                "goal_assists": _coerce_int(row.get("GA")),
            })
        
        return rows
        
    except FileNotFoundError:
        logger.warning("CSV not found: data/afl_%s_stats.csv", season)
        return []
    except Exception as exc:
        logger.error("Failed to load CSV for %s: %s", season, exc)
        return []


def fetch_fryzigg_player_stats(season: int) -> list[dict]:
    """
    Unified player stats fetch:
    1. fitzRoy R package (current season)
    2. Fryzigg (historical)
    """

    # ── 1. 2026 CSV (PRIMARY for current year) ──────────────────────────────
    # fetch_afltables_player_stats_rpy2 uses column names from an older CSV
    # schema (e.g. "Home.Team", "First.Name") that do not match the current
    # afl_2026_stats.csv produced by the GitHub Actions workflow.  Use the
    # dedicated fetch_2026_stats_from_csv() parser which normalises column names
    # and applies _normalise_team_name() so stored values are consistent with
    # the AFL API path and settlement queries.
    if season >= CURRENT_YEAR:
        logger.info("Player stats: loading current-season CSV for %s", season)
        csv_rows = fetch_2026_stats_from_csv()
        if csv_rows:
            logger.info("Player stats: got %s rows from CSV for %s", len(csv_rows), season)
            return csv_rows
        logger.warning("Player stats: CSV returned no rows for %s, falling back to Fryzigg RDS", season)

    return _fetch_fryzigg_player_stats_from_rds(season)

def fetch_fryzigg_player_stats_range(start_year: int, end_year: int) -> list[dict]:
    all_stats: list[dict] = []

    for year in range(start_year, end_year + 1):
        logger.info("Fetching player stats for %s...", year)
        stats = fetch_fryzigg_player_stats(year)
        all_stats.extend(stats)
        time.sleep(1)

    return all_stats


def _filter_valid_stat_rows(rows: list[dict]) -> list[dict]:
    """Drop rows that are missing a player_id or match_id (cannot be upserted)."""
    valid = [r for r in rows if r.get("player_id") and r.get("match_id")]
    dropped = len(rows) - len(valid)
    if dropped:
        logger.warning("Dropped %s rows with missing player_id or match_id", dropped)
    return valid


def _parse_fryzigg_csv_df(df: pd.DataFrame, cols: set, season: int) -> list[dict]:
    """
    Parse a Fryzigg-format DataFrame into DB-ready rows.

    Fryzigg column names (e.g. from fitzRoy fetch_player_stats(source='fryzigg'))
    already match the DB schema.  player_id is the stable Fryzigg ID — identical
    to those stored for 2019-2025 — so no name-matching override is needed for
    existing players, and no hash derivation is needed for match_id.
    """
    c_match_id       = _first_existing(cols, "match_id", "game_id")
    match_date_col   = _first_existing(cols, "match_date", "date")
    c_match_round    = _first_existing(cols, "match_round", "round")
    c_home_team      = _first_existing(cols, "match_home_team", "home_team")
    c_away_team      = _first_existing(cols, "match_away_team", "away_team")
    c_home_score     = _first_existing(cols, "match_home_team_score", "home_score")
    c_away_score     = _first_existing(cols, "match_away_team_score", "away_score")
    c_margin         = _first_existing(cols, "match_margin", "margin")
    c_winner         = _first_existing(cols, "match_winner", "winner")
    c_weather_temp   = _first_existing(cols, "match_weather_temp_c", "weather_temp_c")
    c_weather_type   = _first_existing(cols, "match_weather_type", "weather_type")
    c_attendance     = _first_existing(cols, "match_attendance", "attendance")
    c_venue          = _first_existing(cols, "venue_name", "venue")
    c_player_id      = _first_existing(cols, "player_id")
    c_first          = _first_existing(cols, "player_first_name", "first_name")
    c_last           = _first_existing(cols, "player_last_name", "last_name")
    c_team           = _first_existing(cols, "player_team", "team")
    c_guernsey       = _first_existing(cols, "guernsey_number", "guernsey")
    c_height         = _first_existing(cols, "player_height_cm", "height_cm")
    c_weight         = _first_existing(cols, "player_weight_kg", "weight_kg")
    c_retired        = _first_existing(cols, "player_is_retired", "is_retired")
    c_kicks          = _first_existing(cols, "kicks")
    c_marks          = _first_existing(cols, "marks")
    c_handballs      = _first_existing(cols, "handballs")
    c_disposals      = _first_existing(cols, "disposals")
    c_eff_disp       = _first_existing(cols, "effective_disposals")
    c_disp_pct       = _first_existing(cols, "disposal_efficiency_percentage")
    c_goals          = _first_existing(cols, "goals")
    c_behinds        = _first_existing(cols, "behinds")
    c_hitouts        = _first_existing(cols, "hitouts")
    c_tackles        = _first_existing(cols, "tackles")
    c_rebounds       = _first_existing(cols, "rebounds")
    c_i50            = _first_existing(cols, "inside_fifties")
    c_clearances     = _first_existing(cols, "clearances")
    c_clangers       = _first_existing(cols, "clangers")
    c_free_for       = _first_existing(cols, "free_kicks_for")
    c_free_against   = _first_existing(cols, "free_kicks_against")
    c_brownlow       = _first_existing(cols, "brownlow_votes")
    c_cont_poss      = _first_existing(cols, "contested_possessions")
    c_uncont_poss    = _first_existing(cols, "uncontested_possessions")
    c_cont_marks     = _first_existing(cols, "contested_marks")
    c_marks_i50      = _first_existing(cols, "marks_inside_fifty")
    c_one_pct        = _first_existing(cols, "one_percenters")
    c_bounces        = _first_existing(cols, "bounces")
    c_goal_assists   = _first_existing(cols, "goal_assists")
    c_tog            = _first_existing(cols, "time_on_ground_percentage")
    c_fantasy        = _first_existing(cols, "afl_fantasy_score")
    c_supercoach     = _first_existing(cols, "supercoach_score")
    c_centre_clear   = _first_existing(cols, "centre_clearances")
    c_stop_clear     = _first_existing(cols, "stoppage_clearances")
    c_score_inv      = _first_existing(cols, "score_involvements")
    c_metres         = _first_existing(cols, "metres_gained")
    c_turnovers      = _first_existing(cols, "turnovers")
    c_intercepts     = _first_existing(cols, "intercepts")
    c_tackles_i50    = _first_existing(cols, "tackles_inside_fifty")
    c_def_losses     = _first_existing(cols, "contest_def_losses")
    c_def_1v1        = _first_existing(cols, "contest_def_one_on_ones")
    c_off_1v1        = _first_existing(cols, "contest_off_one_on_ones")

    def build_row(row: pd.Series) -> dict:
        def g(col_name):
            return _safe_series_get(row, col_name)
        return {
            "match_id":                       _coerce_match_id(g(c_match_id)),
            "match_date":                     _coerce_date(g(match_date_col)),
            "match_round":                    _coerce_str(g(c_match_round)),
            "match_home_team":                _normalise_team_name(g(c_home_team)),
            "match_away_team":                _normalise_team_name(g(c_away_team)),
            "match_home_team_score":          _coerce_int(g(c_home_score)),
            "match_away_team_score":          _coerce_int(g(c_away_score)),
            "match_margin":                   _coerce_int(g(c_margin)),
            "match_winner":                   _normalise_team_name(g(c_winner)),
            "match_weather_temp_c":           _coerce_int(g(c_weather_temp)),
            "match_weather_type":             _coerce_str(g(c_weather_type)),
            "match_attendance":               _coerce_int(g(c_attendance)),
            "venue_name":                     _coerce_str(g(c_venue)),
            "season":                         season,
            "player_id":                      _coerce_int(g(c_player_id)),
            "player_first_name":              _coerce_str(g(c_first)),
            "player_last_name":               _coerce_str(g(c_last)),
            "player_team":                    _normalise_team_name(g(c_team)),
            "guernsey_number":                _coerce_int(g(c_guernsey)),
            "player_height_cm":               _coerce_int(g(c_height)),
            "player_weight_kg":               _coerce_int(g(c_weight)),
            "player_is_retired":              _coerce_bool(g(c_retired), False),
            "kicks":                          _coerce_int(g(c_kicks)),
            "marks":                          _coerce_int(g(c_marks)),
            "handballs":                      _coerce_int(g(c_handballs)),
            "disposals":                      _coerce_int(g(c_disposals)),
            "effective_disposals":            _coerce_int(g(c_eff_disp)),
            "disposal_efficiency_percentage": _coerce_int(g(c_disp_pct)),
            "goals":                          _coerce_int(g(c_goals)),
            "behinds":                        _coerce_int(g(c_behinds)),
            "hitouts":                        _coerce_int(g(c_hitouts)),
            "tackles":                        _coerce_int(g(c_tackles)),
            "rebounds":                       _coerce_int(g(c_rebounds)),
            "inside_fifties":                 _coerce_int(g(c_i50)),
            "clearances":                     _coerce_int(g(c_clearances)),
            "clangers":                       _coerce_int(g(c_clangers)),
            "free_kicks_for":                 _coerce_int(g(c_free_for)),
            "free_kicks_against":             _coerce_int(g(c_free_against)),
            "brownlow_votes":                 _coerce_int(g(c_brownlow)),
            "contested_possessions":          _coerce_int(g(c_cont_poss)),
            "uncontested_possessions":        _coerce_int(g(c_uncont_poss)),
            "contested_marks":                _coerce_int(g(c_cont_marks)),
            "marks_inside_fifty":             _coerce_int(g(c_marks_i50)),
            "one_percenters":                 _coerce_int(g(c_one_pct)),
            "bounces":                        _coerce_int(g(c_bounces)),
            "goal_assists":                   _coerce_int(g(c_goal_assists)),
            "time_on_ground_percentage":      _coerce_int(g(c_tog)),
            "afl_fantasy_score":              _coerce_int(g(c_fantasy)),
            "supercoach_score":               _coerce_int(g(c_supercoach)),
            "centre_clearances":              _coerce_int(g(c_centre_clear)),
            "stoppage_clearances":            _coerce_int(g(c_stop_clear)),
            "score_involvements":             _coerce_int(g(c_score_inv)),
            "metres_gained":                  _coerce_int(g(c_metres)),
            "turnovers":                      _coerce_int(g(c_turnovers)),
            "intercepts":                     _coerce_int(g(c_intercepts)),
            "tackles_inside_fifty":           _coerce_int(g(c_tackles_i50)),
            "contest_def_losses":             _coerce_int(g(c_def_losses)),
            "contest_def_one_on_ones":        _coerce_int(g(c_def_1v1)),
            "contest_off_one_on_ones":        _coerce_int(g(c_off_1v1)),
        }

    rows = [build_row(row) for _, row in df.iterrows()]
    rows = _filter_valid_stat_rows(rows)
    logger.info("2026 Fryzigg CSV: prepared %s rows for DB upsert", len(rows))
    return rows


def _parse_afltables_csv_df(df: pd.DataFrame, cols: set, season: int) -> list[dict]:
    """
    Parse an AFLTables-format DataFrame (legacy source='afltables' CSV).

    Column names use dot-separated capitalised form (First.name, Surname, etc.)
    and the ID column is an AFLTables player ID that may collide numerically with
    Fryzigg IDs.  upsert_player_stats() will apply name-matching to resolve the
    correct historical player_id when it detects a collision.
    """
    def col(row, name, default=None):
        return row[name] if name in row.index else default

    rows: list[dict] = []
    for _, row in df.iterrows():
        first_name  = _coerce_str(col(row, "first.name"))
        last_name   = _coerce_str(col(row, "surname"))
        player_id   = _coerce_int(col(row, "id"))
        row_season  = _coerce_int(col(row, "season"), season)

        home_team   = _coerce_str(col(row, "home.team"))
        away_team   = _coerce_str(col(row, "away.team"))
        home_score  = _coerce_int(col(row, "home.score"))
        away_score  = _coerce_int(col(row, "away.score"))

        winner, margin = "", 0
        if home_score > away_score:
            winner, margin = home_team, home_score - away_score
        elif away_score > home_score:
            winner, margin = away_team, away_score - home_score

        date_str    = _coerce_str(col(row, "date"))
        local_start = _coerce_str(col(row, "local.start.time"))
        # Hash is computed from raw names for backward compatibility so
        # ON CONFLICT can fire and update existing rows with normalized names.
        match_key   = f"{row_season}|{date_str}|{home_team}|{away_team}|{local_start}"

        # Normalize team names for storage — ensures consistency with AFL API
        # rows and settlement queries (e.g. "Greater Western Sydney" → "GWS Giants").
        home_team_norm = _normalise_team_name(home_team)
        away_team_norm = _normalise_team_name(away_team)
        winner_norm    = _normalise_team_name(winner) if winner else ""

        rows.append({
            "match_id":                       _hash_match_key_to_bigint(match_key),
            "match_date":                     _coerce_date(col(row, "date")),
            "match_round":                    _coerce_str(col(row, "round")),
            "match_home_team":                home_team_norm,
            "match_away_team":                away_team_norm,
            "match_home_team_score":          home_score,
            "match_away_team_score":          away_score,
            "match_margin":                   margin,
            "match_winner":                   winner_norm,
            "match_weather_temp_c":           0,
            "match_weather_type":             "",
            "match_attendance":               _coerce_int(col(row, "attendance")),
            "venue_name":                     _coerce_str(col(row, "venue")),
            "season":                         row_season,
            "player_id":                      player_id,
            "player_first_name":              first_name,
            "player_last_name":               last_name,
            "player_team":                    _normalise_team_name(_coerce_str(col(row, "playing.for"))),
            "guernsey_number":                _coerce_int(col(row, "jumper.no.")),
            "player_height_cm":               0,
            "player_weight_kg":               0,
            "player_is_retired":              False,
            "kicks":                          _coerce_int(col(row, "kicks")),
            "marks":                          _coerce_int(col(row, "marks")),
            "handballs":                      _coerce_int(col(row, "handballs")),
            "disposals":                      _coerce_int(col(row, "disposals")),
            "effective_disposals":            0,
            "disposal_efficiency_percentage": 0,
            "goals":                          _coerce_int(col(row, "goals")),
            "behinds":                        _coerce_int(col(row, "behinds")),
            "hitouts":                        _coerce_int(col(row, "hit.outs")),
            "tackles":                        _coerce_int(col(row, "tackles")),
            "rebounds":                       _coerce_int(col(row, "rebounds")),
            "inside_fifties":                 _coerce_int(col(row, "inside.50s")),
            "clearances":                     _coerce_int(col(row, "clearances")),
            "clangers":                       _coerce_int(col(row, "clangers")),
            "free_kicks_for":                 _coerce_int(col(row, "frees.for")),
            "free_kicks_against":             _coerce_int(col(row, "frees.against")),
            "brownlow_votes":                 _coerce_int(col(row, "brownlow.votes")),
            "contested_possessions":          _coerce_int(col(row, "contested.possessions")),
            "uncontested_possessions":        _coerce_int(col(row, "uncontested.possessions")),
            "contested_marks":                _coerce_int(col(row, "contested.marks")),
            "marks_inside_fifty":             _coerce_int(col(row, "marks.inside.50")),
            "one_percenters":                 _coerce_int(col(row, "one.percenters")),
            "bounces":                        _coerce_int(col(row, "bounces")),
            "goal_assists":                   _coerce_int(col(row, "goal.assists")),
            "time_on_ground_percentage":      _coerce_int(col(row, "time.on.ground")),
            "afl_fantasy_score":              0,
            "supercoach_score":               0,
            "centre_clearances":              0,
            "stoppage_clearances":            0,
            "score_involvements":             0,
            "metres_gained":                  0,
            "turnovers":                      0,
            "intercepts":                     0,
            "tackles_inside_fifty":           0,
            "contest_def_losses":             0,
            "contest_def_one_on_ones":        0,
            "contest_off_one_on_ones":        0,
        })

    rows = _filter_valid_stat_rows(rows)
    logger.info("2026 AFLTables CSV: prepared %s rows for DB upsert", len(rows))
    return rows


def fetch_2026_stats_from_csv(csv_path: Path | None = None) -> list[dict]:
    """
    Load 2026 AFL stats from the locally committed CSV generated by the
    'Fetch AFL 2026 Stats' GitHub Actions workflow.

    Supports two CSV formats (auto-detected by column names):

    Fryzigg format  (source='fryzigg', preferred)
        Column names are snake_case and match the DB schema directly.
        player_id is the stable Fryzigg ID — identical to those in 2019-2025 —
        so no collision fix is needed for established players, and debut players
        receive the real Fryzigg-assigned ID that will remain consistent in
        future seasons.

    AFLTables legacy format  (source='afltables')
        Column names use dot-separated capitalised form (First.name, Surname…).
        player_id is an AFLTables ID that may collide with Fryzigg IDs.
        upsert_player_stats() applies name-matching to resolve the correct
        historical player_id for each row.
    """
    path = csv_path or AFL_2026_CSV_PATH

    if not path.exists():
        logger.warning("2026 CSV stats file not found: %s", path)
        return []

    try:
        df = pd.read_csv(path)
    except Exception as exc:
        logger.error("Failed reading 2026 CSV stats file %s: %s", path, exc)
        return []

    if df is None or df.empty:
        logger.warning("2026 CSV stats file is empty: %s", path)
        return []

    # Compute lower-cased column set for format detection first, then normalise
    # the DataFrame's own column names so the parsers can access them uniformly.
    cols = {str(c).strip().lower() for c in df.columns}
    df.columns = [str(c).strip().lower() for c in df.columns]

    logger.info("2026 CSV: loaded %s rows, %s columns from %s", len(df), len(cols), path)

    # ── CSV freshness check ────────────────────────────────────────────────
    # Detect stale data: warn if the most recent match date in the CSV is more
    # than 10 days ago.  This surfaces situations where the GitHub Actions
    # workflow hasn't committed updated data (e.g. workflow failure) so that
    # settlement can't grade picks from the latest completed round.
    date_col = next(
        (c for c in ("date", "match_date") if c in cols),
        None,
    )
    if date_col:
        try:
            latest_date = pd.to_datetime(df[date_col], errors="coerce").max()
            if pd.notna(latest_date):
                age_days = (pd.Timestamp.utcnow().normalize() - latest_date.normalize()).days
                if age_days > 10:
                    logger.warning(
                        "2026 CSV: most recent match date is %s (%d days ago) — "
                        "CSV appears stale. Check the 'Fetch AFL 2026 Stats' "
                        "GitHub Actions workflow for failures.",
                        latest_date.date(), age_days,
                    )
                else:
                    logger.info(
                        "2026 CSV: most recent match date %s (%d days ago)",
                        latest_date.date(), age_days,
                    )
        except Exception:
            pass

    # ── Fryzigg format: stable player_id column present ───────────────────
    if "player_id" in cols:
        logger.info("2026 CSV: Fryzigg format detected — player_id values are stable Fryzigg IDs")
        return _parse_fryzigg_csv_df(df, cols, season=2026)

    # ── AFLTables legacy format ────────────────────────────────────────────
    logger.info("2026 CSV: AFLTables legacy format detected — player_id will be resolved via name-matching")
    return _parse_afltables_csv_df(df, cols, season=2026)

# ─────────────────────────────────────────────
# AFL TABLES
# ─────────────────────────────────────────────

def fetch_afltables_results(year: int) -> list[dict]:
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

def get_odds_api_key() -> str:
    """
    Read The Odds API key from Railway environment variables.
    Supports a couple of common variable names.
    """
    key = (
        os.environ.get("ODDS_API_KEY")
        or os.environ.get("THE_ODDS_API_KEY")
        or os.environ.get("ODDSAPI_KEY")
        or ""
    ).strip()

    if not key:
        logger.error("No Odds API key found in environment variables")
        logger.info("Set ODDS_API_KEY in Railway environment variables")
        return ""

    if len(key) < _ODDS_API_KEY_MIN_LENGTH:
        logger.error("Odds API key appears invalid (too short: %d characters)", len(key))
        return ""

    logger.info("Found Odds API key in environment (%d characters)", len(key))
    return key


def validate_odds_api_key(api_key: str) -> bool:
    """Validate The Odds API key by making a test request to the sports endpoint."""
    if not api_key or len(api_key.strip()) < _ODDS_API_KEY_MIN_LENGTH:
        return False

    test_url = f"{ODDS_API_BASE}/sports"
    test_data = _get(test_url, {"api_key": api_key.strip()})

    if test_data and isinstance(test_data, list):
        logger.info("Odds API key validation successful")
        return True

    logger.error("Odds API key validation failed — check that ODDS_API_KEY is correct in Railway")
    return False


def debug_railway_environment() -> None:
    """Log Railway environment variables relevant to The Odds API."""
    logger.info("Debugging Railway environment variables:")

    odds_key = os.environ.get("ODDS_API_KEY")
    if odds_key:
        logger.info("ODDS_API_KEY found (%d characters)", len(odds_key))
    else:
        logger.error("ODDS_API_KEY not found")

    related = [k for k in os.environ if k in _ODDS_API_ENV_VARS]
    if related:
        logger.info("Related environment variables: %s", related)
    else:
        logger.info("No related environment variables found")


def test_odds_api_integration(api_key: Optional[str] = None) -> dict:
    """Test The Odds API integration with current configuration."""
    results: dict = {
        "api_key_found": False,
        "api_key_valid": False,
        "afl_events_found": False,
        "props_available": False,
        "error_messages": [],
    }

    try:
        key = api_key or get_odds_api_key()
        if key:
            results["api_key_found"] = True

            if validate_odds_api_key(key):
                results["api_key_valid"] = True

                events = fetch_afl_events(api_key=key)
                if events:
                    results["afl_events_found"] = True

                    props = fetch_afl_player_props(
                        api_key=key,
                        markets="player_disposals",
                        event_limit=1,
                    )
                    if props:
                        results["props_available"] = True

    except Exception as exc:
        results["error_messages"].append(str(exc))
        logger.exception("Unexpected error during Odds API integration test: %s", exc)

    return results


def fetch_afl_events(api_key: Optional[str] = None) -> list[dict]:
    """
    Fetch upcoming AFL events from The Odds API.
    """
    key = (api_key or get_odds_api_key()).strip()
    if not key:
        logger.warning("No Odds API key configured — skipping AFL events fetch")
        return []

    url = f"{ODDS_API_BASE}/sports/aussierules_afl/events"
    data = _get(url, {"api_key": key})

    if not data or not isinstance(data, list):
        logger.warning("Odds API returned no AFL events")
        return []

    logger.info("Fetched %d AFL events from Odds API", len(data))
    return data


def fetch_afl_match_odds(
    api_key: Optional[str] = None,
    markets: str = "h2h,spreads,totals",
    regions: str = "au",
    event_limit: Optional[int] = None,
) -> list[dict]:
    """
    Fetch featured AFL match odds for upcoming events.
    Useful for head-to-head / line / totals cards on the website.
    """
    key = (api_key or get_odds_api_key()).strip()
    if not key:
        logger.warning("No Odds API key configured — skipping match odds fetch")
        return []

    url = f"{ODDS_API_BASE}/sports/aussierules_afl/odds"
    params = {
        "api_key": key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": "decimal",
    }

    data = _get(url, params)
    if not data or not isinstance(data, list):
        return []

    if event_limit is not None:
        data = data[:event_limit]

    rows: list[dict] = []

    for event in data:
        event_id = event.get("id", "")
        home_team = event.get("home_team", "")
        away_team = event.get("away_team", "")
        commence_time = event.get("commence_time", "")

        for bookmaker in event.get("bookmakers", []) or []:
            bookmaker_key = bookmaker.get("key", "")
            bookmaker_name = bookmaker.get("title", "")
            last_update = bookmaker.get("last_update", "")

            for market in bookmaker.get("markets", []) or []:
                market_key = market.get("key", "")

                for outcome in market.get("outcomes", []) or []:
                    rows.append(
                        {
                            "event_id": event_id,
                            "home_team": home_team,
                            "away_team": away_team,
                            "commence_time": commence_time,
                            "bookmaker_key": bookmaker_key,
                            "bookmaker": bookmaker_name,
                            "last_update": last_update,
                            "market": market_key,
                            "selection_name": outcome.get("name", ""),
                            "line": outcome.get("point"),
                            "odds": outcome.get("price"),
                        }
                    )

    logger.info("Odds API: fetched %s AFL match odds rows", len(rows))
    return rows


def fetch_afl_h2h_spread_odds(
    api_key: Optional[str] = None,
    regions: str = "au",
) -> list[dict]:
    """Fetch AFL H2H + spread markets, flattened per outcome."""
    return fetch_afl_match_odds(
        api_key=api_key,
        markets="h2h,spreads",
        regions=regions,
    )


def _normalise_prop_market(market_key: str) -> str:
    mapping = {
        "player_disposals": "player_disposals",
        "player_kicks": "player_kicks",
        "player_kicks_over": "player_kicks",
        "player_handballs": "player_handballs",
        "player_handballs_over": "player_handballs",
        "player_marks": "player_marks",
        "player_marks_over": "player_marks",
        "player_tackles": "player_tackles",
        "player_tackles_over": "player_tackles",
        "player_goals": "player_goals",
        "player_goals_scored_over": "player_goals",
        "player_goals_over": "player_goals",
        "player_afl_fantasy_points": "player_afl_fantasy_points",
    }
    return mapping.get(_coerce_str(market_key), _coerce_str(market_key))


def _to_odds_api_market(market_key: str) -> str:
    """
    Convert canonical/internal market names into the Odds API keys.

    Odds API uses the *_over suffix for several AFL props. We still store and
    query everything internally by canonical names (e.g. player_marks), but we
    must request the provider's exact key when fetching.
    """
    canonical = _normalise_prop_market(market_key)
    return {
        "player_disposals": "player_disposals",
        "player_kicks": "player_kicks_over",
        "player_handballs": "player_handballs_over",
        "player_marks": "player_marks_over",
        "player_tackles": "player_tackles_over",
        "player_goals": "player_goals_scored_over",
        "player_afl_fantasy_points": "player_afl_fantasy_points",
    }.get(canonical, canonical)


def _normalise_line_type(value: Any) -> str:
    v = _coerce_str(value).strip().lower()
    if v == "over":
        return "Over"
    if v == "under":
        return "Under"
    return _coerce_str(value)


def fetch_afl_player_props(
    api_key: Optional[str] = None,
    markets: Optional[str | list[str]] = None,
    regions: str = "au",
    bookmakers: Optional[list[str]] = None,
    event_limit: int = 9,
) -> list[dict]:
    """
    Fetch AFL player props from The Odds API.

    Supports either:
    - a single market string
    - a list of market strings

    Notes:
    - Player props are queried via the event-specific odds endpoint.
    - `markets` can include one or many AFL player prop market keys.
    """
    key = (api_key or get_odds_api_key()).strip()
    if not key:
        logger.warning("No Odds API key configured — skipping prop fetch")
        return []

    if not markets:
        markets = [
            "player_disposals",
            "player_kicks",
            "player_handballs",
            "player_marks",
            "player_tackles",
            "player_goals",
            "player_afl_fantasy_points",
        ]
    elif isinstance(markets, str):
        markets = [markets]

    events = fetch_afl_events(api_key=key)
    if not events:
        return []

    if event_limit:
        events = events[:event_limit]

    odds_api_markets = []
    seen_markets = set()
    for market in markets:
        provider_market = _to_odds_api_market(market)
        if provider_market and provider_market not in seen_markets:
            odds_api_markets.append(provider_market)
            seen_markets.add(provider_market)

    market_string = ",".join(odds_api_markets)
    bookmaker_string = ",".join(bookmakers) if bookmakers else None

    props: list[dict] = []

    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue

        odds_url = f"{ODDS_API_BASE}/sports/aussierules_afl/events/{event_id}/odds"
        params = {
            "api_key": key,
            "regions": regions,
            "markets": market_string,
            "oddsFormat": "decimal",
        }
        if bookmaker_string:
            params["bookmakers"] = bookmaker_string

        odds_data = _get(odds_url, params)
        if not odds_data or not isinstance(odds_data, dict):
            continue

        home_team = odds_data.get("home_team", "")
        away_team = odds_data.get("away_team", "")
        commence_time = odds_data.get("commence_time", "")

        for bookmaker in odds_data.get("bookmakers", []) or []:
            bookmaker_key = bookmaker.get("key", "")
            bookmaker_name = bookmaker.get("title", "")
            last_update = bookmaker.get("last_update", "")

            for market_row in bookmaker.get("markets", []) or []:
                market_key = _normalise_prop_market(market_row.get("key", ""))

                for outcome in market_row.get("outcomes", []) or []:
                    props.append(
                        {
                            "event_id": event_id,
                            "home_team": home_team,
                            "away_team": away_team,
                            "commence_time": commence_time,
                            "bookmaker_key": bookmaker_key,
                            "bookmaker": bookmaker_name,
                            "last_update": last_update,
                            "market": market_key,
                            "player_name": outcome.get("description", ""),
                            "line_type": _normalise_line_type(outcome.get("name", "")),
                            "selection_name": outcome.get("name", ""),
                            "line": outcome.get("point"),
                            "odds": outcome.get("price"),
                            "deep_link": outcome.get("link", ""),
                        }
                    )

        time.sleep(0.3)

    logger.info(
        "Odds API: fetched %s AFL player prop rows across %s market(s)",
        len(props),
        len(odds_api_markets),
    )
    return props


def get_best_afl_player_props(
    markets: Optional[list[str]] = None,
    bookmakers: Optional[list[str]] = None,
) -> list[dict]:
    """
    Deduplicate player props to the best available odds per:
    (event, player, market, line, selection_name)
    """
    rows = fetch_afl_player_props(markets=markets, bookmakers=bookmakers)
    if not rows:
        return []

    best: dict[tuple, dict] = {}

    for row in rows:
        key = (
            row.get("event_id"),
            row.get("player_name"),
            row.get("market"),
            row.get("line"),
            row.get("selection_name"),
        )

        current_best = best.get(key)
        current_price = _coerce_float(current_best.get("odds")) if current_best else 0.0
        new_price = _coerce_float(row.get("odds"))

        if current_best is None or new_price > current_price:
            best[key] = row

    result = list(best.values())
    result.sort(
        key=lambda x: (
            _coerce_str(x.get("commence_time")),
            _coerce_str(x.get("player_name")),
            _coerce_str(x.get("market")),
            _coerce_float(x.get("line")),
        )
    )

    logger.info("Odds API: reduced %s prop rows to %s best-price rows", len(rows), len(result))
    return result


# ─────────────────────────────────────────────
# CONVENIENCE AGGREGATORS
# ─────────────────────────────────────────────

def get_player_season_averages(player_stats: list[dict]) -> dict:
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
    opponent_team = _normalise_team_name(opponent_team)
    opp_games = [
        game
        for game in player_stats
        if _normalise_team_name(game.get("match_home_team")) == opponent_team
        or _normalise_team_name(game.get("match_away_team")) == opponent_team
    ]

    if not opp_games:
        return {"games": 0, "averages": {}, "hit_rates": {}}

    averages = get_player_season_averages(opp_games)

    disposal_lines = [15, 20, 25, 30, 35]
    hit_rates = {}
    for line in disposal_lines:
        hits = sum(1 for game in opp_games if (game.get("disposals") or 0) >= line)
        hit_rates[f"disp_{line}+"] = round(hits / len(opp_games) * 100, 1)

    return {
        "games": len(opp_games),
        "averages": averages,
        "hit_rates": hit_rates,
        "last_5": sorted(opp_games, key=lambda x: x.get("match_date", ""), reverse=True)[:5],
    }


def get_player_last_n_games(player_stats: list[dict], n: int = 5) -> list[dict]:
    sorted_games = sorted(player_stats, key=lambda x: x.get("match_date", ""), reverse=True)
    return sorted_games[:n]


def calculate_disposal_edge(
    player_avg: float,
    book_line: float,
    vs_opp_avg: float = None,
    last5_avg: float = None,
) -> dict:
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


def calculate_market_edge(
    player_avg: float,
    book_line: float,
    odds: float,
    line_type: str,
    market: str = "player_disposals",
    vs_opp_avg: float = None,
    last5_avg: float = None,
    player_stats: list[dict] = None,
) -> dict:
    """
    Probability-based edge calculation for AFL player props.

    The model returns edge in percentage points where positive means the model's
    probability is higher than the bookmaker's implied probability. It uses the
    stat that matches the requested market, filters out very low-TOG games, and
    blends historical hit rate with a distribution estimate so non-disposal
    markets are not accidentally priced from disposal averages.
    """
    from math import erf, exp, floor, sqrt

    market = _normalise_prop_market(market)
    market_stat = {
        "player_disposals": "disposals",
        "player_kicks": "kicks",
        "player_handballs": "handballs",
        "player_marks": "marks",
        "player_tackles": "tackles",
        "player_goals": "goals",
        "player_afl_fantasy_points": "afl_fantasy_score",
    }.get(market, "disposals")

    usable_games: list[dict] = []
    if player_stats:
        # Keep games where TOG is meaningful. Historical imports sometimes have
        # missing TOG; keep those rows rather than dropping an entire sample.
        usable_games = [
            g for g in player_stats
            if (g.get("time_on_ground_percentage") is None)
            or float(g.get("time_on_ground_percentage") or 0) >= 50
        ]
        if not usable_games:
            usable_games = list(player_stats)

    if usable_games:
        stat_values = [float(g.get(market_stat) or 0) for g in usable_games]
        if stat_values:
            player_avg = sum(stat_values) / len(stat_values)
    else:
        stat_values = []

    # Build blended model prediction from the correct market stat.
    base_pred = max(float(player_avg) if player_avg is not None else 0.0, 0.0)
    model_pred = base_pred

    if vs_opp_avg is not None and vs_opp_avg > 0:
        model_pred = base_pred * 0.70 + float(vs_opp_avg) * 0.30

    if last5_avg is not None and last5_avg > 0:
        model_pred = model_pred * 0.80 + float(last5_avg) * 0.20

    mu = max(model_pred, 0.01)

    def _normal_sf(x: float, mean: float, stdev: float) -> float:
        stdev = max(stdev, 0.1)
        z = (x - mean) / (stdev * sqrt(2))
        return 0.5 * (1 - erf(z))

    def _poisson_sf(k: int, mean: float) -> float:
        # P(X > k) = 1 - cumulative P(0..k). Used only for low-count goals.
        cumulative = 0.0
        term = exp(-mean)
        cumulative += term
        for n in range(1, max(k, 0) + 1):
            term *= mean / n
            cumulative += term
        return 1.0 - cumulative

    # Distribution estimate for the requested market. Goals are genuinely
    # low-count; the other AFL props are better represented by a normal model
    # using each player's observed variance rather than sqrt(mean).
    if market == "player_goals":
        model_prob_over = _poisson_sf(int(floor(float(book_line))), mu)
    else:
        if len(stat_values) >= 2:
            mean_hist = sum(stat_values) / len(stat_values)
            variance = sum((v - mean_hist) ** 2 for v in stat_values) / (len(stat_values) - 1)
            hist_sigma = sqrt(max(variance, 0.0))
        else:
            hist_sigma = sqrt(mu)
        sigma_floor = 12.0 if market == "player_afl_fantasy_points" else 2.0
        model_prob_over = _normal_sf(float(book_line), mu, max(hist_sigma, sigma_floor))

    # Historical hit-rate with light Bayesian smoothing. This anchors the model
    # to how often a player has actually cleared the exact posted line.
    if stat_values:
        over_hits = sum(1 for value in stat_values if value > float(book_line))
        empirical_over = (over_hits + 1.0) / (len(stat_values) + 2.0)
        empirical_weight = 0.60 if len(stat_values) >= 8 else 0.35
        model_prob_over = empirical_weight * empirical_over + (1 - empirical_weight) * model_prob_over

    model_prob_over = max(0.001, min(0.999, model_prob_over))

    if line_type == "Under":
        model_prob = 1.0 - model_prob_over
    else:
        model_prob = model_prob_over

    implied_prob = 1.0 / max(float(odds), 1.01) if odds and odds > 1 else 0.5
    implied_prob = max(0.001, min(0.999, implied_prob))
    edge_pct = (model_prob - implied_prob) * 100.0

    return {
        "model_prediction": round(model_pred, 1),
        "model_prob": round(model_prob * 100.0, 1),
        "implied_prob": round(implied_prob * 100.0, 1),
        "book_line": book_line,
        "edge": round(edge_pct, 1),
        "edge_positive": edge_pct > 0,
        "edge_pct": round(edge_pct, 1),
        "recommendation": "value" if edge_pct >= 2.0 else "skip",
    }
