"""
afl_db.py
=========
PostgreSQL schema + helper functions for the AFL section.
Run init_afl_tables(db) once to create all tables.
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────

AFL_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS afl_games (
        id              INTEGER PRIMARY KEY,
        year            INTEGER NOT NULL,
        round           INTEGER,
        roundname       TEXT,
        date            TIMESTAMP,
        "localtime"     TEXT,
        venue           TEXT,
        hteam           TEXT,
        ateam           TEXT,
        hteamid         INTEGER,
        ateamid         INTEGER,
        hscore          INTEGER,
        ascore          INTEGER,
        hgoals          INTEGER,
        hbehinds        INTEGER,
        agoals          INTEGER,
        abehinds        INTEGER,
        margin          INTEGER,
        winner          TEXT,
        winnerteamid    INTEGER,
        is_final        INTEGER DEFAULT 0,
        complete        INTEGER DEFAULT 0,
        updated_at      TIMESTAMP DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_afl_games_year_round
    ON afl_games(year, round)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_afl_games_teams
    ON afl_games(hteam, ateam)
    """,
    """
    CREATE TABLE IF NOT EXISTS afl_tips (
        id              SERIAL PRIMARY KEY,
        gameid          INTEGER REFERENCES afl_games(id) ON DELETE CASCADE,
        year            INTEGER,
        round           INTEGER,
        hteam           TEXT,
        ateam           TEXT,
        tip             TEXT,
        tipteamid       INTEGER,
        confidence      FLOAT,
        margin          FLOAT,
        source_id       INTEGER DEFAULT 8,
        updated_at      TIMESTAMP DEFAULT NOW(),
        UNIQUE(gameid, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS afl_standings (
        id              SERIAL PRIMARY KEY,
        year            INTEGER NOT NULL,
        round           INTEGER NOT NULL,
        rank            INTEGER,
        team            TEXT,
        teamid          INTEGER,
        pts             INTEGER,
        played          INTEGER,
        wins            INTEGER,
        losses          INTEGER,
        draws           INTEGER,
        for_score       INTEGER,
        against_score   INTEGER,
        percentage      FLOAT,
        last_updated    TIMESTAMP DEFAULT NOW(),
        UNIQUE(year, round, teamid)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS afl_player_stats (
        id                              SERIAL PRIMARY KEY,
        match_id                        BIGINT,
        match_date                      DATE,
        match_round                     TEXT,
        match_home_team                 TEXT,
        match_away_team                 TEXT,
        match_home_team_score           INTEGER,
        match_away_team_score           INTEGER,
        match_margin                    INTEGER,
        match_winner                    TEXT,
        match_weather_temp_c            INTEGER,
        match_weather_type              TEXT,
        match_attendance                INTEGER,
        venue_name                      TEXT,
        season                          INTEGER,

        player_id                       BIGINT,
        player_first_name               TEXT,
        player_last_name                TEXT,
        player_team                     TEXT,
        guernsey_number                 INTEGER,
        player_height_cm                INTEGER,
        player_weight_kg                INTEGER,
        player_is_retired               BOOLEAN DEFAULT FALSE,

        kicks                           INTEGER DEFAULT 0,
        marks                           INTEGER DEFAULT 0,
        handballs                       INTEGER DEFAULT 0,
        disposals                       INTEGER DEFAULT 0,
        effective_disposals             INTEGER DEFAULT 0,
        disposal_efficiency_percentage  INTEGER DEFAULT 0,
        goals                           INTEGER DEFAULT 0,
        behinds                         INTEGER DEFAULT 0,
        hitouts                         INTEGER DEFAULT 0,
        tackles                         INTEGER DEFAULT 0,
        rebounds                        INTEGER DEFAULT 0,
        inside_fifties                  INTEGER DEFAULT 0,
        clearances                      INTEGER DEFAULT 0,
        clangers                        INTEGER DEFAULT 0,
        free_kicks_for                  INTEGER DEFAULT 0,
        free_kicks_against              INTEGER DEFAULT 0,
        brownlow_votes                  INTEGER DEFAULT 0,
        contested_possessions           INTEGER DEFAULT 0,
        uncontested_possessions         INTEGER DEFAULT 0,
        contested_marks                 INTEGER DEFAULT 0,
        marks_inside_fifty              INTEGER DEFAULT 0,
        one_percenters                  INTEGER DEFAULT 0,
        bounces                         INTEGER DEFAULT 0,
        goal_assists                    INTEGER DEFAULT 0,
        time_on_ground_percentage       INTEGER DEFAULT 0,
        afl_fantasy_score               INTEGER DEFAULT 0,
        supercoach_score                INTEGER DEFAULT 0,
        centre_clearances               INTEGER DEFAULT 0,
        stoppage_clearances             INTEGER DEFAULT 0,
        score_involvements              INTEGER DEFAULT 0,
        metres_gained                   INTEGER DEFAULT 0,
        turnovers                       INTEGER DEFAULT 0,
        intercepts                      INTEGER DEFAULT 0,
        tackles_inside_fifty            INTEGER DEFAULT 0,
        contest_def_losses              INTEGER DEFAULT 0,
        contest_def_one_on_ones         INTEGER DEFAULT 0,
        contest_off_one_on_ones         INTEGER DEFAULT 0,

        created_at                      TIMESTAMP DEFAULT NOW(),
        UNIQUE(match_id, player_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_afl_ps_player
    ON afl_player_stats(player_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_afl_ps_team
    ON afl_player_stats(player_team)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_afl_ps_date
    ON afl_player_stats(match_date)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_afl_ps_season
    ON afl_player_stats(season)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_afl_ps_name
    ON afl_player_stats(player_last_name, player_first_name)
    """,
    """
    CREATE TABLE IF NOT EXISTS afl_player_props (
        id              SERIAL PRIMARY KEY,
        event_id        TEXT,
        home_team       TEXT,
        away_team       TEXT,
        commence_time   TIMESTAMP,
        bookmaker       TEXT,
        market          TEXT,
        player_name     TEXT,
        line_type       TEXT,
        line            FLOAT,
        odds            FLOAT,
        fetched_at      TIMESTAMP DEFAULT NOW(),
        UNIQUE(event_id, bookmaker, market, player_name, line_type, line)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_afl_props_player
    ON afl_player_props(player_name)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_afl_props_event
    ON afl_player_props(event_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS afl_sync_log (
        id          SERIAL PRIMARY KEY,
        source      TEXT NOT NULL,
        season      INTEGER,
        round       INTEGER,
        rows_synced INTEGER DEFAULT 0,
        status      TEXT DEFAULT 'ok',
        error_msg   TEXT,
        synced_at   TIMESTAMP DEFAULT NOW()
    )
    """,
]


# ─────────────────────────────────────────────
# NORMALISERS / COERCERS
# ─────────────────────────────────────────────

def _s(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _i(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except Exception:
        return default


def _b(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


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


def _match_id(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    raw = str(value).strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return default
    try:
        return int(digits)
    except Exception:
        return default


def _normalise_name(value: Any) -> str:
    """Normalise a player name: strip, lowercase, collapse internal whitespace."""
    s = str(value).strip().lower() if value else ""
    return " ".join(s.split())


def _stable_debut_id(first: str, last: str, team: str) -> int:
    """
    Generate a stable negative BIGINT player_id for a debut player (no historical match).

    Uses SHA-256 of a 2026-namespaced key so the result:
    - is deterministic across re-imports
    - is always negative (cannot collide with positive Fryzigg IDs)
    - fits within PostgreSQL BIGINT range (-2^63 .. 2^63-1)
    """
    key = f"2026|{first}|{last}|{team}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    abs_id = int.from_bytes(digest[:7], "big") + 1  # 1 .. 2^56, never 0
    return -abs_id


def _build_historical_id_map(db) -> tuple[dict, set, dict]:
    """
    Query existing 2019–2025 player stats and return three lookups for 2026 ID resolution.

    Returns:
        unambiguous_map:  {(norm_first, norm_last, norm_team): player_id}
            Maps a normalised (name, team) key to the single historical Fryzigg
            player_id.  Used to resolve the correct ID for established players.

        ambiguous_keys:   set of (norm_first, norm_last, norm_team) tuples that
            mapped to more than one historical player_id (very rare; treated as
            unresolvable and given a stable debut id).

        id_to_name_keys:  {player_id: set of (norm_first, norm_last, norm_team)}
            Inverse map — used to detect when an incoming player_id from a
            non-Fryzigg source (e.g. AFLTables) already belongs to a different
            historical player (collision detection for debut rows).
    """
    sql_text = """
        SELECT player_first_name, player_last_name, player_team, player_id
        FROM afl_player_stats
        WHERE season BETWEEN 2019 AND 2025
          AND player_id IS NOT NULL
    """
    key_to_ids: dict = defaultdict(set)
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(db.text(sql_text))
            for r in rows:
                first = _normalise_name(r[0])
                last = _normalise_name(r[1])
                team = _normalise_name(_team(r[2]))
                pid = r[3]
                if pid:
                    key_to_ids[(first, last, team)].add(pid)
    except Exception as exc:
        logger.warning("Could not build historical player_id map: %s", exc)
        return {}, set(), {}

    mapping: dict = {}
    ambiguous: set = set()
    id_to_name_keys: dict = defaultdict(set)

    for key, ids in key_to_ids.items():
        if len(ids) == 1:
            pid = next(iter(ids))
            mapping[key] = pid
            id_to_name_keys[pid].add(key)
        else:
            ambiguous.add(key)

    logger.info(
        "Historical player_id map: %s unambiguous keys, %s ambiguous keys (seasons 2019–2025)",
        len(mapping),
        len(ambiguous),
    )
    return mapping, ambiguous, dict(id_to_name_keys)


def _resolve_2026_player_id(
    row: dict,
    hist_map: dict,
    ambiguous_keys: set,
    id_to_name_keys: dict,
) -> tuple[int, str]:
    """
    Determine the correct player_id for a 2026 stat row.

    Resolution order (with incoming_pid = the player_id already on the row):

    1. incoming_pid is valid AND matches the historical ID for (first,last,team)
       → "trusted"  — Fryzigg source; ID is already correct, use as-is.

    2. incoming_pid is valid AND (first,last,team) has a *different* historical ID
       → "historical"  — AFLTables collision; override with the correct Fryzigg ID.

    3. incoming_pid is valid AND player has no history (debut) AND the incoming
       pid does NOT belong to any other known player
       → "trusted"  — New Fryzigg-assigned ID for a debut player; trust it so it
         stays consistent with future Fryzigg seasons.

    4. incoming_pid is valid AND player has no history AND the incoming pid IS
       already assigned to a different historical player
       → "collision_debut"  — AFLTables-style collision on a debut; use stable id.

    5. No incoming_pid, (first,last,team) found unambiguously in history
       → "historical"  — derive from name-match.

    6. No incoming_pid, (first,last,team) is ambiguous
       → "ambiguous"  — stable debut id.

    7. No incoming_pid, not in history
       → "debut"  — stable debut id.

    Returns (player_id, resolution_type).
    """
    first = _normalise_name(row.get("player_first_name"))
    last  = _normalise_name(row.get("player_last_name"))
    team  = _normalise_name(_team(row.get("player_team")))
    key   = (first, last, team)

    incoming_pid = _i(row.get("player_id"))

    if incoming_pid:
        historical_pid = hist_map.get(key)

        if historical_pid is not None:
            if incoming_pid == historical_pid:
                # Fryzigg source: ID is already the correct Fryzigg ID.
                return incoming_pid, "trusted"
            else:
                # AFLTables collision: incoming ID differs from the known Fryzigg
                # ID for this (name, team) — override with the Fryzigg ID.
                return historical_pid, "historical"

        # Player not found in 2019–2025 (potential debut).
        if key in ambiguous_keys:
            return _stable_debut_id(first, last, team), "ambiguous"

        # Check whether this incoming ID already belongs to a *different* player.
        known_keys_for_pid = id_to_name_keys.get(incoming_pid)
        if known_keys_for_pid and key not in known_keys_for_pid:
            # AFLTables-style collision on a debut row — fall back to stable id.
            return _stable_debut_id(first, last, team), "collision_debut"

        # Clean debut (Fryzigg-assigned ID, or truly novel): trust the incoming ID
        # so it stays consistent with future Fryzigg seasons for this player.
        return incoming_pid, "trusted"

    # ── No incoming player_id ─────────────────────────────────────────────
    if key in hist_map:
        return hist_map[key], "historical"
    if key in ambiguous_keys:
        return _stable_debut_id(first, last, team), "ambiguous"
    return _stable_debut_id(first, last, team), "debut"


# ─────────────────────────────────────────────
# SCHEMA INIT
# ─────────────────────────────────────────────

def init_afl_tables(db):
    """Create all AFL tables. Safe to call multiple times."""
    try:
        with db.engine.begin() as conn:
            for statement in AFL_SCHEMA_STATEMENTS:
                conn.execute(db.text(statement))
        logger.info("AFL tables initialised")
    except Exception as exc:
        logger.error("Failed to init AFL tables: %s", exc)
        raise


# ─────────────────────────────────────────────
# DB WRITE HELPERS
# ─────────────────────────────────────────────

def upsert_games(db, games: list[dict]) -> int:
    """Upsert Squiggle games. Returns count inserted/updated."""
    if not games:
        return 0

    sql = db.text("""
        INSERT INTO afl_games (
            id, year, round, roundname, date, "localtime", venue,
            hteam, ateam, hteamid, ateamid,
            hscore, ascore, hgoals, hbehinds, agoals, abehinds,
            margin, winner, winnerteamid, is_final, complete, updated_at
        )
        VALUES (
            :id, :year, :round, :roundname, :date, :localtime, :venue,
            :hteam, :ateam, :hteamid, :ateamid,
            :hscore, :ascore, :hgoals, :hbehinds, :agoals, :abehinds,
            :margin, :winner, :winnerteamid, :is_final, :complete, NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
            year         = EXCLUDED.year,
            round        = EXCLUDED.round,
            roundname    = EXCLUDED.roundname,
            date         = EXCLUDED.date,
            "localtime"  = EXCLUDED."localtime",
            venue        = EXCLUDED.venue,
            hteam        = EXCLUDED.hteam,
            ateam        = EXCLUDED.ateam,
            hteamid      = EXCLUDED.hteamid,
            ateamid      = EXCLUDED.ateamid,
            hscore       = EXCLUDED.hscore,
            ascore       = EXCLUDED.ascore,
            hgoals       = EXCLUDED.hgoals,
            hbehinds     = EXCLUDED.hbehinds,
            agoals       = EXCLUDED.agoals,
            abehinds     = EXCLUDED.abehinds,
            margin       = EXCLUDED.margin,
            winner       = EXCLUDED.winner,
            winnerteamid = EXCLUDED.winnerteamid,
            is_final     = EXCLUDED.is_final,
            complete     = EXCLUDED.complete,
            updated_at   = NOW()
    """)

    count = 0
    with db.engine.begin() as conn:
        for game in games:
            conn.execute(sql, {
                "id": game.get("id"),
                "year": _i(game.get("year")),
                "round": _i(game.get("round")),
                "roundname": _s(game.get("roundname")),
                "date": game.get("date"),
                "localtime": _s(game.get("localtime")),
                "venue": _s(game.get("venue")),
                "hteam": _team(game.get("hteam")),
                "ateam": _team(game.get("ateam")),
                "hteamid": _i(game.get("hteamid")),
                "ateamid": _i(game.get("ateamid")),
                "hscore": _i(game.get("hscore")),
                "ascore": _i(game.get("ascore")),
                "hgoals": _i(game.get("hgoals")),
                "hbehinds": _i(game.get("hbehinds")),
                "agoals": _i(game.get("agoals")),
                "abehinds": _i(game.get("abehinds")),
                "margin": _i(game.get("margin")),
                "winner": _team(game.get("winner")),
                "winnerteamid": _i(game.get("winnerteamid")),
                "is_final": _i(game.get("is_final")),
                "complete": _i(game.get("complete")),
            })
            count += 1

    return count


def upsert_player_stats(db, stats: list[dict], season: int) -> int:
    """
    Upsert player stats. Returns count inserted/updated.

    For season 2026, player_id is resolved deterministically:
      - (first, last, team) matched against 2019–2025 history → reuse historical id
      - No match (debut player) or ambiguous → stable negative BIGINT via _stable_debut_id()
    This prevents collisions between the 2026 CSV IDs and the Fryzigg IDs used in
    prior seasons.
    """
    if not stats:
        return 0

    sql = db.text("""
        INSERT INTO afl_player_stats (
            match_id, match_date, match_round,
            match_home_team, match_away_team,
            match_home_team_score, match_away_team_score,
            match_margin, match_winner,
            match_weather_temp_c, match_weather_type,
            match_attendance, venue_name, season,
            player_id, player_first_name, player_last_name,
            player_team, guernsey_number,
            player_height_cm, player_weight_kg, player_is_retired,
            kicks, marks, handballs, disposals,
            effective_disposals, disposal_efficiency_percentage,
            goals, behinds, hitouts, tackles, rebounds,
            inside_fifties, clearances, clangers,
            free_kicks_for, free_kicks_against, brownlow_votes,
            contested_possessions, uncontested_possessions,
            contested_marks, marks_inside_fifty, one_percenters,
            bounces, goal_assists, time_on_ground_percentage,
            afl_fantasy_score, supercoach_score,
            centre_clearances, stoppage_clearances,
            score_involvements, metres_gained, turnovers,
            intercepts, tackles_inside_fifty,
            contest_def_losses, contest_def_one_on_ones, contest_off_one_on_ones
        )
        VALUES (
            :match_id, :match_date, :match_round,
            :match_home_team, :match_away_team,
            :match_home_team_score, :match_away_team_score,
            :match_margin, :match_winner,
            :match_weather_temp_c, :match_weather_type,
            :match_attendance, :venue_name, :season,
            :player_id, :player_first_name, :player_last_name,
            :player_team, :guernsey_number,
            :player_height_cm, :player_weight_kg, :player_is_retired,
            :kicks, :marks, :handballs, :disposals,
            :effective_disposals, :disposal_efficiency_percentage,
            :goals, :behinds, :hitouts, :tackles, :rebounds,
            :inside_fifties, :clearances, :clangers,
            :free_kicks_for, :free_kicks_against, :brownlow_votes,
            :contested_possessions, :uncontested_possessions,
            :contested_marks, :marks_inside_fifty, :one_percenters,
            :bounces, :goal_assists, :time_on_ground_percentage,
            :afl_fantasy_score, :supercoach_score,
            :centre_clearances, :stoppage_clearances,
            :score_involvements, :metres_gained, :turnovers,
            :intercepts, :tackles_inside_fifty,
            :contest_def_losses, :contest_def_one_on_ones, :contest_off_one_on_ones
        )
        ON CONFLICT (match_id, player_id) DO UPDATE SET
            match_date                      = EXCLUDED.match_date,
            match_round                     = EXCLUDED.match_round,
            match_home_team                 = EXCLUDED.match_home_team,
            match_away_team                 = EXCLUDED.match_away_team,
            match_home_team_score           = EXCLUDED.match_home_team_score,
            match_away_team_score           = EXCLUDED.match_away_team_score,
            match_margin                    = EXCLUDED.match_margin,
            match_winner                    = EXCLUDED.match_winner,
            match_weather_temp_c            = EXCLUDED.match_weather_temp_c,
            match_weather_type              = EXCLUDED.match_weather_type,
            match_attendance                = EXCLUDED.match_attendance,
            venue_name                      = EXCLUDED.venue_name,
            season                          = EXCLUDED.season,
            player_first_name               = EXCLUDED.player_first_name,
            player_last_name                = EXCLUDED.player_last_name,
            player_team                     = EXCLUDED.player_team,
            guernsey_number                 = EXCLUDED.guernsey_number,
            player_height_cm                = EXCLUDED.player_height_cm,
            player_weight_kg                = EXCLUDED.player_weight_kg,
            player_is_retired               = EXCLUDED.player_is_retired,
            kicks                           = EXCLUDED.kicks,
            marks                           = EXCLUDED.marks,
            handballs                       = EXCLUDED.handballs,
            disposals                       = EXCLUDED.disposals,
            effective_disposals             = EXCLUDED.effective_disposals,
            disposal_efficiency_percentage  = EXCLUDED.disposal_efficiency_percentage,
            goals                           = EXCLUDED.goals,
            behinds                         = EXCLUDED.behinds,
            hitouts                         = EXCLUDED.hitouts,
            tackles                         = EXCLUDED.tackles,
            rebounds                        = EXCLUDED.rebounds,
            inside_fifties                  = EXCLUDED.inside_fifties,
            clearances                      = EXCLUDED.clearances,
            clangers                        = EXCLUDED.clangers,
            free_kicks_for                  = EXCLUDED.free_kicks_for,
            free_kicks_against              = EXCLUDED.free_kicks_against,
            brownlow_votes                  = EXCLUDED.brownlow_votes,
            contested_possessions           = EXCLUDED.contested_possessions,
            uncontested_possessions         = EXCLUDED.uncontested_possessions,
            contested_marks                 = EXCLUDED.contested_marks,
            marks_inside_fifty              = EXCLUDED.marks_inside_fifty,
            one_percenters                  = EXCLUDED.one_percenters,
            bounces                         = EXCLUDED.bounces,
            goal_assists                    = EXCLUDED.goal_assists,
            time_on_ground_percentage       = EXCLUDED.time_on_ground_percentage,
            afl_fantasy_score               = EXCLUDED.afl_fantasy_score,
            supercoach_score                = EXCLUDED.supercoach_score,
            centre_clearances               = EXCLUDED.centre_clearances,
            stoppage_clearances             = EXCLUDED.stoppage_clearances,
            score_involvements              = EXCLUDED.score_involvements,
            metres_gained                   = EXCLUDED.metres_gained,
            turnovers                       = EXCLUDED.turnovers,
            intercepts                      = EXCLUDED.intercepts,
            tackles_inside_fifty            = EXCLUDED.tackles_inside_fifty,
            contest_def_losses              = EXCLUDED.contest_def_losses,
            contest_def_one_on_ones         = EXCLUDED.contest_def_one_on_ones,
            contest_off_one_on_ones         = EXCLUDED.contest_off_one_on_ones
    """)

    # For season 2026, build the historical player_id lookup once before the main loop.
    # This maps (norm_first, norm_last, norm_team) → historical Fryzigg player_id so that
    # 2026 rows inherit the same player_id their owner had in 2019–2025.
    _hist_map_2026: dict = {}
    _ambiguous_keys_2026: set = set()
    _id_to_name_keys_2026: dict = {}
    _trusted_2026 = 0       # incoming Fryzigg ID accepted (consistent or new debut)
    _historical_2026 = 0    # AFLTables collision corrected to proper Fryzigg ID
    _debut_2026 = 0         # no incoming ID, no history → stable debut id
    _ambig_2026 = 0         # ambiguous history → stable debut id
    _collision_debut_2026 = 0  # incoming ID belongs to different player → stable debut id
    is_2026_sync = (season == 2026)
    if is_2026_sync:
        _hist_map_2026, _ambiguous_keys_2026, _id_to_name_keys_2026 = _build_historical_id_map(db)

    count = 0
    with db.engine.begin() as conn:
        for row in stats:
            match_id = _match_id(row.get("match_id"))

            # ── Resolve player_id ──────────────────────────────────────────
            row_season = _i(row.get("season"), season)
            if row_season == 2026:
                player_id, resolution = _resolve_2026_player_id(
                    row, _hist_map_2026, _ambiguous_keys_2026, _id_to_name_keys_2026
                )
                if resolution == "trusted":
                    _trusted_2026 += 1
                elif resolution == "historical":
                    _historical_2026 += 1
                elif resolution in ("ambiguous", "collision_debut"):
                    if resolution == "ambiguous":
                        _ambig_2026 += 1
                    else:
                        _collision_debut_2026 += 1
                    logger.warning(
                        "2026 player_id fallback (%s) for %s %s (%s) — assigned id %s",
                        resolution,
                        row.get("player_first_name"),
                        row.get("player_last_name"),
                        row.get("player_team"),
                        player_id,
                    )
                else:
                    _debut_2026 += 1
            else:
                player_id = _i(row.get("player_id"))
                if not player_id:
                    # Fallback for any non-2026 row missing player_id:
                    # use stable negative id (cannot collide with positive Fryzigg IDs)
                    player_id = _stable_debut_id(
                        _normalise_name(row.get("player_first_name")),
                        _normalise_name(row.get("player_last_name")),
                        _normalise_name(_team(row.get("player_team"))),
                    )
            # ───────────────────────────────────────────────────────────────

            if not match_id:
                continue

            conn.execute(sql, {
                "match_id": match_id,
                "match_date": row.get("match_date"),
                "match_round": _s(row.get("match_round")),
                "match_home_team": _team(row.get("match_home_team")),
                "match_away_team": _team(row.get("match_away_team")),
                "match_home_team_score": _i(row.get("match_home_team_score")),
                "match_away_team_score": _i(row.get("match_away_team_score")),
                "match_margin": _i(row.get("match_margin")),
                "match_winner": _team(row.get("match_winner")),
                "match_weather_temp_c": _i(row.get("match_weather_temp_c")),
                "match_weather_type": _s(row.get("match_weather_type")),
                "match_attendance": _i(row.get("match_attendance")),
                "venue_name": _s(row.get("venue_name")),
                "season": _i(row.get("season"), season),
                "player_id": player_id,
                "player_first_name": _s(row.get("player_first_name")),
                "player_last_name": _s(row.get("player_last_name")),
                "player_team": _team(row.get("player_team")),
                "guernsey_number": _i(row.get("guernsey_number")),
                "player_height_cm": _i(row.get("player_height_cm")),
                "player_weight_kg": _i(row.get("player_weight_kg")),
                "player_is_retired": _b(row.get("player_is_retired"), False),
                "kicks": _i(row.get("kicks")),
                "marks": _i(row.get("marks")),
                "handballs": _i(row.get("handballs")),
                "disposals": _i(row.get("disposals")),
                "effective_disposals": _i(row.get("effective_disposals")),
                "disposal_efficiency_percentage": _i(row.get("disposal_efficiency_percentage")),
                "goals": _i(row.get("goals")),
                "behinds": _i(row.get("behinds")),
                "hitouts": _i(row.get("hitouts")),
                "tackles": _i(row.get("tackles")),
                "rebounds": _i(row.get("rebounds")),
                "inside_fifties": _i(row.get("inside_fifties")),
                "clearances": _i(row.get("clearances")),
                "clangers": _i(row.get("clangers")),
                "free_kicks_for": _i(row.get("free_kicks_for")),
                "free_kicks_against": _i(row.get("free_kicks_against")),
                "brownlow_votes": _i(row.get("brownlow_votes")),
                "contested_possessions": _i(row.get("contested_possessions")),
                "uncontested_possessions": _i(row.get("uncontested_possessions")),
                "contested_marks": _i(row.get("contested_marks")),
                "marks_inside_fifty": _i(row.get("marks_inside_fifty")),
                "one_percenters": _i(row.get("one_percenters")),
                "bounces": _i(row.get("bounces")),
                "goal_assists": _i(row.get("goal_assists")),
                "time_on_ground_percentage": _i(row.get("time_on_ground_percentage")),
                "afl_fantasy_score": _i(row.get("afl_fantasy_score")),
                "supercoach_score": _i(row.get("supercoach_score")),
                "centre_clearances": _i(row.get("centre_clearances")),
                "stoppage_clearances": _i(row.get("stoppage_clearances")),
                "score_involvements": _i(row.get("score_involvements")),
                "metres_gained": _i(row.get("metres_gained")),
                "turnovers": _i(row.get("turnovers")),
                "intercepts": _i(row.get("intercepts")),
                "tackles_inside_fifty": _i(row.get("tackles_inside_fifty")),
                "contest_def_losses": _i(row.get("contest_def_losses")),
                "contest_def_one_on_ones": _i(row.get("contest_def_one_on_ones")),
                "contest_off_one_on_ones": _i(row.get("contest_off_one_on_ones")),
            })
            count += 1

    if is_2026_sync:
        logger.info(
            "2026 player_id resolution — trusted (Fryzigg): %s | "
            "historical override (collision fixed): %s | "
            "new debut id: %s | ambiguous fallback: %s | collision debut fallback: %s",
            _trusted_2026,
            _historical_2026,
            _debut_2026,
            _ambig_2026,
            _collision_debut_2026,
        )

    return count


def upsert_standings(db, standings: list[dict], year: int, round_number: int) -> int:
    """Upsert ladder standings."""
    if not standings:
        return 0

    sql = db.text("""
        INSERT INTO afl_standings (
            year, round, rank, team, teamid, pts, played,
            wins, losses, draws, for_score, against_score, percentage
        )
        VALUES (
            :year, :round, :rank, :team, :teamid, :pts, :played,
            :wins, :losses, :draws, :for_score, :against_score, :percentage
        )
        ON CONFLICT (year, round, teamid) DO UPDATE SET
            rank         = EXCLUDED.rank,
            team         = EXCLUDED.team,
            pts          = EXCLUDED.pts,
            played       = EXCLUDED.played,
            wins         = EXCLUDED.wins,
            losses       = EXCLUDED.losses,
            draws        = EXCLUDED.draws,
            for_score    = EXCLUDED.for_score,
            against_score= EXCLUDED.against_score,
            percentage   = EXCLUDED.percentage,
            last_updated = NOW()
    """)

    count = 0
    with db.engine.begin() as conn:
        for standing in standings:
            conn.execute(sql, {
                "year": year,
                "round": round_number,
                "rank": _i(standing.get("rank")),
                "team": _team(standing.get("name", standing.get("team", ""))),
                "teamid": _i(standing.get("id", standing.get("teamid"))),
                "pts": _i(standing.get("pts")),
                "played": _i(standing.get("played")),
                "wins": _i(standing.get("wins")),
                "losses": _i(standing.get("losses")),
                "draws": _i(standing.get("draws")),
                "for_score": _i(standing.get("for")),
                "against_score": _i(standing.get("against")),
                "percentage": standing.get("percentage", 0),
            })
            count += 1

    return count


def upsert_player_props(db, props: list[dict]) -> int:
    """Upsert live prop odds from The Odds API."""
    if not props:
        return 0

    sql = db.text("""
        INSERT INTO afl_player_props (
            event_id, home_team, away_team, commence_time,
            bookmaker, market, player_name, line_type, line, odds, fetched_at
        )
        VALUES (
            :event_id, :home_team, :away_team, :commence_time,
            :bookmaker, :market, :player_name, :line_type, :line, :odds, NOW()
        )
        ON CONFLICT (event_id, bookmaker, market, player_name, line_type, line) DO UPDATE SET
            home_team     = EXCLUDED.home_team,
            away_team     = EXCLUDED.away_team,
            commence_time = EXCLUDED.commence_time,
            odds          = EXCLUDED.odds,
            fetched_at    = NOW()
    """)

    count = 0
    with db.engine.begin() as conn:
        for prop in props:
            conn.execute(sql, {
                "event_id": _s(prop.get("event_id")),
                "home_team": _team(prop.get("home_team")),
                "away_team": _team(prop.get("away_team")),
                "commence_time": prop.get("commence_time"),
                "bookmaker": _s(prop.get("bookmaker")),
                "market": _s(prop.get("market")),
                "player_name": _s(prop.get("player_name", prop.get("player"))),
                "line_type": _s(prop.get("line_type", prop.get("selection_name"))),
                "line": prop.get("line", 0),
                "odds": prop.get("odds", 0),
            })
            count += 1

    return count


def log_sync(
    db,
    source: str,
    season: int = None,
    round_num: int = None,
    rows: int = 0,
    status: str = "ok",
    error: str = None,
):
    """Log a sync run to afl_sync_log."""
    sql = db.text("""
        INSERT INTO afl_sync_log (source, season, round, rows_synced, status, error_msg)
        VALUES (:source, :season, :round, :rows, :status, :error)
    """)

    with db.engine.begin() as conn:
        conn.execute(sql, {
            "source": source,
            "season": season,
            "round": round_num,
            "rows": rows,
            "status": status,
            "error": error,
        })
