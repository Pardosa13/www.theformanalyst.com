"""odds_ingest.py — capture live pre-race Ladbrokes odds into the database.

WHY THIS EXISTS
---------------
Nothing in this codebase captured a live PRE-RACE price as a model input.
`last_sp` is the horse's PREVIOUS start's price — history, not this race — and
`results.sp` is the closing price, which only exists after the race has run.
Anything that wants to know what the market thinks about a race that has not
happened yet (the model/market blend, price drift, a live A/E) has had nothing
to read.

This job fills that gap. It polls the Ladbrokes affiliate feed for the races
this database already knows about and writes one row per runner per
observation into `live_odds_snapshots`.

EVERY SNAPSHOT IS KEPT
----------------------
Scoring only ever needs the most recent snapshot per runner, so it is
tempting to store one row and update it. Don't: the price PATH is the
interesting object (steamers, drifters, when the money arrived), it cannot be
reconstructed after the fact, and re-doing ingestion later to collect what was
thrown away is exactly the wasted work this design avoids. Rows are
insert-only; `captured_at` orders them.

SCHEDULE
--------
Separate from the nightly backtest.py cron — this needs to run every few
minutes in the lead-up to each race, not once at 2am:

    # one pass, for an external scheduler (cron, Railway cron, systemd timer)
    python odds_ingest.py

    # or self-scheduling, for a long-running worker process
    python odds_ingest.py --loop

Environment:
    DATABASE_URL              required, same as every other job here
    ODDS_INGEST_INTERVAL_SECONDS  --loop sleep between passes (default 180)
    ODDS_INGEST_LOOKAHEAD_HOURS   how far ahead of the jump to start polling a
                                  race (default 6)
    ODDS_INGEST_GRACE_MINUTES     keep polling this long past scheduled start,
                                  since a delayed race is still open (default 5)
"""
import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine, text

from ladbrokes import (
    MELBOURNE_TZ,
    _parse_ladbrokes_utc,
    fetch_race_odds,
    match_race_info,
    normalize_runner_name,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
log = logging.getLogger('odds_ingest')

SOURCE = 'ladbrokes'

DEFAULT_INTERVAL_SECONDS = int(os.environ.get('ODDS_INGEST_INTERVAL_SECONDS', '180'))
DEFAULT_LOOKAHEAD_HOURS = float(os.environ.get('ODDS_INGEST_LOOKAHEAD_HOURS', '6'))
DEFAULT_GRACE_MINUTES = float(os.environ.get('ODDS_INGEST_GRACE_MINUTES', '5'))

# Statuses that mean the market is gone. Anything else (open, delayed,
# suspended, or a status this feed has not shown us yet) is still worth a poll:
# missing a real price costs more than one wasted request.
CLOSED_MARKET_STATUSES = {
    'abandoned', 'closed', 'final', 'finalised', 'interim', 'resulted', 'paying',
}

# A price only means something as part of a book. A race that comes back with
# one usable price is a partial feed, not a market, and storing it would create
# exactly the single-runner "book" market_probability.fair_probabilities has to
# defend itself against downstream.
MIN_PRICED_RUNNERS = 2


def get_engine():
    url = os.environ.get('DATABASE_URL')
    if not url:
        log.error("DATABASE_URL not set. Exiting.")
        sys.exit(1)
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return create_engine(url, pool_pre_ping=True)


def ensure_tables(engine):
    """Create live_odds_snapshots if it does not exist yet.

    Same pattern (and same Postgres assumption) as backtest.ensure_tables:
    this project has no migration step that every deployment target runs, so
    each job creates what it needs, idempotently, on the way in.
    """
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS live_odds_snapshots (
                id BIGSERIAL PRIMARY KEY,
                race_id INTEGER NOT NULL,
                horse_id INTEGER NOT NULL,
                source VARCHAR(32) NOT NULL DEFAULT 'ladbrokes',
                odds FLOAT,
                place_odds FLOAT,
                is_scratched BOOLEAN DEFAULT FALSE,
                market_status VARCHAR(32),
                captured_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))
        # Serves the only read this table has in the hot path: "latest snapshot
        # per runner for these races". DESC on captured_at so the planner can
        # walk straight to the newest row per (race_id, horse_id).
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_live_odds_snapshots_latest
            ON live_odds_snapshots (race_id, horse_id, captured_at DESC)
        """))
        # Serves retention/drift queries over a time window rather than a race.
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_live_odds_snapshots_captured_at
            ON live_odds_snapshots (captured_at)
        """))
        conn.commit()


# ─────────────────────────────────────────────
# WHAT TO POLL
# ─────────────────────────────────────────────
def melbourne_today():
    """Today's date in racing-local time, not the container's.

    Meeting dates in this database are Australian dates and the containers run
    in UTC, so "today" from the server's point of view is the wrong day for
    roughly half of every Australian racing afternoon.
    """
    return datetime.now(MELBOURNE_TZ).date()


def _track_from_meeting_name(meeting_name):
    """Track name out of the 'YYMMDD_Track' meeting_name prefix.

    meetings.track is empty for every row in this database (see the note in
    backtest.run_model_competition), so meeting_name is the only usable source.
    Reimplemented in three lines here rather than importing backtest.py, which
    pulls in sklearn/catboost/xgboost — a poller that runs every few minutes
    should not pay that import cost.
    """
    name = str(meeting_name or '')
    if len(name) > 7 and name[:6].isdigit() and name[6] == '_':
        return name[7:].strip() or None
    return name.strip() or None


def load_upcoming_races(engine, meeting_date=None):
    """Races on `meeting_date` (default: today, Melbourne) and their runners.

    Returns a list of dicts: race_id, race_number, meeting_id, meeting_name,
    track, and runners as {normalised_name: horse_id}.
    """
    meeting_date = meeting_date or melbourne_today()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT r.id AS race_id, r.race_number, m.id AS meeting_id,
                   m.meeting_name, h.id AS horse_id, h.horse_name
            FROM races r
            JOIN meetings m ON m.id = r.meeting_id
            JOIN horses h ON h.race_id = r.id
            WHERE m.date = :meeting_date
            ORDER BY m.id, r.race_number, h.id
        """), {'meeting_date': meeting_date}).mappings().all()

    races = {}
    for row in rows:
        race = races.setdefault(row['race_id'], {
            'race_id': row['race_id'],
            'race_number': row['race_number'],
            'meeting_id': row['meeting_id'],
            'meeting_name': row['meeting_name'],
            'track': _track_from_meeting_name(row['meeting_name']),
            'runners': {},
        })
        name_norm = normalize_runner_name(row['horse_name'])
        if name_norm:
            # Two runners normalising to the same name in one race would make
            # the match ambiguous; drop both rather than attach a price to a
            # coin flip.
            if name_norm in race['runners'] and race['runners'][name_norm] != row['horse_id']:
                race['runners'][name_norm] = None
            else:
                race['runners'].setdefault(name_norm, row['horse_id'])
    return list(races.values())


def race_is_in_window(start_time_utc, now_utc, lookahead_hours, grace_minutes):
    """Is this race close enough to the jump to be worth polling?

    A race with no parseable start time is polled: an unknown start is far more
    likely to be a feed quirk than a reason to skip a race that is about to run.
    """
    if start_time_utc is None:
        return True
    if start_time_utc > now_utc + timedelta(hours=lookahead_hours):
        return False
    # Past the scheduled jump, keep polling briefly — races run late, and the
    # closed-market status below is the authoritative stop signal, not the clock.
    return start_time_utc > now_utc - timedelta(minutes=grace_minutes)


def snapshot_rows_for_race(race, odds_payload, captured_at):
    """Build the insert rows for one race from one Ladbrokes odds payload.

    Returns (rows, diagnostics). Pure — no database, no clock, no network — so
    the matching rules that decide what gets stored are testable on their own.
    """
    diagnostics = []
    status = str((odds_payload or {}).get('status') or '').strip().lower()
    if status in CLOSED_MARKET_STATUSES:
        return [], [f"market closed (status={status})"]

    runners = (odds_payload or {}).get('odds') or {}
    rows = []
    priced = 0
    for name_norm, runner in runners.items():
        horse_id = race['runners'].get(name_norm)
        if not horse_id:
            diagnostics.append(f"unmatched Ladbrokes runner: {runner.get('name') or name_norm}")
            continue
        odds = _coerce_price(runner.get('win'))
        is_scratched = bool(runner.get('is_scratched')) or runner.get('is_available') is False
        if odds is not None and not is_scratched:
            priced += 1
        rows.append({
            'race_id': race['race_id'],
            'horse_id': horse_id,
            'source': SOURCE,
            'odds': odds,
            'place_odds': _coerce_price(runner.get('place')),
            'is_scratched': is_scratched,
            'market_status': status[:32] or None,
            'captured_at': captured_at,
        })

    if priced < MIN_PRICED_RUNNERS:
        return [], diagnostics + [
            f"only {priced} priced runner(s) — not a book, nothing stored"
        ]
    return rows, diagnostics


def _coerce_price(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 1.0 else None


def store_snapshots(engine, rows):
    if not rows:
        return 0
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO live_odds_snapshots
                (race_id, horse_id, source, odds, place_odds, is_scratched,
                 market_status, captured_at)
            VALUES
                (:race_id, :horse_id, :source, :odds, :place_odds, :is_scratched,
                 :market_status, :captured_at)
        """), rows)
        conn.commit()
    return len(rows)


# ─────────────────────────────────────────────
# ONE PASS
# ─────────────────────────────────────────────
def run_once(engine, meeting_date=None, lookahead_hours=DEFAULT_LOOKAHEAD_HOURS,
             grace_minutes=DEFAULT_GRACE_MINUTES):
    """Poll every in-window race for one meeting date and store what comes back.

    Returns a summary dict. Never raises for a single bad race: one unmatched
    meeting or one feed hiccup must not stop the other races in the pass.
    """
    meeting_date = meeting_date or melbourne_today()
    date_str = meeting_date.isoformat()
    now_utc = datetime.now(timezone.utc)

    races = load_upcoming_races(engine, meeting_date)
    summary = {
        'meeting_date': date_str, 'races_known': len(races), 'races_polled': 0,
        'races_out_of_window': 0, 'races_unmatched': 0, 'races_closed': 0,
        'rows_stored': 0, 'runners_unmatched': 0,
    }
    if not races:
        log.info("Odds ingest: no races in the database for %s — nothing to poll.", date_str)
        return summary

    unmatched_by_track = defaultdict(int)
    for race in races:
        try:
            race_info = match_race_info(race['track'], date_str, race['race_number'])
            if not (race_info and race_info.get('uuid')):
                summary['races_unmatched'] += 1
                unmatched_by_track[race['track']] += 1
                continue

            start_time = _parse_ladbrokes_utc(race_info.get('start_time'))
            if not race_is_in_window(start_time, now_utc, lookahead_hours, grace_minutes):
                summary['races_out_of_window'] += 1
                continue

            payload = fetch_race_odds(race_info['uuid'])
            rows, diagnostics = snapshot_rows_for_race(race, payload, now_utc)
            if not rows:
                summary['races_closed'] += 1
                if diagnostics:
                    log.debug("Odds ingest: %s R%s stored nothing (%s)",
                              race['track'], race['race_number'], "; ".join(diagnostics))
                continue

            summary['rows_stored'] += store_snapshots(engine, rows)
            summary['races_polled'] += 1
            summary['runners_unmatched'] += sum(1 for d in diagnostics if d.startswith('unmatched'))
        except Exception as e:
            # One race must never take down the pass — the next poll in a few
            # minutes gets another go at it.
            log.warning("Odds ingest failed for %s R%s: %s",
                        race.get('track'), race.get('race_number'), e)

    if unmatched_by_track:
        log.warning(
            "Odds ingest could not match %s race(s) to a Ladbrokes event: %s. "
            "A whole track unmatched usually means the venue name in "
            "meeting_name does not match the Ladbrokes meeting name.",
            summary['races_unmatched'],
            ", ".join(f"{track or '<no track>'}x{count}"
                      for track, count in sorted(unmatched_by_track.items(), key=lambda kv: -kv[1])),
        )
    log.info(
        "Odds ingest pass for %s: races_known=%s polled=%s stored_rows=%s "
        "out_of_window=%s unmatched_races=%s closed_or_empty=%s unmatched_runners=%s",
        date_str, summary['races_known'], summary['races_polled'], summary['rows_stored'],
        summary['races_out_of_window'], summary['races_unmatched'], summary['races_closed'],
        summary['runners_unmatched'],
    )
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--date', help="Meeting date to poll (YYYY-MM-DD). Default: today, Melbourne time.")
    parser.add_argument('--loop', action='store_true',
                        help="Keep polling instead of exiting after one pass.")
    parser.add_argument('--interval', type=int, default=DEFAULT_INTERVAL_SECONDS,
                        help=f"Seconds between passes in --loop mode (default {DEFAULT_INTERVAL_SECONDS}).")
    parser.add_argument('--lookahead-hours', type=float, default=DEFAULT_LOOKAHEAD_HOURS,
                        help=f"Start polling a race this long before the jump (default {DEFAULT_LOOKAHEAD_HOURS}).")
    parser.add_argument('--grace-minutes', type=float, default=DEFAULT_GRACE_MINUTES,
                        help=f"Keep polling this long past the scheduled start (default {DEFAULT_GRACE_MINUTES}).")
    parser.add_argument('--duration-seconds', type=float, default=None,
                        help="Stop the --loop cleanly after this long. For a scheduler that "
                             "cannot fire every few minutes itself (a 15-minute GitHub Actions "
                             "cron holding a 3-minute poll loop, say) — the alternative is "
                             "killing the process, which is indistinguishable from a crash in "
                             "the logs.")
    args = parser.parse_args(argv)

    meeting_date = date.fromisoformat(args.date) if args.date else None
    engine = get_engine()
    ensure_tables(engine)

    if not args.loop:
        run_once(engine, meeting_date, args.lookahead_hours, args.grace_minutes)
        return 0

    log.info("Odds ingest loop started (interval=%ss lookahead=%sh grace=%smin duration=%ss).",
             args.interval, args.lookahead_hours, args.grace_minutes,
             args.duration_seconds if args.duration_seconds else 'unbounded')
    deadline = (time.monotonic() + args.duration_seconds) if args.duration_seconds else None
    while True:
        started = time.monotonic()
        try:
            # Recomputed each pass rather than captured once, so a worker that
            # runs across midnight rolls onto the new race day by itself.
            run_once(engine, meeting_date, args.lookahead_hours, args.grace_minutes)
        except KeyboardInterrupt:
            log.info("Odds ingest loop stopped.")
            return 0
        except Exception as e:
            log.exception("Odds ingest pass failed entirely: %s", e)
        elapsed = time.monotonic() - started
        sleep_for = max(1.0, args.interval - elapsed)
        if deadline is not None:
            # Only sleep if a whole further pass still fits: waking up just to
            # exit wastes the scheduler's window, and a pass cut off midway
            # would write a partial book.
            if time.monotonic() + sleep_for >= deadline:
                log.info("Odds ingest loop finished its %.0fs window.", args.duration_seconds)
                return 0
        time.sleep(sleep_for)


if __name__ == '__main__':
    sys.exit(main())
