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

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

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

    # ── 3. Fryzigg player stats (last 5 seasons) ──────────────────
    fryzigg_total = 0
    current_year = season
    seasons_to_sync = list(range(current_year - 4, current_year + 1))
    # e.g. if season=2026, syncs [2022, 2023, 2024, 2025, 2026]

    for yr in seasons_to_sync:
        try:
            stats = fetch_fryzigg_player_stats(yr)
            if stats:
                count = upsert_player_stats(db, stats, yr)
                fryzigg_total += count
                log_sync(db, "fryzigg", season=yr, rows=count)
                logger.info(f"  ✓ Fryzigg {yr}: {count} rows synced")
            else:
                logger.info(f"  - Fryzigg {yr}: no data returned (may not be published yet)")
                log_sync(db, "fryzigg", season=yr, rows=0, status="empty")
            # Be polite to the API between seasons
            import time
            time.sleep(1)
        except Exception as e:
            logger.error(f"  ✗ Fryzigg {yr} failed: {e}")
            try: log_sync(db, "fryzigg", season=yr, status="error", error=str(e))
            except: pass

    logger.info(f"  ✓ Fryzigg total: {fryzigg_total} rows across {len(seasons_to_sync)} seasons")

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
