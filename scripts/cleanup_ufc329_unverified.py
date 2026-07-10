#!/usr/bin/env python3
"""Deactivate only provenance-proven unverified/Odds API-only UFC 329 rows.

This intentionally does not fetch ESPN HTML and does not guess by fighter names.
Rows are eligible only when their stored provenance says they are not a
canonically verified bout: card_source='odds_api' OR verified=false.
"""

from __future__ import annotations

import argparse
import sys

import mma_sync


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely clean unverified UFC 329 rows by provenance only.")
    parser.add_argument("--event-id", help="ESPN event id; if omitted, searches mma_events for UFC 329")
    parser.add_argument("--dry-run", action="store_true", help="Print eligible rows without updating")
    args = parser.parse_args()

    if not mma_sync.DATABASE_URL:
        raise RuntimeError("DATABASE_URL must be set")

    conn = mma_sync.get_conn()
    try:
        mma_sync.ensure_mma_integrity_schema(conn)
        event_id = args.event_id
        if not event_id:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM mma_events
                    WHERE name ILIKE '%%UFC 329%%'
                    ORDER BY date DESC NULLS LAST
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
            if not row:
                raise RuntimeError("Could not find UFC 329 in mma_events; pass --event-id explicitly")
            event_id = row[0]

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, bout_uid, fighter_1_name, fighter_2_name, card_source, verified
                FROM mma_fights
                WHERE event_id = %s
                  AND COALESCE(is_active, TRUE) = TRUE
                  AND (card_source = 'odds_api' OR COALESCE(verified, FALSE) = FALSE)
                ORDER BY id
                """,
                (event_id,),
            )
            rows = cur.fetchall()

        print(f"EVENT_ID={event_id}")
        print(f"ELIGIBLE_UNVERIFIED_ROWS={len(rows)}")
        for row in rows:
            print(
                f"id={row[0]} bout_uid={row[1]} fight={row[2]} vs {row[3]} "
                f"card_source={row[4]} verified={row[5]}"
            )

        if args.dry_run:
            conn.rollback()
            print("DRY_RUN=true no rows updated")
            return 0

        deactivated = mma_sync.deactivate_unverified_event_bouts(conn, event_id, commit=True)
        print(f"DEACTIVATED_ROWS={deactivated}")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
