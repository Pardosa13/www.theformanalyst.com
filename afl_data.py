"""
afl_data.py
===========
Data layer for The Form Analyst AFL section.

Sources (matching fitzRoy's data sources exactly):
  1. Fryzigg API  — advanced player stats (81 cols), 2019-present
  2. Squiggle API — fixtures, results, ladder, tips, 2012-present
  3. AFL Tables   — historical results/player stats, 1897-present
  4. The Odds API — live player prop lines (optional, needs API key)

All data is cached in PostgreSQL. Fetchers are designed to be called
from a nightly Railway cron job — fetch only what's new each run.
"""

import requests
import time
import logging
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

FRYZIGG_BASE   = "https://api.fryzigg.com/afl"
SQUIGGLE_BASE  = "https://api.squiggle.com.au"
AFLTABLES_BASE = "https://afltables.com/afl"
ODDS_API_BASE  = "https://api.the-odds-api.com/v4"

# Squiggle requires a descriptive User-Agent — see api.squiggle.com.au/#section_bots
HEADERS = {
    "User-Agent": "TheFormAnalyst/1.0 (theformanalyst.com; personal research project)"
}

CURRENT_YEAR = datetime.now().year

# Squiggle team ID map (from https://api.squiggle.com.au/?q=teams)
SQUIGGLE_TEAM_IDS = {
    "Adelaide":          1,
    "Brisbane Lions":    2,
    "Carlton":           3,
    "Collingwood":       4,
    "Essendon":          5,
    "Fremantle":         6,
    "Geelong":           7,
    "Gold Coast":        8,
    "GWS Giants":        9,
    "Hawthorn":         10,
    "Melbourne":        11,
    "North Melbourne":  12,
    "Port Adelaide":    13,
    "Richmond":         14,
    "St Kilda":         15,
    "Sydney":           16,
    "West Coast":       17,
    "Western Bulldogs": 18,
}


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _get(url: str, params: dict = None, retries: int = 3) -> Optional[dict]:
    """GET with retry + rate-limit respect."""
    import json as _json
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            if r.status_code == 429:
                logger.warning("Rate limited — sleeping 10s")
                time.sleep(10)
                continue
            r.raise_for_status()
            if not r.text or not r.text.strip():
                logger.warning(f"Empty response from {url} (params={params})")
                return None
            return r.json()
        except _json.JSONDecodeError as e:
            logger.error(f"JSON decode error from {url}: {e} — body was: {r.text[:200]!r}")
            return None
        except requests.RequestException as e:
            logger.error(f"Request failed ({attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


# ─────────────────────────────────────────────
# SQUIGGLE API
# ─────────────────────────────────────────────

def fetch_squiggle_games(year: int, round_number: int = None) -> list[dict]:
    """
    Fetch game results/fixtures from Squiggle.
    Fields: id, year, round, roundname, date, localtime,
            tz, venue, hteam, ateam, hscore, ascore,
            hgoals, hbehinds, agoals, abehinds,
            winnerteamid, winner, margin, is_final,
            complete (0-100), hteamid, ateamid
    """
    params = {"q": "games", "year": year}
    if round_number is not None:
        params["round"] = round_number

    data = _get(SQUIGGLE_BASE, params)
    if not data:
        return []
    return data.get("games", [])


def fetch_squiggle_standings(year: int, round_number: int = None) -> list[dict]:
    """
    Fetch ladder from Squiggle.
    Fields: rank, team, teamid, pts, played, wins, losses, draws,
            for, against, percentage, form (last 5 W/L/D)
    """
    params = {"q": "standings", "year": year}
    if round_number is not None:
        params["round"] = round_number

    data = _get(SQUIGGLE_BASE, params)
    if not data:
        return []
    return data.get("standings", [])


def fetch_squiggle_tips(year: int, round_number: int = None, source_id: int = 8) -> list[dict]:
    """
    Fetch model tips/predictions from Squiggle.
    source_id=8 is the Aggregate model (consensus of all models).
    Fields: gameid, year, round, hteam, ateam, hteamid, ateamid,
            tip, tipteamid, confidence, margin, err, correct, venue
    """
    params = {"q": "tips", "year": year, "source": source_id}
    if round_number is not None:
        params["round"] = round_number

    data = _get(SQUIGGLE_BASE, params)
    if not data:
        return []
    return data.get("tips", [])


def fetch_squiggle_current_round(year: int = None) -> int:
    """Get the current round number from live/incomplete games."""
    y = year or CURRENT_YEAR
    # Get all incomplete games for this year
    params = {"q": "games", "year": y, "complete": "!100"}
    data = _get(SQUIGGLE_BASE, params)
    if not data or not data.get("games"):
        return 1
    rounds = [g.get("round", 1) for g in data["games"] if g.get("round")]
    return min(rounds) if rounds else 1


def fetch_squiggle_upcoming_games(year: int = None) -> list[dict]:
    """Fetch all unplayed games this season (complete=0)."""
    y = year or CURRENT_YEAR
    params = {"q": "games", "year": y, "complete": "0"}
    data = _get(SQUIGGLE_BASE, params)
    if not data:
        return []
    return data.get("games", [])


# ─────────────────────────────────────────────
# FRYZIGG API  (advanced player stats)
# ─────────────────────────────────────────────
# fitzRoy calls: fetch_player_stats_fryzigg(season)
# The underlying endpoint is stats.fryzigg.com

def fetch_fryzigg_player_stats(season: int) -> list[dict]:
    """
    Fetch advanced player stats from Fryzigg API.
    Returns 81-column dataset per fitzRoy documentation.

    Key fields returned:
      match_id, match_date, match_round,
      match_home_team, match_away_team,
      match_home_team_score, match_away_team_score,
      match_margin, match_winner,
      match_weather_temp_c, match_weather_type,
      player_id, player_first_name, player_last_name,
      player_team, guernsey_number,
      player_height_cm, player_weight_kg,
      kicks, marks, handballs, disposals,
      effective_disposals, disposal_efficiency_percentage,
      goals, behinds, hitouts, tackles,
      rebounds, inside_fifties, clearances,
      clangers, free_kicks_for, free_kicks_against,
      brownlow_votes, contested_possessions,
      uncontested_possessions, contested_marks,
      marks_inside_fifty, one_percenters, bounces,
      goal_assists, time_on_ground_percentage,
      afl_fantasy_score, supercoach_score,
      centre_clearances, stoppage_clearances,
      score_involvements, metres_gained,
      turnovers, intercepts, tackles_inside_fifty,
      venue_name, match_attendance
    """
    url = f"{FRYZIGG_BASE}/stats/{season}"
    data = _get(url)
    if not data:
        logger.warning(f"Fryzigg returned no data for {season}")
        return []

    # Fryzigg returns {"stats": [...]} 
    if isinstance(data, dict):
        return data.get("stats", data.get("data", []))
    return data if isinstance(data, list) else []


def fetch_fryzigg_player_stats_range(start_year: int, end_year: int) -> list[dict]:
    """Fetch multiple seasons. Respects rate limiting."""
    all_stats = []
    for year in range(start_year, end_year + 1):
        logger.info(f"Fetching Fryzigg stats for {year}...")
        stats = fetch_fryzigg_player_stats(year)
        all_stats.extend(stats)
        time.sleep(1)  # Be nice to the server
    return all_stats


# ─────────────────────────────────────────────
# AFL TABLES  (historical, 1897-present)
# ─────────────────────────────────────────────

def fetch_afltables_results(year: int) -> list[dict]:
    """
    Scrape match results from AFL Tables for a given year.
    Returns: date, round, home_team, away_team,
             home_goals, home_behinds, home_score,
             away_goals, away_behinds, away_score,
             venue, margin, winner
    """
    from bs4 import BeautifulSoup

    url = f"{AFLTABLES_BASE}/seas/{year}.html"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"AFL Tables fetch failed for {year}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results = []

    # AFL Tables uses a consistent table structure
    tables = soup.find_all("table", class_="sortable")
    for table in tables:
        rows = table.find_all("tr")[1:]  # skip header
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 8:
                continue
            try:
                results.append({
                    "year":        year,
                    "date":        cells[0].text.strip(),
                    "round":       cells[1].text.strip(),
                    "home_team":   cells[2].text.strip(),
                    "home_score":  cells[3].text.strip(),
                    "away_team":   cells[4].text.strip(),
                    "away_score":  cells[5].text.strip(),
                    "venue":       cells[6].text.strip(),
                    "crowd":       cells[7].text.strip() if len(cells) > 7 else "",
                })
            except (IndexError, AttributeError):
                continue

    return results


# ─────────────────────────────────────────────
# THE ODDS API  (player prop lines)
# ─────────────────────────────────────────────

def fetch_afl_player_props(api_key: str, market: str = "player_disposals") -> list[dict]:
    """
    Fetch live AFL player prop lines from The Odds API.
    Requires paid API key (~$50 USD/month).

    market options:
      player_disposals  — disposal over/under lines
      player_marks      — marks over/under
      player_tackles    — tackles over/under
      player_goals      — goals scorer markets

    Returns list of {player, team, bookmaker, market, line, over_odds, under_odds}
    """
    if not api_key:
        logger.warning("No Odds API key configured — skipping prop fetch")
        return []

    # Step 1: get event IDs for upcoming AFL games
    events_url = f"{ODDS_API_BASE}/sports/aussierules_afl/events"
    events_data = _get(events_url, {"apiKey": api_key, "regions": "au"})
    if not events_data:
        return []

    props = []
    for event in events_data[:9]:  # max 9 games per round
        event_id = event.get("id")
        if not event_id:
            continue

        # Step 2: fetch props per event (costs 1 API request per event)
        odds_url = f"{ODDS_API_BASE}/sports/aussierules_afl/events/{event_id}/odds"
        params = {
            "apiKey":     api_key,
            "regions":    "au",
            "markets":    market,
            "oddsFormat": "decimal",
        }
        odds_data = _get(odds_url, params)
        if not odds_data:
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
                    props.append({
                        "event_id":      event_id,
                        "home_team":     home,
                        "away_team":     away,
                        "commence_time": commence,
                        "bookmaker":     bk_name,
                        "market":        market,
                        "player":        outcome.get("description", ""),
                        "name":          outcome.get("name", ""),  # Over/Under
                        "line":          outcome.get("point", 0),
                        "odds":          outcome.get("price", 0),
                    })
        time.sleep(0.5)  # rate limit

    return props


# ─────────────────────────────────────────────
# CONVENIENCE AGGREGATORS
# ─────────────────────────────────────────────

def get_player_season_averages(player_stats: list[dict]) -> dict:
    """
    Given a list of game rows for one player (from Fryzigg),
    calculate season averages across key stats.
    """
    if not player_stats:
        return {}

    stats_to_avg = [
        "disposals", "effective_disposals", "disposal_efficiency_percentage",
        "kicks", "marks", "handballs", "goals", "behinds",
        "tackles", "hitouts", "rebounds", "inside_fifties",
        "clearances", "contested_possessions", "uncontested_possessions",
        "marks_inside_fifty", "score_involvements", "metres_gained",
        "afl_fantasy_score", "supercoach_score", "time_on_ground_percentage",
    ]

    totals = {s: 0 for s in stats_to_avg}
    count  = len(player_stats)

    for game in player_stats:
        for stat in stats_to_avg:
            totals[stat] += game.get(stat, 0) or 0

    return {
        stat: round(totals[stat] / count, 1)
        for stat in stats_to_avg
    }


def get_player_vs_opponent(player_stats: list[dict], opponent_team: str) -> dict:
    """
    Filter player's game log to only games against a specific opponent,
    then return averages + hit rates for common prop lines.
    """
    opp_games = [
        g for g in player_stats
        if g.get("match_home_team") == opponent_team
        or g.get("match_away_team") == opponent_team
    ]

    if not opp_games:
        return {"games": 0, "averages": {}, "hit_rates": {}}

    averages = get_player_season_averages(opp_games)

    # Calculate hit rates for common disposal lines
    disp_lines = [15, 20, 25, 30, 35]
    hit_rates = {}
    for line in disp_lines:
        hits = sum(1 for g in opp_games if (g.get("disposals") or 0) >= line)
        hit_rates[f"disp_{line}+"] = round(hits / len(opp_games) * 100, 1)

    return {
        "games":     len(opp_games),
        "averages":  averages,
        "hit_rates": hit_rates,
        "last_5":    sorted(opp_games, key=lambda x: x.get("match_date", ""), reverse=True)[:5],
    }


def get_player_last_n_games(player_stats: list[dict], n: int = 5) -> list[dict]:
    """Return the n most recent games for a player, sorted newest first."""
    sorted_games = sorted(
        player_stats,
        key=lambda x: x.get("match_date", ""),
        reverse=True
    )
    return sorted_games[:n]


def calculate_disposal_edge(
    player_avg: float,
    book_line:  float,
    vs_opp_avg: float = None,
    last5_avg:  float = None,
) -> dict:
    """
    Calculate edge between model prediction and book line.
    Uses a weighted blend: 50% season avg, 30% vs-opponent avg, 20% last 5.
    """
    model_pred = player_avg  # base

    if vs_opp_avg and vs_opp_avg > 0:
        model_pred = player_avg * 0.50 + vs_opp_avg * 0.30 + model_pred * 0.20
    if last5_avg and last5_avg > 0:
        model_pred = model_pred * 0.80 + last5_avg * 0.20

    edge = round(model_pred - book_line, 1)

    return {
        "model_prediction": round(model_pred, 1),
        "book_line":        book_line,
        "edge":             edge,
        "edge_positive":    edge > 0,
        "edge_pct":         round(abs(edge) / book_line * 100, 1) if book_line else 0,
        "recommendation":   "value" if edge >= 2.0 else "skip",
    }
