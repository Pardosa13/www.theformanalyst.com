"""
afl_routes.py
=============
Flask routes for the AFL section of The Form Analyst.
Add to app.py with:
    from afl_routes import register_afl_routes
    register_afl_routes(app, db)

Or copy the route functions directly into app.py if you prefer.
"""

from flask import render_template, jsonify, request, current_app
from flask_login import login_required
from datetime import datetime
import logging

from afl_data import (
    fetch_squiggle_games,
    fetch_squiggle_standings,
    fetch_squiggle_tips,
    fetch_squiggle_current_round,
    fetch_squiggle_upcoming_games,
    fetch_fryzigg_player_stats,
    fetch_afl_player_props,
    get_player_season_averages,
    get_player_vs_opponent,
    get_player_last_n_games,
    calculate_disposal_edge,
    CURRENT_YEAR,
)
from afl_db import (
    upsert_games,
    upsert_player_stats,
    upsert_standings,
    upsert_player_props,
    log_sync,
)

logger = logging.getLogger(__name__)


def register_afl_routes(app, db):
    """Register all AFL routes onto the Flask app."""

    # ─────────────────────────────────────────────
    # MAIN PAGE
    # ─────────────────────────────────────────────

    @app.route("/afl")
    @login_required
    def afl_hub():
        """Main AFL Hub page."""
        year = request.args.get("year", CURRENT_YEAR, type=int)

        # Current round from Squiggle
        current_round = _db_current_round(db, year) or fetch_squiggle_current_round(year)

        # Next upcoming game
        next_game = _db_next_game(db, year)

        # Stat counts
        total_players = _db_count(db, "SELECT COUNT(DISTINCT player_id) FROM afl_player_stats")
        total_games   = _db_count(db, "SELECT COUNT(*) FROM afl_games")
        value_bets    = _db_count(db,
            "SELECT COUNT(DISTINCT player_name) FROM afl_player_props WHERE fetched_at > NOW() - INTERVAL '24 hours'"
        )

        # Data source status
        sources = _check_data_sources(db)

        fixtures = _db_get_fixtures(db, year=year) or fetch_squiggle_upcoming_games(year) or []
        standings = _db_get_standings(db, year=year)
        if not standings:
            raw_standings = fetch_squiggle_standings(year, current_round)
            if raw_standings:
                upsert_standings(db, raw_standings, year, current_round)
                standings = _db_get_standings(db, year=year)

        return render_template(
            "afl.html",
            current_round=current_round,
            year=year,
            total_players=f"{total_players:,}" if total_players else "5,700+",
            total_games=f"{total_games:,}" if total_games else "15K+",
            value_bets_today=value_bets or 0,
            next_game_time=next_game.get("time", "—") if next_game else "—",
            next_game_teams=next_game.get("teams", "—") if next_game else "—",
            data_sources=sources,
            fixtures=fixtures,
            standings=standings,
        )


    # ─────────────────────────────────────────────
    # API: PLAYER STATS
    # ─────────────────────────────────────────────

    @app.route("/api/afl/player-stats")
    @login_required
    def api_afl_player_stats():
        """
        Search player stats.
        Query params: name, team, season, stat (disposals/marks/etc), limit
        """
        name   = request.args.get("name", "").strip()
        team   = request.args.get("team", "").strip()
        season = request.args.get("season", CURRENT_YEAR, type=int)
        stat   = request.args.get("stat", "disposals")
        limit  = request.args.get("limit", 20, type=int)

        if not name and not team:
            return jsonify({"error": "Provide name or team parameter"}), 400

        rows = _db_player_search(db, name=name, team=team, season=season, limit=limit)
        if not rows:
            return jsonify({"players": [], "message": "No players found"})

        # Group by player_id
        players = {}
        for row in rows:
            pid = row["player_id"]
            if pid not in players:
                players[pid] = {
                    "player_id":         pid,
                    "name":              f"{row['player_first_name']} {row['player_last_name']}",
                    "first_name":        row["player_first_name"],
                    "last_name":         row["player_last_name"],
                    "team":              row["player_team"],
                    "guernsey":          row["guernsey_number"],
                    "height_cm":         row["player_height_cm"],
                    "weight_kg":         row["player_weight_kg"],
                    "games":             [],
                }
            players[pid]["games"].append(dict(row))

        # Calculate averages + last 5 for each player
        result = []
        for pid, p in players.items():
            games = p["games"]
            avgs  = get_player_season_averages(games)
            last5 = get_player_last_n_games(games, 5)

            # Trend: compare last 3 avg vs season avg for primary stat
            last3_vals = [g.get(stat, 0) or 0 for g in last5[:3]]
            last3_avg  = sum(last3_vals) / len(last3_vals) if last3_vals else 0
            season_avg = avgs.get(stat, 0)
            trend_diff = round(last3_avg - season_avg, 1)

            result.append({
                **{k: v for k, v in p.items() if k != "games"},
                "season":       season,
                "games_played": len(games),
                "averages":     avgs,
                "trend":        {
                    "stat":      stat,
                    "direction": "up" if trend_diff > 0.5 else "down" if trend_diff < -0.5 else "flat",
                    "diff":      trend_diff,
                },
                "last_5": [
                    {
                        "date":      g.get("match_date", ""),
                        "round":     g.get("match_round", ""),
                        "opponent":  _get_opponent(g, p["team"]),
                        "venue":     g.get("venue_name", ""),
                        "result":    "W" if g.get("match_winner") == p["team"] else "L",
                        "disposals": g.get("disposals", 0),
                        "marks":     g.get("marks", 0),
                        "kicks":     g.get("kicks", 0),
                        "handballs": g.get("handballs", 0),
                        "tackles":   g.get("tackles", 0),
                        "goals":     g.get("goals", 0),
                        "fantasy":   g.get("afl_fantasy_score", 0),
                    }
                    for g in last5
                ],
            })

        return jsonify({"players": result, "season": season, "stat": stat})


    @app.route("/api/afl/player-vs-opponent")
    @login_required
    def api_afl_player_vs_opponent():
        """
        Get a player's historical stats vs a specific opponent team.
        Query params: player_id OR name, opponent, season_from
        """
        player_id   = request.args.get("player_id", type=int)
        name        = request.args.get("name", "").strip()
        opponent    = request.args.get("opponent", "").strip()
        season_from = request.args.get("season_from", CURRENT_YEAR - 3, type=int)

        if not opponent:
            return jsonify({"error": "opponent parameter required"}), 400

        rows = _db_player_vs_opponent(db, player_id=player_id, name=name,
                                      opponent=opponent, season_from=season_from)

        if not rows:
            return jsonify({"games": 0, "opponent": opponent, "averages": {}, "hit_rates": {}})

        result = get_player_vs_opponent(rows, opponent)

        # Add game-by-game log
        result["game_log"] = [
            {
                "date":      g.get("match_date", ""),
                "season":    g.get("season"),
                "round":     g.get("match_round", ""),
                "venue":     g.get("venue_name", ""),
                "disposals": g.get("disposals", 0),
                "marks":     g.get("marks", 0),
                "kicks":     g.get("kicks", 0),
                "goals":     g.get("goals", 0),
                "tackles":   g.get("tackles", 0),
                "result":    "W" if g.get("match_winner") == g.get("player_team") else "L",
            }
            for g in result.get("last_5", [])
        ]
        result.pop("last_5", None)

        return jsonify({
            "opponent":    opponent,
            "season_from": season_from,
            **result
        })


    # ─────────────────────────────────────────────
    # API: FIXTURES & LADDER
    # ─────────────────────────────────────────────

    @app.route("/api/afl/fixtures")
    @login_required
    def api_afl_fixtures():
        """Upcoming fixtures with Squiggle tips."""
        year         = request.args.get("year", CURRENT_YEAR, type=int)
        round_number = request.args.get("round", type=int)

        games = _db_get_fixtures(db, year=year, round_number=round_number)

        if not games:
            # Fall back to live Squiggle fetch
            raw = fetch_squiggle_upcoming_games(year)
            if raw:
                upsert_games(db, raw)
                games = _db_get_fixtures(db, year=year, round_number=round_number)

        # Attach prop lines if available
        for g in games:
            g["props_available"] = _db_has_props(db, g.get("hteam"), g.get("ateam"))

        return jsonify({
            "year":   year,
            "round":  round_number,
            "games":  games,
            "count":  len(games),
        })


    @app.route("/api/afl/ladder")
    @login_required
    def api_afl_ladder():
        """Current AFL ladder from Squiggle."""
        year         = request.args.get("year", CURRENT_YEAR, type=int)
        round_number = request.args.get("round", type=int)

        standings = _db_get_standings(db, year=year, round_number=round_number)

        if not standings:
            current_round = fetch_squiggle_current_round(year)
            raw = fetch_squiggle_standings(year, current_round)
            if raw:
                upsert_standings(db, raw, year, current_round)
                standings = _db_get_standings(db, year=year)

        return jsonify({
            "year":      year,
            "standings": standings,
            "count":     len(standings),
        })


    # ─────────────────────────────────────────────
    # API: VALUE FINDER
    # ─────────────────────────────────────────────

    @app.route("/api/afl/value-finder")
    @login_required
    def api_afl_value_finder():
        """
        Compare book prop lines vs model predictions.
        Returns players where model edge >= min_edge (default 2.0).
        """
        market   = request.args.get("market", "player_disposals")
        min_edge = request.args.get("min_edge", 2.0, type=float)
        year     = request.args.get("year", CURRENT_YEAR, type=int)

        # Get live prop lines
        props = _db_get_props(db, market=market)

        if not props:
            return jsonify({"bets": [], "message": "No prop lines loaded. Configure The Odds API key."})

        value_bets = []
        for prop in props:
            if prop.get("line_type") != "Over":
                continue

            player_name = prop["player_name"]
            book_line   = prop["line"]

            # Look up this player's stats from DB
            player_rows = _db_player_search(db, name=player_name, season=year, limit=50)
            if not player_rows:
                continue

            # Season average
            season_avg = _safe_avg(player_rows, "disposals" if "disposal" in market else "marks")

            # vs opponent average
            opponent    = prop.get("away_team", "") or prop.get("home_team", "")
            opp_rows    = [r for r in player_rows if
                           r.get("match_home_team") == opponent or
                           r.get("match_away_team") == opponent]
            vs_opp_avg  = _safe_avg(opp_rows, "disposals") if opp_rows else None

            # Last 5 average
            last5       = sorted(player_rows, key=lambda x: x.get("match_date", ""), reverse=True)[:5]
            last5_avg   = _safe_avg(last5, "disposals") if last5 else None

            edge_data = calculate_disposal_edge(
                player_avg=season_avg,
                book_line=book_line,
                vs_opp_avg=vs_opp_avg,
                last5_avg=last5_avg,
            )

            if abs(edge_data["edge"]) >= min_edge or edge_data["recommendation"] == "value":
                # Historical hit rate for this line
                total  = len(player_rows)
                hits   = sum(1 for r in player_rows if (r.get("disposals", 0) or 0) >= book_line)
                hist_pct = round(hits / total * 100, 1) if total else 0

                value_bets.append({
                    "player":        player_name,
                    "team":          player_rows[0].get("player_team", "") if player_rows else "",
                    "opponent":      opponent,
                    "home_team":     prop.get("home_team", ""),
                    "away_team":     prop.get("away_team", ""),
                    "commence_time": str(prop.get("commence_time", "")),
                    "bookmaker":     prop.get("bookmaker", ""),
                    "market":        market,
                    "book_line":     book_line,
                    "over_odds":     prop.get("odds", 0),
                    "season_avg":    season_avg,
                    "vs_opp_avg":    vs_opp_avg,
                    "last5_avg":     last5_avg,
                    "hist_pct":      hist_pct,
                    **edge_data,
                })

        # Sort by absolute edge descending
        value_bets.sort(key=lambda x: abs(x.get("edge", 0)), reverse=True)

        return jsonify({
            "market":    market,
            "min_edge":  min_edge,
            "bets":      value_bets,
            "count":     len(value_bets),
        })


    # ─────────────────────────────────────────────
    # API: MATCH PROPS
    # ─────────────────────────────────────────────

    @app.route("/api/afl/match-props")
    @login_required
    def api_afl_match_props():
        """All prop lines for a specific matchup."""
        home_team = request.args.get("home", "").strip()
        away_team = request.args.get("away", "").strip()

        if not home_team or not away_team:
            return jsonify({"error": "home and away params required"}), 400

        props = _db_get_match_props(db, home_team, away_team)

        return jsonify({
            "home_team": home_team,
            "away_team": away_team,
            "props":     props,
            "count":     len(props),
        })


    # ─────────────────────────────────────────────
    # SYNC ENDPOINTS (called by cron or manually)
    # ─────────────────────────────────────────────

    @app.route("/api/afl/sync/fixtures", methods=["POST"])
    @login_required
    def api_afl_sync_fixtures():
        """Sync current season fixtures + results from Squiggle."""
        year  = request.json.get("year", CURRENT_YEAR) if request.json else CURRENT_YEAR
        round = request.json.get("round", None) if request.json else None

        try:
            games = fetch_squiggle_games(year, round)
            count = upsert_games(db, games)
            log_sync(db, "squiggle_games", season=year, round_num=round, rows=count)
            return jsonify({"status": "ok", "rows_synced": count, "year": year})
        except Exception as e:
            log_sync(db, "squiggle_games", season=year, status="error", error=str(e))
            return jsonify({"status": "error", "message": str(e)}), 500


    @app.route("/api/afl/sync/ladder", methods=["POST"])
    @login_required
    def api_afl_sync_ladder():
        """Sync AFL ladder from Squiggle."""
        year = request.json.get("year", CURRENT_YEAR) if request.json else CURRENT_YEAR

        try:
            current_round = fetch_squiggle_current_round(year)
            standings     = fetch_squiggle_standings(year, current_round)
            count         = upsert_standings(db, standings, year, current_round)
            log_sync(db, "squiggle_standings", season=year, round_num=current_round, rows=count)
            return jsonify({"status": "ok", "rows_synced": count, "round": current_round})
        except Exception as e:
            log_sync(db, "squiggle_standings", season=year, status="error", error=str(e))
            return jsonify({"status": "error", "message": str(e)}), 500


    @app.route("/api/afl/sync/player-stats", methods=["POST"])
    @login_required
    def api_afl_sync_player_stats():
        """Sync player stats from Fryzigg for a given season."""
        season = request.json.get("season", CURRENT_YEAR) if request.json else CURRENT_YEAR

        try:
            stats = fetch_fryzigg_player_stats(season)
            count = upsert_player_stats(db, stats, season)
            log_sync(db, "fryzigg", season=season, rows=count)
            return jsonify({"status": "ok", "rows_synced": count, "season": season})
        except Exception as e:
            log_sync(db, "fryzigg", season=season, status="error", error=str(e))
            return jsonify({"status": "error", "message": str(e)}), 500


    @app.route("/api/afl/sync/props", methods=["POST"])
    @login_required
    def api_afl_sync_props():
        """Sync live prop lines from The Odds API."""
        api_key = current_app.config.get("ODDS_API_KEY", "")
        market  = request.json.get("market", "player_disposals") if request.json else "player_disposals"

        if not api_key:
            return jsonify({"status": "error", "message": "ODDS_API_KEY not configured"}), 400

        try:
            props = fetch_afl_player_props(api_key, market)
            count = upsert_player_props(db, props)
            log_sync(db, "odds_api", rows=count)
            return jsonify({"status": "ok", "rows_synced": count, "market": market})
        except Exception as e:
            log_sync(db, "odds_api", status="error", error=str(e))
            return jsonify({"status": "error", "message": str(e)}), 500


    @app.route("/api/afl/refresh", methods=["POST"])
    @login_required
    def api_afl_refresh():
        """Quick refresh: sync fixtures + ladder only (no heavy player stats)."""
        year = CURRENT_YEAR
        results = {}

        try:
            games = fetch_squiggle_games(year)
            results["fixtures"] = upsert_games(db, games)
        except Exception as e:
            results["fixtures_error"] = str(e)

        try:
            rnd       = fetch_squiggle_current_round(year)
            standings = fetch_squiggle_standings(year, rnd)
            results["ladder"] = upsert_standings(db, standings, year, rnd)
        except Exception as e:
            results["ladder_error"] = str(e)

        return jsonify({"status": "ok", "synced": results})


# ─────────────────────────────────────────────
# PRIVATE DB QUERY HELPERS
# ─────────────────────────────────────────────

def _db_current_round(db, year: int) -> int:
    """Get current round from DB (latest round with complete=100 games)."""
    sql = db.text("""
        SELECT COALESCE(MAX(round), 1)
        FROM afl_games
        WHERE year = :year AND complete = 100
    """)
    with db.engine.connect() as conn:
        result = conn.execute(sql, {"year": year}).scalar()
    return result or 1


def _db_next_game(db, year: int) -> dict:
    """Get next upcoming game."""
    sql = db.text("""
        SELECT hteam, ateam, date, venue
        FROM afl_games
        WHERE year = :year AND complete < 100
        ORDER BY date ASC
        LIMIT 1
    """)
    with db.engine.connect() as conn:
        row = conn.execute(sql, {"year": year}).mappings().fetchone()
    if not row:
        return {}
    dt = row["date"]
    time_str = dt.strftime("%-I:%M%p") if dt else "—"
    return {
        "time":  time_str,
        "teams": f"{row['hteam']} vs {row['ateam']}",
        "venue": row["venue"],
    }


def _db_count(db, sql_str: str) -> int:
    """Execute a COUNT query and return the integer result."""
    try:
        with db.engine.connect() as conn:
            return conn.execute(db.text(sql_str)).scalar() or 0
    except Exception:
        return 0


def _db_player_search(db, name: str = "", team: str = "",
                      season: int = None, limit: int = 50) -> list[dict]:
    """Search player stats from DB."""
    conditions = []
    params = {"limit": limit}

    if name:
        conditions.append(
            "(LOWER(player_first_name || ' ' || player_last_name) LIKE :name "
            " OR LOWER(player_last_name) LIKE :name_last)"
        )
        params["name"]      = f"%{name.lower()}%"
        params["name_last"] = f"%{name.split()[-1].lower()}%"

    if team:
        conditions.append("LOWER(player_team) LIKE :team")
        params["team"] = f"%{team.lower()}%"

    if season:
        conditions.append("season = :season")
        params["season"] = season

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    sql = db.text(f"""
        SELECT * FROM afl_player_stats
        {where}
        ORDER BY match_date DESC
        LIMIT :limit
    """)
    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().fetchall()
    return [dict(r) for r in rows]


def _db_player_vs_opponent(db, player_id: int = None, name: str = "",
                            opponent: str = "", season_from: int = 2019) -> list[dict]:
    """Fetch all games for a player vs a specific opponent since season_from."""
    params = {"season_from": season_from, "opp": opponent}

    if player_id:
        id_filter = "AND player_id = :player_id"
        params["player_id"] = player_id
    elif name:
        id_filter = "AND LOWER(player_first_name || ' ' || player_last_name) LIKE :name"
        params["name"] = f"%{name.lower()}%"
    else:
        return []

    sql = db.text(f"""
        SELECT * FROM afl_player_stats
        WHERE season >= :season_from
          AND (LOWER(match_home_team) LIKE LOWER(:opp)
               OR LOWER(match_away_team) LIKE LOWER(:opp))
          {id_filter}
        ORDER BY match_date DESC
    """)
    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().fetchall()
    return [dict(r) for r in rows]


def _db_get_fixtures(db, year: int, round_number: int = None) -> list[dict]:
    """Get fixtures from DB."""
    params = {"year": year}
    round_filter = ""
    if round_number:
        round_filter = "AND round = :round"
        params["round"] = round_number

    sql = db.text(f"""
        SELECT g.*
        FROM afl_games g
        WHERE g.year = :year AND g.complete < 100
              {round_filter}
        ORDER BY g.date ASC
        LIMIT 50
    """)
    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().fetchall()
    return [dict(r) for r in rows]


def _db_get_standings(db, year: int, round_number: int = None) -> list[dict]:
    """Get ladder from DB."""
    params = {"year": year}

    if round_number:
        params["round"] = round_number
        round_filter = "AND round = :round"
    else:
        # Latest round available
        round_filter = """
            AND round = (
                SELECT MAX(round) FROM afl_standings WHERE year = :year
            )
        """

    sql = db.text(f"""
        SELECT * FROM afl_standings
        WHERE year = :year {round_filter}
        ORDER BY rank ASC
    """)
    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().fetchall()
    return [dict(r) for r in rows]


def _db_get_props(db, market: str = "player_disposals") -> list[dict]:
    """Get latest prop lines from DB (last 24 hours)."""
    sql = db.text("""
        SELECT DISTINCT ON (player_name, line_type)
               *
        FROM afl_player_props
        WHERE market = :market
          AND fetched_at > NOW() - INTERVAL '24 hours'
        ORDER BY player_name, line_type, fetched_at DESC
    """)
    with db.engine.connect() as conn:
        rows = conn.execute(sql, {"market": market}).mappings().fetchall()
    return [dict(r) for r in rows]


def _db_get_match_props(db, home_team: str, away_team: str) -> list[dict]:
    """Get all prop lines for a specific match."""
    sql = db.text("""
        SELECT * FROM afl_player_props
        WHERE (LOWER(home_team) LIKE LOWER(:home) OR LOWER(away_team) LIKE LOWER(:home))
          AND (LOWER(home_team) LIKE LOWER(:away) OR LOWER(away_team) LIKE LOWER(:away))
          AND fetched_at > NOW() - INTERVAL '24 hours'
        ORDER BY market, player_name, line_type
    """)
    with db.engine.connect() as conn:
        rows = conn.execute(sql, {
            "home": f"%{home_team}%",
            "away": f"%{away_team}%",
        }).mappings().fetchall()
    return [dict(r) for r in rows]


def _db_has_props(db, home_team: str, away_team: str) -> bool:
    """Check if props exist for a matchup."""
    sql = db.text("""
        SELECT 1 FROM afl_player_props
        WHERE (LOWER(home_team) LIKE LOWER(:home) OR LOWER(away_team) LIKE LOWER(:home))
          AND fetched_at > NOW() - INTERVAL '24 hours'
        LIMIT 1
    """)
    with db.engine.connect() as conn:
        result = conn.execute(sql, {"home": f"%{home_team}%"}).fetchone()
    return result is not None


def _check_data_sources(db) -> dict:
    """Check which data sources have recent data."""
    def _has_data(sql_str):
        try:
            with db.engine.connect() as conn:
                return bool(conn.execute(db.text(sql_str)).scalar())
        except Exception:
            return False

    return {
        "afltables":  _has_data("SELECT 1 FROM afl_player_stats LIMIT 1"),
        "fryzigg":    _has_data("SELECT 1 FROM afl_player_stats WHERE season >= 2019 LIMIT 1"),
        "squiggle":   _has_data("SELECT 1 FROM afl_games LIMIT 1"),
        "odds_api":   _has_data(
            "SELECT 1 FROM afl_player_props WHERE fetched_at > NOW() - INTERVAL '24 hours' LIMIT 1"
        ),
    }


# ─────────────────────────────────────────────
# CRON JOB  (add to your existing Railway cron)
# ─────────────────────────────────────────────

def afl_nightly_sync(app_context, db):
    """
    Call this from your existing Railway cron job.

    In app.py, add to your existing cron function:

        from afl_routes import afl_nightly_sync
        afl_nightly_sync(app, db)

    Or add a standalone cron route:

        @app.route("/cron/afl-sync")
        def cron_afl_sync():
            if request.headers.get("X-Railway-Secret") != os.environ.get("CRON_SECRET"):
                abort(403)
            afl_nightly_sync(app, db)
            return "ok"
    """
    logger.info("AFL nightly sync starting...")

    # 1. Sync current season fixtures + results
    try:
        games = fetch_squiggle_games(CURRENT_YEAR)
        count = upsert_games(db, games)
        log_sync(db, "squiggle_games", season=CURRENT_YEAR, rows=count)
        logger.info(f"  Fixtures: {count} games synced")
    except Exception as e:
        logger.error(f"  Fixtures sync failed: {e}")

    # 2. Sync ladder
    try:
        rnd       = fetch_squiggle_current_round(CURRENT_YEAR)
        standings = fetch_squiggle_standings(CURRENT_YEAR, rnd)
        count     = upsert_standings(db, standings, CURRENT_YEAR, rnd)
        log_sync(db, "squiggle_standings", season=CURRENT_YEAR, round_num=rnd, rows=count)
        logger.info(f"  Ladder: {count} teams synced (round {rnd})")
    except Exception as e:
        logger.error(f"  Ladder sync failed: {e}")

    # 3. Sync current season player stats from Fryzigg
    try:
        stats = fetch_fryzigg_player_stats(CURRENT_YEAR)
        count = upsert_player_stats(db, stats, CURRENT_YEAR)
        log_sync(db, "fryzigg", season=CURRENT_YEAR, rows=count)
        logger.info(f"  Player stats: {count} rows synced")
    except Exception as e:
        logger.error(f"  Player stats sync failed: {e}")

    logger.info("AFL nightly sync complete.")


# ─────────────────────────────────────────────
# UTILS
# ─────────────────────────────────────────────

def _get_opponent(game: dict, player_team: str) -> str:
    """Return the opponent team name from a game row."""
    home = game.get("match_home_team", "")
    away = game.get("match_away_team", "")
    return away if home == player_team else home


def _safe_avg(rows: list[dict], stat: str) -> float:
    """Average a stat across rows, ignoring None/0."""
    vals = [r.get(stat, 0) or 0 for r in rows]
    return round(sum(vals) / len(vals), 1) if vals else 0.0
