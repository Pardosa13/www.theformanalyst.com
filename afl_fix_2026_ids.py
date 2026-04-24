#!/usr/bin/env python3
"""
afl_fix_2026_ids.py
===================
One-off repair script: deletes all 2026 player stats rows from afl_player_stats
and re-imports them using the corrected player_id mapping logic in
upsert_player_stats() (afl_db.py).

Why this is needed
------------------
The 2026 CSV (data/afl_2026_stats.csv) uses "ID" values from fitzRoy/AFLTables.
Those IDs numerically overlap with the Fryzigg player_ids used for 2019–2025,
causing the same player_id to refer to two different people (e.g., player_id 12393
= Ed Langdon in 2024, Lachie Weller in 2026). This breaks any endpoint that groups
or filters by player_id across seasons.

The fix in upsert_player_stats() resolves 2026 player_ids by matching
(first_name, last_name, club) against historical 2019–2025 rows to reuse the
correct Fryzigg player_id. Genuine debut players receive a stable negative BIGINT
id that cannot collide with any positive Fryzigg id.

Usage
-----
Run once after deploying the code fix:

    DATABASE_URL=postgresql://... python afl_fix_2026_ids.py

Or on Railway (where DATABASE_URL is already set):

    python afl_fix_2026_ids.py

After running, the 2026 rows will have correct, non-colliding player_ids.
The nightly cron (afl_sync.py) will continue to maintain correct IDs on every
subsequent run.
"""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL environment variable is not set — aborting")
        sys.exit(1)

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    from sqlalchemy import create_engine, text
    from types import SimpleNamespace

    engine = create_engine(db_url)
    db = SimpleNamespace(engine=engine, text=text)

    # ── Step 1: delete all existing 2026 rows ─────────────────────────────
    logger.info("Deleting all existing afl_player_stats rows where season = 2026 …")
    with engine.begin() as conn:
        result = conn.execute(text("DELETE FROM afl_player_stats WHERE season = 2026"))
        deleted = result.rowcount
    logger.info("  Deleted %s rows", deleted)

    # ── Step 2: re-import 2026 with corrected player_id mapping ───────────
    logger.info("Fetching 2026 stats from CSV …")
    from afl_data import fetch_2026_stats_from_csv
    from afl_db import upsert_player_stats

    stats = fetch_2026_stats_from_csv()
    if not stats:
        logger.warning("No 2026 stats found in CSV — nothing imported")
        return

    logger.info("Importing %s rows with corrected player_id mapping …", len(stats))
    count = upsert_player_stats(db, stats, 2026)
    logger.info("Done — %s rows inserted/updated for season 2026", count)


if __name__ == "__main__":
    main()
