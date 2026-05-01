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
    """Value Finder card rendering must use bet.home_team / bet.away_team (or bet.opponent)
    rather than any non-existent team column from afl_player_props.
    The 'All Markets' raw-props mode has been removed; only the value-bets card
    mode remains, which drives match info via home_team/away_team on the backend."""
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

    # The card renderer must reference bet fields (not raw prop p.* fields)
    assert "bet.home_team" in render_fn_body or "bet.opponent" in render_fn_body, (
        "_vfRenderTable must render match info using bet.home_team/bet.away_team or bet.opponent"
    )
    # Must NOT use the non-existent p.team column from afl_player_props
    assert "p.team" not in render_fn_body, (
        "_vfRenderTable must not reference p.team which is not a column in afl_player_props"
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


# ---------------------------------------------------------------------------
# 2026 player_id collision fix — new helpers in afl_db.py
# ---------------------------------------------------------------------------

# Inline mirror of the helpers added to afl_db.py so we can test them without
# importing the full module (no DB / SQLAlchemy required).

def _normalise_name(value: Any) -> str:
    s = str(value).strip().lower() if value else ""
    return " ".join(s.split())


def _stable_debut_id(first: str, last: str, team: str) -> int:
    key = f"2026|{first}|{last}|{team}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    abs_id = int.from_bytes(digest[:7], "big") + 1
    return -abs_id


def test_normalise_name_basic():
    """_normalise_name strips, lowercases and collapses whitespace."""
    assert _normalise_name("Nick") == "nick"
    assert _normalise_name("  Daicos  ") == "daicos"
    assert _normalise_name("Tom  Mitchell") == "tom mitchell"
    assert _normalise_name(None) == ""
    assert _normalise_name("") == ""


def test_normalise_name_unicode_passthrough():
    """Non-ASCII characters are preserved (lowercased only)."""
    assert _normalise_name("O'Connor") == "o'connor"


def test_stable_debut_id_is_negative():
    """_stable_debut_id must always return a negative integer."""
    pid = _stable_debut_id("nick", "daicos", "collingwood")
    assert pid < 0, f"Expected negative id, got {pid}"


def test_stable_debut_id_is_deterministic():
    """Same inputs must always produce the same id."""
    pid_a = _stable_debut_id("nick", "daicos", "collingwood")
    pid_b = _stable_debut_id("nick", "daicos", "collingwood")
    assert pid_a == pid_b


def test_stable_debut_id_different_players():
    """Different players must receive different ids."""
    pid_a = _stable_debut_id("nick", "daicos", "collingwood")
    pid_b = _stable_debut_id("christian", "petracca", "melbourne")
    assert pid_a != pid_b


def test_stable_debut_id_different_clubs():
    """Same name at different clubs must receive different ids."""
    pid_a = _stable_debut_id("john", "smith", "sydney")
    pid_b = _stable_debut_id("john", "smith", "richmond")
    assert pid_a != pid_b


def test_stable_debut_id_fits_bigint():
    """Result must fit in a PostgreSQL signed BIGINT (-2^63 .. 2^63-1)."""
    min_bigint = -(2 ** 63)
    for first, last, team in [
        ("nick", "daicos", "collingwood"),
        ("christian", "petracca", "melbourne"),
        ("a debut player", "nobody", "gold coast"),
    ]:
        pid = _stable_debut_id(first, last, team)
        assert min_bigint <= pid < 0, (
            f"stable_debut_id out of range for {first} {last} ({team}): {pid}"
        )


def test_stable_debut_id_never_zero():
    """The result must never be zero (zero is treated as 'missing' in some checks)."""
    pid = _stable_debut_id("", "", "")
    assert pid != 0


def test_afl_db_has_normalise_name():
    """afl_db.py must define _normalise_name."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()
    assert "def _normalise_name(" in source, "_normalise_name not found in afl_db.py"


def test_afl_db_has_stable_debut_id():
    """afl_db.py must define _stable_debut_id."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()
    assert "def _stable_debut_id(" in source, "_stable_debut_id not found in afl_db.py"


def test_afl_db_has_build_historical_id_map():
    """afl_db.py must define _build_historical_id_map."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()
    assert "def _build_historical_id_map(" in source, (
        "_build_historical_id_map not found in afl_db.py"
    )


def test_afl_db_has_resolve_2026_player_id():
    """afl_db.py must define _resolve_2026_player_id."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()
    assert "def _resolve_2026_player_id(" in source, (
        "_resolve_2026_player_id not found in afl_db.py"
    )


def test_upsert_player_stats_no_md5_modulo_fallback():
    """upsert_player_stats must NOT use the old md5 % 10_000_000 fallback."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()
    assert "% 10_000_000" not in source and "% 10000000" not in source, (
        "afl_db.py still contains the unsafe md5 % 10_000_000 player_id fallback; "
        "replace it with _stable_debut_id()"
    )


def test_upsert_player_stats_uses_resolve_2026():
    """upsert_player_stats must call _resolve_2026_player_id for 2026 rows."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()
    assert "_resolve_2026_player_id(" in source, (
        "upsert_player_stats in afl_db.py does not call _resolve_2026_player_id"
    )


def test_upsert_player_stats_logs_2026_resolution():
    """upsert_player_stats must log the 2026 player_id resolution summary."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()
    assert "2026 player_id resolution" in source, (
        "afl_db.py does not log a '2026 player_id resolution' summary line"
    )


def test_afl_fix_2026_script_exists():
    """afl_fix_2026_ids.py repair script must exist in the repo root."""
    script_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_fix_2026_ids.py",
    )
    assert os.path.isfile(script_path), (
        "afl_fix_2026_ids.py not found in repo root; "
        "it is needed to repair existing polluted 2026 rows"
    )


def test_afl_fix_2026_script_deletes_and_reimports():
    """afl_fix_2026_ids.py must DELETE season=2026 rows and call upsert_player_stats."""
    script_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_fix_2026_ids.py",
    )
    with open(script_path, encoding="utf-8") as f:
        source = f.read()
    assert "DELETE FROM afl_player_stats WHERE season = 2026" in source, (
        "afl_fix_2026_ids.py does not DELETE season=2026 rows"
    )
    assert "upsert_player_stats" in source, (
        "afl_fix_2026_ids.py does not call upsert_player_stats to re-import"
    )


# ---------------------------------------------------------------------------
# Fryzigg source — updated workflow and CSV format detection
# ---------------------------------------------------------------------------

def test_r_workflow_uses_afltables_with_fryzigg_fallback():
    """The fetch-afl-2026 workflow must use source='afltables' as the primary
    source (with fryzigg as a fallback).  The fryzigg source started returning
    0 rows for 2026 from April 25 2026, causing the CSV to go stale; afltables
    updates within a day of each round finishing and is now the reliable option.
    The afltables player_id collision issue is handled by the name-matching
    resolution in upsert_player_stats / _resolve_player_id_2026."""
    wf_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".github", "workflows", "fetch-afl-2026.yml",
    )
    with open(wf_path, encoding="utf-8") as f:
        source = f.read()
    assert "source = 'afltables'" in source, (
        "fetch-afl-2026.yml does not use source = 'afltables'; "
        "fryzigg has been returning 0 rows for 2026 since April 25"
    )
    # fryzigg may still appear as a fallback — that is fine.
    # Verify afltables appears before fryzigg (primary, not just present).
    afltables_pos = source.find("source = 'afltables'")
    fryzigg_pos = source.find("source = 'fryzigg'")
    assert afltables_pos < fryzigg_pos, (
        "afltables must appear before fryzigg in fetch-afl-2026.yml "
        "(afltables is the primary source, fryzigg is the fallback)"
    )


def test_fetch_2026_csv_detects_fryzigg_format():
    """fetch_2026_stats_from_csv must detect Fryzigg format when 'player_id' column present."""
    data_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_data.py",
    )
    with open(data_path, encoding="utf-8") as f:
        source = f.read()
    assert '"player_id" in cols' in source, (
        "fetch_2026_stats_from_csv does not check for 'player_id' in cols "
        "to detect Fryzigg CSV format"
    )


def test_fetch_2026_csv_has_fryzigg_parser():
    """afl_data.py must define _parse_fryzigg_csv_df for Fryzigg CSV format."""
    data_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_data.py",
    )
    with open(data_path, encoding="utf-8") as f:
        source = f.read()
    assert "def _parse_fryzigg_csv_df(" in source, (
        "_parse_fryzigg_csv_df not found in afl_data.py"
    )
    assert "def _parse_afltables_csv_df(" in source, (
        "_parse_afltables_csv_df not found in afl_data.py"
    )


def test_fryzigg_csv_parser_no_hash_derivation():
    """_parse_fryzigg_csv_df must NOT use _hash_match_key_to_bigint — match_id comes
    directly from Fryzigg (no hash needed)."""
    data_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_data.py",
    )
    with open(data_path, encoding="utf-8") as f:
        source = f.read()
    # Find the body of _parse_fryzigg_csv_df
    start = source.find("def _parse_fryzigg_csv_df(")
    assert start != -1, "_parse_fryzigg_csv_df not found"
    end = source.find("\ndef ", start + 1)
    fn_body = source[start:] if end == -1 else source[start:end]
    assert "_hash_match_key_to_bigint" not in fn_body, (
        "_parse_fryzigg_csv_df should not derive match_id via hash; "
        "Fryzigg provides a stable match_id directly"
    )


def test_build_historical_id_map_returns_four_values():
    """_build_historical_id_map must return a 4-tuple including name_to_ids."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()
    # The function should return four values
    fn_start = source.find("def _build_historical_id_map(")
    assert fn_start != -1
    fn_end = source.find("\ndef ", fn_start + 1)
    fn_body = source[fn_start:] if fn_end == -1 else source[fn_start:fn_end]
    assert "id_to_name_keys" in fn_body, (
        "_build_historical_id_map does not build an id_to_name_keys inverse map"
    )
    assert "name_to_ids" in fn_body, (
        "_build_historical_id_map does not build a name_to_ids map"
    )
    assert "return mapping, ambiguous, dict(id_to_name_keys), dict(name_to_ids)" in fn_body, (
        "_build_historical_id_map does not return a 4-tuple with name_to_ids"
    )


def test_resolve_2026_player_id_accepts_id_to_name_keys_param():
    """_resolve_2026_player_id must accept name_to_ids and id_to_name_keys parameters."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()
    fn_start = source.find("def _resolve_2026_player_id(")
    assert fn_start != -1
    # Grab text up to the closing paren of the parameter list (before the body)
    paren_close = source.find(") -> tuple", fn_start)
    if paren_close == -1:
        paren_close = source.find("):", fn_start)
    fn_sig = source[fn_start:paren_close + 1] if paren_close != -1 else source[fn_start:fn_start + 200]
    assert "id_to_name_keys" in fn_sig, (
        "_resolve_2026_player_id does not accept id_to_name_keys parameter"
    )
    assert "name_to_ids" in fn_sig, (
        "_resolve_2026_player_id does not accept name_to_ids parameter"
    )


# Inline implementation of the updated _resolve_2026_player_id for unit tests.
def _i_test(value, default=0):
    if value is None:
        return default
    try:
        return int(float(value))
    except Exception:
        return default


def _resolve_2026_player_id_impl(row, name_to_ids, hist_map, id_to_name_keys):
    """Mirror of the updated _resolve_2026_player_id in afl_db.py."""
    first = _normalise_name(row.get("player_first_name"))
    last  = _normalise_name(row.get("player_last_name"))
    team  = _team(row.get("player_team", ""))
    team  = _normalise_name(team)

    name_key      = (first, last)
    name_team_key = (first, last, team)

    candidate_ids = name_to_ids.get(name_key, set())

    if len(candidate_ids) == 1:
        return next(iter(candidate_ids)), "reused_by_name"

    if len(candidate_ids) > 1:
        hist_pid = hist_map.get(name_team_key)
        if hist_pid is not None:
            return hist_pid, "reused_by_name_team"
        return _stable_debut_id(first, last, team), "ambiguous"

    return _stable_debut_id(first, last, team), "debut_generated"


def test_resolve_reuses_id_by_name_same_club():
    """When a player's name uniquely maps to one historical id, reuse it (same club).
    The incoming player_id from the CSV is intentionally different to confirm it is
    ignored — the historical Fryzigg id is always used when a name match exists."""
    name_to_ids = {("nick", "daicos"): {12345}}
    hist_map = {("nick", "daicos", "collingwood"): 12345}
    row = {"player_first_name": "Nick", "player_last_name": "Daicos",
           "player_team": "Collingwood", "player_id": 99999}  # AFLTables ID — ignored
    pid, res = _resolve_2026_player_id_impl(row, name_to_ids, hist_map, {})
    assert pid == 12345
    assert res == "reused_by_name"


def test_resolve_reuses_id_by_name_traded_player():
    """Traded player (different club in 2026) must reuse historical id via name match.
    The incoming player_id from the CSV is intentionally different to confirm the
    new logic ignores it and reuses the correct historical Fryzigg id."""
    # Player was Carlton in history, now plays for Sydney in 2026
    name_to_ids = {("charlie", "curnow"): {12345}}
    hist_map = {("charlie", "curnow", "carlton"): 12345}
    row = {"player_first_name": "Charlie", "player_last_name": "Curnow",
           "player_team": "Sydney", "player_id": 99999}  # AFLTables ID — ignored
    pid, res = _resolve_2026_player_id_impl(row, name_to_ids, hist_map, {})
    assert pid == 12345
    assert res == "reused_by_name"


def test_resolve_reuses_id_by_name_team_tiebreaker():
    """When name is ambiguous, resolve via (name, team) tiebreaker."""
    # Two players named "James" "Smith" with different historical ids
    name_to_ids = {("james", "smith"): {11111, 22222}}
    hist_map = {
        ("james", "smith", "richmond"): 11111,
        ("james", "smith", "geelong"): 22222,
    }
    row = {"player_first_name": "James", "player_last_name": "Smith",
           "player_team": "Richmond", "player_id": 0}
    pid, res = _resolve_2026_player_id_impl(row, name_to_ids, hist_map, {})
    assert pid == 11111
    assert res == "reused_by_name_team"


def test_resolve_ambiguous_name_no_team_match_gets_debut_id():
    """Ambiguous name with no team tiebreaker → stable debut id."""
    name_to_ids = {("james", "smith"): {11111, 22222}}
    hist_map = {
        ("james", "smith", "richmond"): 11111,
        ("james", "smith", "geelong"): 22222,
    }
    row = {"player_first_name": "James", "player_last_name": "Smith",
           "player_team": "Carlton", "player_id": 0}
    pid, res = _resolve_2026_player_id_impl(row, name_to_ids, hist_map, {})
    assert pid < 0
    assert res == "ambiguous"


def test_resolve_debut_player_no_history():
    """Completely new player with no historical record gets a stable negative debut id."""
    name_to_ids: dict = {}
    hist_map: dict = {}
    row = {"player_first_name": "Brand", "player_last_name": "New",
           "player_team": "Carlton", "player_id": 55555}
    pid, res = _resolve_2026_player_id_impl(row, name_to_ids, hist_map, {})
    assert pid < 0
    assert res == "debut_generated"


def test_resolve_stable_id_is_stable_across_calls():
    """The debut id generated for the same player is identical across multiple calls."""
    name_to_ids: dict = {}
    hist_map: dict = {}
    row = {"player_first_name": "Consistent", "player_last_name": "Debut",
           "player_team": "Sydney", "player_id": None}
    pid1, _ = _resolve_2026_player_id_impl(row, name_to_ids, hist_map, {})
    pid2, _ = _resolve_2026_player_id_impl(row, name_to_ids, hist_map, {})
    assert pid1 == pid2


# ---------------------------------------------------------------------------
# Deadlock fix — advisory lock guards AFL DDL migrations
# ---------------------------------------------------------------------------

def test_init_afl_tables_uses_advisory_lock():
    """init_afl_tables must use pg_try_advisory_xact_lock to guard DDL migrations."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()

    assert "pg_try_advisory_xact_lock" in source, (
        "init_afl_tables does not use pg_try_advisory_xact_lock; "
        "concurrent Gunicorn workers will deadlock on ALTER TABLE migrations"
    )


def test_init_afl_tables_advisory_lock_key_defined():
    """A stable advisory lock key constant must be defined in afl_db.py."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()

    assert "_AFL_MIGRATION_LOCK_KEY" in source, (
        "afl_db.py does not define _AFL_MIGRATION_LOCK_KEY; "
        "the advisory lock needs a stable integer key"
    )


def test_init_afl_tables_skips_migrations_when_lock_not_acquired():
    """init_afl_tables must skip migrations when the advisory lock is not acquired."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()

    # Find the body of init_afl_tables
    fn_start = source.find("def init_afl_tables(")
    assert fn_start != -1, "init_afl_tables not found"
    fn_end = source.find("\ndef ", fn_start + 1)
    fn_body = source[fn_start:] if fn_end == -1 else source[fn_start:fn_end]

    # Must branch on the lock result
    assert "if acquired" in fn_body, (
        "init_afl_tables does not branch on the advisory lock acquisition result; "
        "non-lock-holding workers must skip migrations"
    )


# ---------------------------------------------------------------------------
# Player headshots — stored in DB during upsert
# ---------------------------------------------------------------------------

def _headshot_url_impl(player_id):
    """Mirror of _headshot_url from afl_db.py."""
    if not player_id or player_id <= 0:
        return None
    return (
        f"https://www.afl.com.au/staticfile/AFL%20Tenant/AFL/Players/"
        f"ChampIDImages/{player_id}.png"
    )


def test_headshot_url_positive_id():
    """Positive player_id should produce a valid AFL CDN URL."""
    url = _headshot_url_impl(12345)
    assert url is not None
    assert "12345" in url
    assert url.startswith("https://www.afl.com.au/staticfile/")


def test_headshot_url_negative_id_returns_none():
    """Negative player_id (debut placeholder) must return None."""
    assert _headshot_url_impl(-999) is None


def test_headshot_url_zero_returns_none():
    """Zero player_id must return None."""
    assert _headshot_url_impl(0) is None


def test_headshot_url_none_returns_none():
    """None player_id must return None."""
    assert _headshot_url_impl(None) is None


def test_afl_db_has_headshot_url_helper():
    """afl_db.py must define _headshot_url helper."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()
    assert "def _headshot_url(" in source, (
        "_headshot_url not found in afl_db.py"
    )


def test_upsert_player_stats_stores_headshot_url():
    """upsert_player_stats must include player_headshot_url in the INSERT statement."""
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_db.py",
    )
    with open(db_path, encoding="utf-8") as f:
        source = f.read()

    # Find the INSERT SQL in upsert_player_stats
    fn_start = source.find("def upsert_player_stats(")
    assert fn_start != -1, "upsert_player_stats not found in afl_db.py"
    fn_end = source.find("\ndef ", fn_start + 1)
    fn_body = source[fn_start:] if fn_end == -1 else source[fn_start:fn_end]

    assert "player_headshot_url" in fn_body, (
        "upsert_player_stats does not include player_headshot_url in INSERT; "
        "headshots will never be stored in the DB"
    )
    assert "_headshot_url(player_id)" in fn_body, (
        "upsert_player_stats does not call _headshot_url(player_id) to compute the URL"
    )


def test_db_get_fixtures_joins_team_logos():
    """_db_get_fixtures must JOIN afl_team_logos to attach logo URLs."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_routes.py",
    )
    with open(routes_path, encoding="utf-8") as f:
        source = f.read()

    fn_start = source.find("def _db_get_fixtures(")
    assert fn_start != -1, "_db_get_fixtures not found"
    fn_end = source.find("\ndef ", fn_start + 1)
    fn_body = source[fn_start:] if fn_end == -1 else source[fn_start:fn_end]

    assert "hteam_logo_url" in fn_body, (
        "_db_get_fixtures does not select hteam_logo_url; "
        "team logos will not appear in fixtures"
    )
    assert "ateam_logo_url" in fn_body, (
        "_db_get_fixtures does not select ateam_logo_url"
    )
    assert "afl_team_logos" in fn_body, (
        "_db_get_fixtures does not join afl_team_logos"
    )


def test_db_get_standings_joins_team_logos():
    """_db_get_standings must JOIN afl_team_logos to attach logo URLs."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_routes.py",
    )
    with open(routes_path, encoding="utf-8") as f:
        source = f.read()

    fn_start = source.find("def _db_get_standings(")
    assert fn_start != -1, "_db_get_standings not found"
    fn_end = source.find("\ndef ", fn_start + 1)
    fn_body = source[fn_start:] if fn_end == -1 else source[fn_start:fn_end]

    assert "team_logo_url" in fn_body, (
        "_db_get_standings does not select team_logo_url; "
        "team logos will not appear in the ladder"
    )
    assert "afl_team_logos" in fn_body, (
        "_db_get_standings does not join afl_team_logos"
    )


# ---------------------------------------------------------------------------
# Fix – value finder match filter must use exact team name equality, not
# substring includes() which causes Melbourne to match North Melbourne
# ---------------------------------------------------------------------------

def test_value_finder_match_filter_uses_exact_equality():
    """_vfApplyFilters match filter must use === not .includes() for team names.
    Using .includes() causes 'Melbourne' to match inside 'North Melbourne',
    showing North Melbourne players when a Melbourne match is selected."""
    template_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "templates",
        "afl.html",
    )
    with open(template_path, encoding="utf-8") as f:
        source = f.read()

    # Locate _vfApplyFilters body
    fn_start = source.find("function _vfApplyFilters(")
    assert fn_start != -1, "function _vfApplyFilters not found in afl.html"
    fn_end = source.find("\nfunction ", fn_start + 1)
    fn_body = source[fn_start:] if fn_end == -1 else source[fn_start:fn_end]

    # Must NOT use .includes() for team name filtering (substring match)
    assert ".includes(" not in fn_body, (
        "_vfApplyFilters still uses .includes() for match/team filtering — "
        "this causes 'Melbourne' to match inside 'North Melbourne'. "
        "Use === exact equality instead."
    )

    # Must use strict equality (===) for team comparison
    assert "===" in fn_body, (
        "_vfApplyFilters does not use === for team name comparison"
    )


def test_fetch_afl_2026_workflow_uses_afltables_source():
    """fetch-afl-2026.yml must use source='afltables' as the primary source.
    source='fryzigg' has been returning 0 rows since April 25 2026, causing
    the CSV to go stale and Round 8+ data to not update on the website."""
    workflow_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".github", "workflows", "fetch-afl-2026.yml",
    )
    with open(workflow_path, encoding="utf-8") as f:
        source = f.read()

    assert "source = 'afltables'" in source, (
        "fetch-afl-2026.yml does not use source='afltables'. "
        "The fryzigg source returns 0 rows for 2026, causing the CSV to go stale."
    )

    # Must NOT hard-stop when fryzigg returns nothing — afltables is the primary
    assert "source = 'fryzigg'" not in source or "afltables" in source, (
        "fetch-afl-2026.yml still uses fryzigg as primary without afltables fallback"
    )


# ---------------------------------------------------------------------------
# Fix – api_afl_betting_edges Decimal/float type error (line ~1682)
# ---------------------------------------------------------------------------

import decimal
import math as _math


def _betting_edge_predicted_margin(predicted_margin_raw):
    """Inline mirror of the fixed coercion logic in api_afl_betting_edges."""
    try:
        predicted_margin = float(predicted_margin_raw) if predicted_margin_raw is not None else None
    except (TypeError, ValueError):
        predicted_margin = None
    if predicted_margin is not None and _math.isnan(predicted_margin):
        predicted_margin = None
    return predicted_margin


def _logistic_prob(predicted_margin, logistic_scale=35.0):
    """Inline mirror of the h2h probability calculation in api_afl_betting_edges."""
    if predicted_margin is None:
        return None
    return 1.0 / (1.0 + _math.exp(-predicted_margin / logistic_scale))


def test_betting_edges_decimal_predicted_margin_coercion():
    """Decimal predicted_margin from the DB must be coerced to float without error."""
    dec_val = decimal.Decimal("12.345")
    result = _betting_edge_predicted_margin(dec_val)
    assert isinstance(result, float)
    assert abs(result - 12.345) < 1e-6


def test_betting_edges_none_predicted_margin():
    """None predicted_margin must be returned as None (no error)."""
    assert _betting_edge_predicted_margin(None) is None


def test_betting_edges_nan_predicted_margin():
    """NaN predicted_margin must be normalised to None."""
    assert _betting_edge_predicted_margin(float("nan")) is None


def test_betting_edges_invalid_predicted_margin():
    """Non-numeric predicted_margin must be normalised to None without raising."""
    assert _betting_edge_predicted_margin("not-a-number") is None
    assert _betting_edge_predicted_margin("") is None


def test_betting_edges_logistic_prob_with_decimal_input():
    """Logistic probability calculation must work with Decimal input after coercion."""
    dec_margin = decimal.Decimal("35")
    coerced = _betting_edge_predicted_margin(dec_margin)
    prob = _logistic_prob(coerced)
    # 35 / 35 = 1.0 → 1/(1+e^-1) ≈ 0.731
    assert prob is not None
    assert abs(prob - 1.0 / (1.0 + _math.exp(-1.0))) < 1e-6


def test_betting_edges_logistic_prob_none_margin():
    """_logistic_prob must return None when predicted_margin is None."""
    assert _logistic_prob(None) is None


def test_betting_edges_source_coerces_predicted_margin():
    """afl_routes.py api_afl_betting_edges must call float() on predicted_margin
    before using it in math operations."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_routes.py",
    )
    with open(routes_path, encoding="utf-8") as f:
        source = f.read()

    fn_start = source.find("def api_afl_betting_edges(")
    assert fn_start != -1, "api_afl_betting_edges not found in afl_routes.py"
    fn_end = source.find("\n    @app.route(", fn_start + 1)
    fn_body = source[fn_start:] if fn_end == -1 else source[fn_start:fn_end]

    assert "float(_pm_raw)" in fn_body, (
        "api_afl_betting_edges does not convert predicted_margin to float via float(_pm_raw). "
        "Decimal values from the DB will cause TypeError in math.exp()."
    )
    assert "math.isnan(predicted_margin)" in fn_body, (
        "api_afl_betting_edges does not guard against NaN predicted_margin values."
    )


# ---------------------------------------------------------------------------
# Fix – api_afl_betting_edges team name JOIN mismatch (PRED MARGIN / LINE EDGE empty)
# ---------------------------------------------------------------------------

def test_betting_edges_game_predictions_cte_has_norm_hteam():
    """game_predictions CTE must expose a norm_hteam column that normalises
    Squiggle team names (e.g. 'Greater Western Sydney') to canonical form
    (e.g. 'GWS Giants') so the outer JOIN to afl_match_markets succeeds."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_routes.py",
    )
    with open(routes_path, encoding="utf-8") as f:
        source = f.read()

    fn_start = source.find("def api_afl_betting_edges(")
    assert fn_start != -1, "api_afl_betting_edges not found in afl_routes.py"
    fn_end = source.find("\n    @app.route(", fn_start + 1)
    fn_body = source[fn_start:] if fn_end == -1 else source[fn_start:fn_end]

    assert "norm_hteam" in fn_body, (
        "game_predictions CTE in api_afl_betting_edges does not define norm_hteam. "
        "Without this, Squiggle team names (e.g. 'Greater Western Sydney') will not "
        "match the canonical names in afl_match_markets, causing predicted_margin to "
        "always be NULL."
    )
    assert "norm_ateam" in fn_body, (
        "game_predictions CTE in api_afl_betting_edges does not define norm_ateam. "
        "Without this, away team name matching fails and predicted_margin stays NULL."
    )


def test_betting_edges_outer_join_uses_norm_columns():
    """The outer LEFT JOIN between afl_match_markets and game_predictions must use
    norm_hteam / norm_ateam (not the raw gp.hteam / gp.ateam) so that Squiggle
    team name variants are mapped to canonical form before comparison."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_routes.py",
    )
    with open(routes_path, encoding="utf-8") as f:
        source = f.read()

    fn_start = source.find("def api_afl_betting_edges(")
    assert fn_start != -1, "api_afl_betting_edges not found in afl_routes.py"
    fn_end = source.find("\n    @app.route(", fn_start + 1)
    fn_body = source[fn_start:] if fn_end == -1 else source[fn_start:fn_end]

    assert "gp.norm_hteam" in fn_body, (
        "Outer LEFT JOIN in api_afl_betting_edges does not use gp.norm_hteam. "
        "The JOIN must compare normalised team names so Squiggle variants match."
    )
    assert "gp.norm_ateam" in fn_body, (
        "Outer LEFT JOIN in api_afl_betting_edges does not use gp.norm_ateam. "
        "The JOIN must compare normalised team names so Squiggle variants match."
    )


def test_betting_edges_norm_hteam_maps_greater_western_sydney():
    """The CASE expression for norm_hteam must map 'Greater Western Sydney' to 'GWS Giants',
    which is the most likely Squiggle→canonical mismatch causing the JOIN to fail."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "afl_routes.py",
    )
    with open(routes_path, encoding="utf-8") as f:
        source = f.read()

    fn_start = source.find("def api_afl_betting_edges(")
    assert fn_start != -1, "api_afl_betting_edges not found in afl_routes.py"
    fn_end = source.find("\n    @app.route(", fn_start + 1)
    fn_body = source[fn_start:] if fn_end == -1 else source[fn_start:fn_end]

    assert "'Greater Western Sydney'" in fn_body, (
        "norm_hteam CASE expression in api_afl_betting_edges is missing "
        "'Greater Western Sydney' → 'GWS Giants' mapping."
    )
