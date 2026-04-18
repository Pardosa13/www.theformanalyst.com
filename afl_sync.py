"""
afl_sync.py
===========
Railway cron entry point. Invoked by:
    python -c "from afl_sync import sync_afl_all; sync_afl_all(2026)"

Uses the same raw-SQL upsert path as afl_setup.py (proven working).
Does NOT use ORM models — that was the old broken import chain.
"""

import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def sync_afl_all(season: int = None):
    """
    Nightly AFL sync. Called by Railway cron.
    
    season: the season to sync. Defaults to current year if not provided.
    """
    from datetime import datetime
    from sqlalchemy import create_engine, text
    from types import SimpleNamespace

    from afl_data import (
        fetch_squiggle_games,
        fetch_squiggle_standings,
        fetch_squiggle_current_round,
        fetch_fryzigg_player_stats,
        fetch_afl_player_props,
    )
    from afl_db import (
        upsert_games,
        upsert_standings,
        upsert_player_stats,
        upsert_player_props,
        log_sync,
    )

    if season is None:
        season = datetime.now().year

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set — aborting")
        return

    engine = create_engine(db_url)
    db = SimpleNamespace(engine=engine, text=text)

    logger.info(f"=== AFL nightly sync for season {season} ===")

    # ── 1. Squiggle fixtures ──────────────────────────────────────
    try:
        games = fetch_squiggle_games(season)
        count = upsert_games(db, games)
        log_sync(db, "squiggle_games", season=season, rows=count)
        logger.info(f"  ✓ Fixtures: {count} games synced")
    except Exception as e:
        logger.error(f"  ✗ Fixtures sync failed: {e}")
        try: log_sync(db, "squiggle_games", season=season, status="error", error=str(e))
        except: pass

    # ── 2. Squiggle ladder ────────────────────────────────────────
    try:
        rnd = fetch_squiggle_current_round(season)
        standings = fetch_squiggle_standings(season, rnd)
        count = upsert_standings(db, standings, season, rnd)
        log_sync(db, "squiggle_standings", season=season, round_num=rnd, rows=count)
        logger.info(f"  ✓ Ladder: {count} teams synced (round {rnd})")
    except Exception as e:
        logger.error(f"  ✗ Ladder sync failed: {e}")
        try: log_sync(db, "squiggle_standings", season=season, status="error", error=str(e))
        except: pass

    # ── 3. Fryzigg player stats ───────────────────────────────────
    try:
        stats = fetch_fryzigg_player_stats(season)
        count = upsert_player_stats(db, stats, season)
        log_sync(db, "fryzigg", season=season, rows=count)
        logger.info(f"  ✓ Player stats: {count} rows synced")
    except Exception as e:
        logger.error(f"  ✗ Player stats sync failed: {e}")
        try: log_sync(db, "fryzigg", season=season, status="error", error=str(e))
        except: pass

    # ── 4. Prop lines (skip if no key) ────────────────────────────
    api_key = os.environ.get("ODDS_API_KEY", "")
    if api_key:
        total = 0
        for market in ["player_disposals", "player_marks", "player_goals"]:
            try:
                props = fetch_afl_player_props(api_key, market)
                count = upsert_player_props(db, props)
                total += count
                log_sync(db, "odds_api", rows=count)
                logger.info(f"  ✓ Props ({market}): {count} lines synced")
            except Exception as e:
                logger.error(f"  ✗ Props sync failed for {market}: {e}")
                try: log_sync(db, "odds_api", status="error", error=str(e))
                except: pass
        logger.info(f"  ✓ Props total: {total} lines")
    else:
        logger.info("  - Props: skipped (ODDS_API_KEY not configured)")

    logger.info("=== AFL sync complete ===")


# Allow direct invocation: python afl_sync.py
if __name__ == "__main__":
    import sys
    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else None
    sync_afl_all(season_arg)
