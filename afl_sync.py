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
import subprocess
import sys
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def normalize_round(round_value):
    if round_value is None:
        return None

    s = str(round_value).strip()
    if not s:
        return None

    lower = s.lower()
    if lower in ("opening round", "round 0"):
        return 0
    if lower.startswith("round "):
        try:
            return int(s.split(" ", 1)[1])
        except Exception:
            pass
    try:
        return int(s)
    except Exception as exc:
        raise ValueError(f"Unrecognized AFL round format: {round_value}") from exc


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



def _run_afl_ml_command(args: list[str], success_message: str | None = None) -> bool:
    """Run one AFL ML command from the existing Railway cron environment."""
    cmd = [sys.executable, "afl_backtest.py", *args]
    label = " ".join(["python", "afl_backtest.py", *args])
    try:
        result = subprocess.run(
            cmd,
            check=False,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            text=True,
            capture_output=True,
        )
    except Exception as exc:
        logger.error("AFL ML command failed to start (%s): %s", label, exc, exc_info=True)
        return False

    if result.stdout:
        logger.info("AFL ML stdout (%s):\n%s", label, result.stdout.rstrip())
    if result.stderr:
        log_method = logger.error if result.returncode else logger.info
        log_method("AFL ML stderr (%s):\n%s", label, result.stderr.rstrip())

    if result.returncode != 0:
        logger.error("AFL ML command failed (%s) with exit code %s", label, result.returncode)
        return False

    if success_message:
        logger.info(success_message)
    return True


def run_afl_ml_pipeline_after_sync() -> None:
    """Run the post-sync AFL ML pipeline without breaking the AFL cron."""
    logger.info("Starting AFL ML schema report")
    schema_ok = _run_afl_ml_command(["--schema-report"])
    if not schema_ok:
        logger.warning(
            "AFL ML schema report failed; continuing to training because training performs "
            "its own validation and writes model artifacts atomically."
        )

    logger.info("Starting AFL ML training")
    train_ok = _run_afl_ml_command(["--train"], success_message="AFL ML model saved")
    if not train_ok:
        logger.error(
            "AFL ML training failed; old model artifact was not intentionally replaced. "
            "Skipping current scoring to avoid scoring with a failed training run."
        )
        return

    logger.info("Starting AFL ML current scoring")
    score_ok = _run_afl_ml_command(["--score-current"], success_message="AFL ML current scoring complete")
    if not score_ok:
        logger.error("AFL ML current scoring failed; AFL sync completed but current ML scores were not refreshed.")

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
                    logger.info("  • Loading %s player stats from AFL official API", yr)
                    api_stats = fetch_afl_player_stats_current_season(yr, round_number=None)

                    if not api_stats:
                        # Hard failure — do NOT fall back to stale CSV data.
                        # Using the CSV here previously caused stale/wrong player IDs to
                        # be written to the DB.  An empty API response is a signal that
                        # something is wrong upstream and must be investigated, not silently
                        # papered over.
                        logger.error(
                            "  ✗ AFL API returned 0 rows for season %s — skipping upsert. "
                            "Investigate auth token, completed-round detection, or API availability.",
                            yr,
                        )
                        stats = []
                    else:
                        api_max_round = max(
                            ((normalize_round(s.get("match_round")) or 0) for s in api_stats), default=0
                        )
                        try:
                            current_round = fetch_squiggle_current_round(yr)
                        except Exception:
                            current_round = 0

                        # current_round from Squiggle is the first *incomplete* round.
                        # API having data through current_round-1 means fully up to date.
                        if current_round == 0 or api_max_round >= current_round - 1:
                            stats = api_stats
                            logger.info(
                                "  • AFL API: %s rows, max round %s (current round %s)",
                                len(stats), api_max_round, current_round,
                            )
                        else:
                            # API is more than one round behind.  This is a data-quality
                            # problem — do NOT fall back to CSV which may carry stale or
                            # mismatched player IDs.
                            logger.error(
                                "  ✗ AFL API has data only through round %s but current round "
                                "is %s — more than one round behind. Skipping upsert rather "
                                "than writing incomplete data.",
                                api_max_round, current_round,
                            )
                            stats = []
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
        run_afl_ml_pipeline_after_sync()

    finally:
        if previous_cron_mode is None:
            os.environ.pop("AFL_CRON_MODE", None)
        else:
            os.environ["AFL_CRON_MODE"] = previous_cron_mode


if __name__ == "__main__":
    import sys
    season_arg = int(sys.argv[1]) if len(sys.argv) > 1 else None
    sync_afl_all(season_arg)
