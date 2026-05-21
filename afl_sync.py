"""
afl_sync.py
===========
Railway cron entry point. Invoked by:
    python -c "from afl_sync import sync_afl_all; sync_afl_all(2026)"

Uses the same raw-SQL upsert path as afl_setup.py.
Does not use ORM models.
"""

from __future__ import annotations

import os
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def _safe_log_sync(db, source: str, season: int = None, round_num: int = None,
                   rows: int = 0, status: str = "ok", error: str = None):
    try:
        from afl_db import log_sync
        log_sync(
            db,
            source=source,
            season=season,
            round_num=round_num,
            rows=rows,
            status=status,
            error=error,
        )
    except Exception as exc:
        logger.warning("Failed writing sync log for %s season=%s: %s", source, season, exc)


def sync_afl_all(season: int = None):
    """
    Nightly AFL sync. Called by Railway cron.
    """
    from datetime import datetime
    from sqlalchemy import create_engine, text
    from types import SimpleNamespace

    from afl_data import (
        fetch_squiggle_games,
        fetch_squiggle_standings,
        fetch_squiggle_current_round,
        fetch_squiggle_teams,
        fetch_fryzigg_player_stats,
        fetch_afl_player_stats_current_season,
        fetch_2026_stats_from_csv,
        fetch_afl_player_props,
        fetch_afl_h2h_spread_odds,
    )
    from afl_db import (
        upsert_games,
        upsert_standings,
        upsert_player_stats,
        upsert_player_props,
        upsert_match_markets,
        snapshot_model_selections_from_props,
        settle_model_selections,
        upsert_team_logos,
        normalise_player_stats_team_names,
    )

    if season is None:
        season = datetime.now().year

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set — aborting")
        return

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    # Ensure historical Fryzigg RDS path is allowed during cron.
    previous_cron_mode = os.environ.get("AFL_CRON_MODE")
    os.environ["AFL_CRON_MODE"] = "1"

    engine = create_engine(db_url)
    db = SimpleNamespace(engine=engine, text=text)

    logger.info("=== AFL nightly sync for season %s ===", season)

    try:
        # ── 1. Squiggle fixtures ──────────────────────────────────
        try:
            games = fetch_squiggle_games(season)
            count = upsert_games(db, games)
            _safe_log_sync(db, "squiggle_games", season=season, rows=count)
            logger.info("  ✓ Fixtures: %s games synced", count)
        except Exception as exc:
            logger.error("  ✗ Fixtures sync failed: %s", exc)
            _safe_log_sync(db, "squiggle_games", season=season, status="error", error=str(exc))

        # ── 2. Squiggle ladder ────────────────────────────────────
        try:
            current_round = fetch_squiggle_current_round(season)
            standings = fetch_squiggle_standings(season, current_round)
            count = upsert_standings(db, standings, season, current_round)
            _safe_log_sync(db, "squiggle_standings", season=season, round_num=current_round, rows=count)
            logger.info("  ✓ Ladder: %s teams synced (round %s)", count, current_round)
        except Exception as exc:
            logger.error("  ✗ Ladder sync failed: %s", exc)
            _safe_log_sync(db, "squiggle_standings", season=season, status="error", error=str(exc))

                # ── 3. Player stats (last 5 seasons) ──────────────────────
        player_stats_total = 0
        seasons_to_sync = list(range(season - 4, season + 1))

        for yr in seasons_to_sync:
            try:
                if yr == season:
                    logger.info("  • Loading 2026 player stats from AFL official API")
                    api_stats = fetch_afl_player_stats_current_season(yr, round_number=None)

                    # Determine the latest round the AFL API actually returned data for.
                    # If the API is lagging (e.g. round 8 finished but API only has 1-7),
                    # fall back to the CSV which is updated daily by GitHub Actions via
                    # afltables and is typically 1-2 hours ahead of the AFL API after a round.
                    api_max_round = (
                        max((int(s.get("match_round") or 0) for s in api_stats), default=0)
                        if api_stats else 0
                    )
                    try:
                        current_round = fetch_squiggle_current_round(yr)
                    except Exception:
                        current_round = 0

                    # Allow API to be 1 round behind: current_round from Squiggle is the
                    # first *incomplete* round, so API having data through current_round-1
                    # means it's fully up to date with completed play.
                    if api_stats and (current_round == 0 or api_max_round >= current_round - 1):
                        stats = api_stats
                        logger.info("  • AFL API: %s rows, max round %s", len(stats), api_max_round)
                    else:
                        if api_stats:
                            logger.warning(
                                "  • AFL API only has data through round %s (current: %s) — using CSV",
                                api_max_round, current_round,
                            )
                        else:
                            logger.warning("  • AFL API returned no rows, falling back to CSV")
                        stats = fetch_2026_stats_from_csv()
                        if not stats:
                            stats = api_stats or []
                else:
                    stats = fetch_fryzigg_player_stats(yr)

                if stats:
                    count = upsert_player_stats(db, stats, yr)
                    player_stats_total += count
                    _safe_log_sync(db, "player_stats", season=yr, rows=count)
                    logger.info("  ✓ Player stats %s: %s rows synced", yr, count)
                else:
                    logger.info("  - Player stats %s: no data returned", yr)
                    _safe_log_sync(db, "player_stats", season=yr, rows=0, status="empty")

                time.sleep(1)
            except Exception as exc:
                logger.error("  ✗ Player stats %s failed: %s", yr, exc)
                _safe_log_sync(db, "player_stats", season=yr, status="error", error=str(exc))

        logger.info(
            "  ✓ Player stats total: %s rows across %s seasons",
            player_stats_total,
            len(seasons_to_sync),
        )

        # ── 3b. Normalise legacy team names in player stats ────────
        # Idempotent — fixes rows written by the AFLTables CSV parser before
        # the team-name normalisation fix (e.g. "Greater Western Sydney" →
        # "GWS Giants").  Safe to run every sync cycle.
        try:
            fixed = normalise_player_stats_team_names(db)
            if fixed:
                logger.info("  ✓ Team name normalisation: fixed %d field(s)", fixed)
        except Exception as exc:
            logger.warning("  - Team name normalisation failed: %s", exc)

        # ── 4. Team logos ─────────────────────────────────────────
        try:
            teams = fetch_squiggle_teams()
            count = upsert_team_logos(db, teams)
            _safe_log_sync(db, "team_logos", rows=count)
            logger.info("  ✓ Team logos: %s teams synced", count)
        except Exception as exc:
            logger.error("  ✗ Team logos sync failed: %s", exc)
            _safe_log_sync(db, "team_logos", status="error", error=str(exc))

        # ── 5. Prop lines (skip if no key) ────────────────────────
        api_key = os.environ.get("ODDS_API_KEY", "")
        if api_key:
            try:
                props = fetch_afl_player_props(api_key)
                count = upsert_player_props(db, props)
                _safe_log_sync(db, "odds_api", season=season, rows=count)
                logger.info("  ✓ Props: %s lines synced", count)
                match_rows = fetch_afl_h2h_spread_odds(api_key)
                match_count = upsert_match_markets(db, match_rows)
                _safe_log_sync(db, "odds_api_match_markets", season=season, rows=match_count)
                logger.info("  ✓ Match markets: %s lines synced", match_count)
                selection_count = snapshot_model_selections_from_props(db, season=season)
                _safe_log_sync(db, "afl_model_selections_snapshot", season=season, rows=selection_count)
                logger.info("  ✓ Model selections snapshotted: %s", selection_count)
            except Exception as exc:
                logger.error("  ✗ Props sync failed: %s", exc)
                _safe_log_sync(db, "odds_api", season=season, status="error", error=str(exc))
        else:
            logger.info("  - Props: skipped (ODDS_API_KEY not configured)")

        try:
            settled = settle_model_selections(db)
            _safe_log_sync(db, "afl_model_selections_settle", season=season, rows=settled)
            logger.info("  ✓ Model selections settled: %s", settled)
        except Exception as exc:
            logger.error("  ✗ Model selection settlement failed: %s", exc)
            _safe_log_sync(db, "afl_model_selections_settle", season=season, status="error", error=str(exc))

        logger.info("=== AFL sync complete ===")

    finally:
        if previous_cron_mode is None:
            os.environ.pop("AFL_CRON_MODE", None)
        else:
            os.environ["AFL_CRON_MODE"] = previous_cron_mode


if __name__ == "__main__":
    import sys
    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else None
    sync_afl_all(season_arg)
