"""
afl_db.py
=========
PostgreSQL schema + helper functions for the AFL section.
Run init_afl_tables(db) once to create all tables.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────

AFL_SCHEMA_SQL = """
-- ── Squiggle games (fixtures + results) ──────────────────────────────
CREATE TABLE IF NOT EXISTS afl_games (
    id              INTEGER PRIMARY KEY,   -- Squiggle game ID
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
    complete        INTEGER DEFAULT 0,   -- 0=unplayed, 100=completed
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_afl_games_year_round ON afl_games(year, round);
CREATE INDEX IF NOT EXISTS idx_afl_games_teams      ON afl_games(hteam, ateam);

-- ── Squiggle tips/predictions ─────────────────────────────────────────
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
    source_id       INTEGER DEFAULT 8,   -- 8 = Squiggle Aggregate
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(gameid, source_id)
);

-- ── Squiggle ladder ──────────────────────────────────────────────────
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
);

-- ── Fryzigg player stats (one row per player per game) ───────────────
CREATE TABLE IF NOT EXISTS afl_player_stats (
    id                              SERIAL PRIMARY KEY,
    match_id                        INTEGER,
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

    -- Player identity
    player_id                       INTEGER,
    player_first_name               TEXT,
    player_last_name                TEXT,
    player_team                     TEXT,
    guernsey_number                 INTEGER,
    player_height_cm                INTEGER,
    player_weight_kg                INTEGER,
    player_is_retired               BOOLEAN DEFAULT FALSE,

    -- Core stats (from fitzRoy/Fryzigg 81-col schema)
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
);

CREATE INDEX IF NOT EXISTS idx_afl_ps_player    ON afl_player_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_afl_ps_team      ON afl_player_stats(player_team);
CREATE INDEX IF NOT EXISTS idx_afl_ps_date      ON afl_player_stats(match_date);
CREATE INDEX IF NOT EXISTS idx_afl_ps_season    ON afl_player_stats(season);
CREATE INDEX IF NOT EXISTS idx_afl_ps_name      ON afl_player_stats(player_last_name, player_first_name);

-- ── Live prop odds (from The Odds API) ───────────────────────────────
CREATE TABLE IF NOT EXISTS afl_player_props (
    id              SERIAL PRIMARY KEY,
    event_id        TEXT,
    home_team       TEXT,
    away_team       TEXT,
    commence_time   TIMESTAMP,
    bookmaker       TEXT,
    market          TEXT,    -- player_disposals, player_marks, etc.
    player_name     TEXT,
    line_type       TEXT,    -- Over / Under
    line            FLOAT,
    odds            FLOAT,
    fetched_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(event_id, bookmaker, market, player_name, line_type)
);

CREATE INDEX IF NOT EXISTS idx_afl_props_player ON afl_player_props(player_name);
CREATE INDEX IF NOT EXISTS idx_afl_props_event  ON afl_player_props(event_id);

-- ── Sync log ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS afl_sync_log (
    id          SERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    season      INTEGER,
    round       INTEGER,
    rows_synced INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'ok',
    error_msg   TEXT,
    synced_at   TIMESTAMP DEFAULT NOW()
);
"""


def init_afl_tables(db):
    """Create all AFL tables. Safe to call multiple times (IF NOT EXISTS)."""
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text(AFL_SCHEMA_SQL))
            conn.commit()
        logger.info("AFL tables initialised")
    except Exception as e:
        logger.error(f"Failed to init AFL tables: {e}")
        raise


# ─────────────────────────────────────────────
# DB WRITE HELPERS
# ─────────────────────────────────────────────

def upsert_games(db, games: list[dict]) -> int:
    """Upsert Squiggle games. Returns count inserted/updated."""
    if not games:
        return 0
    count = 0
    sql = db.text("""
        INSERT INTO afl_games
            (id, year, round, roundname, date, "localtime", venue,
             hteam, ateam, hteamid, ateamid,
             hscore, ascore, hgoals, hbehinds, agoals, abehinds,
             margin, winner, winnerteamid, is_final, complete, updated_at)
        VALUES
            (:id, :year, :round, :roundname, :date, :localtime, :venue,
             :hteam, :ateam, :hteamid, :ateamid,
             :hscore, :ascore, :hgoals, :hbehinds, :agoals, :abehinds,
             :margin, :winner, :winnerteamid, :is_final, :complete, NOW())
        ON CONFLICT (id) DO UPDATE SET
            hscore       = EXCLUDED.hscore,
            ascore       = EXCLUDED.ascore,
            hgoals       = EXCLUDED.hgoals,
            hbehinds     = EXCLUDED.hbehinds,
            agoals       = EXCLUDED.agoals,
            abehinds     = EXCLUDED.abehinds,
            margin       = EXCLUDED.margin,
            winner       = EXCLUDED.winner,
            winnerteamid = EXCLUDED.winnerteamid,
            complete     = EXCLUDED.complete,
            updated_at   = NOW()
    """)
    with db.engine.begin() as conn:
        for g in games:
            conn.execute(sql, {
                "id":          g.get("id"),
                "year":        g.get("year"),
                "round":       g.get("round"),
                "roundname":   g.get("roundname", ""),
                "date":        g.get("date"),
                "localtime":   g.get("localtime", ""),
                "venue":       g.get("venue", ""),
                "hteam":       g.get("hteam", ""),
                "ateam":       g.get("ateam", ""),
                "hteamid":     g.get("hteamid"),
                "ateamid":     g.get("ateamid"),
                "hscore":      g.get("hscore"),
                "ascore":      g.get("ascore"),
                "hgoals":      g.get("hgoals"),
                "hbehinds":    g.get("hbehinds"),
                "agoals":      g.get("agoals"),
                "abehinds":    g.get("abehinds"),
                "margin":      g.get("margin"),
                "winner":      g.get("winner", ""),
                "winnerteamid":g.get("winnerteamid"),
                "is_final":    g.get("is_final", 0),
                "complete":    g.get("complete", 0),
            })
            count += 1
    return count


def upsert_player_stats(db, stats: list[dict], season: int) -> int:
    """Upsert Fryzigg player stats. Returns count inserted/updated."""
    if not stats:
        return 0
    count = 0
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
            intercepts, tackles_inside_fifty
        ) VALUES (
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
            :intercepts, :tackles_inside_fifty
        )
        ON CONFLICT (match_id, player_id) DO UPDATE SET
            disposals                      = EXCLUDED.disposals,
            effective_disposals            = EXCLUDED.effective_disposals,
            disposal_efficiency_percentage = EXCLUDED.disposal_efficiency_percentage,
            kicks                          = EXCLUDED.kicks,
            marks                          = EXCLUDED.marks,
            handballs                      = EXCLUDED.handballs,
            goals                          = EXCLUDED.goals,
            tackles                        = EXCLUDED.tackles,
            afl_fantasy_score              = EXCLUDED.afl_fantasy_score,
            supercoach_score               = EXCLUDED.supercoach_score
    """)

    def _i(row, key, default=0):
    v = row.get(key)
    if v is None:
        return default
    try:
        return int(v)
    except Exception:
        return default

def _match_id(row, key="match_id", default=0):
    v = row.get(key)
    if v is None:
        return default

    s = str(v).strip()
    digits = "".join(ch for ch in s if ch.isdigit())

    if not digits:
        return default

    try:
        return int(digits)
    except Exception:
        return default

    with db.engine.begin() as conn:
        for row in stats:
            conn.execute(sql, {
                "match_id":                        _i(row, "match_id"),
                "match_date":                      row.get("match_date"),
                "match_round":                     str(row.get("match_round", "")),
                "match_home_team":                 row.get("match_home_team", ""),
                "match_away_team":                 row.get("match_away_team", ""),
                "match_home_team_score":            _i(row, "match_home_team_score"),
                "match_away_team_score":            _i(row, "match_away_team_score"),
                "match_margin":                    _i(row, "match_margin"),
                "match_winner":                    row.get("match_winner", ""),
                "match_weather_temp_c":             _i(row, "match_weather_temp_c"),
                "match_weather_type":              row.get("match_weather_type", ""),
                "match_attendance":                _i(row, "match_attendance"),
                "venue_name":                      row.get("venue_name", ""),
                "season":                          season,
                "player_id":                       _i(row, "player_id"),
                "player_first_name":               row.get("player_first_name", ""),
                "player_last_name":                row.get("player_last_name", ""),
                "player_team":                     row.get("player_team", ""),
                "guernsey_number":                 _i(row, "guernsey_number"),
                "player_height_cm":                _i(row, "player_height_cm"),
                "player_weight_kg":                _i(row, "player_weight_kg"),
                "player_is_retired":               bool(row.get("player_is_retired", False)),
                "kicks":                           _i(row, "kicks"),
                "marks":                           _i(row, "marks"),
                "handballs":                       _i(row, "handballs"),
                "disposals":                       _i(row, "disposals"),
                "effective_disposals":             _i(row, "effective_disposals"),
                "disposal_efficiency_percentage":  _i(row, "disposal_efficiency_percentage"),
                "goals":                           _i(row, "goals"),
                "behinds":                         _i(row, "behinds"),
                "hitouts":                         _i(row, "hitouts"),
                "tackles":                         _i(row, "tackles"),
                "rebounds":                        _i(row, "rebounds"),
                "inside_fifties":                  _i(row, "inside_fifties"),
                "clearances":                      _i(row, "clearances"),
                "clangers":                        _i(row, "clangers"),
                "free_kicks_for":                  _i(row, "free_kicks_for"),
                "free_kicks_against":              _i(row, "free_kicks_against"),
                "brownlow_votes":                  _i(row, "brownlow_votes"),
                "contested_possessions":           _i(row, "contested_possessions"),
                "uncontested_possessions":         _i(row, "uncontested_possessions"),
                "contested_marks":                 _i(row, "contested_marks"),
                "marks_inside_fifty":              _i(row, "marks_inside_fifty"),
                "one_percenters":                  _i(row, "one_percenters"),
                "bounces":                         _i(row, "bounces"),
                "goal_assists":                    _i(row, "goal_assists"),
                "time_on_ground_percentage":       _i(row, "time_on_ground_percentage"),
                "afl_fantasy_score":               _i(row, "afl_fantasy_score"),
                "supercoach_score":                _i(row, "supercoach_score"),
                "centre_clearances":               _i(row, "centre_clearances"),
                "stoppage_clearances":             _i(row, "stoppage_clearances"),
                "score_involvements":              _i(row, "score_involvements"),
                "metres_gained":                   _i(row, "metres_gained"),
                "turnovers":                       _i(row, "turnovers"),
                "intercepts":                      _i(row, "intercepts"),
                "tackles_inside_fifty":            _i(row, "tackles_inside_fifty"),
            })
            count += 1
    return count


def upsert_standings(db, standings: list[dict], year: int, round_number: int) -> int:
    """Upsert ladder standings."""
    if not standings:
        return 0
    sql = db.text("""
        INSERT INTO afl_standings
            (year, round, rank, team, teamid, pts, played,
             wins, losses, draws, for_score, against_score, percentage)
        VALUES
            (:year, :round, :rank, :team, :teamid, :pts, :played,
             :wins, :losses, :draws, :for_score, :against_score, :percentage)
        ON CONFLICT (year, round, teamid) DO UPDATE SET
            rank       = EXCLUDED.rank,
            pts        = EXCLUDED.pts,
            played     = EXCLUDED.played,
            wins       = EXCLUDED.wins,
            losses     = EXCLUDED.losses,
            percentage = EXCLUDED.percentage,
            last_updated = NOW()
    """)
    count = 0
    with db.engine.begin() as conn:
        for s in standings:
            conn.execute(sql, {
                "year":        year,
                "round":       round_number,
                "rank":        s.get("rank"),
                "team":        s.get("name", s.get("team", "")),
                "teamid":      s.get("id",   s.get("teamid")),
                "pts":         s.get("pts",  0),
                "played":      s.get("played", 0),
                "wins":        s.get("wins",   0),
                "losses":      s.get("losses", 0),
                "draws":       s.get("draws",  0),
                "for_score":   s.get("for",    0),
                "against_score": s.get("against", 0),
                "percentage":  s.get("percentage", 0),
            })
            count += 1
    return count


def upsert_player_props(db, props: list[dict]) -> int:
    """Upsert live prop odds from The Odds API."""
    if not props:
        return 0
    sql = db.text("""
        INSERT INTO afl_player_props
            (event_id, home_team, away_team, commence_time,
             bookmaker, market, player_name, line_type, line, odds, fetched_at)
        VALUES
            (:event_id, :home_team, :away_team, :commence_time,
             :bookmaker, :market, :player_name, :line_type, :line, :odds, NOW())
        ON CONFLICT (event_id, bookmaker, market, player_name, line_type) DO UPDATE SET
            line       = EXCLUDED.line,
            odds       = EXCLUDED.odds,
            fetched_at = NOW()
    """)
    count = 0
    with db.engine.begin() as conn:
        for p in props:
            conn.execute(sql, {
                "event_id":      p.get("event_id", ""),
                "home_team":     p.get("home_team", ""),
                "away_team":     p.get("away_team", ""),
                "commence_time": p.get("commence_time"),
                "bookmaker":     p.get("bookmaker", ""),
                "market":        p.get("market", ""),
                "player_name":   p.get("player", ""),
                "line_type":     p.get("name", ""),
                "line":          p.get("line", 0),
                "odds":          p.get("odds", 0),
            })
            count += 1
    return count


def log_sync(db, source: str, season: int = None, round_num: int = None,
             rows: int = 0, status: str = "ok", error: str = None):
    """Log a sync run to afl_sync_log."""
    sql = db.text("""
        INSERT INTO afl_sync_log (source, season, round, rows_synced, status, error_msg)
        VALUES (:source, :season, :round, :rows, :status, :error)
    """)
    with db.engine.begin() as conn:
        conn.execute(sql, {
            "source": source, "season": season, "round": round_num,
            "rows": rows, "status": status, "error": error
        })
