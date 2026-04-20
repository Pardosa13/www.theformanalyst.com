"""
afl_setup.py
============
One-time setup script. Run this ONCE to:
  1. Create all AFL database tables
  2. Load historical fixtures from Squiggle
  3. Load player stats (AFL official current-season where available, Fryzigg otherwise)
  4. Load current ladder from Squiggle

Usage:
    python afl_setup.py

Or from Flask shell:
    >>> from afl_setup import run_setup
    >>> run_setup(db)
"""

from __future__ import annotations

import os
import sys
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def run_setup(db, start_year: int = 2019, end_year: int = None):
    """
    Full one-time setup.

    start_year:
        Historical player stats start year. Fryzigg data is used for past seasons.
    end_year:
        Defaults to current year.
    """
    from afl_db import (
        init_afl_tables,
        upsert_games,
        upsert_standings,
        upsert_player_stats,
        log_sync,
    )
    from afl_data import (
        fetch_fryzigg_player_stats,
        fetch_afl_player_stats_current_season,
        fetch_squiggle_games,
        fetch_squiggle_standings,
        fetch_squiggle_current_round,
        CURRENT_YEAR,
    )

    end_year = end_year or CURRENT_YEAR

    # Allow Fryzigg RDS access during one-time setup.
    previous_cron_mode = os.environ.get("AFL_CRON_MODE")
    os.environ["AFL_CRON_MODE"] = "1"

    total_games = 0
    total_stats = 0

    try:
        # ── Step 1: Create tables ──────────────────────────────────────
        logger.info("Step 1/4: Creating AFL tables...")
        init_afl_tables(db)
        logger.info("  ✓ Tables created")

        # ── Step 2: Load fixtures (Squiggle, 2012-present) ─────────────
        logger.info("Step 2/4: Loading fixtures from Squiggle (2012-%s)...", end_year)
        for year in range(2012, end_year + 1):
            try:
                games = fetch_squiggle_games(year)
                count = upsert_games(db, games)
                total_games += count
                logger.info("  %s: %s games", year, count)
                log_sync(db, "squiggle_games", season=year, rows=count)
                time.sleep(0.5)
            except Exception as exc:
                logger.error("  %s failed: %s", year, exc)
                try:
                    log_sync(db, "squiggle_games", season=year, status="error", error=str(exc))
                except Exception:
                    pass

        logger.info("  ✓ Total: %s games loaded", total_games)

        # ── Step 3: Load player stats (hybrid source path) ─────────────
        logger.info(
            "Step 3/4: Loading player stats (%s-%s)...",
            start_year,
            end_year,
        )
        logger.info("         Past seasons use Fryzigg. Current season uses AFL official first, with Fryzigg fallback.")

        for year in range(start_year, end_year + 1):
            try:
                # First attempt: normal fetch path
                stats = fetch_fryzigg_player_stats(year)

                # Critical retry for current season if first attempt returned nothing
                if year == CURRENT_YEAR and not stats:
                    logger.warning("Retrying %s without round filter...", year)
                    stats = fetch_afl_player_stats_current_season(year, round_number=None)

                if not stats:
                    logger.warning("  %s: no stats returned", year)
                    log_sync(db, "player_stats", season=year, rows=0, status="empty")
                    continue

                count = upsert_player_stats(db, stats, year)
                total_stats += count
                logger.info("  %s: %s player-game rows", year, count)
                log_sync(db, "player_stats", season=year, rows=count)
                time.sleep(1)
            except Exception as exc:
                logger.error("  %s failed: %s", year, exc)
                try:
                    log_sync(db, "player_stats", season=year, status="error", error=str(exc))
                except Exception:
                    pass

        logger.info("  ✓ Total: %s player stats rows loaded", total_stats)

        # ── Step 4: Load current ladder ────────────────────────────────
        logger.info("Step 4/4: Loading current ladder from Squiggle...")
        try:
            current_round = fetch_squiggle_current_round(end_year)
            standings = fetch_squiggle_standings(end_year, current_round)
            count = upsert_standings(db, standings, end_year, current_round)
            log_sync(db, "squiggle_standings", season=end_year, round_num=current_round, rows=count)
            logger.info("  ✓ %s teams loaded (Round %s)", count, current_round)
        except Exception as exc:
            logger.error("  Ladder load failed: %s", exc)
            try:
                log_sync(db, "squiggle_standings", season=end_year, status="error", error=str(exc))
            except Exception:
                pass

        logger.info("=" * 50)
        logger.info("Setup complete!")
        logger.info("  Games:        %s", total_games)
        logger.info("  Player rows:  %s", total_stats)
        logger.info("")
        logger.info("Next steps:")
        logger.info("  1. Add afl_routes to app.py")
        logger.info("  2. Add AFL nav link to base.html")
        logger.info("  3. Add ODDS_API_KEY to Railway env vars (optional)")
        logger.info("  4. Add afl_nightly_sync() to your cron job")

    finally:
        if previous_cron_mode is None:
            os.environ.pop("AFL_CRON_MODE", None)
        else:
            os.environ["AFL_CRON_MODE"] = previous_cron_mode


# ─────────────────────────────────────────────
# INSTRUCTIONS: HOW TO WIRE INTO APP.PY
# ─────────────────────────────────────────────

INSTRUCTIONS = """
═══════════════════════════════════════════════════════
HOW TO WIRE AFL INTO YOUR EXISTING APP.PY
═══════════════════════════════════════════════════════

1. ADD IMPORTS (top of app.py):
─────────────────────────────
from afl_routes import register_afl_routes, afl_nightly_sync

2. REGISTER ROUTES (after app + db are created):
─────────────────────────────────────────────────
register_afl_routes(app, db)

3. ADD TO YOUR EXISTING CRON JOB:
──────────────────────────────────
In your nightly cron function, add:
    afl_nightly_sync(app, db)

4. ADD NAV LINK TO base.html:
──────────────────────────────
Inside your authenticated nav:
    <li class="nav-item">
        <a class="nav-link {{ 'active' if request.endpoint == 'afl_hub' }}"
           href="{{ url_for('afl_hub') }}">
            <i class="bi bi-dribbble"></i> AFL
        </a>
    </li>

5. ADD ODDS API KEY TO RAILWAY ENV VARS (optional):
────────────────────────────────────────────────────
ODDS_API_KEY=your_key_here

And in app.config:
    app.config["ODDS_API_KEY"] = os.environ.get("ODDS_API_KEY", "")

6. RUN SETUP ONCE:
──────────────────
From Railway console:
    python afl_setup.py

Or from Flask shell:
    from afl_setup import run_setup
    from app import db
    run_setup(db)

═══════════════════════════════════════════════════════
"""


if __name__ == "__main__":
    print(INSTRUCTIONS)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("Set DATABASE_URL environment variable to run setup.")
        sys.exit(1)

    from sqlalchemy import create_engine, text
    from types import SimpleNamespace

    engine = create_engine(db_url)
    db = SimpleNamespace(engine=engine, text=text)

    run_setup(db)
