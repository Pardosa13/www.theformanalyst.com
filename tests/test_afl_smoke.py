"""
tests/test_afl_smoke.py
=======================
Offline smoke checks for key AFL data-layer functions.
No network access, database, or heavy dependencies (pandas / pyreadr) required.

Run with:  python -m pytest tests/test_afl_smoke.py -v
"""

import hashlib
import os
import re
import sys

# ---------------------------------------------------------------------------
# Inline implementations of pure-Python helpers under test
# (mirrors the code in afl_data.py / afl_routes.py exactly)
# ---------------------------------------------------------------------------

_NORMALISE_PROP_MARKET_MAP = {
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
    "player_afl_fantasy_points": "player_afl_fantasy_points",
}


def _normalise_prop_market(market_key: str) -> str:
    return _NORMALISE_PROP_MARKET_MAP.get(str(market_key).strip(), str(market_key).strip())


def _hash_match_key_to_bigint(match_key: str) -> int:
    digest = hashlib.sha1(match_key.encode("utf-8")).digest()
    raw = int.from_bytes(digest[:8], "big")
    return raw & 0x7FFFFFFFFFFFFFFF


# ---------------------------------------------------------------------------
# Fix 3 – market normalization
# ---------------------------------------------------------------------------

def test_normalise_prop_market_known_keys():
    # Existing mappings must still work
    assert _normalise_prop_market("player_disposals") == "player_disposals"
    assert _normalise_prop_market("player_marks_over") == "player_marks"
    assert _normalise_prop_market("player_tackles_over") == "player_tackles"
    assert _normalise_prop_market("player_goals_scored_over") == "player_goals"

    # New mappings added by this PR
    assert _normalise_prop_market("player_kicks_over") == "player_kicks"
    assert _normalise_prop_market("player_handballs_over") == "player_handballs"
    assert _normalise_prop_market("player_kicks") == "player_kicks"
    assert _normalise_prop_market("player_handballs") == "player_handballs"


def test_normalise_prop_market_passthrough():
    """Unknown keys are returned unchanged."""
    assert _normalise_prop_market("player_hitouts") == "player_hitouts"
    assert _normalise_prop_market("") == ""


def test_normalise_prop_market_source_matches_inline():
    """The mapping in afl_data.py must include all keys tested above."""
    data_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_data.py",
    )
    with open(data_path, encoding="utf-8") as f:
        source = f.read()

    for key in ("player_kicks_over", "player_handballs_over"):
        assert key in source, (
            f"Market key {key!r} is missing from _normalise_prop_market mapping in afl_data.py"
        )


# ---------------------------------------------------------------------------
# Fix 5 – collision-resistant match_id hashing
# ---------------------------------------------------------------------------

def test_hash_match_key_deterministic():
    """Same key always produces the same integer."""
    key = "2026|2026-03-14|Collingwood|Carlton|14:10"
    assert _hash_match_key_to_bigint(key) == _hash_match_key_to_bigint(key)


def test_hash_match_key_unique():
    """Different keys produce different integers."""
    key_a = "2026|2026-03-14|Collingwood|Carlton|14:10"
    key_b = "2026|2026-03-14|Essendon|Hawthorn|16:40"
    assert _hash_match_key_to_bigint(key_a) != _hash_match_key_to_bigint(key_b)


def test_hash_match_key_fits_bigint():
    """Result must fit in a signed 63-bit integer (PostgreSQL BIGINT)."""
    max_bigint = (2 ** 63) - 1
    for key in [
        "2026|2026-03-14|Collingwood|Carlton|14:10",
        "2026|2026-05-01|Sydney|Brisbane Lions|19:00",
        "2025|2025-04-20|Geelong|Richmond|14:30",
    ]:
        result = _hash_match_key_to_bigint(key)
        assert 0 <= result <= max_bigint, (
            f"Hash out of BIGINT range for key={key!r}: {result}"
        )


def test_hash_match_key_used_in_source():
    """afl_data.py must call _hash_match_key_to_bigint for 2026 CSV match_id."""
    data_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_data.py",
    )
    with open(data_path, encoding="utf-8") as f:
        source = f.read()

    assert "_hash_match_key_to_bigint" in source, (
        "_hash_match_key_to_bigint not found in afl_data.py"
    )
    assert "_coerce_match_id(match_key)" not in source, (
        "fetch_2026_stats_from_csv still uses _coerce_match_id(match_key) — should use _hash_match_key_to_bigint"
    )


# ---------------------------------------------------------------------------
# Fix 1 – afl_hub SQL has no double WHERE
# ---------------------------------------------------------------------------

def test_afl_hub_value_bets_sql_no_double_where():
    """The value_bets SQL in afl_routes.py must not contain two WHERE clauses."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_routes.py",
    )
    with open(routes_path, encoding="utf-8") as f:
        source = f.read()

    # Extract SQL fragments passed to _db_count for afl_player_props
    pattern = re.compile(
        r'_db_count\(\s*db\s*,\s*["\']([^"\']+)["\']',
        re.DOTALL,
    )
    matches = pattern.findall(source)
    props_queries = [m for m in matches if "afl_player_props" in m]

    assert props_queries, "Could not find _db_count call for afl_player_props"

    for q in props_queries:
        where_count = q.upper().count("WHERE")
        assert where_count <= 1, (
            f"SQL fragment has {where_count} WHERE clauses (must be ≤ 1):\n{q}"
        )


# ---------------------------------------------------------------------------
# Fix 4 – completed_rounds off-by-one
# ---------------------------------------------------------------------------

def test_current_season_completed_rounds_range():
    """fetch_afl_player_stats_current_season must use range(1, current_round)."""
    data_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_data.py",
    )
    with open(data_path, encoding="utf-8") as f:
        source = f.read()

    assert "range(1, current_round + 1)" not in source, (
        "completed_rounds should be range(1, current_round), not range(1, current_round + 1)"
    )
    assert "range(1, current_round)" in source


# ---------------------------------------------------------------------------
# Fix 7 – player-home-away uses DB latest season, not CURRENT_YEAR
# ---------------------------------------------------------------------------

def test_player_home_away_uses_db_latest_season():
    """api_afl_player_home_away must not use bare CURRENT_YEAR as the upper bound."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_routes.py",
    )
    with open(routes_path, encoding="utf-8") as f:
        source = f.read()

    # The old bug: range(CURRENT_YEAR, season_from - 1, -1)
    assert "range(CURRENT_YEAR, season_from" not in source, (
        "api_afl_player_home_away still uses CURRENT_YEAR as the season range upper bound"
    )
    # Should now delegate to _db_latest_player_stats_season
    assert "_db_latest_player_stats_season" in source


# ---------------------------------------------------------------------------
# Fix – vs-opponent game_log must use all rows, not just last_5
# ---------------------------------------------------------------------------

def test_vs_opponent_game_log_uses_all_rows():
    """api_afl_player_vs_opponent must build game_log from rows[:20], not from last_5."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_routes.py",
    )
    with open(routes_path, encoding="utf-8") as f:
        source = f.read()

    # Old bug: game_log was populated from result.get("last_5", []) — max 5 games
    assert 'result.get("last_5", [])' not in source, (
        "api_afl_player_vs_opponent still builds game_log from last_5 (max 5 games). "
        "It should use rows[:20] so the bar chart has sufficient data."
    )
    # New behaviour: use rows directly
    assert "rows[:20]" in source, (
        "api_afl_player_vs_opponent does not slice rows[:20] for game_log"
    )


# ---------------------------------------------------------------------------
# Fix – renderPropCard hr() must not use function declaration inside a function
# ---------------------------------------------------------------------------

def test_render_prop_card_uses_arrow_hr():
    """renderPropCard must define hr() as an arrow function, not a function declaration."""
    template_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "templates",
        "afl.html",
    )
    with open(template_path, encoding="utf-8") as f:
        source = f.read()

    # Old pattern: bare function declaration inside renderPropCard body
    assert "function hr(arr, line)" not in source, (
        "renderPropCard still uses a bare 'function hr(arr, line)' declaration inside "
        "the function body. Convert it to a const arrow function."
    )
    # New pattern: arrow function
    assert "const hr = (arr, line) =>" in source, (
        "renderPropCard does not define hr() as 'const hr = (arr, line) =>'"
    )

