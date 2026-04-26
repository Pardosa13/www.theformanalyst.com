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
    """
    CREATE TABLE IF NOT EXISTS afl_team_logos (
        squiggle_id INTEGER PRIMARY KEY,
        team_name   TEXT NOT NULL,
        abbrev      TEXT,
        logo_url    TEXT,
        updated_at  TIMESTAMP DEFAULT NOW()
    )
    """,
]

# Columns added after initial schema deployment — run as migrations on startup.
AFL_MIGRATIONS = [
    "ALTER TABLE afl_player_stats ADD COLUMN IF NOT EXISTS player_headshot_url TEXT",
]

# Stable advisory-lock key derived from a namespace string.
# Fits in PostgreSQL BIGINT and is consistent across all workers/restarts.
_AFL_MIGRATION_LOCK_KEY = int(hashlib.md5(b"afl_migrations").hexdigest()[:16], 16) % (2 ** 63)


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


def _headshot_url(player_id: int | None) -> str | None:
    """Return AFL.com.au CDN headshot URL for positive (Fryzigg/ChampID) player IDs.

    Returns None for negative or zero IDs (debut placeholders) so that the UI
    can gracefully fall back to initials.
    """
    if not player_id or player_id <= 0:
        return None
    return (
        f"https://www.afl.com.au/staticfile/AFL%20Tenant/AFL/Players/"
        f"ChampIDImages/{player_id}.png"
    )


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


def _build_historical_id_map(db) -> tuple[dict, set, dict, dict]:
    """
    Query existing 2019–2025 player stats and return four lookups for 2026 ID resolution.

    Returns:
        unambiguous_map:  {(norm_first, norm_last, norm_team): player_id}
            Maps a normalised (name, team) key to the single historical Fryzigg
            player_id.  Used as a tie-breaker when a player name is ambiguous.

        ambiguous_keys:   set of (norm_first, norm_last, norm_team) tuples that
            mapped to more than one historical player_id (very rare).

        id_to_name_keys:  {player_id: set of (norm_first, norm_last, norm_team)}
            Inverse map — kept for collision detection.

        name_to_ids:      {(norm_first, norm_last): set of player_id}
            Name-only map — if exactly one player_id exists for a name across
            all clubs and seasons, that id is reused regardless of current club.
            Used to correctly handle players traded between seasons.
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
        return {}, set(), {}, {}

    mapping: dict = {}
    ambiguous: set = set()
    id_to_name_keys: dict = defaultdict(set)
    name_to_ids: dict = defaultdict(set)

    for key, ids in key_to_ids.items():
        first, last, _ = key
        if len(ids) == 1:
            pid = next(iter(ids))
            mapping[key] = pid
            id_to_name_keys[pid].add(key)
        else:
            ambiguous.add(key)
        for pid in ids:
            name_to_ids[(first, last)].add(pid)

    logger.info(
        "Historical player_id map: %s name keys (%s with team), %s ambiguous (seasons 2019–2025)",
        len(name_to_ids),
        len(mapping),
        len(ambiguous),
    )
    return mapping, ambiguous, dict(id_to_name_keys), dict(name_to_ids)


def _resolve_2026_player_id(
    row: dict,
    name_to_ids: dict,
    hist_map: dict,
    id_to_name_keys: dict,
) -> tuple[int, str]:
    """
    Determine the correct player_id for a 2026 stat row.

    Resolution order (club is NOT required to map a player to their historical id):

    1. Normalise (first_name, last_name) into a name key.
    2. Look up name_to_ids[(first, last)] — all historical player_ids for that name:
       - Exactly ONE distinct player_id → reuse it regardless of current club.
         → "reused_by_name"
       - More than one distinct player_id (ambiguous name) → use (first, last, team)
         from hist_map as a tie-breaker:
           - Unambiguous match → "reused_by_name_team"
           - Still unresolvable → generate stable negative debut id → "ambiguous"
       - Zero matches (no history for this name) → generate stable debut id
         → "debut_generated"

    Returns (player_id, resolution_type).
    """
    first = _normalise_name(row.get("player_first_name"))
    last  = _normalise_name(row.get("player_last_name"))
    team  = _normalise_name(_team(row.get("player_team")))

    name_key      = (first, last)
    name_team_key = (first, last, team)

    candidate_ids = name_to_ids.get(name_key, set())

    if len(candidate_ids) == 1:
        # Unique name match — reuse historical Fryzigg id regardless of club.
        return next(iter(candidate_ids)), "reused_by_name"

    if len(candidate_ids) > 1:
        # Ambiguous name: try team as tie-breaker.
        hist_pid = hist_map.get(name_team_key)
        if hist_pid is not None:
            return hist_pid, "reused_by_name_team"
        # Could not resolve even with team — fall back to stable debut id.
        return _stable_debut_id(first, last, team), "ambiguous"

    # No historical record for this name → genuine debut player.
    return _stable_debut_id(first, last, team), "debut_generated"


# ─────────────────────────────────────────────
# SCHEMA INIT
# ─────────────────────────────────────────────

def init_afl_tables(db):
    """Create all AFL tables and run pending migrations.

    Schema creation (CREATE TABLE IF NOT EXISTS) is safe to run concurrently
    because Postgres handles those idempotently without exclusive table locks.

    DDL *migrations* (ALTER TABLE ADD COLUMN …) take an AccessExclusiveLock and
    will deadlock when multiple Gunicorn workers attempt them simultaneously.
    We guard them with a PostgreSQL transaction-level advisory lock so that only
    one worker ever runs the migration statements; any other worker that cannot
    acquire the lock assumes the first worker is handling it and skips.
    """
    try:
        with db.engine.begin() as conn:
            for statement in AFL_SCHEMA_STATEMENTS:
                conn.execute(db.text(statement))
    except Exception as exc:
        logger.error("Failed to create AFL schema: %s", exc)
        raise

    # Advisory-lock-guarded migrations — only one worker runs DDL at a time.
    try:
        with db.engine.begin() as conn:
            acquired = conn.execute(
                db.text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": _AFL_MIGRATION_LOCK_KEY},
            ).scalar()
            if acquired:
                for migration in AFL_MIGRATIONS:
                    conn.execute(db.text(migration))
                logger.debug("AFL migrations applied under advisory lock")
            else:
                logger.info(
                    "AFL migrations skipped — another worker holds the advisory lock"
                )
    except Exception as exc:
        logger.warning("AFL migration lock/run error (non-fatal): %s", exc)

    logger.info("AFL tables initialised")


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
      - (first, last) matched against 2019–2025 history → reuse historical id
        regardless of current club (handles trades between seasons).
      - If the name is ambiguous, (first, last, team) is used as a tie-breaker.
      - No match (true debut) or still ambiguous → stable negative BIGINT via
        _stable_debut_id().
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
            contest_def_losses, contest_def_one_on_ones, contest_off_one_on_ones,
            player_headshot_url
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
            :contest_def_losses, :contest_def_one_on_ones, :contest_off_one_on_ones,
            :player_headshot_url
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
            contest_off_one_on_ones         = EXCLUDED.contest_off_one_on_ones,
            player_headshot_url             = EXCLUDED.player_headshot_url
    """)

    # For season 2026, build the historical player_id lookup once before the main loop.
    # name_to_ids maps (norm_first, norm_last) → set(player_id) so that traded players
    # (club changed between 2025 and 2026) still inherit the correct Fryzigg player_id.
    _hist_map_2026: dict = {}
    _ambiguous_keys_2026: set = set()
    _id_to_name_keys_2026: dict = {}
    _name_to_ids_2026: dict = {}
    _reused_by_name_2026 = 0        # name uniquely identified historical player
    _reused_by_name_team_2026 = 0   # ambiguous name resolved via team tie-breaker
    _debut_generated_2026 = 0       # no historical match → stable debut id
    _ambiguous_2026 = 0             # ambiguous name + no team tie-breaker → stable debut id
    is_2026_sync = (season == 2026)
    if is_2026_sync:
        _hist_map_2026, _ambiguous_keys_2026, _id_to_name_keys_2026, _name_to_ids_2026 = \
            _build_historical_id_map(db)

    count = 0
    with db.engine.begin() as conn:
        for row in stats:
            match_id = _match_id(row.get("match_id"))

            # ── Resolve player_id ──────────────────────────────────────────
            row_season = _i(row.get("season"), season)
            if row_season == 2026:
                player_id, resolution = _resolve_2026_player_id(
                    row, _name_to_ids_2026, _hist_map_2026, _id_to_name_keys_2026
                )
                if resolution == "reused_by_name":
                    _reused_by_name_2026 += 1
                elif resolution == "reused_by_name_team":
                    _reused_by_name_team_2026 += 1
                elif resolution == "ambiguous":
                    _ambiguous_2026 += 1
                    logger.warning(
                        "2026 player_id ambiguous for %s %s (%s) — assigned debut id %s",
                        row.get("player_first_name"),
                        row.get("player_last_name"),
                        row.get("player_team"),
                        player_id,
                    )
                else:
                    # "debut_generated"
                    _debut_generated_2026 += 1
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
                "player_headshot_url": _headshot_url(player_id),
            })
            count += 1

    if is_2026_sync:
        logger.info(
            "2026 player_id resolution — reused_by_name: %s | "
            "reused_by_name_team: %s | "
            "debut_generated: %s | ambiguous: %s",
            _reused_by_name_2026,
            _reused_by_name_team_2026,
            _debut_generated_2026,
            _ambiguous_2026,
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


# ─────────────────────────────────────────────
# TEAM LOGOS
# ─────────────────────────────────────────────

def upsert_team_logos(db, teams: list[dict]) -> int:
    """Upsert team logo rows from Squiggle ?q=teams response."""
    if not teams:
        return 0

    sql = db.text("""
        INSERT INTO afl_team_logos (squiggle_id, team_name, abbrev, logo_url, updated_at)
        VALUES (:squiggle_id, :team_name, :abbrev, :logo_url, NOW())
        ON CONFLICT (squiggle_id) DO UPDATE SET
            team_name  = EXCLUDED.team_name,
            abbrev     = EXCLUDED.abbrev,
            logo_url   = EXCLUDED.logo_url,
            updated_at = NOW()
    """)

    count = 0
    with db.engine.begin() as conn:
        for team in teams:
            tid = team.get("id")
            name = team.get("name", "")
            logo = team.get("logo") or team.get("logo_url") or ""
            abbrev = team.get("abbrev") or team.get("abbreviation") or ""
            if not tid or not name:
                continue
            conn.execute(sql, {
                "squiggle_id": tid,
                "team_name": name,
                "abbrev": abbrev,
                "logo_url": logo,
            })
            count += 1

    return count


def get_team_logo_map(db) -> dict:
    """Return {team_name_lower: logo_url} and {squiggle_id: logo_url} merged into one dict."""
    sql = db.text("SELECT squiggle_id, team_name, logo_url FROM afl_team_logos WHERE logo_url IS NOT NULL AND logo_url != ''")
    result = {}
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(sql).fetchall()
        for row in rows:
            sid, name, logo = row[0], row[1], row[2]
            if logo:
                result[name.lower()] = logo
                result[str(sid)] = logo
    except Exception as exc:
        logger.warning("Could not load team logo map: %s", exc)
    return result
