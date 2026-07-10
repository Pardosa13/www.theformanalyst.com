#!/usr/bin/env python3
"""Safe one-off Railway repair for a single UFC event card.

Default target is UFC 329. The script intentionally uses the same canonical
mma_sync ingestion helpers as the cron path and refuses to perform stale cleanup
unless ESPN returns a complete event-card payload.
"""

from __future__ import annotations

import argparse
import sys

import mma_sync


def _event_label(event: dict) -> str:
    return f"{event.get('event_name')} ({event.get('event_id')})"


def _find_event(events: list[dict], query: str) -> dict:
    q = query.lower().strip()
    for event in events:
        if q == str(event.get('event_id', '')).lower() or q in str(event.get('event_name', '')).lower():
            return event
    raise RuntimeError(f"Could not find UFC event matching {query!r}; events={[ _event_label(e) for e in events ]}")


def _active_fights(conn, event_id: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, bout_uid, fighter_1_name, fighter_2_name, status
            FROM mma_fights
            WHERE event_id = %s
              AND COALESCE(is_active, TRUE) = TRUE
              AND COALESCE(status, 'confirmed') = 'confirmed'
            ORDER BY is_main_card DESC, id ASC
            """,
            (event_id,),
        )
        return cur.fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely repair one UFC event card via canonical sync logic.")
    parser.add_argument("--event", default="UFC 329", help="Event name fragment or ESPN event_id; default: UFC 329")
    args = parser.parse_args()

    if not mma_sync.DATABASE_URL:
        raise RuntimeError("DATABASE_URL must be set in the Railway service environment")

    conn = mma_sync.get_conn()
    try:
        mma_sync.ensure_mma_integrity_schema(conn)
        events = mma_sync.scrape_upcoming_events()
        event = _find_event(events, args.event)
        event_id = event["event_id"]
        print(f"EVENT_ID={event_id}")
        print(f"EVENT_NAME={event['event_name']}")

        fights = mma_sync.scrape_event_details(event["url"], event_id)
        print(f"BOUTS_RECEIVED={len(fights)}")
        payload_complete = mma_sync.event_card_fetch_is_complete(conn, event_id, fights)
        print(f"ESPN_CARD_COMPLETE={payload_complete}")

        if not payload_complete:
            conn.rollback()
            print("ABORTED: payload incomplete; rolled back and performed no cleanup")
            return 2

        seen_bout_uids: set[str] = set()
        deactivated_duplicates: list[int] = []

        try:
            mma_sync.upsert_event(conn, event, commit=False)
            for fight in fights:
                bout_uid = mma_sync.canonical_bout_uid(event_id, fight)
                fight_id = mma_sync.upsert_fight(conn, event_id, fight, commit=False)
                seen_bout_uids.add(bout_uid)
                deactivated = mma_sync.deactivate_duplicate_active_matchups(
                    conn,
                    event_id,
                    fight_id,
                    bout_uid,
                    None,
                    None,
                    fight["fighter_1"],
                    fight["fighter_2"],
                )
                if deactivated:
                    deactivated_duplicates.append(fight_id)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE mma_fights
                    SET is_active = FALSE, status = 'cancelled', updated_at = NOW()
                    WHERE event_id = %s
                      AND COALESCE(is_active, TRUE) = TRUE
                      AND bout_uid <> ALL(%s)
                    RETURNING id, bout_uid, fighter_1_name, fighter_2_name
                    """,
                    (event_id, list(seen_bout_uids)),
                )
                stale_rows = cur.fetchall()

            stale_ids = [row[0] for row in stale_rows]
            with conn.cursor() as cur:
                if stale_ids:
                    cur.execute(
                        """
                        DELETE FROM mma_predictions
                        WHERE fight_id = ANY(%s)
                        RETURNING fight_id, predicted_winner
                        """,
                        (stale_ids,),
                    )
                    deleted_predictions = cur.fetchall()
                else:
                    deleted_predictions = []

            conn.commit()
        except Exception:
            conn.rollback()
            raise

        print("STALE_BOUTS_DEACTIVATED=")
        if stale_rows:
            for row in stale_rows:
                print(f"  id={row[0]} bout_uid={row[1]} fight={row[2]} vs {row[3]}")
        else:
            print("  none")

        print("PREDICTIONS_DELETED=")
        if deleted_predictions:
            for row in deleted_predictions:
                print(f"  fight_id={row[0]} predicted_winner={row[1]}")
        else:
            print("  none")

        active = _active_fights(conn, event_id)
        print(f"FINAL_ACTIVE_BOUT_COUNT={len(active)}")
        print("FINAL_ACTIVE_BOUTS=")
        for row in active:
            print(f"  id={row[0]} bout_uid={row[1]} status={row[4]} fight={row[2]} vs {row[3]}")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
