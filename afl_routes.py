"""
afl_routes.py
=============
Flask routes for the AFL section of The Form Analyst.
Retrofit for Flask + SQLAlchemy + Railway + PostgreSQL.

What this file does:
- AFL hub page
- Player search
- Player detail
- Player game log
- Player vs opponent
- Team summary / team players
- Fixtures + Squiggle tips merge
- Ladder
- Value finder
- Match props
- Manual sync endpoints
- Nightly sync helper

Important:
- Current-season player stats use the latest season that actually exists in your DB.
- If 2026 rows are not yet present in afl_player_stats, the routes will fall back
  to the latest available season instead of returning broken last-5 data.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from collections import defaultdict

from flask import render_template, jsonify, request, current_app
from flask_login import login_required

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


# ─────────────────────────────────────────────
# ROUTE REGISTRATION
# ─────────────────────────────────────────────

def register_afl_routes(app, db):
    """Register all AFL routes onto the Flask app."""

    @app.route("/afl")
    @login_required
    def afl_hub():
        """Main AFL hub page."""
        year = request.args.get("year", CURRENT_YEAR, type=int)

        current_round = _db_current_round(db, year) or fetch_squiggle_current_round(year)
        next_game = _db_next_game(db, year)

        total_players = _db_count(db, "SELECT COUNT(DISTINCT player_id) FROM afl_player_stats")
        total_games = _db_count(db, "SELECT COUNT(*) FROM afl_games")
        value_bets = _db_count(
            db,
            """
            SELECT COUNT(DISTINCT player_name)
            FROM afl_player_props
            WHERE fetched_at > NOW() - INTERVAL '24 hours'
            """
        )

        sources = _check_data_sources(db)

        fixtures = _db_get_fixtures(db, year=year)
        if not fixtures:
            raw_games = fetch_squiggle_upcoming_games(year) or []
            if raw_games:
                upsert_games(db, raw_games)
                fixtures = _db_get_fixtures(db, year=year)

        fixtures = _merge_fixture_tips(db, fixtures, year=year, round_number=current_round)

        standings = _db_get_standings(db, year=year)
        if not standings:
            raw_standings = fetch_squiggle_standings(year, current_round)
            if raw_standings:
                upsert_standings(db, raw_standings, year, current_round)
                standings = _db_get_standings(db, year=year)

        latest_stats_season = _db_latest_player_stats_season(db)
        player_stats_season = latest_stats_season or (year - 1)

        return render_template(
            "afl.html",
            current_round=current_round,
            year=year,
            player_stats_season=player_stats_season,
            total_players=f"{total_players:,}" if total_players else "0",
            total_games=f"{total_games:,}" if total_games else "0",
            value_bets_today=value_bets or 0,
            next_game_time=next_game.get("time", "—") if next_game else "—",
            next_game_teams=next_game.get("teams", "—") if next_game else "—",
            data_sources=sources,
            fixtures=fixtures,
            standings=standings,
        )

    @app.route("/api/afl/player-stats")
    @login_required
    def api_afl_player_stats():
        """
        Search player stats.
        Query params:
          - name
          - team
          - season
          - stat
          - limit
        """
        name = request.args.get("name", "").strip()
        team = request.args.get("team", "").strip()
        requested_season = request.args.get("season", type=int)
        stat = request.args.get("stat", "disposals").strip()
        limit = request.args.get("limit", 20, type=int)

        if not name and not team:
            return jsonify({"error": "Provide name or team parameter"}), 400

        effective_season = _resolve_stats_season(db, requested_season)
        rows = _db_player_search(
            db,
            name=name,
            team=team,
            season=effective_season,
            limit=max(limit * 30, 300)
        )

        if not rows:
            return jsonify({
                "players": [],
                "requested_season": requested_season,
                "season": effective_season,
                "stat": stat,
                "message": "No players found"
            })

        players = _group_players(rows)
        result = []

        for _, player in players.items():
            games = sorted(player["games"], key=_sort_date_key, reverse=True)
            avgs = get_player_season_averages(games)
            last5 = get_player_last_n_games(games, 5)
            last10 = get_player_last_n_games(games, 10)

            last3_vals = [g.get(stat, 0) or 0 for g in last5[:3]]
            last3_avg = round(sum(last3_vals) / len(last3_vals), 1) if last3_vals else 0.0
            season_avg = avgs.get(stat, 0) or 0
            trend_diff = round(last3_avg - season_avg, 1)

            home_games = [g for g in games if g.get("match_home_team") == player["team"]]
            away_games = [g for g in games if g.get("match_away_team") == player["team"]]

            result.append({
                "player_id": player["player_id"],
                "name": player["name"],
                "first_name": player["first_name"],
                "last_name": player["last_name"],
                "team": player["team"],
                "guernsey": player["guernsey"],
                "height_cm": player["height_cm"],
                "weight_kg": player["weight_kg"],
                "season": effective_season,
                "games_played": len(games),
                "averages": avgs,
                "last5_avg": _safe_avg(last5, stat),
                "last10_avg": _safe_avg(last10, stat),
                "home_avg": _safe_avg(home_games, stat),
                "away_avg": _safe_avg(away_games, stat),
                "hit_rates": {
                    "15_plus": _hit_rate(games, stat, 15),
                    "20_plus": _hit_rate(games, stat, 20),
                    "25_plus": _hit_rate(games, stat, 25),
                    "30_plus": _hit_rate(games, stat, 30),
                },
                "trend": {
                    "stat": stat,
                    "direction": "up" if trend_diff > 0.5 else "down" if trend_diff < -0.5 else "flat",
                    "diff": trend_diff,
                },
                "last_5": [_format_game_log_row(g, player["team"]) for g in last5],
            })

        if team and not name:
            result.sort(key=lambda x: x.get("averages", {}).get(stat, 0), reverse=True)
        else:
            result.sort(key=lambda x: x.get("games_played", 0), reverse=True)

        result = result[:limit]

        return jsonify({
            "players": result,
            "requested_season": requested_season,
            "season": effective_season,
            "stat": stat,
            "count": len(result),
        })

    @app.route("/api/afl/player-detail")
    @login_required
    def api_afl_player_detail():
        """
        Detailed single-player endpoint.
        Query params:
          - player_id OR name
          - season
        """
        player_id = request.args.get("player_id", type=int)
        name = request.args.get("name", "").strip()
        requested_season = request.args.get("season", type=int)

        if not player_id and not name:
            return jsonify({"error": "Provide player_id or name"}), 400

        effective_season = _resolve_stats_season(db, requested_season)

if player_id:
    rows = _db_player_by_id(db, player_id=player_id, season=None, limit=200)
    # If only 1 row, the player_id may be from AFL API (different to Fryzigg)
    # Fall back to name search to get full history
    if len(rows) <= 2 and rows:
        first = rows[0]
        fname = first.get("player_first_name", "")
        lname = first.get("player_last_name", "")
        if fname and lname:
            name_rows = _db_player_search(db, name=f"{fname} {lname}", season=None, limit=200)
            if len(name_rows) > len(rows):
                rows = name_rows
    else:
        rows = _db_player_search(db, name=name, season=None, limit=200)

        if not rows:
            return jsonify({
                "error": "Player not found",
                "requested_season": requested_season,
                "season": effective_season,
            }), 404

        grouped = _group_players(rows)
        player = next(iter(grouped.values()))
        games = sorted(player["games"], key=_sort_date_key, reverse=True)

        averages = get_player_season_averages(games)
        last5 = get_player_last_n_games(games, 5)
        last10 = get_player_last_n_games(games, 10)

        opponents = defaultdict(list)
        for g in games:
            opp = _get_opponent(g, player["team"])
            opponents[opp].append(g)

        opponent_splits = []
        for opp, opp_games in opponents.items():
            opponent_splits.append({
                "opponent": opp,
                "games": len(opp_games),
                "disposals_avg": _safe_avg(opp_games, "disposals"),
                "marks_avg": _safe_avg(opp_games, "marks"),
                "kicks_avg": _safe_avg(opp_games, "kicks"),
                "tackles_avg": _safe_avg(opp_games, "tackles"),
                "goals_avg": _safe_avg(opp_games, "goals"),
            })

        opponent_splits.sort(key=lambda x: x["games"], reverse=True)

        return jsonify({
            "player_id": player["player_id"],
            "name": player["name"],
            "first_name": player["first_name"],
            "last_name": player["last_name"],
            "team": player["team"],
            "guernsey": player["guernsey"],
            "height_cm": player["height_cm"],
            "weight_kg": player["weight_kg"],
            "season": effective_season,
            "requested_season": requested_season,
            "games_played": len(games),
            "averages": averages,
            "last5_avg": {
                "disposals": _safe_avg(last5, "disposals"),
                "marks": _safe_avg(last5, "marks"),
                "kicks": _safe_avg(last5, "kicks"),
                "tackles": _safe_avg(last5, "tackles"),
                "goals": _safe_avg(last5, "goals"),
            },
            "last10_avg": {
                "disposals": _safe_avg(last10, "disposals"),
                "marks": _safe_avg(last10, "marks"),
                "kicks": _safe_avg(last10, "kicks"),
                "tackles": _safe_avg(last10, "tackles"),
                "goals": _safe_avg(last10, "goals"),
            },
            "hit_rates": {
                "disp_20_plus": _hit_rate(games, "disposals", 20),
                "disp_25_plus": _hit_rate(games, "disposals", 25),
                "disp_30_plus": _hit_rate(games, "disposals", 30),
                "marks_5_plus": _hit_rate(games, "marks", 5),
                "tackles_5_plus": _hit_rate(games, "tackles", 5),
                "goals_1_plus": _hit_rate(games, "goals", 1),
            },
            "opponent_splits": opponent_splits,
            "game_log": [_format_game_log_row(g, player["team"]) for g in games[:20]],
        })

    @app.route("/api/afl/player-game-log")
    @login_required
    def api_afl_player_game_log():
        """Full/limited game log for a player."""
        player_id = request.args.get("player_id", type=int)
        name = request.args.get("name", "").strip()
        requested_season = request.args.get("season", type=int)
        limit = request.args.get("limit", 20, type=int)

        if not player_id and not name:
            return jsonify({"error": "Provide player_id or name"}), 400

        effective_season = _resolve_stats_season(db, requested_season)

        if player_id:
            rows = _db_player_by_id(db, player_id=player_id, season=effective_season, limit=max(limit, 100))
        else:
            rows = _db_player_search(db, name=name, season=effective_season, limit=max(limit, 100))

        if not rows:
            return jsonify({
                "games": [],
                "requested_season": requested_season,
                "season": effective_season,
            })

        grouped = _group_players(rows)
        player = next(iter(grouped.values()))
        games = sorted(player["games"], key=_sort_date_key, reverse=True)[:limit]

        return jsonify({
            "player_id": player["player_id"],
            "name": player["name"],
            "team": player["team"],
            "season": effective_season,
            "requested_season": requested_season,
            "games": [_format_game_log_row(g, player["team"]) for g in games],
            "count": len(games),
        })

    @app.route("/api/afl/player-vs-opponent")
    @login_required
    def api_afl_player_vs_opponent():
        """
        Historical stats vs a specific opponent.
        Query params:
          - player_id OR name
          - opponent
          - season_from
        """
        player_id = request.args.get("player_id", type=int)
        name = request.args.get("name", "").strip()
        opponent = request.args.get("opponent", "").strip()
        season_from = request.args.get("season_from", CURRENT_YEAR - 4, type=int)

        if not opponent:
            return jsonify({"error": "opponent parameter required"}), 400

        rows = _db_player_vs_opponent(
            db,
            player_id=player_id,
            name=name,
            opponent=opponent,
            season_from=season_from,
        )

        if not rows:
            return jsonify({
                "games": 0,
                "opponent": opponent,
                "averages": {},
                "hit_rates": {},
                "game_log": [],
            })

        result = get_player_vs_opponent(rows, opponent)
        result["game_log"] = [
            _format_game_log_row(g, g.get("player_team", ""))
            for g in result.get("last_5", [])
        ]
        result.pop("last_5", None)

        return jsonify({
            "opponent": opponent,
            "season_from": season_from,
            **result,
        })

    @app.route("/api/afl/team-players")
    @login_required
    def api_afl_team_players():
        """Return team player summaries for a season."""
        team = request.args.get("team", "").strip()
        requested_season = request.args.get("season", type=int)
        stat = request.args.get("stat", "disposals").strip()
        limit = request.args.get("limit", 30, type=int)

        if not team:
            return jsonify({"error": "team parameter required"}), 400

        effective_season = _resolve_stats_season(db, requested_season)
        rows = _db_player_search(db, team=team, season=effective_season, limit=max(limit * 40, 400))

        if not rows:
            return jsonify({
                "team": team,
                "season": effective_season,
                "requested_season": requested_season,
                "players": [],
            })

        grouped = _group_players(rows)
        players = []

        for _, player in grouped.items():
            games = sorted(player["games"], key=_sort_date_key, reverse=True)
            avgs = get_player_season_averages(games)
            last5 = get_player_last_n_games(games, 5)

            players.append({
                "player_id": player["player_id"],
                "name": player["name"],
                "team": player["team"],
                "guernsey": player["guernsey"],
                "games_played": len(games),
                "averages": avgs,
                "last5_avg": _safe_avg(last5, stat),
                "last_5": [_format_game_log_row(g, player["team"]) for g in last5],
            })

        players.sort(key=lambda x: x.get("averages", {}).get(stat, 0), reverse=True)

        return jsonify({
            "team": team,
            "season": effective_season,
            "requested_season": requested_season,
            "stat": stat,
            "players": players[:limit],
            "count": min(len(players), limit),
        })

    @app.route("/api/afl/team-summary")
    @login_required
    def api_afl_team_summary():
        """Return simple season team summary built from player rows."""
        team = request.args.get("team", "").strip()
        requested_season = request.args.get("season", type=int)

        if not team:
            return jsonify({"error": "team parameter required"}), 400

        effective_season = _resolve_stats_season(db, requested_season)
        rows = _db_player_search(db, team=team, season=effective_season, limit=1200)

        if not rows:
            return jsonify({
                "team": team,
                "season": effective_season,
                "requested_season": requested_season,
                "summary": {},
                "leaders": {},
            })

        grouped = _group_players(rows)
        summaries = []

        for _, player in grouped.items():
            games = sorted(player["games"], key=_sort_date_key, reverse=True)
            avgs = get_player_season_averages(games)
            summaries.append({
                "player_id": player["player_id"],
                "name": player["name"],
                "games_played": len(games),
                "disposals": avgs.get("disposals", 0),
                "marks": avgs.get("marks", 0),
                "kicks": avgs.get("kicks", 0),
                "tackles": avgs.get("tackles", 0),
                "goals": avgs.get("goals", 0),
                "fantasy": avgs.get("afl_fantasy_score", 0),
            })

        def _leader(stat_key):
            return max(summaries, key=lambda x: x.get(stat_key, 0)) if summaries else None

        return jsonify({
            "team": team,
            "season": effective_season,
            "requested_season": requested_season,
            "player_count": len(summaries),
            "leaders": {
                "disposals": _leader("disposals"),
                "marks": _leader("marks"),
                "kicks": _leader("kicks"),
                "tackles": _leader("tackles"),
                "goals": _leader("goals"),
                "fantasy": _leader("fantasy"),
            },
        })

    @app.route("/api/afl/fixtures")
    @login_required
    def api_afl_fixtures():
        """Upcoming fixtures with Squiggle tips and prop availability."""
        year = request.args.get("year", CURRENT_YEAR, type=int)
        round_number = request.args.get("round", type=int)

        games = _db_get_fixtures(db, year=year, round_number=round_number)

        if not games:
            raw = fetch_squiggle_upcoming_games(year)
            if raw:
                upsert_games(db, raw)
                games = _db_get_fixtures(db, year=year, round_number=round_number)

        games = _merge_fixture_tips(db, games, year=year, round_number=round_number)

        for g in games:
            g["props_available"] = _db_has_props(db, g.get("hteam"), g.get("ateam"))

        return jsonify({
            "year": year,
            "round": round_number,
            "games": games,
            "count": len(games),
        })

    @app.route("/api/afl/ladder")
    @login_required
    def api_afl_ladder():
        """Current AFL ladder."""
        year = request.args.get("year", CURRENT_YEAR, type=int)
        round_number = request.args.get("round", type=int)

        standings = _db_get_standings(db, year=year, round_number=round_number)

        if not standings:
            current_round = fetch_squiggle_current_round(year)
            raw = fetch_squiggle_standings(year, current_round)
            if raw:
                upsert_standings(db, raw, year, current_round)
                standings = _db_get_standings(db, year=year)

        return jsonify({
            "year": year,
            "standings": standings,
            "count": len(standings),
        })

    @app.route("/api/afl/value-finder")
    @login_required
    def api_afl_value_finder():
        """
        Compare prop lines vs model predictions.
        Query params:
          - market
          - min_edge
          - year
        """
        market = request.args.get("market", "player_disposals")
        min_edge = request.args.get("min_edge", 2.0, type=float)
        requested_season = request.args.get("year", type=int)
        effective_season = _resolve_stats_season(db, requested_season)

        props = _db_get_props(db, market=market)
        if not props:
            return jsonify({
                "bets": [],
                "message": "No prop lines loaded. Configure The Odds API key.",
                "season": effective_season,
                "requested_season": requested_season,
            })

        if "disposal" in market:
            stat_name = "disposals"
        elif "mark" in market:
            stat_name = "marks"
        elif "tackle" in market:
            stat_name = "tackles"
        elif "goal" in market:
            stat_name = "goals"
        else:
            stat_name = "disposals"

        value_bets = []

        for prop in props:
            if prop.get("line_type") != "Over":
                continue

            player_name = prop.get("player_name", "")
            book_line = prop.get("line", 0)

            player_rows = _db_player_search(db, name=player_name, season=effective_season, limit=100)
            if not player_rows:
                continue

            team_name = player_rows[0].get("player_team", "") if player_rows else ""
            season_avg = _safe_avg(player_rows, stat_name)

            home_team = prop.get("home_team", "")
            away_team = prop.get("away_team", "")
            opponent = away_team if home_team == team_name else home_team if away_team == team_name else (away_team or home_team)

            opp_rows = [
                r for r in player_rows
                if r.get("match_home_team") == opponent or r.get("match_away_team") == opponent
            ]
            vs_opp_avg = _safe_avg(opp_rows, stat_name) if opp_rows else None

            last5 = sorted(player_rows, key=_sort_date_key, reverse=True)[:5]
            last5_avg = _safe_avg(last5, stat_name) if last5 else None

            edge_data = calculate_disposal_edge(
                player_avg=season_avg,
                book_line=book_line,
                vs_opp_avg=vs_opp_avg,
                last5_avg=last5_avg,
            )

            if abs(edge_data["edge"]) >= min_edge or edge_data["recommendation"] == "value":
                total = len(player_rows)
                hits = sum(1 for r in player_rows if (r.get(stat_name, 0) or 0) >= book_line)
                hist_pct = round(hits / total * 100, 1) if total else 0.0

                value_bets.append({
                    "player": player_name,
                    "team": team_name,
                    "opponent": opponent,
                    "home_team": home_team,
                    "away_team": away_team,
                    "commence_time": str(prop.get("commence_time", "")),
                    "bookmaker": prop.get("bookmaker", ""),
                    "market": market,
                    "book_line": book_line,
                    "over_odds": prop.get("odds", 0),
                    "season_avg": season_avg,
                    "vs_opp_avg": vs_opp_avg,
                    "last5_avg": last5_avg,
                    "hist_pct": hist_pct,
                    **edge_data,
                })

        value_bets.sort(key=lambda x: abs(x.get("edge", 0)), reverse=True)

        return jsonify({
            "market": market,
            "min_edge": min_edge,
            "requested_season": requested_season,
            "season": effective_season,
            "bets": value_bets,
            "count": len(value_bets),
        })

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
            "props": props,
            "count": len(props),
        })

    @app.route("/api/afl/sync/fixtures", methods=["POST"])
    @login_required
    def api_afl_sync_fixtures():
        """Sync current season fixtures + results from Squiggle."""
        year = request.json.get("year", CURRENT_YEAR) if request.json else CURRENT_YEAR
        round_number = request.json.get("round", None) if request.json else None

        try:
            games = fetch_squiggle_games(year, round_number)
            count = upsert_games(db, games)
            log_sync(db, "squiggle_games", season=year, round_num=round_number, rows=count)
            return jsonify({"status": "ok", "rows_synced": count, "year": year})
        except Exception as e:
            log_sync(db, "squiggle_games", season=year, status="error", error=str(e))
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/afl/sync/ladder", methods=["POST"])
    @login_required
    def api_afl_sync_ladder():
        """Sync ladder from Squiggle."""
        year = request.json.get("year", CURRENT_YEAR) if request.json else CURRENT_YEAR

        try:
            current_round = fetch_squiggle_current_round(year)
            standings = fetch_squiggle_standings(year, current_round)
            count = upsert_standings(db, standings, year, current_round)
            log_sync(db, "squiggle_standings", season=year, round_num=current_round, rows=count)
            return jsonify({"status": "ok", "rows_synced": count, "round": current_round})
        except Exception as e:
            log_sync(db, "squiggle_standings", season=year, status="error", error=str(e))
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/afl/sync/player-stats", methods=["POST"])
    @login_required
    def api_afl_sync_player_stats():
        """
        Sync player stats from Fryzigg for a given season.
        If season is omitted, sync latest available DB season or current year.
        """
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
        market = request.json.get("market", "player_disposals") if request.json else "player_disposals"

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
        """
        Quick refresh:
        - fixtures
        - ladder
        - latest available player-stats season
        """
        results = {}
        year = CURRENT_YEAR
        stats_season = _db_latest_player_stats_season(db) or (CURRENT_YEAR - 1)

        try:
            games = fetch_squiggle_games(year)
            results["fixtures"] = upsert_games(db, games)
        except Exception as e:
            results["fixtures_error"] = str(e)

        try:
            rnd = fetch_squiggle_current_round(year)
            standings = fetch_squiggle_standings(year, rnd)
            results["ladder"] = upsert_standings(db, standings, year, rnd)
        except Exception as e:
            results["ladder_error"] = str(e)

        results["player_stats"] = "skipped — run via cron only"

        return jsonify({
            "status": "ok",
            "stats_season": stats_season,
            "synced": results,
        })


# ─────────────────────────────────────────────
# PRIVATE DB QUERY HELPERS
# ─────────────────────────────────────────────

def _db_current_round(db, year: int) -> int:
    """Get current round from DB (latest completed round)."""
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
    try:
        time_str = dt.strftime("%-I:%M%p") if dt else "—"
    except Exception:
        time_str = str(dt) if dt else "—"

    return {
        "time": time_str,
        "teams": f"{row['hteam']} vs {row['ateam']}",
        "venue": row["venue"],
    }


def _db_count(db, sql_str: str) -> int:
    """Execute a COUNT query and return integer result."""
    try:
        with db.engine.connect() as conn:
            return conn.execute(db.text(sql_str)).scalar() or 0
    except Exception:
        return 0


def _db_latest_player_stats_season(db) -> int | None:
    """Latest season present in afl_player_stats."""
    sql = db.text("SELECT MAX(season) FROM afl_player_stats")
    try:
        with db.engine.connect() as conn:
            value = conn.execute(sql).scalar()
        return int(value) if value is not None else None
    except Exception:
        return None


def _db_has_player_stats_for_season(db, season: int) -> bool:
    sql = db.text("SELECT 1 FROM afl_player_stats WHERE season = :season LIMIT 1")
    try:
        with db.engine.connect() as conn:
            return conn.execute(sql, {"season": season}).fetchone() is not None
    except Exception:
        return False


def _resolve_stats_season(db, requested_season: int | None) -> int:
    """
    Use requested season if it exists in DB.
    Otherwise fall back to latest available season in DB.
    """
    latest = _db_latest_player_stats_season(db)

    if requested_season and _db_has_player_stats_for_season(db, requested_season):
        return requested_season

    if latest:
        return latest

    return requested_season or (CURRENT_YEAR - 1)


def _db_player_search(db, name: str = "", team: str = "", season: int = None, limit: int = 50) -> list[dict]:
    """Search player stats from DB."""
    conditions = []
    params = {"limit": limit}

    if name:
        conditions.append(
            """
            (
                LOWER(player_first_name || ' ' || player_last_name) LIKE :name
                OR LOWER(player_last_name) LIKE :name_last
            )
            """
        )
        params["name"] = f"%{name.lower()}%"
        params["name_last"] = f"%{name.split()[-1].lower()}%"

    if team:
        conditions.append("LOWER(player_team) LIKE :team")
        params["team"] = f"%{team.lower()}%"

    if season:
        conditions.append("season = :season")
        params["season"] = season

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    sql = db.text(f"""
        SELECT *
        FROM afl_player_stats
        {where}
        ORDER BY match_date DESC
        LIMIT :limit
    """)
    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().fetchall()

    return [dict(r) for r in rows]


def _db_player_by_id(db, player_id: int, season: int = None, limit: int = 100) -> list[dict]:
    """Fetch player rows by player_id."""
    params = {"player_id": player_id, "limit": limit}
    season_filter = ""
    if season:
        params["season"] = season
        season_filter = "AND season = :season"

    sql = db.text(f"""
        SELECT *
        FROM afl_player_stats
        WHERE player_id = :player_id
        {season_filter}
        ORDER BY match_date DESC
        LIMIT :limit
    """)
    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().fetchall()

    return [dict(r) for r in rows]


def _db_player_vs_opponent(db, player_id: int = None, name: str = "", opponent: str = "", season_from: int = 2019) -> list[dict]:
    """Fetch all games for a player vs a specific opponent since season_from."""
    params = {"season_from": season_from, "opp": f"%{opponent}%"}

    if player_id:
        id_filter = "AND player_id = :player_id"
        params["player_id"] = player_id
    elif name:
        id_filter = "AND LOWER(player_first_name || ' ' || player_last_name) LIKE :name"
        params["name"] = f"%{name.lower()}%"
    else:
        return []

    sql = db.text(f"""
        SELECT *
        FROM afl_player_stats
        WHERE season >= :season_from
          AND (
                LOWER(match_home_team) LIKE LOWER(:opp)
                OR LOWER(match_away_team) LIKE LOWER(:opp)
          )
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
        WHERE g.year = :year
          AND g.complete < 100
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
        round_filter = """
            AND round = (
                SELECT MAX(round)
                FROM afl_standings
                WHERE year = :year
            )
        """

    sql = db.text(f"""
        SELECT *
        FROM afl_standings
        WHERE year = :year
        {round_filter}
        ORDER BY rank ASC
    """)
    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().fetchall()

    return [dict(r) for r in rows]


def _db_get_props(db, market: str = "player_disposals") -> list[dict]:
    """Get latest prop lines from DB (last 24h)."""
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
        SELECT *
        FROM afl_player_props
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
        SELECT 1
        FROM afl_player_props
        WHERE (
            LOWER(home_team) LIKE LOWER(:home)
            OR LOWER(away_team) LIKE LOWER(:home)
        )
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
        "afltables": _has_data("SELECT 1 FROM afl_player_stats LIMIT 1"),
        "fryzigg": _has_data("SELECT 1 FROM afl_player_stats WHERE season >= 2019 LIMIT 1"),
        "squiggle": _has_data("SELECT 1 FROM afl_games LIMIT 1"),
        "odds_api": _has_data(
            """
            SELECT 1
            FROM afl_player_props
            WHERE fetched_at > NOW() - INTERVAL '24 hours'
            LIMIT 1
            """
        ),
    }


# ─────────────────────────────────────────────
# CRON JOB
# ─────────────────────────────────────────────

def afl_nightly_sync(app_context, db):
    """
    Nightly AFL sync helper for the main app.
    This still uses Fryzigg + Squiggle only.
    """
    logger.info("=== AFL nightly sync for season %s ===", CURRENT_YEAR)

    try:
        games = fetch_squiggle_games(CURRENT_YEAR)
        count = upsert_games(db, games)
        log_sync(db, "squiggle_games", season=CURRENT_YEAR, rows=count)
        logger.info("  ✓ Fixtures: %s games synced", count)
    except Exception as e:
        logger.error("  ✗ Fixtures sync failed: %s", e)

    try:
        rnd = fetch_squiggle_current_round(CURRENT_YEAR)
        standings = fetch_squiggle_standings(CURRENT_YEAR, rnd)
        count = upsert_standings(db, standings, CURRENT_YEAR, rnd)
        log_sync(db, "squiggle_standings", season=CURRENT_YEAR, round_num=rnd, rows=count)
        logger.info("  ✓ Ladder: %s teams synced (round %s)", count, rnd)
    except Exception as e:
        logger.error("  ✗ Ladder sync failed: %s", e)

    latest_stats_season = _db_latest_player_stats_season(db) or (CURRENT_YEAR - 1)
    seasons_to_try = list(range(max(2019, latest_stats_season - 3), latest_stats_season + 2))

    total_stats = 0
    for season in seasons_to_try:
        try:
            stats = fetch_fryzigg_player_stats(season)
            if not stats:
                logger.info("  - Fryzigg %s: no data returned (may not be published yet)", season)
                continue

            count = upsert_player_stats(db, stats, season)
            log_sync(db, "fryzigg", season=season, rows=count)
            total_stats += count
            logger.info("  ✓ Fryzigg %s: %s rows synced", season, count)
            time.sleep(1)
        except Exception as e:
            log_sync(db, "fryzigg", season=season, status="error", error=str(e))
            logger.error("  ✗ Fryzigg %s failed: %s", season, e)

    logger.info("  ✓ Fryzigg total: %s rows across %s seasons", total_stats, len(seasons_to_try))

    api_key = None
    try:
        api_key = current_app.config.get("ODDS_API_KEY", "")
    except Exception:
        api_key = ""

    if api_key:
        try:
            props = fetch_afl_player_props(api_key, "player_disposals")
            count = upsert_player_props(db, props)
            log_sync(db, "odds_api", rows=count)
            logger.info("  ✓ Props: %s rows synced", count)
        except Exception as e:
            logger.error("  ✗ Props sync failed: %s", e)
    else:
        logger.info("  - Props: skipped (ODDS_API_KEY not configured)")

    logger.info("=== AFL sync complete ===")


# ─────────────────────────────────────────────
# UTILS
# ─────────────────────────────────────────────

def _sort_date_key(row: dict):
    return row.get("match_date") or ""


def _get_opponent(game: dict, player_team: str) -> str:
    """Return opponent team from a game row."""
    home = game.get("match_home_team", "")
    away = game.get("match_away_team", "")
    return away if home == player_team else home


def _safe_avg(rows: list[dict], stat: str) -> float:
    """Average a stat across rows."""
    vals = [r.get(stat, 0) or 0 for r in rows]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def _hit_rate(rows: list[dict], stat: str, line: float) -> float:
    """Percentage of games hitting or exceeding a line."""
    if not rows:
        return 0.0
    hits = sum(1 for r in rows if (r.get(stat, 0) or 0) >= line)
    return round(hits / len(rows) * 100, 1)


def _group_players(rows: list[dict]) -> dict:
    """Group raw player rows by player_id."""
    players = {}

    for row in rows:
        pid = row.get("player_id")
        if not pid:
            continue

        if pid not in players:
            players[pid] = {
                "player_id": pid,
                "name": f"{row.get('player_first_name', '')} {row.get('player_last_name', '')}".strip(),
                "first_name": row.get("player_first_name", ""),
                "last_name": row.get("player_last_name", ""),
                "team": row.get("player_team", ""),
                "guernsey": row.get("guernsey_number"),
                "height_cm": row.get("player_height_cm"),
                "weight_kg": row.get("player_weight_kg"),
                "games": [],
            }

        players[pid]["games"].append(dict(row))

    return players


def _format_game_log_row(game: dict, player_team: str) -> dict:
    """Standardised game-log row for APIs/UI."""
    return {
        "date": game.get("match_date", ""),
        "season": game.get("season"),
        "round": game.get("match_round", ""),
        "opponent": _get_opponent(game, player_team) if player_team else "",
        "venue": game.get("venue_name", ""),
        "result": "W" if game.get("match_winner") == player_team else "L",
        "disposals": game.get("disposals", 0),
        "marks": game.get("marks", 0),
        "kicks": game.get("kicks", 0),
        "handballs": game.get("handballs", 0),
        "tackles": game.get("tackles", 0),
        "goals": game.get("goals", 0),
        "fantasy": game.get("afl_fantasy_score", 0),
        "supercoach": game.get("supercoach_score", 0),
    }


def _merge_fixture_tips(db, fixtures: list[dict], year: int, round_number: int | None = None) -> list[dict]:
    """
    Attach Squiggle tips to fixture rows.
    Tries live Squiggle fetch each time because tips are lightweight and useful.
    """
    if not fixtures:
        return []

    try:
        tips = fetch_squiggle_tips(year, round_number) or []
    except Exception as e:
        logger.warning("Fixture tip merge failed: %s", e)
        tips = []

    tips_by_game_id = {}
    tips_by_matchup = {}

    for tip in tips:
        game_id = tip.get("gameid")
        if game_id:
            tips_by_game_id[game_id] = tip

        matchup_key = (tip.get("hteam", ""), tip.get("ateam", ""), tip.get("round"))
        tips_by_matchup[matchup_key] = tip

    merged = []
    for fixture in fixtures:
        game_id = fixture.get("id")
        tip = tips_by_game_id.get(game_id)

        if not tip:
            tip = tips_by_matchup.get((
                fixture.get("hteam", ""),
                fixture.get("ateam", ""),
                fixture.get("round"),
            ))

        row = dict(fixture)
        if tip:
            row["squiggle_tip"] = tip.get("tip", "")
            row["squiggle_confidence"] = tip.get("confidence")
            row["squiggle_margin"] = tip.get("margin")
        else:
            row["squiggle_tip"] = ""
            row["squiggle_confidence"] = None
            row["squiggle_margin"] = None

        merged.append(row)

    return merged
