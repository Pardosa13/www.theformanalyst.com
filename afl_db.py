"""
afl_db.py
=========
PostgreSQL schema + helper functions for the AFL section.
Run init_afl_tables(db) once to create all tables.
"""

from __future__ import annotations

import logging
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
        UNIQUE(event_id, bookmaker, market, player_name, line_type)
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
        "West Coast Eagles": "West Coast",
        "Greater Western Sydney": "GWS Giants",
        "GWS": "GWS Giants",
        "Footscray": "Western Bulldogs",
        "Brisbane": "Brisbane Lions",
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

    count = 0
    with db.engine.begin() as conn:
        for row in stats:
            match_id = _match_id(row.get("match_id"))

            # FIX: ensure player_id always exists (needed for 2026 fallback data)
            player_id = _i(row.get("player_id"))
            if not player_id:
                player_id = abs(hash(
                    f"{row.get('player_first_name')}_{row.get('player_last_name')}_{row.get('player_team')}"
                )) % 10_000_000

# still skip if match_id is invalid
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
        ON CONFLICT (event_id, bookmaker, market, player_name, line_type) DO UPDATE SET
            home_team   = EXCLUDED.home_team,
            away_team   = EXCLUDED.away_team,
            commence_time = EXCLUDED.commence_time,
            line        = EXCLUDED.line,
            odds        = EXCLUDED.odds,
            fetched_at  = NOW()
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
                "player_name": _s(prop.get("player")),
                "line_type": _s(prop.get("name")),
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
