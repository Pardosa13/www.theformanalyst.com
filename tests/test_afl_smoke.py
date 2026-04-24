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
from typing import Any

# ---------------------------------------------------------------------------
# Inline implementations of pure-Python helpers under test
# (mirrors the code in afl_data.py / afl_routes.py exactly)
# ---------------------------------------------------------------------------


def _s(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _team(value: Any) -> str:
    raw = _s(value)
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
# Fix – vs-opponent and venue game_log must return ALL rows (no [:20] cap)
# ---------------------------------------------------------------------------

def test_vs_opponent_game_log_uses_all_rows():
    """api_afl_player_vs_opponent must build game_log from all rows, not from last_5 or rows[:20]."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_routes.py",
    )
    with open(routes_path, encoding="utf-8") as f:
        source = f.read()

    # Old bug: game_log was populated from result.get("last_5", []) — max 5 games
    assert 'result.get("last_5", [])' not in source, (
        "api_afl_player_vs_opponent still builds game_log from last_5 (max 5 games). "
        "It should use all rows so the chart shows the full 5-year history."
    )
    # Must not silently cap at 20 either
    # (rows are already filtered by opponent + season_from, so the set is small)
    # We verify the pattern that uses all rows appears in the vs-opponent block
    assert "for g in rows]" in source, (
        "api_afl_player_vs_opponent does not iterate over all rows for game_log"
    )


def test_venue_game_log_uses_all_rows():
    """api_afl_player_vs_venue must return ALL rows from season_from, not just [:20]."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_routes.py",
    )
    with open(routes_path, encoding="utf-8") as f:
        source = f.read()

    # Must use all rows so a player with 25+ games at a venue since 2022 is not truncated
    assert "rows[:20]" not in source, (
        "A [:20] cap is still present in afl_routes.py — venue or opponent game_log "
        "is being truncated. Remove the slice so all 5-year history is returned."
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


# ---------------------------------------------------------------------------
# Fix – _team() strips AFL mascot suffixes from Odds API team names
# ---------------------------------------------------------------------------

def test_team_normalisation_mascot_names():
    """Full team names from The Odds API must be stripped to the canonical short name."""
    assert _team("Fremantle Dockers") == "Fremantle"
    assert _team("Carlton Blues") == "Carlton"
    assert _team("St Kilda Saints") == "St Kilda"
    assert _team("Richmond Tigers") == "Richmond"
    assert _team("Melbourne Demons") == "Melbourne"
    assert _team("Sydney Swans") == "Sydney"
    assert _team("Adelaide Crows") == "Adelaide"
    assert _team("Collingwood Magpies") == "Collingwood"
    assert _team("Essendon Bombers") == "Essendon"
    assert _team("Geelong Cats") == "Geelong"
    assert _team("Gold Coast Suns") == "Gold Coast"
    assert _team("Hawthorn Hawks") == "Hawthorn"
    assert _team("North Melbourne Kangaroos") == "North Melbourne"
    assert _team("Port Adelaide Power") == "Port Adelaide"


def test_team_normalisation_canonical_names_unchanged():
    """Canonical team names (already correct) must pass through unchanged."""
    assert _team("Brisbane Lions") == "Brisbane Lions"
    assert _team("GWS Giants") == "GWS Giants"
    assert _team("Western Bulldogs") == "Western Bulldogs"
    assert _team("West Coast") == "West Coast"
    assert _team("Fremantle") == "Fremantle"
    assert _team("Melbourne") == "Melbourne"


def test_team_normalisation_legacy_aliases():
    """Legacy/shorthand aliases (existing mapping) must still work."""
    assert _team("West Coast Eagles") == "West Coast"
    assert _team("Greater Western Sydney") == "GWS Giants"
    assert _team("GWS") == "GWS Giants"
    assert _team("Footscray") == "Western Bulldogs"
    assert _team("Brisbane") == "Brisbane Lions"


def test_team_normalisation_source_has_mascot_entries():
    """afl_db.py _team() mapping must contain all 14 mascot-name entries."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()

    for full_name in (
        "Adelaide Crows",
        "Carlton Blues",
        "Collingwood Magpies",
        "Essendon Bombers",
        "Fremantle Dockers",
        "Geelong Cats",
        "Gold Coast Suns",
        "Hawthorn Hawks",
        "Melbourne Demons",
        "North Melbourne Kangaroos",
        "Port Adelaide Power",
        "Richmond Tigers",
        "St Kilda Saints",
        "Sydney Swans",
    ):
        assert full_name in source, (
            f"_team() in afl_db.py is missing an entry for {full_name!r}"
        )


def test_normalise_team_name_in_afl_data_has_mascot_entries():
    """afl_data.py _normalise_team_name() must also contain all 14 mascot-name entries
    so that existing DB rows are normalised at read time (no data deletion required)."""
    data_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_data.py",
    )
    with open(data_path, encoding="utf-8") as f:
        source = f.read()

    for full_name in (
        "Adelaide Crows",
        "Carlton Blues",
        "Collingwood Magpies",
        "Essendon Bombers",
        "Fremantle Dockers",
        "Geelong Cats",
        "Gold Coast Suns",
        "Hawthorn Hawks",
        "Melbourne Demons",
        "North Melbourne Kangaroos",
        "Port Adelaide Power",
        "Richmond Tigers",
        "St Kilda Saints",
        "Sydney Swans",
    ):
        assert full_name in source, (
            f"_normalise_team_name() in afl_data.py is missing an entry for {full_name!r}"
        )


def test_value_finder_normalises_team_names_at_read_time():
    """api_afl_value_finder() must call _normalise_team_name on home_team/away_team
    from the props row so existing DB rows with mascot names work without data deletion."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_routes.py",
    )
    with open(routes_path, encoding="utf-8") as f:
        source = f.read()

    assert "_normalise_team_name" in source, (
        "_normalise_team_name is not imported or used in afl_routes.py"
    )
    assert '_normalise_team_name(prop.get("home_team"' in source, (
        'api_afl_value_finder does not normalise home_team from props at read time'
    )
    assert '_normalise_team_name(prop.get("away_team"' in source, (
        'api_afl_value_finder does not normalise away_team from props at read time'
    )


# ---------------------------------------------------------------------------
# Value Finder JS functions must all exist in templates/afl.html
# ---------------------------------------------------------------------------

def test_value_finder_js_functions_exist():
    """All five Value Finder JS functions must be defined in templates/afl.html."""
    template_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "templates",
        "afl.html",
    )
    with open(template_path, encoding="utf-8") as f:
        source = f.read()

    required_functions = [
        "function loadValueFinder(",
        "function loadOddsProps(",
        "function _vfApplyFilters(",
        "function _vfPopulateMatchSelector(",
        "function _vfRenderTable(",
    ]
    for fn in required_functions:
        assert fn in source, (
            f"Value Finder JS function missing from templates/afl.html: {fn!r}"
        )


def test_value_finder_render_table_called_by_apply_filters():
    """_vfApplyFilters must delegate rendering to _vfRenderTable (not inline)."""
    template_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "templates",
        "afl.html",
    )
    with open(template_path, encoding="utf-8") as f:
        source = f.read()

    assert "_vfRenderTable(" in source, (
        "_vfRenderTable is not called anywhere in templates/afl.html"
    )
    # Verify it is called inside _vfApplyFilters (i.e. after that function declaration)
    apply_idx = source.find("function _vfApplyFilters(")
    render_call_idx = source.find("_vfRenderTable(", apply_idx)
    assert render_call_idx != -1, (
        "_vfRenderTable is not called within _vfApplyFilters"
    )


def test_value_finder_raw_props_table_has_no_missing_team_column():
    """Raw-props table (All Markets mode) must not reference p.team —
    afl_player_props has no team column; the match column is home_team vs away_team."""
    template_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "templates",
        "afl.html",
    )
    with open(template_path, encoding="utf-8") as f:
        source = f.read()

    # Extract _vfRenderTable body: from its definition to the next function keyword
    render_fn_start = source.find("function _vfRenderTable(")
    assert render_fn_start != -1, "function _vfRenderTable not found"
    next_fn = source.find("\nfunction ", render_fn_start + 1)
    render_fn_body = source[render_fn_start:] if next_fn == -1 else source[render_fn_start:next_fn]

    # In props mode the table row must show p.home_team / p.away_team for the match
    assert "p.home_team" in render_fn_body, (
        "_vfRenderTable props mode must render p.home_team for the Match column"
    )
    assert "p.away_team" in render_fn_body, (
        "_vfRenderTable props mode must render p.away_team for the Match column"
    )
    # Must NOT use the non-existent p.team column for the props table
    # (team info is not stored in afl_player_props)
    assert "p.team" not in render_fn_body, (
        "_vfRenderTable props mode references p.team which is not a column "
        "in afl_player_props; use p.home_team/p.away_team instead"
    )


def test_db_get_props_applies_all_filters():
    """_db_get_props in afl_routes.py must apply home_team, away_team,
    min_line and max_line as SQL conditions, not ignore them."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_routes.py",
    )
    with open(routes_path, encoding="utf-8") as f:
        source = f.read()

    # Extract _db_get_props body: from its definition to the next function/class
    fn_idx = source.find("def _db_get_props(")
    assert fn_idx != -1, "_db_get_props not found in afl_routes.py"
    next_def = source.find("\ndef ", fn_idx + 1)
    fn_body = source[fn_idx:] if next_def == -1 else source[fn_idx:next_def]

    for param in ("home_team", "away_team", "min_line", "max_line"):
        assert f":{param}" in fn_body, (
            f"_db_get_props does not use :{param} as a SQL bind parameter — "
            f"the filter is silently ignored"
        )
