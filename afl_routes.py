"""
afl_routes.py
=============
Flask routes for the AFL section of The Form Analyst.

Key fixes in this rewrite:
- Uses player_id as the primary identity everywhere possible.
- Removes dangerous surname-only matching that caused Baker-style collisions.
- Applies season filtering consistently to player detail and game-log routes.
- Uses the last 3 seasons of player data for analysis views where appropriate.
- Keeps team list routes strict to the requested season to prevent duplicate players
  when IDs change between seasons.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

import requests as _requests
from flask import abort, current_app, jsonify, make_response, render_template, request
from flask_login import login_required

# Simple in-memory cache for player headshot images: str(photo_id) -> (bytes, mime) | None
_headshot_cache: dict[str, tuple[bytes, str] | None] = {}
_fantasy_player_id_cache: dict[str, int] = {}
_HEADSHOT_CACHE_MAX = 2000

from afl_data import (
    CURRENT_YEAR,
    _normalise_prop_market,
    _normalise_team_name,
    afl_player_headshot_url,
    calculate_disposal_edge,
    calculate_market_edge,
    fetch_afl_player_props,
    fetch_afl_h2h_spread_odds,
    fetch_fryzigg_player_stats,
    fetch_squiggle_current_round,
    fetch_squiggle_games,
    fetch_squiggle_standings,
    fetch_squiggle_tips,
    fetch_squiggle_upcoming_games,
    get_odds_api_key,
    get_player_last_n_games,
    get_player_season_averages,
    get_player_vs_opponent,
)
from afl_db import (
    SQUIGGLE_SITE,
    _team as _normalise_team,
    get_team_logo_map,
    log_sync,
    upsert_games,
    upsert_player_props,
    upsert_match_markets,
    upsert_player_stats,
    upsert_standings,
)

logger = logging.getLogger(__name__)


def register_afl_routes(app, db):
    """Register all AFL routes onto the Flask app."""

    @app.route("/afl")
    @login_required
    def afl_hub():
        year = request.args.get("year", CURRENT_YEAR, type=int)
        current_round = _db_current_round(db, year) or fetch_squiggle_current_round(year)
        next_game = _db_next_game(db, year)

        total_players = _db_count(db, "SELECT COUNT(DISTINCT player_id) FROM afl_player_stats")
        total_games = _db_count(db, "SELECT COUNT(*) FROM afl_games")
        value_bets = _db_count(
            db,
            "SELECT COUNT(*) FROM afl_player_props WHERE fetched_at > NOW() - INTERVAL '24 hours'",
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
        name = request.args.get("name", "").strip()
        team = request.args.get("team", "").strip()
        requested_season = request.args.get("season", type=int)
        stat = request.args.get("stat", "disposals").strip()
        limit = request.args.get("limit", 20, type=int)

        if not name and not team:
            return jsonify({"error": "Provide name or team parameter"}), 400

        effective_season = _resolve_stats_season(db, requested_season)
        effective_seasons = _resolve_stats_seasons(db, requested_season)

        rows = _db_player_search(
            db,
            name=name,
            team=team,
            seasons=effective_seasons,
            limit=max(limit * 30, 300),
        )

        if not rows:
            return jsonify(
                {
                    "players": [],
                    "season": effective_season,
                    "seasons_used": effective_seasons,
                    "stat": stat,
                    "message": "No players found",
                }
            )

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

            result.append(
                {
                    "player_id": player["player_id"],
                    "name": player["name"],
                    "first_name": player["first_name"],
                    "last_name": player["last_name"],
                    "team": player["team"],
                    "guernsey": player["guernsey"],
                    "height_cm": player["height_cm"],
                    "weight_kg": player["weight_kg"],
                    "headshot_url": afl_player_headshot_url(
                     player["player_id"],
                     player["first_name"],
                     player["last_name"],
                     ),
                    "season": effective_season,
                    "seasons_used": effective_seasons,
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
                }
            )

        if name:
            result.sort(key=lambda x: (x.get("name", "").lower(), x.get("team", "").lower()))
        else:
            result.sort(key=lambda x: x.get("averages", {}).get(stat, 0), reverse=True)

        result = result[:limit]

        return jsonify(
            {
                "players": result,
                "season": effective_season,
                "seasons_used": effective_seasons,
                "stat": stat,
                "count": len(result),
            }
        )

    @app.route("/api/afl/player-detail")
    @login_required
    def api_afl_player_detail():
        player_id = request.args.get("player_id", type=int)
        name = request.args.get("name", "").strip()
        team = request.args.get("team", "").strip()
        requested_season = request.args.get("season", type=int)

        if not player_id and not name:
            return jsonify({"error": "Provide player_id or name"}), 400

        effective_season = _resolve_stats_season(db, requested_season)
        effective_seasons = _resolve_stats_seasons(db, requested_season)

        rows = _resolve_player_rows(
            db=db,
            player_id=player_id,
            name=name,
            team=team,
            seasons=effective_seasons,
            limit=300,
        )

        if not rows:
            return jsonify(
                {
                    "error": "Player not found",
                    "season": effective_season,
                    "seasons_used": effective_seasons,
                }
            ), 404

        grouped = _group_players(rows)

        if player_id and player_id in grouped:
            player = grouped[player_id]
        elif len(grouped) == 1:
            player = next(iter(grouped.values()))
        else:
            matches = [
                {
                    "player_id": p["player_id"],
                    "name": p["name"],
                    "team": p["team"],
                }
                for p in grouped.values()
            ]
            matches.sort(key=lambda x: (x["name"].lower(), x["team"].lower()))
            return jsonify(
                {
                    "error": "Multiple players matched",
                    "season": effective_season,
                    "seasons_used": effective_seasons,
                    "matches": matches,
                }
            ), 409

        games = sorted(player["games"], key=_sort_date_key, reverse=True)
        season_games = [g for g in games if g.get("season") == effective_season] or games

        # "Season avg" cards should reflect the requested season (e.g. 2026),
        # while history tabs still use the broader multi-season sample.
        averages = get_player_season_averages(season_games)
        last5 = get_player_last_n_games(season_games, 5)
        last10 = get_player_last_n_games(season_games, 10)

        opponents = defaultdict(list)
        for game in games:
            opponent = _get_opponent(game, player["team"])
            opponents[opponent].append(game)

        opponent_splits = []
        for opponent, opponent_games in opponents.items():
            opponent_splits.append(
                {
                    "opponent": opponent,
                    "games": len(opponent_games),
                    "disposals_avg": _safe_avg(opponent_games, "disposals"),
                    "marks_avg": _safe_avg(opponent_games, "marks"),
                    "kicks_avg": _safe_avg(opponent_games, "kicks"),
                    "tackles_avg": _safe_avg(opponent_games, "tackles"),
                    "goals_avg": _safe_avg(opponent_games, "goals"),
                }
            )

        opponent_splits.sort(key=lambda x: x["games"], reverse=True)

        return jsonify(
            {
                "player_id": player["player_id"],
                "name": player["name"],
                "first_name": player["first_name"],
                "last_name": player["last_name"],
                "team": player["team"],
                "guernsey": player["guernsey"],
                "height_cm": player["height_cm"],
                "weight_kg": player["weight_kg"],
                "headshot_url": afl_player_headshot_url(
                    player["player_id"],
                    player["first_name"],
                    player["last_name"],
                ),
                "season": effective_season,
                "seasons_used": effective_seasons,
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
                    "disp_20_plus": _hit_rate(season_games, "disposals", 20),
                    "disp_25_plus": _hit_rate(season_games, "disposals", 25),
                    "disp_30_plus": _hit_rate(season_games, "disposals", 30),
                    "marks_5_plus": _hit_rate(season_games, "marks", 5),
                    "tackles_5_plus": _hit_rate(season_games, "tackles", 5),
                    "goals_1_plus": _hit_rate(season_games, "goals", 1),
                },
                "opponent_splits": opponent_splits,
                "game_log": [_format_game_log_row(g, player["team"]) for g in games[:20]],
            }
        )

    @app.route("/api/afl/player-game-log")
    @login_required
    def api_afl_player_game_log():
        player_id = request.args.get("player_id", type=int)
        name = request.args.get("name", "").strip()
        team = request.args.get("team", "").strip()
        requested_season = request.args.get("season", type=int)
        limit = request.args.get("limit", 20, type=int)

        if not player_id and not name:
            return jsonify({"error": "Provide player_id or name"}), 400

        effective_season = _resolve_stats_season(db, requested_season)
        effective_seasons = _resolve_stats_seasons(db, requested_season)

        rows = _resolve_player_rows(
            db=db,
            player_id=player_id,
            name=name,
            team=team,
            seasons=effective_seasons,
            limit=max(limit, 150),
        )

        if not rows:
            return jsonify({"games": [], "season": effective_season, "seasons_used": effective_seasons})

        grouped = _group_players(rows)

        if player_id and player_id in grouped:
            player = grouped[player_id]
        elif len(grouped) == 1:
            player = next(iter(grouped.values()))
        else:
            matches = [
                {
                    "player_id": p["player_id"],
                    "name": p["name"],
                    "team": p["team"],
                }
                for p in grouped.values()
            ]
            matches.sort(key=lambda x: (x["name"].lower(), x["team"].lower()))
            return jsonify(
                {
                    "error": "Multiple players matched",
                    "season": effective_season,
                    "seasons_used": effective_seasons,
                    "matches": matches,
                }
            ), 409

        games = sorted(player["games"], key=_sort_date_key, reverse=True)[:limit]

        return jsonify(
            {
                "player_id": player["player_id"],
                "name": player["name"],
                "team": player["team"],
                "season": effective_season,
                "seasons_used": effective_seasons,
                "games": [_format_game_log_row(g, player["team"]) for g in games],
                "count": len(games),
            }
        )

    @app.route("/api/afl/player-vs-opponent")
    @login_required
    def api_afl_player_vs_opponent():
        player_id = request.args.get("player_id", type=int)
        name = request.args.get("name", "").strip()
        team = request.args.get("team", "").strip()
        opponent = request.args.get("opponent", "").strip()
        season_from = request.args.get("season_from", CURRENT_YEAR - 4, type=int)

        if not opponent:
            return jsonify({"error": "opponent parameter required"}), 400

        rows = _db_player_vs_opponent(
            db,
            player_id=player_id,
            name=name,
            team=team,
            opponent=opponent,
            season_from=season_from,
        )

        if not rows:
            return jsonify(
                {
                    "games": 0,
                    "opponent": opponent,
                    "averages": {},
                    "hit_rates": {},
                    "game_log": [],
                }
            )

        result = get_player_vs_opponent(rows, opponent)
        # rows is already filtered by opponent and sorted by match_date DESC;
        # return ALL games since season_from so the chart shows the full 5-year history.
        result["game_log"] = [_format_game_log_row(g, g.get("player_team", "")) for g in rows]
        result.pop("last_5", None)

        return jsonify({"opponent": opponent, "season_from": season_from, **result})

    @app.route("/api/afl/team-players")
    @login_required
    def api_afl_team_players():
        team = request.args.get("team", "").strip()
        stat = request.args.get("stat", "disposals").strip()
        requested_season = request.args.get("season", type=int)
        limit = request.args.get("limit", 30, type=int)

        if not team:
            return jsonify({"error": "team parameter required"}), 400

        effective_season = _resolve_stats_season(db, requested_season)

        rows = _db_player_search(
            db,
            team=team,
            season=effective_season,
            limit=max(limit * 40, 400),
        )

        if not rows:
            return jsonify(
                {
                    "team": team,
                    "season": effective_season,
                    "stat": stat,
                    "players": [],
                    "count": 0,
                }
            )

        grouped = _group_players(rows)
        players = []

        for _, player in grouped.items():
            games = sorted(player["games"], key=_sort_date_key, reverse=True)
            avgs = get_player_season_averages(games)
            last5 = get_player_last_n_games(games, 5)

            home_games = [g for g in games if g.get("match_home_team") == player["team"]]
            away_games = [g for g in games if g.get("match_away_team") == player["team"]]

            players.append(
                {
                    "player_id": player["player_id"],
                    "name": player["name"],
                    "first_name": player["first_name"],
                    "last_name": player["last_name"],
                    "team": player["team"],
                    "guernsey": player["guernsey"],
                    "height_cm": player["height_cm"],
                    "weight_kg": player["weight_kg"],
                    "headshot_url": afl_player_headshot_url(
                        player["player_id"],
                        player["first_name"],
                        player["last_name"],
                    ),
                    "season": effective_season,
                    "games_played": len(games),
                    "averages": avgs,
                    "last5_avg": _safe_avg(last5, stat),
                    "home_avg": _safe_avg(home_games, stat),
                    "away_avg": _safe_avg(away_games, stat),
                    "last_5": [_format_game_log_row(g, player["team"]) for g in last5],
                }
            )

        players.sort(key=lambda x: x.get("averages", {}).get(stat, 0), reverse=True)

        return jsonify(
            {
                "team": team,
                "season": effective_season,
                "stat": stat,
                "players": players[:limit],
                "count": min(len(players), limit),
            }
        )

    @app.route("/api/afl/team-summary")
    @login_required
    def api_afl_team_summary():
        team = request.args.get("team", "").strip()
        requested_season = request.args.get("season", type=int)

        if not team:
            return jsonify({"error": "team parameter required"}), 400

        effective_season = _resolve_stats_season(db, requested_season)

        rows = _db_player_search(
            db,
            team=team,
            season=effective_season,
            limit=2000,
        )

        if not rows:
            return jsonify(
                {
                    "team": team,
                    "season": effective_season,
                    "summary": {},
                    "leaders": {},
                }
            )

        grouped = _group_players(rows)
        summaries = []

        for _, player in grouped.items():
            games = sorted(player["games"], key=_sort_date_key, reverse=True)
            avgs = get_player_season_averages(games)
            summaries.append(
                {
                    "player_id": player["player_id"],
                    "name": player["name"],
                    "games_played": len(games),
                    "disposals": avgs.get("disposals", 0),
                    "marks": avgs.get("marks", 0),
                    "kicks": avgs.get("kicks", 0),
                    "tackles": avgs.get("tackles", 0),
                    "goals": avgs.get("goals", 0),
                    "fantasy": avgs.get("afl_fantasy_score", 0),
                }
            )

        def _leader(stat_key: str):
            return max(summaries, key=lambda x: x.get(stat_key, 0)) if summaries else None

        return jsonify(
            {
                "team": team,
                "season": effective_season,
                "player_count": len(summaries),
                "leaders": {
                    "disposals": _leader("disposals"),
                    "marks": _leader("marks"),
                    "kicks": _leader("kicks"),
                    "tackles": _leader("tackles"),
                    "goals": _leader("goals"),
                    "fantasy": _leader("fantasy"),
                },
            }
        )

    @app.route("/api/afl/fixtures")
    @login_required
    def api_afl_fixtures():
        year = request.args.get("year", CURRENT_YEAR, type=int)
        round_number = request.args.get("round", type=int)

        games = _db_get_fixtures(db, year=year, round_number=round_number)
        if not games:
            raw = fetch_squiggle_upcoming_games(year)
            if raw:
                upsert_games(db, raw)
                games = _db_get_fixtures(db, year=year, round_number=round_number)

        games = _merge_fixture_tips(db, games, year=year, round_number=round_number)
        for game in games:
            game["props_available"] = _db_has_props(db, game.get("hteam"), game.get("ateam"))

        return jsonify({"year": year, "round": round_number, "games": games, "count": len(games)})

    @app.route("/api/afl/ladder")
    @login_required
    def api_afl_ladder():
        year = request.args.get("year", CURRENT_YEAR, type=int)
        round_number = request.args.get("round", type=int)

        standings = _db_get_standings(db, year=year, round_number=round_number)
        if not standings:
            current_round = fetch_squiggle_current_round(year)
            raw = fetch_squiggle_standings(year, current_round)
            if raw:
                upsert_standings(db, raw, year, current_round)
                standings = _db_get_standings(db, year=year)

        return jsonify({"year": year, "standings": standings, "count": len(standings)})
    @app.route("/api/afl/value-finder")
    @login_required
    def api_afl_value_finder():
        market = request.args.get("market", "player_disposals")
        min_edge = request.args.get("min_edge", 2.0, type=float)
        requested_season = request.args.get("year", type=int)
        round_number = request.args.get("round", type=int)
        home_team = request.args.get("home", "").strip()
        away_team = request.args.get("away", "").strip()
        min_line = request.args.get("min_line", type=float)
        max_line = request.args.get("max_line", type=float)

        market = _normalise_prop_market(market)

        effective_season = _resolve_stats_season(db, requested_season)
        effective_seasons = _resolve_stats_seasons(db, requested_season)

        if round_number and not home_team and not away_team:
            fixtures = _db_get_fixtures(
                db,
                year=requested_season or CURRENT_YEAR,
                round_number=round_number,
            )

            props = []

            for fixture in fixtures:
                fixture_props = _db_get_props(
                    db,
                    market=market,
                    home_team=fixture.get("hteam"),
                    away_team=fixture.get("ateam"),
                    min_line=min_line,
                    max_line=max_line,
                )
                props.extend(fixture_props)
        else:
            props = _db_get_props(
                db,
                market=market,
                home_team=home_team or None,
                away_team=away_team or None,
                min_line=min_line,
                max_line=max_line,
            )

        if not props:
            return jsonify(
                {
                    "bets": [],
                    "message": (
                        "No prop lines found for this market. "
                        "Props are synced daily — check back later or use "
                        "'Load All Props' to manually refresh from the Odds API."
                    ),
                    "season": effective_season,
                    "seasons_used": effective_seasons,
                }
            )

        _MARKET_STAT: dict[str, str] = {
            "player_disposals": "disposals",
            "player_kicks": "kicks",
            "player_handballs": "handballs",
            "player_marks": "marks",
            "player_tackles": "tackles",
            "player_goals": "goals",
            "player_afl_fantasy_points": "afl_fantasy_score",
        }

        stat_name = _MARKET_STAT.get(market, "disposals")

        seen: dict[tuple, dict] = {}

        for prop in props:
            key = (
                prop.get("player_name", ""),
                prop.get("line_type", ""),
                prop.get("line", 0),
            )

            existing = seen.get(key)

            if existing is None or (prop.get("odds") or 0) > (existing.get("odds") or 0):
                seen[key] = prop

        deduped_props = list(seen.values())

        prop_name_keys: list[tuple[str, str, str]] = []

        for prop in deduped_props:
            pname = prop.get("player_name", "")
            parts = pname.replace(".", " ").split()
            parts = [p for p in parts if p]

            if len(parts) >= 2:
                prop_name_keys.append((pname, parts[0][0].lower(), parts[-1].lower()))

        all_last_names = list({k[2] for k in prop_name_keys})

        all_stats_rows: list[dict] = []

        if all_last_names:
            bulk_sql = db.text(
                """
                SELECT *
                FROM afl_player_stats
                WHERE LOWER(player_last_name) = ANY(:last_names)
                  AND season = ANY(:seasons)
                ORDER BY season DESC, match_date DESC
                """
            )

            with db.engine.connect() as conn:
                bulk_rows = conn.execute(
                    bulk_sql,
                    {
                        "last_names": all_last_names,
                        "seasons": effective_seasons,
                    },
                ).mappings().fetchall()

            all_stats_rows = [dict(r) for r in bulk_rows]

        stats_by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)

        for row in all_stats_rows:
            ln = (row.get("player_last_name") or "").lower()
            fn = (row.get("player_first_name") or "").lower()

            if ln and fn:
                stats_by_key[(fn[0], ln)].append(row)

        value_bets = []

        for pname, first_initial, last_name in prop_name_keys:
            player_rows = stats_by_key.get((first_initial, last_name), [])

            if not player_rows:
                continue

            grouped = _group_players(player_rows)

            if len(grouped) != 1:
                continue

            player = next(iter(grouped.values()))
            games = sorted(player["games"], key=_sort_date_key, reverse=True)
            season_games = [g for g in games if g.get("season") == effective_season] or games
            team_name = player.get("team", "")

            season_avg = _safe_avg(season_games, stat_name)

            for prop in deduped_props:
                if prop.get("player_name", "") != pname:
                    continue

                line_type = prop.get("line_type", "")

                if line_type not in ("Over", "Under"):
                    continue

                book_line = prop.get("line", 0)

                if book_line is None:
                    continue

                odds = prop.get("odds", 0) or 0
                prop_home_team = _normalise_team_name(prop.get("home_team", ""))
                prop_away_team = _normalise_team_name(prop.get("away_team", ""))

                opponent = (
                    prop_away_team
                    if prop_home_team == team_name
                    else prop_home_team
                    if prop_away_team == team_name
                    else (prop_away_team or prop_home_team)
                )

                opp_lower = opponent.lower()

                opp_rows = [
                    r
                    for r in games
                    if (r.get("match_home_team") or "").lower() == opp_lower
                    or (r.get("match_away_team") or "").lower() == opp_lower
                ]

                vs_opp_avg = _safe_avg(opp_rows, stat_name) if opp_rows else None
                last5_avg = _safe_avg(season_games[:5], stat_name) if season_games else None

                edge_data = calculate_market_edge(
                    player_avg=season_avg,
                    book_line=book_line,
                    odds=odds,
                    line_type=line_type,
                    market=market,
                    vs_opp_avg=vs_opp_avg,
                    last5_avg=last5_avg,
                )

                edge = edge_data["edge"]

                if abs(edge) >= min_edge:
                    total = len(games)

                    if line_type == "Over":
                        hits = sum(
                            1
                            for r in games
                            if (r.get(stat_name, 0) or 0) >= book_line
                        )
                    else:
                        hits = sum(
                            1
                            for r in games
                            if (r.get(stat_name, 0) or 0) < book_line
                        )

                    hist_pct = round(hits / total * 100, 1) if total else 0.0

                    value_bets.append(
                        {
                            "player": pname,
                            "player_id": player.get("player_id"),
                            "team": team_name,
                            "opponent": opponent,
                            "home_team": prop_home_team,
                            "away_team": prop_away_team,
                            "commence_time": str(prop.get("commence_time", "")),
                            "bookmaker": prop.get("bookmaker", ""),
                            "market": market,
                            "line_type": line_type,
                            "book_line": book_line,
                            "odds": odds,
                            "season_avg": season_avg,
                            "vs_opp_avg": vs_opp_avg,
                            "last5_avg": last5_avg,
                            "hist_pct": hist_pct,
                            "model_prediction": edge_data["model_prediction"],
                            "model_prob": edge_data.get("model_prob"),
                            "implied_prob": edge_data.get("implied_prob"),
                            "edge": round(edge, 1),
                            "edge_pct": edge_data["edge_pct"],
                            "recommendation": edge_data["recommendation"],
                            "headshot_url": afl_player_headshot_url(
                                player.get("player_id"),
                                player.get("first_name", ""),
                                player.get("last_name", ""),
                            ),
                        }
                    )

        best_per_player: dict[tuple, dict] = {}

        for bet in value_bets:
            key = (bet["player"], bet["line_type"])

            if key not in best_per_player or abs(bet["edge"]) > abs(best_per_player[key]["edge"]):
                best_per_player[key] = bet

        value_bets = list(best_per_player.values())
        value_bets.sort(key=lambda x: abs(x.get("edge", 0)), reverse=True)

        return jsonify(
            {
                "market": market,
                "stat": stat_name,
                "min_edge": min_edge,
                "season": effective_season,
                "seasons_used": effective_seasons,
                "bets": value_bets,
                "count": len(value_bets),
            }
        )
       
    @app.route("/api/afl/props")
    @app.route("/api/afl/props/all")
    @login_required
    def api_afl_props_all():
        """Return raw props from the database with optional filtering.

        Query params:
          market     – e.g. player_disposals (optional, all markets if omitted)
          home_team  – partial match on home_team (optional)
          away_team  – partial match on away_team (optional)
          min_line   – minimum line value (optional)
          max_line   – maximum line value (optional)
        """
        market = request.args.get("market", "").strip()
        home_team = request.args.get("home_team", "").strip()
        away_team = request.args.get("away_team", "").strip()
        min_line = request.args.get("min_line", type=float)
        max_line = request.args.get("max_line", type=float)

        if market:
            market = _normalise_prop_market(market)

        conditions = ["fetched_at > NOW() - INTERVAL '7 days'"]
        params: dict = {}

        if market:
            market_aliases = [market]
            market_aliases.extend(
                {
                    "player_kicks": ["player_kicks_over"],
                    "player_handballs": ["player_handballs_over"],
                    "player_marks": ["player_marks_over"],
                    "player_tackles": ["player_tackles_over"],
                    "player_goals": ["player_goals_scored_over", "player_goals_over"],
                }.get(market, [])
            )
            conditions.append("market = ANY(:markets)")
            params["markets"] = market_aliases
        if home_team:
            conditions.append(
                "(LOWER(home_team) LIKE LOWER(:home_team) OR LOWER(away_team) LIKE LOWER(:home_team))"
            )
            params["home_team"] = f"%{home_team}%"
        if away_team:
            conditions.append(
                "(LOWER(home_team) LIKE LOWER(:away_team) OR LOWER(away_team) LIKE LOWER(:away_team))"
            )
            params["away_team"] = f"%{away_team}%"
        if min_line is not None:
            conditions.append("line >= :min_line")
            params["min_line"] = min_line
        if max_line is not None:
            conditions.append("line <= :max_line")
            params["max_line"] = max_line

        where_clause = " AND ".join(conditions)
        # where_clause is built entirely from hardcoded string literals above;
        # all user-supplied values are passed via the parameterised `params` dict.
        sql = db.text(
            f"""
            SELECT DISTINCT ON (player_name, market, line_type, line)
                   player_name, team, home_team, away_team, market,
                   line_type, line, odds, bookmaker, commence_time, fetched_at
            FROM afl_player_props
            WHERE {where_clause}
            ORDER BY player_name, market, line_type, line, fetched_at DESC
            """
        )

        with db.engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().fetchall()

        props = []
        for r in rows:
            row = dict(r)
            row["commence_time"] = str(row.get("commence_time", "") or "")
            row["fetched_at"] = str(row.get("fetched_at", "") or "")
            props.append(row)

        # Build distinct match list for UI filter
        matches = sorted(
            {
                f"{r.get('home_team', '')} vs {r.get('away_team', '')}"
                for r in props
                if r.get("home_team") and r.get("away_team")
            }
        )

        return jsonify({"props": props, "count": len(props), "matches": matches})

    @app.route("/api/afl/player-home-away")
    @login_required
    def api_afl_player_home_away():
        player_id = request.args.get("player_id", type=int)
        name = request.args.get("name", "").strip()
        team = request.args.get("team", "").strip()
        season_from = request.args.get("season_from", 2022, type=int)

        if not player_id and not name:
            return jsonify({"error": "Provide player_id or name"}), 400

        seasons = [s for s in range(_db_latest_player_stats_season(db) or CURRENT_YEAR, season_from - 1, -1) if s >= 2019]

        rows = _resolve_player_rows(
            db=db,
            player_id=player_id,
            name=name,
            team=team,
            seasons=seasons,
            limit=500,
        )

        if not rows:
            return jsonify(
                {
                    "games": 0,
                    "season_from": season_from,
                    "home_games": [],
                    "away_games": [],
                    "home_avg": 0.0,
                    "away_avg": 0.0,
                    "home_best": 0,
                    "away_best": 0,
                }
            )

        grouped = _group_players(rows)

        if player_id and player_id in grouped:
            player = grouped[player_id]
        elif len(grouped) == 1:
            player = next(iter(grouped.values()))
        else:
            matches = [
                {
                    "player_id": p["player_id"],
                    "name": p["name"],
                    "team": p["team"],
                }
                for p in grouped.values()
            ]
            matches.sort(key=lambda x: (x["name"].lower(), x["team"].lower()))
            return jsonify(
                {
                    "error": "Multiple players matched",
                    "season_from": season_from,
                    "matches": matches,
                }
            ), 409

        games = sorted(player["games"], key=_sort_date_key, reverse=True)
        player_team = player["team"]

        home_games = [g for g in games if g.get("match_home_team") == player_team]
        away_games = [g for g in games if g.get("match_away_team") == player_team]

        return jsonify(
            {
                "player_id": player["player_id"],
                "name": player["name"],
                "team": player_team,
                "season_from": season_from,
                "games": len(games),
                "home_games": [_format_game_log_row(g, player_team) for g in home_games],
                "away_games": [_format_game_log_row(g, player_team) for g in away_games],
            }
        )
    @app.route("/api/afl/match-props")
    @login_required
    def api_afl_match_props():
        home_team = request.args.get("home", "").strip()
        away_team = request.args.get("away", "").strip()

        if not home_team or not away_team:
            return jsonify({"error": "home and away params required"}), 400

        props = _db_get_match_props(db, home_team, away_team)
        return jsonify({"home_team": home_team, "away_team": away_team, "props": props, "count": len(props)})

    @app.route("/api/afl/sync/fixtures", methods=["POST"])
    @login_required
    def api_afl_sync_fixtures():
        year = request.json.get("year", CURRENT_YEAR) if request.json else CURRENT_YEAR
        round_number = request.json.get("round") if request.json else None

        try:
            games = fetch_squiggle_games(year, round_number)
            count = upsert_games(db, games)
            log_sync(db, "squiggle_games", season=year, round_num=round_number, rows=count)
            return jsonify({"status": "ok", "rows_synced": count, "year": year})
        except Exception as exc:
            log_sync(db, "squiggle_games", season=year, status="error", error=str(exc))
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/api/afl/sync/ladder", methods=["POST"])
    @login_required
    def api_afl_sync_ladder():
        year = request.json.get("year", CURRENT_YEAR) if request.json else CURRENT_YEAR

        try:
            current_round = fetch_squiggle_current_round(year)
            standings = fetch_squiggle_standings(year, current_round)
            count = upsert_standings(db, standings, year, current_round)
            log_sync(db, "squiggle_standings", season=year, round_num=current_round, rows=count)
            return jsonify({"status": "ok", "rows_synced": count, "round": current_round})
        except Exception as exc:
            log_sync(db, "squiggle_standings", season=year, status="error", error=str(exc))
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/api/afl/sync/player-stats", methods=["POST"])
    @login_required
    def api_afl_sync_player_stats():
        season = request.json.get("season", CURRENT_YEAR) if request.json else CURRENT_YEAR

        try:
            stats = fetch_fryzigg_player_stats(season)
            count = upsert_player_stats(db, stats, season)
            log_sync(db, "fryzigg", season=season, rows=count)
            return jsonify({"status": "ok", "rows_synced": count, "season": season})
        except RuntimeError as exc:
            # Fryzigg RDS is intentionally blocked outside cron context.
            # Return 503 (Service Unavailable) with a clear message.
            msg = str(exc)
            logger.warning("Fryzigg sync blocked: %s", msg)
            log_sync(db, "fryzigg", season=season, status="blocked", error=msg)
            return jsonify({
                "status": "blocked",
                "message": "Fryzigg RDS sync is blocked in this context. "
                           "Set AFL_CRON_MODE=1, FLASK_ENV=development, or ALLOW_FRYZIGG_RDS=1.",
                "season": season,
            }), 503
        except Exception as exc:
            logger.error("Fryzigg sync error for season %s: %s (%s)", season, exc, type(exc).__name__)
            log_sync(db, "fryzigg", season=season, status="error", error=str(exc))
            return jsonify({
                "status": "error",
                "message": f"Player stats sync failed ({type(exc).__name__}). Check server logs.",
            }), 500

    @app.route("/api/afl/player-vs-venue")
    @login_required
    def api_afl_player_vs_venue():
        player_id = request.args.get("player_id", type=int)
        venue = request.args.get("venue", "").strip()
        season_from = request.args.get("season_from", CURRENT_YEAR - 4, type=int)

        if not player_id:
            return jsonify({"error": "player_id parameter required"}), 400
        if not venue:
            return jsonify({"error": "venue parameter required"}), 400

        # Expand the venue to all known aliases (e.g. "MCG" → also search
        # "Melbourne Cricket Ground" and "M.C.G.") so Squiggle short-names
        # match Fryzigg/fitzRoy full-names already stored in the DB.
        venue_aliases = _venue_search_names(venue)
        alias_clauses = " OR ".join(
            f"LOWER(venue_name) LIKE LOWER(:v{i})" for i in range(len(venue_aliases))
        )
        venue_params = {f"v{i}": f"%{name}%" for i, name in enumerate(venue_aliases)}

        sql = db.text(f"""
            SELECT * FROM (
                SELECT DISTINCT ON (match_date, match_home_team, match_away_team) *
                FROM afl_player_stats
                WHERE player_id = :player_id
                  AND season >= :season_from
                  AND ({alias_clauses})
                ORDER BY match_date, match_home_team, match_away_team, id DESC
            ) deduped
            ORDER BY match_date DESC
        """)

        with db.engine.connect() as conn:
            rows = conn.execute(
                sql,
                {
                    "player_id": player_id,
                    "season_from": season_from,
                    **venue_params,
                },
            ).mappings().fetchall()

        rows = [dict(r) for r in rows]

        if not rows:
            return jsonify({
                "venue": venue,
                "season_from": season_from,
                "games": 0,
                "averages": {},
                "hit_rates": {},
                "game_log": [],
            })

        averages = get_player_season_averages(rows)

        return jsonify({
            "venue": venue,
            "season_from": season_from,
            "games": len(rows),
            "averages": averages,
            "game_log": [_format_game_log_row(g, g.get("player_team", "")) for g in rows],
        })

    @app.route("/api/afl/disposal-lines")
    @login_required
    def api_afl_disposal_lines():
        """Return per-player disposal hit rates at 10/15/20/25/30+ thresholds.

        Accepts ``home`` and/or ``away`` team names.  Returns every player from
        those teams who appeared in the current (or requested) season, together
        with their season average, last-5 average, and hit-rate % at each of
        the standard disposal-line thresholds.  No Odds API key required.
        """
        home_team = request.args.get("home", "").strip()
        away_team = request.args.get("away", "").strip()
        requested_season = request.args.get("year", type=int)

        if not home_team and not away_team:
            return jsonify({"error": "Provide home and/or away team"}), 400

        effective_season = _resolve_stats_season(db, requested_season)
        effective_seasons = _resolve_stats_seasons(db, requested_season)

        DISPOSAL_LINES = [10, 15, 20, 25, 30]

        teams = [t for t in [home_team, away_team] if t]
        all_players = []

        for team in teams:
            rows = _resolve_player_rows(
                db=db,
                player_id=None,
                name="",
                team=team,
                seasons=effective_seasons,
                limit=500,
            )

            if not rows:
                continue

            grouped = _group_players(rows)

            for player_id, player in grouped.items():
                games = sorted(player["games"], key=_sort_date_key, reverse=True)
                # Only use games from the effective (current) season for the
                # "form" columns; the hit-rate calculation uses all available
                # seasons so sample sizes stay meaningful.
                season_games = [g for g in games if g.get("season") == effective_season]

                if not season_games:
                    continue

                season_avg = _safe_avg(season_games, "disposals")
                last5_avg = _safe_avg(season_games[:5], "disposals")

                # Hit-rate at each threshold uses all loaded seasons for a
                # larger, more reliable sample.
                hit_rates: dict[str, float] = {}
                for line in DISPOSAL_LINES:
                    total = len(games)
                    hits = sum(
                        1 for g in games if (g.get("disposals") or 0) >= line
                    )
                    hit_rates[str(line)] = round(hits / total * 100, 1) if total else 0.0

                all_players.append(
                    {
                        "player_id": player_id,
                        "name": player["name"],
                        "team": player["team"],
                        "games": len(season_games),
                        "season_avg": round(season_avg, 1) if season_avg is not None else 0.0,
                        "last5_avg": round(last5_avg, 1) if last5_avg is not None else 0.0,
                        "hit_rates": hit_rates,
                    }
                )

        all_players.sort(key=lambda x: x.get("season_avg", 0), reverse=True)

        return jsonify(
            {
                "home_team": home_team,
                "away_team": away_team,
                "season": effective_season,
                "players": all_players,
                "count": len(all_players),
                "disposal_lines": DISPOSAL_LINES,
            }
        )

    @app.route("/api/afl/sync/props", methods=["POST"])
    @login_required
    def api_afl_sync_props():
        api_key = get_odds_api_key()
        if not api_key:
            return jsonify({"status": "error", "message": "ODDS_API_KEY not configured"}), 400

        body = request.json or {}
        load_all = body.get("all_markets", True)
        market = body.get("market")

        # When all_markets=True (the default) fetch every supported market in
        # one batch — this is the same number of Odds API calls as fetching one
        # market, but returns the complete prop dataset in a single click.
        markets_arg = None if load_all or not market else market

        try:
            props = fetch_afl_player_props(api_key=api_key, markets=markets_arg)
            count = upsert_player_props(db, props)
            log_sync(db, "odds_api", rows=count)
            return jsonify({
                "status": "ok",
                "rows_synced": count,
                "markets": "all" if markets_arg is None else markets_arg,
            })
        except Exception as exc:
            log_sync(db, "odds_api", status="error", error=str(exc))
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/api/afl/sync/match-markets", methods=["POST"])
    @login_required
    def api_afl_sync_match_markets():
        api_key = get_odds_api_key()
        if not api_key:
            return jsonify({"status": "error", "message": "ODDS_API_KEY not configured"}), 400
        rows = fetch_afl_h2h_spread_odds(api_key=api_key)
        count = upsert_match_markets(db, rows)
        log_sync(db, "odds_api_match_markets", season=CURRENT_YEAR, rows=count)
        return jsonify({"status": "ok", "rows_synced": count})

    @app.route("/api/afl/match-predictions")
    @login_required
    def api_afl_match_predictions():
        year = request.args.get("year", CURRENT_YEAR, type=int)
        round_num = request.args.get("round", type=int)

        # Default to the current completed round, same pattern as value-finder / player-props
        if round_num is None:
            round_num = _db_current_round(db, year)

        params = {"year": year, "round_num": round_num, "limit": 100}

        sql = db.text("""
            WITH team_match_stats AS (
                SELECT
                    ps.match_date,
                    LOWER(TRIM(ps.player_team)) AS team_key,
                    (
                        0.90 * SUM(COALESCE(ps.inside_fifties, 0)) +
                        1.15 * SUM(COALESCE(ps.clearances, 0)) +
                        1.30 * SUM(COALESCE(ps.contested_possessions, 0)) +
                        0.75 * SUM(COALESCE(ps.score_involvements, 0)) +
                        0.04 * SUM(COALESCE(ps.metres_gained, 0)) +
                        0.50 * SUM(COALESCE(ps.tackles, 0)) +
                        0.45 * SUM(COALESCE(ps.intercepts, 0)) +
                        0.18 * SUM(COALESCE(ps.disposals, 0)) -
                        0.75 * SUM(COALESCE(ps.turnovers, 0)) -
                        0.65 * SUM(COALESCE(ps.clangers, 0)) -
                        0.35 * SUM(COALESCE(ps.free_kicks_against, 0))
                    ) AS team_rating
                FROM afl_player_stats ps
                WHERE COALESCE(ps.player_team, '') <> ''
                GROUP BY ps.match_date, LOWER(TRIM(ps.player_team))
            ),
            team_rolling AS (
                -- Rolling 5-game average rating per team, ordered by match date
                SELECT
                    match_date,
                    team_key,
                    AVG(team_rating) OVER (
                        PARTITION BY team_key
                        ORDER BY match_date
                        ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                    ) AS rolling_avg_5
                FROM team_match_stats
            ),
            team_latest_before AS (
                -- For each game, pick the most recent rolling rating for each team
                -- using only matches played BEFORE that game's date
                SELECT DISTINCT ON (g.id, tk.team_key)
                    g.id   AS game_id,
                    tk.team_key,
                    tr.rolling_avg_5
                FROM afl_games g
                JOIN (SELECT DISTINCT team_key FROM team_match_stats) tk ON TRUE
                JOIN team_rolling tr
                    ON tr.team_key   = tk.team_key
                   AND tr.match_date < g.date::DATE
                WHERE EXTRACT(YEAR FROM g.date) = :year
                ORDER BY g.id, tk.team_key, tr.match_date DESC
            )
            SELECT
                g.id       AS match_id,
                g.date,
                g.round,
                g.venue,
                g.hteam,
                g.ateam,
                g.hteamid,
                g.ateamid,
                g.complete,
                g.hscore,
                g.ascore,
                home_r.rolling_avg_5 AS home_rating,
                away_r.rolling_avg_5 AS away_rating,
                (home_r.rolling_avg_5 - away_r.rolling_avg_5) AS predicted_margin
            FROM afl_games g
            LEFT JOIN team_latest_before home_r
                ON home_r.game_id  = g.id
               AND home_r.team_key = LOWER(TRIM(g.hteam))
            LEFT JOIN team_latest_before away_r
                ON away_r.game_id  = g.id
               AND away_r.team_key = LOWER(TRIM(g.ateam))
            WHERE EXTRACT(YEAR FROM g.date) = :year
              AND g.round = :round_num
              AND COALESCE(TRIM(g.hteam), '') <> ''
              AND COALESCE(TRIM(g.ateam), '') <> ''
              AND COALESCE(g.hteamid, 0) <> 0
              AND COALESCE(g.ateamid, 0) <> 0
            ORDER BY g.date ASC
            LIMIT :limit
        """)
        rows = [dict(r._mapping) for r in db.session.execute(sql, params)]

        out = []
        for r in rows:
            predicted_margin_raw = r.get("predicted_margin")
            predicted_margin = float(predicted_margin_raw) if predicted_margin_raw is not None else None
            hscore = r.get("hscore")
            ascore = r.get("ascore")
            complete = r.get("complete") or 0
            actual_margin = (hscore - ascore) if (complete == 100 and hscore is not None and ascore is not None) else None
            out.append({
                "match_id": r["match_id"],
                "year": year,
                "round": r.get("round"),
                "home_team": r["hteam"],
                "away_team": r["ateam"],
                "actual_margin": actual_margin,
                "home_rating": float(r["home_rating"]) if r.get("home_rating") is not None else None,
                "away_rating": float(r["away_rating"]) if r.get("away_rating") is not None else None,
                "predicted_margin": round(predicted_margin, 1) if predicted_margin is not None else None,
                "predicted_winner": (r["hteam"] if predicted_margin >= 0 else r["ateam"]) if predicted_margin is not None else None,
            })
        return jsonify({"year": year, "round": round_num, "matches": out, "count": len(out)})

    @app.route("/api/afl/match-markets")
    @login_required
    def api_afl_match_markets():
        year = request.args.get("year", CURRENT_YEAR, type=int)
        sql = db.text("""
            SELECT
                event_id, home_team, away_team, commence_time, bookmaker, market,
                line, odds, selection_name, fetched_at
            FROM afl_match_markets
            WHERE EXTRACT(YEAR FROM COALESCE(commence_time, fetched_at)) = :year
            ORDER BY COALESCE(commence_time, fetched_at) DESC, home_team, away_team, bookmaker
            LIMIT 1500
        """)
        rows = [dict(r._mapping) for r in db.session.execute(sql, {"year": year})]
        return jsonify({"year": year, "markets": rows, "count": len(rows)})

    @app.route("/api/afl/betting-edges")
    @login_required
    def api_afl_betting_edges():
        import math
        year = request.args.get("year", CURRENT_YEAR, type=int)
        min_edge = request.args.get("min_edge", 2.0, type=float)

        # Logistic scale: tuned so a 35-pt model margin ≈ 73% win probability.
        # This is intentionally conservative given the model ratings aren't
        # calibrated to actual point margins.
        LOGISTIC_SCALE = 35.0

        # Rolling team ratings CTE (same logic as match-predictions endpoint),
        # but restricted to upcoming fixtures that have market data.
        # home_team / away_team in afl_match_markets are pre-normalised via _team().
        sql = db.text("""
            WITH team_match_stats AS (
                SELECT
                    ps.match_date,
                    LOWER(TRIM(ps.player_team)) AS team_key,
                    (
                        0.90 * SUM(COALESCE(ps.inside_fifties, 0)) +
                        1.15 * SUM(COALESCE(ps.clearances, 0)) +
                        1.30 * SUM(COALESCE(ps.contested_possessions, 0)) +
                        0.75 * SUM(COALESCE(ps.score_involvements, 0)) +
                        0.04 * SUM(COALESCE(ps.metres_gained, 0)) +
                        0.50 * SUM(COALESCE(ps.tackles, 0)) +
                        0.45 * SUM(COALESCE(ps.intercepts, 0)) +
                        0.18 * SUM(COALESCE(ps.disposals, 0)) -
                        0.75 * SUM(COALESCE(ps.turnovers, 0)) -
                        0.65 * SUM(COALESCE(ps.clangers, 0)) -
                        0.35 * SUM(COALESCE(ps.free_kicks_against, 0))
                    ) AS team_rating
                FROM afl_player_stats ps
                WHERE COALESCE(ps.player_team, '') <> ''
                GROUP BY ps.match_date, LOWER(TRIM(ps.player_team))
            ),
            team_rolling AS (
                SELECT
                    match_date,
                    team_key,
                    AVG(team_rating) OVER (
                        PARTITION BY team_key
                        ORDER BY match_date
                        ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                    ) AS rolling_avg_5
                FROM team_match_stats
            ),
            team_latest_before AS (
                SELECT DISTINCT ON (g.id, tk.team_key)
                    g.id   AS game_id,
                    tk.team_key,
                    tr.rolling_avg_5
                FROM afl_games g
                JOIN (SELECT DISTINCT team_key FROM team_match_stats) tk ON TRUE
                JOIN team_rolling tr
                    ON tr.team_key   = tk.team_key
                   AND tr.match_date < g.date::DATE
                WHERE EXTRACT(YEAR FROM g.date) = :year
                  AND g.complete < 100
                ORDER BY g.id, tk.team_key, tr.match_date DESC
            ),
            game_predictions AS (
                SELECT
                    g.id  AS game_id,
                    g.hteam,
                    g.ateam,
                    (home_r.rolling_avg_5 - away_r.rolling_avg_5) AS predicted_margin
                FROM afl_games g
                LEFT JOIN team_latest_before home_r
                    ON home_r.game_id  = g.id
                   AND home_r.team_key = LOWER(TRIM(g.hteam))
                LEFT JOIN team_latest_before away_r
                    ON away_r.game_id  = g.id
                   AND away_r.team_key = LOWER(TRIM(g.ateam))
                WHERE EXTRACT(YEAR FROM g.date) = :year
                  AND g.complete < 100
            )
            SELECT
                mm.event_id,
                mm.home_team,
                mm.away_team,
                mm.commence_time,
                mm.bookmaker,
                mm.market,
                mm.line,
                mm.odds,
                mm.selection_name,
                gp.predicted_margin
            FROM afl_match_markets mm
            LEFT JOIN game_predictions gp
                ON LOWER(TRIM(gp.hteam)) = LOWER(TRIM(mm.home_team))
               AND LOWER(TRIM(gp.ateam)) = LOWER(TRIM(mm.away_team))
            WHERE EXTRACT(YEAR FROM COALESCE(mm.commence_time, mm.fetched_at)) = :year
              AND mm.odds IS NOT NULL
              AND mm.odds > 1.0
            ORDER BY COALESCE(mm.commence_time, NOW()) DESC
            LIMIT 3000
        """)
        rows = [dict(r._mapping) for r in db.session.execute(sql, {"year": year})]

        grouped = defaultdict(list)
        for row in rows:
            key = (
                row.get("event_id"),
                row.get("market"),
                row.get("line"),
                row.get("selection_name"),
            )
            grouped[key].append(row)

        edges = []
        for same_outcome in grouped.values():
            if len(same_outcome) < 2:
                continue
            best = max(same_outcome, key=lambda x: x.get("odds") or 0)
            mean_odds = sum((r.get("odds") or 0) for r in same_outcome) / len(same_outcome)
            if mean_odds <= 1:
                continue
            implied_prob = 1.0 / mean_odds
            best_prob = 1.0 / (best.get("odds") or 1.0)
            edge_pct = (implied_prob - best_prob) * 100.0
            if edge_pct < min_edge:
                continue

            market = best.get("market", "")
            predicted_margin = best.get("predicted_margin")
            line = best.get("line")
            selection_name = (best.get("selection_name") or "").strip()
            home_team = (best.get("home_team") or "").strip()

            # Normalise the selection name (Odds API → Squiggle) so we can
            # reliably detect which side of the market this selection is on.
            normalised_selection = _normalise_team(selection_name)
            is_home = normalised_selection.lower() == home_team.lower()

            # ── Spread edge ──────────────────────────────────────────────────
            # line < 0  → home selection (home gives points)
            # line > 0  → away selection (away receives points)
            # Adjusted spread result from the selection's perspective:
            #   home: predicted_margin + line  (positive → home covers)
            #   away: -predicted_margin + line (positive → away covers)
            line_edge = None
            spread_line = None
            if market == "spreads" and predicted_margin is not None and line is not None:
                spread_line = line
                if line <= 0:  # home team selection (giving points)
                    line_edge = round(predicted_margin + line, 1)
                else:          # away team selection (receiving points)
                    line_edge = round(-predicted_margin + line, 1)

            # ── H2H probability edge ─────────────────────────────────────────
            # Only for standard back markets (not Betfair lay).
            model_prob = None
            market_prob = None
            prob_edge = None
            if market == "h2h" and predicted_margin is not None:
                raw_home_prob = 1.0 / (1.0 + math.exp(-predicted_margin / LOGISTIC_SCALE))
                raw_model_prob = raw_home_prob if is_home else (1.0 - raw_home_prob)
                model_prob  = round(raw_model_prob, 3)
                market_prob = round(1.0 / (best.get("odds") or 1.0), 3)
                prob_edge   = round((model_prob - market_prob) * 100.0, 2)

            edges.append({
                **best,
                "bookmakers_compared": len(same_outcome),
                "consensus_odds": round(mean_odds, 3),
                "edge_pct": round(edge_pct, 2),
                "predicted_margin": round(predicted_margin, 1) if predicted_margin is not None else None,
                "spread_line": spread_line,
                "line_edge": line_edge,
                "model_prob": model_prob,
                "market_prob": market_prob,
                "prob_edge": prob_edge,
            })

        edges.sort(key=lambda x: x.get("edge_pct", 0), reverse=True)
        return jsonify({"year": year, "min_edge": min_edge, "edges": edges[:200], "count": len(edges)})

    @app.route("/api/afl/refresh", methods=["POST"])
    @login_required
    def api_afl_refresh():
        results = {}
        year = CURRENT_YEAR
        stats_season = _db_latest_player_stats_season(db) or (CURRENT_YEAR - 1)

        try:
            games = fetch_squiggle_games(year)
            results["fixtures"] = upsert_games(db, games)
        except Exception as exc:
            results["fixtures_error"] = str(exc)

        try:
            current_round = fetch_squiggle_current_round(year)
            standings = fetch_squiggle_standings(year, current_round)
            results["ladder"] = upsert_standings(db, standings, year, current_round)
        except Exception as exc:
            results["ladder_error"] = str(exc)

        results["player_stats"] = "skipped — run via cron only"

        return jsonify({"status": "ok", "stats_season": stats_season, "synced": results})

    @app.route("/api/afl/player-headshot/<int:player_id>")
    def api_afl_player_headshot(player_id):
        """Proxy AFL player headshot images through this server to avoid CDN hotlink blocks."""
        if not player_id or player_id <= 0:
            abort(404)

        # Always cache under player_id so frontend and direct requests share the same slot.
        cache_key = str(player_id)

        if cache_key in _headshot_cache:
            entry = _headshot_cache[cache_key]
            if entry is None:
                abort(404)
            content, mime = entry
            resp = make_response(content)
            resp.headers["Content-Type"] = mime
            resp.headers["Cache-Control"] = "public, max-age=86400"
            return resp

        first_name = request.args.get("first_name")
        last_name = request.args.get("last_name")

        fantasy_id = None
        if first_name and last_name:
            fantasy_id = _fantasy_photo_id_from_name(first_name, last_name)

        # fantasy_id is the AFL Fantasy photo ID; player_id is the ChampID.
        # They are different ID spaces — keep them separate in the URL list.
        photo_id = fantasy_id or player_id
        cdn_urls = [
            f"https://fantasy.afl.com.au/assets/media/players/afl/{photo_id}_450.png",
            f"https://fantasy.afl.com.au/assets/mug-shots/afl/{photo_id}.webp",
            # ChampID URL must use the original player_id, not the fantasy ID.
            f"https://www.afl.com.au/staticfile/AFL%20Tenant/AFL/Players/ChampIDImages/{player_id}.png",
        ]
        headers = {"User-Agent": "Mozilla/5.0 (compatible; TheFormAnalyst/1.0)"}

        for url in cdn_urls:
            try:
                cdn_resp = _requests.get(url, timeout=5, headers=headers)
                if cdn_resp.ok:
                    mime = cdn_resp.headers.get("Content-Type", "image/png")
                    entry = (cdn_resp.content, mime)
                    if len(_headshot_cache) < _HEADSHOT_CACHE_MAX:
                        _headshot_cache[cache_key] = entry
                    resp = make_response(cdn_resp.content)
                    resp.headers["Content-Type"] = mime
                    resp.headers["Cache-Control"] = "public, max-age=86400"
                    return resp
            except Exception:
                continue

        if len(_headshot_cache) < _HEADSHOT_CACHE_MAX:
            _headshot_cache[cache_key] = None
        abort(404)
  
    @app.route("/api/afl/debug")
    @login_required
    def api_afl_debug():
        """Diagnostic endpoint — confirms logo and headshot keys in API payloads."""
        year = request.args.get("year", CURRENT_YEAR, type=int)

        fixtures = _db_get_fixtures(db, year=year)[:3]
        standings = _db_get_standings(db, year=year)[:3]

        fixture_sample = [
            {k: v for k, v in f.items() if k in (
                "id", "hteam", "hteamid", "ateam", "ateamid",
                "hteam_logo_url", "ateam_logo_url",
            )}
            for f in fixtures
        ]
        standing_sample = [
            {k: v for k, v in s.items() if k in (
                "rank", "team", "teamid", "team_logo_url",
            )}
            for s in standings
        ]

        headshot_sample = None
        try:
            with db.engine.connect() as conn:
                row = conn.execute(db.text(
                    "SELECT player_id, player_first_name, player_last_name, "
                    "player_headshot_url "
                    "FROM afl_player_stats "
                    "WHERE player_headshot_url IS NOT NULL LIMIT 1"
                )).mappings().fetchone()
            if row:
                headshot_sample = dict(row)
        except Exception:
            headshot_sample = {"error": "query failed — check server logs"}

        return jsonify({
            "fixtures": fixture_sample,
            "standings": standing_sample,
            "headshot_sample": headshot_sample,
        })

# ─────────────────────────────────────────────
# PRIVATE DB QUERY HELPERS
# ─────────────────────────────────────────────
def _fantasy_photo_id_from_name(first_name: str, last_name: str) -> "int | None":
    """Look up the AFL Fantasy photo id for a player by name."""
    key = f"{first_name.strip().lower()}|{last_name.strip().lower()}"
    if key in _fantasy_player_id_cache:
        return _fantasy_player_id_cache[key]
    try:
        resp = _requests.get(
            "https://fantasy.afl.com.au/data/afl/players.json",
            timeout=3,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TheFormAnalyst/1.0)"},
        )
        resp.raise_for_status()
        data = resp.json()
        players = data.get("players", data)
        for p in players:
            fn = str(p.get("first_name") or p.get("firstname") or p.get("given_name") or "").strip().lower()
            ln = str(p.get("last_name") or p.get("lastname") or p.get("surname") or "").strip().lower()
            pid = p.get("id") or p.get("player_id")
            if fn == first_name.strip().lower() and ln == last_name.strip().lower() and pid:
                _fantasy_player_id_cache[key] = int(pid)
                return int(pid)
    except Exception:
        return None
    return None

def _abs_logo(url: str | None) -> str | None:
    """Return an absolute logo URL, prefixing Squiggle's site for relative paths."""
    if not url:
        return None
    if url.startswith("/"):
        return SQUIGGLE_SITE + url
    return url


def _db_current_round(db, year: int) -> int:
    sql = db.text(
        """
        SELECT COALESCE(MAX(round), 1)
        FROM afl_games
        WHERE year = :year AND complete = 100
        """
    )
    with db.engine.connect() as conn:
        result = conn.execute(sql, {"year": year}).scalar()
    return result or 1


def _db_next_game(db, year: int) -> dict:
    sql = db.text(
        """
        SELECT hteam, ateam, date, venue
        FROM afl_games
        WHERE year = :year AND complete < 100
        ORDER BY date ASC
        LIMIT 1
        """
    )
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
    try:
        with db.engine.connect() as conn:
            return conn.execute(db.text(sql_str)).scalar() or 0
    except Exception:
        return 0


def _db_latest_player_stats_season(db) -> int | None:
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
    latest = _db_latest_player_stats_season(db)
    if requested_season and _db_has_player_stats_for_season(db, requested_season):
        return requested_season
    if latest:
        return latest
    return requested_season or (CURRENT_YEAR - 1)


def _resolve_stats_seasons(db, requested_season: int | None) -> list[int]:
    effective_season = _resolve_stats_season(db, requested_season)
    seasons = [effective_season, effective_season - 1, effective_season - 2]
    return [season for season in seasons if season >= 2019]


def _normalize_whitespace(value: str) -> str:
    return " ".join((value or "").strip().split())


def _db_player_search(
    db,
    name: str = "",
    team: str = "",
    season: int | None = None,
    seasons: list[int] | None = None,
    limit: int = 50,
) -> list[dict]:
    conditions = []
    params = {"limit": limit}

    if name:
        normalized_name = _normalize_whitespace(name).lower()
        conditions.append("LOWER(TRIM(player_first_name || ' ' || player_last_name)) LIKE :name")
        params["name"] = f"%{normalized_name}%"

    if team:
        conditions.append("LOWER(player_team) = :team")
        params["team"] = team.lower()

    if seasons:
        conditions.append("season = ANY(:seasons)")
        params["seasons"] = seasons
    elif season is not None:
        conditions.append("season = :season")
        params["season"] = season

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = db.text(
        f"""
        SELECT * FROM (
            SELECT DISTINCT ON (player_id, match_date, match_home_team, match_away_team) *
            FROM afl_player_stats
            {where}
            ORDER BY player_id, match_date, match_home_team, match_away_team, id DESC
        ) deduped
        ORDER BY season DESC, match_date DESC
        LIMIT :limit
        """
    )

    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().fetchall()

    return [dict(row) for row in rows]


def _db_player_by_id(
    db,
    player_id: int,
    season: int | None = None,
    seasons: list[int] | None = None,
    limit: int = 100,
) -> list[dict]:
    params = {"player_id": player_id, "limit": limit}
    season_filter = ""

    if seasons:
        params["seasons"] = seasons
        season_filter = "AND season = ANY(:seasons)"
    elif season is not None:
        params["season"] = season
        season_filter = "AND season = :season"

    sql = db.text(
        f"""
        SELECT * FROM (
            SELECT DISTINCT ON (match_date, match_home_team, match_away_team) *
            FROM afl_player_stats
            WHERE player_id = :player_id
            {season_filter}
            ORDER BY match_date, match_home_team, match_away_team, id DESC
        ) deduped
        ORDER BY season DESC, match_date DESC
        LIMIT :limit
        """
    )

    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().fetchall()

    return [dict(row) for row in rows]


def _resolve_player_rows(
    db,
    player_id: int | None = None,
    name: str = "",
    team: str = "",
    season: int | None = None,
    seasons: list[int] | None = None,
    limit: int = 200,
) -> list[dict]:
    if player_id:
        exact_rows = _db_player_by_id(
            db,
            player_id=player_id,
            season=season,
            seasons=seasons,
            limit=limit,
        )
        if exact_rows:
            return exact_rows

    if name:
        name_rows = _db_player_search(
            db,
            name=name,
            team=team,
            season=season,
            seasons=seasons,
            limit=limit,
        )
        if not name_rows:
            return []

        if player_id:
            matched_rows = [row for row in name_rows if row.get("player_id") == player_id]
            if matched_rows:
                return matched_rows

        return name_rows

    return []


def _db_player_vs_opponent(
    db,
    player_id: int | None = None,
    name: str = "",
    team: str = "",
    opponent: str = "",
    season_from: int = 2019,
) -> list[dict]:
    # Exact match only — prevents "Melbourne" from matching "North Melbourne"
    params = {"season_from": season_from, "opp": opponent.lower().strip()}
    filters = [
        "season >= :season_from",
        "(LOWER(match_home_team) = :opp OR LOWER(match_away_team) = :opp)",
    ]

    if player_id:
        filters.append("player_id = :player_id")
        params["player_id"] = player_id
    elif name:
        filters.append("LOWER(TRIM(player_first_name || ' ' || player_last_name)) LIKE :name")
        params["name"] = f"%{_normalize_whitespace(name).lower()}%"
        if team:
            filters.append("LOWER(player_team) = :team")
            params["team"] = team.lower()
    else:
        return []

    sql = db.text(
        f"""
        SELECT * FROM (
            SELECT DISTINCT ON (player_id, match_date, match_home_team, match_away_team) *
            FROM afl_player_stats
            WHERE {' AND '.join(filters)}
            ORDER BY player_id, match_date, match_home_team, match_away_team, id DESC
        ) deduped
        ORDER BY match_date DESC
        """
    )

    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().fetchall()

    return [dict(row) for row in rows]


def _db_get_fixtures(db, year: int, round_number: int | None = None) -> list[dict]:
    params = {"year": year}
    round_filter = ""

    if round_number:
        round_filter = "AND round = :round"
        params["round"] = round_number

    sql = db.text(
        f"""
        SELECT g.*,
               hl.logo_url AS hteam_logo_url,
               al.logo_url AS ateam_logo_url
        FROM afl_games g
        LEFT JOIN afl_team_logos hl ON hl.squiggle_id = g.hteamid
        LEFT JOIN afl_team_logos al ON al.squiggle_id = g.ateamid
        WHERE g.year = :year
          AND g.complete < 100
          {round_filter}
        ORDER BY g.date ASC
        LIMIT 50
        """
    )

    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().fetchall()

    result = []
    for row in rows:
        d = dict(row)
        d["hteam_logo_url"] = _abs_logo(d.get("hteam_logo_url"))
        d["ateam_logo_url"] = _abs_logo(d.get("ateam_logo_url"))
        result.append(d)
    return result


def _db_get_standings(db, year: int, round_number: int | None = None) -> list[dict]:
    params = {"year": year}

    if round_number:
        params["round"] = round_number
        round_filter = "AND s.round = :round"
    else:
        round_filter = "AND s.round = (SELECT MAX(round) FROM afl_standings WHERE year = :year)"

    sql = db.text(
        f"""
        SELECT s.*, tl.logo_url AS team_logo_url
        FROM afl_standings s
        LEFT JOIN afl_team_logos tl ON tl.squiggle_id = s.teamid
        WHERE s.year = :year
          {round_filter}
        ORDER BY s.rank ASC
        """
    )

    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().fetchall()

    result = []
    for row in rows:
        d = dict(row)
        d["team_logo_url"] = _abs_logo(d.get("team_logo_url"))
        result.append(d)
    return result


def _db_get_props(
    db,
    market: str = "player_disposals",
    home_team: str | None = None,
    away_team: str | None = None,
    min_line: float | None = None,
    max_line: float | None = None,
) -> list[dict]:
    canonical_market = _normalise_prop_market(market)
    market_aliases = [canonical_market]
    _legacy_market_aliases = {
        "player_kicks": ["player_kicks_over"],
        "player_handballs": ["player_handballs_over"],
        "player_marks": ["player_marks_over"],
        "player_tackles": ["player_tackles_over"],
        "player_goals": ["player_goals_scored_over", "player_goals_over"],
    }
    market_aliases.extend(_legacy_market_aliases.get(canonical_market, []))

    conditions = [
        "market = ANY(:markets)",
        "fetched_at > NOW() - INTERVAL '7 days'",
    ]
    params: dict = {"markets": market_aliases}

    if home_team:
        conditions.append(
            "(LOWER(home_team) = LOWER(:home_team) OR LOWER(away_team) = LOWER(:home_team))"
        )
        params["home_team"] = home_team.strip()
    if away_team:
        conditions.append(
            "(LOWER(home_team) = LOWER(:away_team) OR LOWER(away_team) = LOWER(:away_team))"
        )
        params["away_team"] = away_team.strip()
    if min_line is not None:
        conditions.append("line >= :min_line")
        params["min_line"] = min_line
    if max_line is not None:
        conditions.append("line <= :max_line")
        params["max_line"] = max_line

    where_clause = " AND ".join(conditions)
    # where_clause is built entirely from hardcoded string literals above;
    # all user-supplied values are passed via the parameterised `params` dict.
    sql = db.text(
        f"""
        SELECT DISTINCT ON (player_name, line_type, line) *
        FROM afl_player_props
        WHERE {where_clause}
        ORDER BY player_name, line_type, line, fetched_at DESC
        """
    )
    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().fetchall()
    return [dict(row) for row in rows]


def _db_get_match_props(db, home_team: str, away_team: str) -> list[dict]:
    sql = db.text(
        """
        SELECT *
        FROM afl_player_props
        WHERE (LOWER(home_team) = LOWER(:home) OR LOWER(away_team) = LOWER(:home))
          AND (LOWER(home_team) = LOWER(:away) OR LOWER(away_team) = LOWER(:away))
          AND fetched_at > NOW() - INTERVAL '7 days'
        ORDER BY market, player_name, line_type
        """
    )
    with db.engine.connect() as conn:
        rows = conn.execute(
            sql,
            {"home": home_team.strip(), "away": away_team.strip()},
        ).mappings().fetchall()
    return [dict(row) for row in rows]


def _db_has_props(db, home_team: str, away_team: str) -> bool:
    sql = db.text(
        """
        SELECT 1
        FROM afl_player_props
        WHERE (LOWER(home_team) = LOWER(:home) OR LOWER(away_team) = LOWER(:home))
          AND fetched_at > NOW() - INTERVAL '7 days'
        LIMIT 1
        """
    )
    with db.engine.connect() as conn:
        result = conn.execute(sql, {"home": home_team.strip()}).fetchone()
    return result is not None


def _check_data_sources(db) -> dict:
    def _has_data(sql_str: str) -> bool:
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
            "SELECT 1 FROM afl_player_props WHERE fetched_at > NOW() - INTERVAL '24 hours' LIMIT 1"
        ),
    }


def afl_nightly_sync(app_context, db):
    logger.info("=== AFL nightly sync for season %s ===", CURRENT_YEAR)

    try:
        games = fetch_squiggle_games(CURRENT_YEAR)
        count = upsert_games(db, games)
        log_sync(db, "squiggle_games", season=CURRENT_YEAR, rows=count)
        logger.info("  ✓ Fixtures: %s games synced", count)
    except Exception as exc:
        logger.error("  ✗ Fixtures sync failed: %s", exc)

    try:
        current_round = fetch_squiggle_current_round(CURRENT_YEAR)
        standings = fetch_squiggle_standings(CURRENT_YEAR, current_round)
        count = upsert_standings(db, standings, CURRENT_YEAR, current_round)
        log_sync(db, "squiggle_standings", season=CURRENT_YEAR, round_num=current_round, rows=count)
        logger.info("  ✓ Ladder: %s teams synced (round %s)", count, current_round)
    except Exception as exc:
        logger.error("  ✗ Ladder sync failed: %s", exc)

    latest_stats_season = _db_latest_player_stats_season(db) or (CURRENT_YEAR - 1)
    seasons_to_try = sorted(
        {
            CURRENT_YEAR,
            latest_stats_season,
            latest_stats_season - 1,
            latest_stats_season - 2,
            latest_stats_season - 3,
        }
    )
    seasons_to_try = [season for season in seasons_to_try if season >= 2019]

    total_stats = 0

    for season in seasons_to_try:
        try:
            stats = fetch_fryzigg_player_stats(season)
            if not stats:
                logger.info("  - Fryzigg %s: no data returned", season)
                continue

            count = upsert_player_stats(db, stats, season)
            log_sync(db, "fryzigg", season=season, rows=count)
            total_stats += count
            logger.info("  ✓ Fryzigg %s: %s rows synced", season, count)
            time.sleep(1)
        except Exception as exc:
            log_sync(db, "fryzigg", season=season, status="error", error=str(exc))
            logger.error("  ✗ Fryzigg %s failed: %s", season, exc)

    logger.info("  ✓ Fryzigg total: %s rows", total_stats)

    try:
        api_key = get_odds_api_key()
    except Exception:
        api_key = ""

    if api_key:
        total_props = 0
        for market in [
            "player_disposals",
            "player_kicks",
            "player_marks",
            "player_tackles",
            "player_goals",
        ]:
            try:
                props = fetch_afl_player_props(api_key, market)
                count = upsert_player_props(db, props)
                log_sync(db, "odds_api", rows=count)
                total_props += count
                logger.info("  ✓ Props (%s): %s rows synced", market, count)
            except Exception as exc:
                logger.error("  ✗ Props sync failed for %s: %s", market, exc)
        logger.info("  ✓ Props total: %s rows synced", total_props)
    else:
        logger.info("  - Props: skipped (ODDS_API_KEY not configured)")

    logger.info("=== AFL sync complete ===")


# ─────────────────────────────────────────────
# VENUE ALIAS GROUPS
# Squiggle uses short names (MCG, SCG); Fryzigg/fitzRoy use full names.
# Any name in a group is treated as equivalent when querying venue history.
# ─────────────────────────────────────────────

_VENUE_GROUPS: list[list[str]] = [
    ["MCG", "Melbourne Cricket Ground", "M.C.G."],
    ["SCG", "Sydney Cricket Ground", "S.C.G."],
    ["Marvel Stadium", "Docklands", "Etihad Stadium"],
    ["GMHBA Stadium", "Kardinia Park", "Simonds Stadium"],
    ["Optus Stadium", "Perth Stadium"],
    ["People First Stadium", "Metricon Stadium", "Carrara", "Gold Coast Stadium"],
    ["ENGIE Stadium", "Spotless Stadium", "Sydney Showground", "Stadium Australia"],
    ["Blundstone Arena", "Bellerive Oval"],
    ["University of Tasmania Stadium", "York Park", "Aurora Stadium"],
    ["TIO Stadium", "Marrara"],
    ["Adelaide Oval", "Football Park"],
    ["Gabba", "Brisbane Cricket Ground"],
    ["Cazalys Stadium", "Cazalys"],
]


def _venue_search_names(venue: str) -> list[str]:
    """Return all known alias names for a given venue string."""
    v_lower = venue.lower().strip()
    if not v_lower:
        return [venue]
    for group in _VENUE_GROUPS:
        if any(v_lower == g.lower() or v_lower in g.lower() for g in group):
            return group
    return [venue]


def _sort_date_key(row: dict):
    return row.get("match_date") or ""


def _get_opponent(game: dict, player_team: str) -> str:
    home = game.get("match_home_team", "")
    away = game.get("match_away_team", "")
    return away if home == player_team else home


def _safe_avg(rows: list[dict], stat: str) -> float:
    values = [row.get(stat, 0) or 0 for row in rows]
    return round(sum(values) / len(values), 1) if values else 0.0


def _hit_rate(rows: list[dict], stat: str, line: float) -> float:
    if not rows:
        return 0.0
    hits = sum(1 for row in rows if (row.get(stat, 0) or 0) >= line)
    return round(hits / len(rows) * 100, 1)


def _group_players(rows: list[dict]) -> dict:
    players: dict[int, dict] = {}
    # Track seen games per player to deduplicate rows from different data sources
    # that share the same natural game identity but different match_ids.
    seen_games: dict[int, set] = {}

    for row in rows:
        player_id = row.get("player_id")
        if not player_id:
            continue

        if player_id not in players:
            players[player_id] = {
                "player_id": player_id,
                "name": f"{row.get('player_first_name', '')} {row.get('player_last_name', '')}".strip(),
                "first_name": row.get("player_first_name", ""),
                "last_name": row.get("player_last_name", ""),
                "team": row.get("player_team", ""),
                "guernsey": row.get("guernsey_number"),
                "height_cm": row.get("player_height_cm"),
                "weight_kg": row.get("player_weight_kg"),
                "games": [],
            }
            seen_games[player_id] = set()

        # Deduplicate by natural game identity — prevents two data-source rows
        # (different match_ids, same game) from both appearing in the game log.
        game_key = (
            str(row.get("match_date", "")),
            str(row.get("match_home_team", "") or "").lower(),
            str(row.get("match_away_team", "") or "").lower(),
        )
        if game_key in seen_games[player_id]:
            continue
        seen_games[player_id].add(game_key)

        players[player_id]["games"].append(dict(row))

    return players


def _format_game_log_row(game: dict, player_team: str) -> dict:
    winner = game.get("match_winner")
    result = "W" if winner == player_team else "L" if winner else "—"

    return {
        "date": game.get("match_date", ""),
        "season": game.get("season"),
        "round": game.get("match_round", ""),
        "home_team": game.get("match_home_team", ""),
        "away_team": game.get("match_away_team", ""),
        "opponent": _get_opponent(game, player_team) if player_team else "",
        "venue": game.get("venue_name", ""),
        "result": result,
        "disposals": game.get("disposals", 0),
        "marks": game.get("marks", 0),
        "kicks": game.get("kicks", 0),
        "handballs": game.get("handballs", 0),
        "tackles": game.get("tackles", 0),
        "goals": game.get("goals", 0),
        "fantasy": game.get("afl_fantasy_score", 0),
        "supercoach": game.get("supercoach_score", 0),
    }

def _preload_fantasy_ids():
    """Fetch all Fantasy player IDs once at startup and cache them."""
    try:
        resp = _requests.get(
            "https://fantasy.afl.com.au/data/afl/players.json",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TheFormAnalyst/1.0)"},
        )
        resp.raise_for_status()
        data = resp.json()
        players = data if isinstance(data, list) else data.get("players", [])
        for p in players:
            fn = str(p.get("first_name") or p.get("firstname") or p.get("given_name") or "").strip().lower()
            ln = str(p.get("last_name") or p.get("lastname") or p.get("surname") or "").strip().lower()
            pid = p.get("id") or p.get("player_id")
            if fn and ln and pid:
                key = f"{fn}|{ln}"
                _fantasy_player_id_cache[key] = int(pid)
        logger.info("Fantasy ID cache loaded: %s players", len(_fantasy_player_id_cache))
    except Exception as e:
        logger.warning("Fantasy ID preload failed: %s", e)

_preload_fantasy_ids()

def _merge_fixture_tips(db, fixtures: list[dict], year: int, round_number: int | None = None) -> list[dict]:
    if not fixtures:
        return []

    try:
        tips = fetch_squiggle_tips(year, round_number) or []
    except Exception as exc:
        logger.warning("Fixture tip merge failed: %s", exc)
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
            tip = tips_by_matchup.get(
                (fixture.get("hteam", ""), fixture.get("ateam", ""), fixture.get("round"))
            )

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
