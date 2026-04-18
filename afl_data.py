"""
afl_data.py
===========
Data layer for The Form Analyst AFL section.

Sources:
  1. Fryzigg API  — advanced player stats (2019-present)
  2. Squiggle API — fixtures, results, ladder, tips
  3. AFL Tables   — historical results/player stats
  4. The Odds API — live player prop lines (optional)

All data is cached in PostgreSQL. Fetchers are designed to be called
from a nightly Railway cron job.
"""

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

HEADERS = {
    "User-Agent": (
        "TheFormAnalyst/1.0 "
        "(https://theformanalyst.com; contact: admin@theformanalyst.com)"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-AU,en;q=0.9",
    "Referer": "https://squiggle.com.au/",
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


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _get(url: str, params: Optional[dict] = None, retries: int = 3) -> Optional[Any]:
    """HTTP GET with retries and basic rate-limit handling."""
    import json as _json

    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=20)

            if response.status_code == 429:
                logger.warning("Rate limited from %s — sleeping 10s", url)
                time.sleep(10)
                continue

            response.raise_for_status()

            if not response.text or not response.text.strip():
                logger.warning("Empty response from %s (params=%s)", url, params)
                return None

            return response.json()

        except _json.JSONDecodeError as exc:
            body = response.text[:300] if "response" in locals() else ""
            logger.error("JSON decode error from %s: %s — body=%r", url, exc, body)
            return None
        except requests.RequestException as exc:
            logger.error("Request failed (%s/%s) for %s: %s", attempt + 1, retries, url, exc)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)

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
# FRYZIGG API
# ─────────────────────────────────────────────

def fetch_fryzigg_player_stats(season: int) -> list[dict]:
    """
    Fetch advanced player stats from Fryzigg.
    Tries known URL patterns and logs which one succeeds so we can
    lock it in once confirmed.
    """
    # Candidate URL patterns, most likely first
    candidates = [
        f"{FRYZIGG_BASE}/seasons/{season}",
        f"{FRYZIGG_BASE}/stats/{season}",
        f"{FRYZIGG_BASE}/stats?season={season}",
        f"{FRYZIGG_BASE}/player_stats/{season}",
    ]

    for url in candidates:
        logger.info("Fryzigg: trying %s", url)
        data = _get(url)

        if data is None:
            continue

        # Normalise response shape
        rows = None
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = data.get("stats") or data.get("data") or data.get("results")

        if rows:
            logger.info("Fryzigg: ✓ success via %s (%d rows)", url, len(rows))
            return rows

    logger.warning("Fryzigg: all candidate URLs failed for season %s", season)
    return []


def fetch_fryzigg_player_stats_range(start_year: int, end_year: int) -> list[dict]:
    """Fetch multiple Fryzigg seasons."""
    all_stats: list[dict] = []

    for year in range(start_year, end_year + 1):
        logger.info("Fetching Fryzigg stats for %s...", year)
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
        g
        for g in player_stats
        if g.get("match_home_team") == opponent_team
        or g.get("match_away_team") == opponent_team
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
