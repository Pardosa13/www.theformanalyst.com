"""
mma_data.py
===========
Fetches UFC fight odds from The Odds API and provides edge-calculation
helpers for the MMA Edge Finder feature.

Mirrors afl_data.py's odds-fetch pattern exactly, using the
mma_mixed_martial_arts sport key and the h2h (moneyline) market.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional, Union

import requests

from mma_name_utils import (
    normalize_name as _normalise_name,
    normalized_name_aliases as name_aliases,
    names_match,
    unordered_pair_key,
    pairs_match,
)

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
MMA_SPORT_KEY = "mma_mixed_martial_arts"

_ODDS_API_ENV_VARS = ("ODDS_API_KEY", "THE_ODDS_API_KEY", "ODDSAPI_KEY")
_ODDS_API_KEY_MIN_LENGTH = 10


# ─── HTTP helper ─────────────────────────────────────────────────────────────

def _get(url: str, params: dict) -> Optional[Union[dict, list]]:
    """Simple GET with basic error handling."""
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        logger.error("Odds API HTTP error %s: %s", exc.response.status_code, url)
    except Exception as exc:
        logger.error("Odds API request failed: %s — %s", url, exc)
    return None


# ─── API key helpers ─────────────────────────────────────────────────────────

def get_odds_api_key() -> str:
    """Read The Odds API key from environment variables."""
    key = (
        os.environ.get("ODDS_API_KEY")
        or os.environ.get("THE_ODDS_API_KEY")
        or os.environ.get("ODDSAPI_KEY")
        or ""
    ).strip()

    if not key:
        logger.error("No Odds API key found in environment variables")
        return ""

    if len(key) < _ODDS_API_KEY_MIN_LENGTH:
        logger.error("Odds API key appears invalid (too short: %d chars)", len(key))
        return ""

    return key


# ─── Name normalisation ───────────────────────────────────────────────────────
# Canonical implementation lives in mma_name_utils.py (imported above) and is
# shared with mma_sync.py so the two modules can't silently drift apart.

# Public alias (imported by mma_routes.py)
normalise_name = _normalise_name


# ─── Odds API fetch ───────────────────────────────────────────────────────────

def fetch_mma_events(api_key: Optional[str] = None) -> list[dict]:
    """Fetch upcoming UFC/MMA events from The Odds API."""
    key = (api_key or get_odds_api_key()).strip()
    if not key:
        logger.warning("No Odds API key — skipping MMA events fetch")
        return []

    url = f"{ODDS_API_BASE}/sports/{MMA_SPORT_KEY}/events"
    data = _get(url, {"api_key": key})

    if not data or not isinstance(data, list):
        logger.warning("Odds API returned no MMA events")
        return []

    logger.info("Odds API: fetched %d MMA events", len(data))
    return data


def fetch_mma_fight_odds(
    api_key: Optional[str] = None,
    regions: str = "us,au,uk,eu",
    event_limit: int = 10,
) -> list[dict]:
    """
    Fetch h2h (moneyline / fighter-win) odds for upcoming UFC fights from
    The Odds API.

    Returns a list of flat row dicts, one per (event, bookmaker, fighter):
    {
      event_key, fighter_1_name, fighter_2_name, commence_time,
      bookmaker, fighter_name, odds
    }
    """
    key = (api_key or get_odds_api_key()).strip()
    if not key:
        logger.warning("No Odds API key — skipping MMA odds fetch")
        return []

    events = fetch_mma_events(api_key=key)
    if not events:
        return []

    if event_limit:
        events = events[:event_limit]

    rows: list[dict] = []

    for event in events:
        event_key = event.get("id")
        if not event_key:
            continue

        odds_url = f"{ODDS_API_BASE}/sports/{MMA_SPORT_KEY}/events/{event_key}/odds"
        params = {
            "api_key": key,
            "regions": regions,
            "markets": "h2h",
            "oddsFormat": "decimal",
        }

        odds_data = _get(odds_url, params)
        if not odds_data or not isinstance(odds_data, dict):
            continue

        fighter_1_name = odds_data.get("home_team", "")
        fighter_2_name = odds_data.get("away_team", "")
        commence_time = odds_data.get("commence_time", "")

        for bookmaker in odds_data.get("bookmakers", []) or []:
            bookmaker_name = bookmaker.get("title", "")

            for market in bookmaker.get("markets", []) or []:
                if market.get("key") != "h2h":
                    continue

                for outcome in market.get("outcomes", []) or []:
                    fighter_name = outcome.get("name", "")
                    price = outcome.get("price")
                    if not fighter_name or not price:
                        continue

                    rows.append({
                        "event_key": event_key,
                        "fighter_1_name": fighter_1_name,
                        "fighter_2_name": fighter_2_name,
                        "commence_time": commence_time,
                        "bookmaker": bookmaker_name,
                        "fighter_name": fighter_name,
                        "odds": float(price),
                    })

        time.sleep(0.3)

    logger.info(
        "Odds API: fetched %d MMA fight-odds rows across %d event(s)",
        len(rows),
        len(events),
    )
    return rows


# ─── Edge calculation ─────────────────────────────────────────────────────────

# Minimum |edge| (percentage points) for a bet to be flagged "value" by default.
# Mirrors the front-end's default Edge Finder slider value (efMinEdge in mma.html).
DEFAULT_VALUE_EDGE_THRESHOLD_PCT = 2.0


def calculate_mma_edge(
    model_prob: float,
    odds: float,
    opponent_odds: Optional[float] = None,
) -> dict:
    """
    Probability-based edge for an MMA fight outcome (fighter win).

    model_prob     – model's estimated win probability (0-1).
    odds           – bookmaker's decimal odds for that fighter.
    opponent_odds  – the SAME bookmaker's decimal odds for the opponent, if
                      known. A two-way moneyline's raw 1/odds prices sum to
                      more than 100% (the bookmaker's overround/vig), so
                      without this the "implied probability" overstates the
                      market's true view and understates the model's real
                      edge by roughly half the vig. When both prices come
                      from the same book, the two are normalised so they sum
                      to 100% ("devigged") before computing edge. Passing
                      odds/opponent_odds from *different* bookmakers would
                      not share a common overround, so callers should only
                      pass a same-book pair here.

    Returns a dict matching the AFL calculate_market_edge() return shape so
    the front-end can share the same rendering logic, plus a `devigged` flag.
    """
    if not odds or odds <= 1.0:
        raw_implied = 0.5
    else:
        raw_implied = 1.0 / float(odds)

    devigged = False
    implied_prob = raw_implied
    if opponent_odds and float(opponent_odds) > 1.0:
        opponent_implied = 1.0 / float(opponent_odds)
        overround = raw_implied + opponent_implied
        if overround > 0:
            implied_prob = raw_implied / overround
            devigged = True

    implied_prob = max(0.001, min(0.999, implied_prob))
    model_prob = max(0.001, min(0.999, float(model_prob)))

    edge_pct = (model_prob - implied_prob) * 100.0

    return {
        "model_prob": round(model_prob * 100.0, 1),
        "implied_prob": round(implied_prob * 100.0, 1),
        "odds": round(odds, 2),
        "devigged": devigged,
        "edge": round(edge_pct, 1),
        "edge_pct": round(abs(edge_pct), 1),
        "recommendation": "value" if edge_pct >= DEFAULT_VALUE_EDGE_THRESHOLD_PCT else "skip",
    }
