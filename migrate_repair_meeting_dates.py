#!/usr/bin/env python3
"""Write repaired meeting dates back to meetings.date, once and permanently.

185 meetings were uploaded manually with no date. Their date exists only in the
'YYMMDD_Track' prefix of meeting_name. backtest.py has been repairing them in
memory on every single run (`24,935 rows had NULL meeting_date; 24,935 repaired
from meeting_name prefix`) and throwing the result away, so every other consumer
of the meetings table — the app, the API, any dashboard — still sees NULL and has
to rediscover the same fix.

This writes the repair to the database so it stops being recomputed. It is
deliberately conservative:

  * only rows where date IS NULL are touched, so a real date can never be
    overwritten;
  * the prefix must parse as a valid YYMMDD calendar date;
  * the parsed date must fall inside a sane window (no 1970s, nothing far in
    the future), which catches a name that merely looks like a date prefix;
  * dry-run by default — nothing is written without --apply.

Usage:
    DATABASE_URL=postgresql://...  python migrate_repair_meeting_dates.py
    DATABASE_URL=postgresql://...  python migrate_repair_meeting_dates.py --apply
"""
import argparse
import os
import sys
from datetime import date, datetime

from sqlalchemy import create_engine, text

# A meeting date outside this window means the prefix is not really a date.
EARLIEST_PLAUSIBLE = date(2015, 1, 1)
FUTURE_TOLERANCE_DAYS = 400


def parse_prefix(meeting_name):
    """'251208_Dubbo' -> date(2025, 12, 8). None when the prefix is not a date."""
    name = str(meeting_name or '')
    if len(name) < 7 or name[6] != '_' or not name[:6].isdigit():
        return None
    try:
        return datetime.strptime(name[:6], '%y%m%d').date()
    except ValueError:
        return None


def plausible(value, today):
    return (value is not None
            and value >= EARLIEST_PLAUSIBLE
            and (value - today).days <= FUTURE_TOLERANCE_DAYS)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apply', action='store_true',
                    help='actually write the dates (default: report only)')
    args = ap.parse_args()

    url = os.environ.get('DATABASE_URL')
    if not url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)

    engine = create_engine(url, pool_pre_ping=True)
    today = datetime.utcnow().date()

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, meeting_name FROM meetings WHERE date IS NULL ORDER BY id"
        )).fetchall()

    if not rows:
        print("No meetings with a NULL date. Nothing to do.")
        return 0

    repairable, unparseable, implausible = [], [], []
    for meeting_id, name in rows:
        parsed = parse_prefix(name)
        if parsed is None:
            unparseable.append((meeting_id, name))
        elif not plausible(parsed, today):
            implausible.append((meeting_id, name, parsed))
        else:
            repairable.append((meeting_id, name, parsed))

    print(f"meetings with NULL date : {len(rows)}")
    print(f"  repairable from prefix: {len(repairable)}")
    print(f"  prefix does not parse : {len(unparseable)}")
    print(f"  parsed but implausible: {len(implausible)}")

    if repairable:
        lo = min(r[2] for r in repairable)
        hi = max(r[2] for r in repairable)
        print(f"  date range to be written: {lo} .. {hi}")
        print("\n  first few:")
        for meeting_id, name, parsed in repairable[:5]:
            print(f"    id={meeting_id:<6} {str(name)[:28]:<30} -> {parsed}")
    for label, items in (("unparseable", unparseable), ("implausible", implausible)):
        if items:
            print(f"\n  {label} (left NULL):")
            for item in items[:5]:
                print(f"    id={item[0]:<6} {str(item[1])[:40]}")

    if not args.apply:
        print(f"\nDry run. Re-run with --apply to write {len(repairable)} dates.")
        return 0

    if not repairable:
        print("\nNothing repairable to write.")
        return 0

    updated = 0
    with engine.begin() as conn:
        for meeting_id, _name, parsed in repairable:
            # The NULL check is repeated here so a concurrent writer cannot be
            # clobbered between the read above and this write.
            result = conn.execute(text(
                "UPDATE meetings SET date = :d WHERE id = :id AND date IS NULL"
            ), {'d': parsed, 'id': meeting_id})
            updated += result.rowcount

    with engine.connect() as conn:
        remaining = conn.execute(text(
            "SELECT COUNT(*) FROM meetings WHERE date IS NULL"
        )).scalar()

    print(f"\nWrote {updated} meeting dates. Meetings still NULL: {remaining} "
          f"({len(unparseable)} unparseable + {len(implausible)} implausible).")
    return 0


if __name__ == '__main__':
    sys.exit(main())
