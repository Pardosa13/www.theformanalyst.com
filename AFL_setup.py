"""
afl_setup.py
============
One-time setup script. Run this ONCE from Railway console to:
  1. Create all AFL database tables
  2. Load historical player stats from Fryzigg (2019-present)
  3. Load current season fixtures + ladder from Squiggle

Usage (Railway console or locally):
    python afl_setup.py

Or call from Flask shell:
    flask shell
    >>> from afl_setup import run_setup
    >>> run_setup(db)
"""

import os
import sys
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def run_setup(db, start_year: int = 2019, end_year: int = None):
    """
    Full one-time setup.
    start_year: Fryzigg only has data from 2019.
    end_year: defaults to current year.
    """
    from datetime import datetime
    from afl_db import init_afl_tables, upsert_games, upsert_standings, upsert_player_stats, log_sync
    from afl_data import (
        fetch_fryzigg_player_stats,
        fetch_squiggle_games,
        fetch_squiggle_standings,
        fetch_squiggle_current_round,
        CURRENT_YEAR,
    )

    end_year = end_year or CURRENT_YEAR

    # ── Step 1: Create tables ──────────────────────────────────────────
    logger.info("Step 1/4: Creating AFL tables...")
    init_afl_tables(db)
    logger.info("  ✓ Tables created")

    # ── Step 2: Load historical fixtures (Squiggle, 2012-present) ─────
    logger.info(f"Step 2/4: Loading fixtures from Squiggle (2012-{end_year})...")
    total_games = 0
    for year in range(2012, end_year + 1):
        try:
            games = fetch_squiggle_games(year)
            count = upsert_games(db, games)
            total_games += count
            logger.info(f"  {year}: {count} games")
            log_sync(db, "squiggle_games", season=year, rows=count)
            time.sleep(0.5)  # be nice to Squiggle
        except Exception as e:
            logger.error(f"  {year} failed: {e}")
    logger.info(f"  ✓ Total: {total_games} games loaded")

    # ── Step 3: Load player stats (Fryzigg, 2019-present) ─────────────
    logger.info(f"Step 3/4: Loading player stats from Fryzigg ({start_year}-{end_year})...")
    total_stats = 0
    for year in range(start_year, end_year + 1):
        try:
            stats = fetch_fryzigg_player_stats(year)
            count = upsert_player_stats(db, stats, year)
            total_stats += count
            logger.info(f"  {year}: {count} player-game rows")
            log_sync(db, "fryzigg", season=year, rows=count)
            time.sleep(1)  # be nice to Fryzigg
        except Exception as e:
            logger.error(f"  {year} failed: {e}")
    logger.info(f"  ✓ Total: {total_stats} player stats rows loaded")

    # ── Step 4: Load current ladder ────────────────────────────────────
    logger.info("Step 4/4: Loading current ladder from Squiggle...")
    try:
        current_round = fetch_squiggle_current_round(end_year)
        standings     = fetch_squiggle_standings(end_year, current_round)
        count         = upsert_standings(db, standings, end_year, current_round)
        log_sync(db, "squiggle_standings", season=end_year, round_num=current_round, rows=count)
        logger.info(f"  ✓ {count} teams loaded (Round {current_round})")
    except Exception as e:
        logger.error(f"  Ladder load failed: {e}")

    logger.info("=" * 50)
    logger.info("Setup complete!")
    logger.info(f"  Games:        {total_games}")
    logger.info(f"  Player rows:  {total_stats}")
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Add afl_routes to app.py (see instructions below)")
    logger.info("  2. Add AFL nav link to base.html")
    logger.info("  3. Add ODDS_API_KEY to Railway env vars (optional)")
    logger.info("  4. Add afl_nightly_sync() to your cron job")


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

2. REGISTER ROUTES (after app + db are created, before if __name__):
────────────────────────────────────────────────────────────────────
register_afl_routes(app, db)

3. ADD TO YOUR EXISTING CRON JOB:
──────────────────────────────────
In your existing nightly cron function, add:
    afl_nightly_sync(app, db)

4. ADD NAV LINK TO base.html:
──────────────────────────────
In the {% if current_user.is_authenticated %} section,
add after your Best Bets link:
    <li class="nav-item">
        <a class="nav-link {{ 'active' if request.endpoint == 'afl_hub' }}"
           href="{{ url_for('afl_hub') }}">
            <i class="bi bi-dribbble"></i> AFL
        </a>
    </li>

5. ADD ODDS API KEY TO RAILWAY ENV VARS (optional):
────────────────────────────────────────────────────
ODDS_API_KEY=your_key_here

And add to app.config in app.py:
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

    # If DATABASE_URL is set, run setup automatically
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("Set DATABASE_URL environment variable to run setup.")
        sys.exit(1)

    # Bootstrap minimal SQLAlchemy connection
    from sqlalchemy import create_engine, text
    from types import SimpleNamespace

    engine = create_engine(db_url)
    db = SimpleNamespace(engine=engine, text=text)

    run_setup(db)
