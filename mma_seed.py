"""
mma_seed.py - ONE-TIME script to import Octagon-AI CSV data into Postgres.

Run LOCALLY (not on Railway) after copying the newdata/ CSVs from the
Octagon-AI repo into a local folder.

Usage:
    python mma_seed.py --data-dir ./newdata

This imports:
    - Fighters.csv      -> mma_fighters table
    - Events.csv        -> mma_events table
    - Fights.csv        -> mma_fights table
    - current_glicko.csv -> updates mma_fighters.glicko_* columns

Safe to re-run: uses INSERT ... ON CONFLICT DO UPDATE (upsert).
"""

import os
import sys
import argparse
from datetime import datetime

import pandas as pd

# ── Resolve DATABASE_URL from environment (set .env before running) ──────────
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.environ.get('DATABASE_URL', '')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set. Copy it from Railway and set in .env")
    sys.exit(1)

import psycopg2
from psycopg2.extras import execute_values


def parse_height(val):
    """Convert height string like '5.11' or \"5'11\"\" to cm float."""
    try:
        if pd.isnull(val):
            return None
        s = str(val).strip()
        if "'" in s:
            parts = s.split("'")
            feet = int(parts[0])
            inches = int(parts[1].replace('"', '').strip()) if len(parts) > 1 and parts[1].strip() else 0
            return round(feet * 30.48 + inches * 2.54, 1)
        elif '.' in s:
            parts = s.split('.')
            feet = int(parts[0])
            inches = int(parts[1]) if len(parts) > 1 else 0
            return round(feet * 30.48 + inches * 2.54, 1)
        v = float(s)
        return v if v > 100 else None
    except Exception:
        return None


def parse_reach(val):
    """Convert reach in inches to cm."""
    try:
        if pd.isnull(val):
            return None
        return round(float(val) * 2.54, 1)
    except Exception:
        return None


def seed_fighters(conn, fighters_df, glicko_df):
    print(f"  Seeding {len(fighters_df)} fighters...")

    # Build glicko lookup by Fighter_Id
    glicko_map = {}
    if glicko_df is not None:
        for _, row in glicko_df.iterrows():
            glicko_map[row['Fighter_Id']] = {
                'rating': float(row.get('Rating', 1500)),
                'rd': float(row.get('RD', 350)),
                'vol': float(row.get('Vol', 0.06)),
            }

    rows = []
    for _, f in fighters_df.iterrows():
        fid = str(f['Fighter_Id']).strip()
        g = glicko_map.get(fid, {'rating': 1500.0, 'rd': 350.0, 'vol': 0.06})
        rows.append((
            fid,
            str(f.get('Full Name', '')).strip(),
            str(f.get('Nickname', '')).strip() or None,
            parse_height(f.get('Ht.')),
            float(f['Wt.']) if pd.notnull(f.get('Wt.')) else None,
            parse_reach(f.get('Reach')),
            str(f.get('Stance', '')).strip() or None,
            int(f.get('W', 0)) if pd.notnull(f.get('W')) else 0,
            int(f.get('L', 0)) if pd.notnull(f.get('L')) else 0,
            int(f.get('D', 0)) if pd.notnull(f.get('D')) else 0,
            bool(f.get('Belt', False)),
            g['rating'],
            g['rd'],
            g['vol'],
            datetime.utcnow(),
        ))

    sql = """
        INSERT INTO mma_fighters
            (id, full_name, nickname, height_cm, weight_lbs, reach_cm, stance,
             wins, losses, draws, has_belt,
             glicko_rating, glicko_rd, glicko_vol, glicko_updated_at,
             created_at, updated_at)
        VALUES %s
        ON CONFLICT (id) DO UPDATE SET
            full_name       = EXCLUDED.full_name,
            wins            = EXCLUDED.wins,
            losses          = EXCLUDED.losses,
            draws           = EXCLUDED.draws,
            has_belt        = EXCLUDED.has_belt,
            glicko_rating   = EXCLUDED.glicko_rating,
            glicko_rd       = EXCLUDED.glicko_rd,
            glicko_vol      = EXCLUDED.glicko_vol,
            glicko_updated_at = EXCLUDED.glicko_updated_at,
            updated_at      = NOW()
    """

    # Add created_at / updated_at to each row
    now = datetime.utcnow()
    rows_with_ts = [r + (now, now) for r in rows]

    with conn.cursor() as cur:
        execute_values(cur, sql, rows_with_ts, page_size=500)
    conn.commit()
    print(f"  ✓ {len(rows)} fighters upserted")


def seed_events(conn, events_df):
    print(f"  Seeding {len(events_df)} events...")

    rows = []
    for _, e in events_df.iterrows():
        try:
            date_val = pd.to_datetime(e.get('Date')).date() if pd.notnull(e.get('Date')) else None
        except Exception:
            date_val = None

        rows.append((
            str(e['Event_Id']).strip(),
            str(e.get('Name', '')).strip(),
            date_val,
            str(e.get('Location', '')).strip() or None,
            True,   # historical events are all completed
        ))

    sql = """
        INSERT INTO mma_events (id, name, date, location, is_completed, created_at, updated_at)
        VALUES %s
        ON CONFLICT (id) DO UPDATE SET
            name         = EXCLUDED.name,
            date         = EXCLUDED.date,
            location     = EXCLUDED.location,
            is_completed = EXCLUDED.is_completed,
            updated_at   = NOW()
    """
    now = datetime.utcnow()
    rows_with_ts = [r + (now, now) for r in rows]

    with conn.cursor() as cur:
        execute_values(cur, sql, rows_with_ts, page_size=500)
    conn.commit()
    print(f"  ✓ {len(rows)} events upserted")


def seed_fights(conn, fights_df, events_df):
    print(f"  Seeding {len(fights_df)} fights...")

    # Build event_id set for FK validation
    valid_event_ids = set(str(e).strip() for e in events_df['Event_Id'])

    rows = []
    skipped = 0
    for _, f in fights_df.iterrows():
        eid = str(f.get('Event_Id', '')).strip()
        if eid not in valid_event_ids:
            skipped += 1
            continue

        rows.append((
            eid,
            str(f.get('Fighter_1', '')).strip(),
            str(f.get('Fighter_2', '')).strip(),
            str(f.get('Fighter_Id_1', '')).strip() or None,
            str(f.get('Fighter_Id_2', '')).strip() or None,
            str(f.get('Weight_Class', '')).strip() or None,
            False,  # is_main_card - unknown from historical CSV
            False,  # is_title_fight - unknown from historical CSV
            # Result fields
            None,   # winner_name — we can infer but leave null for now
            str(f.get('Method', '')).strip() or None,
            int(f['Round']) if pd.notnull(f.get('Round')) else None,
            str(f.get('Fight_Time', '')).strip() or None,
        ))

    sql = """
        INSERT INTO mma_fights
            (event_id, fighter_1_name, fighter_2_name,
             fighter_1_id, fighter_2_id,
             weight_class, is_main_card, is_title_fight,
             winner_name, method, round_ended, time_ended,
             created_at)
        VALUES %s
        ON CONFLICT DO NOTHING
    """
    now = datetime.utcnow()
    rows_with_ts = [r + (now,) for r in rows]

    with conn.cursor() as cur:
        execute_values(cur, sql, rows_with_ts, page_size=500)
    conn.commit()
    print(f"  ✓ {len(rows)} fights inserted ({skipped} skipped — unknown event)")


def main():
    parser = argparse.ArgumentParser(description='Seed MMA data from Octagon-AI CSVs')
    parser.add_argument('--data-dir', required=True, help='Path to Octagon-AI newdata/ folder')
    args = parser.parse_args()

    data_dir = args.data_dir
    if not os.path.isdir(data_dir):
        print(f"ERROR: {data_dir} is not a directory")
        sys.exit(1)

    print(f"\n=== MMA Seed Script ===")
    print(f"Data dir: {data_dir}")
    print(f"Database: {DATABASE_URL[:30]}...")

    # Load CSVs
    fighters_path = os.path.join(data_dir, 'Fighters.csv')
    events_path   = os.path.join(data_dir, 'Events.csv')
    fights_path   = os.path.join(data_dir, 'Fights.csv')
    glicko_path   = os.path.join(data_dir, 'current_glicko.csv')

    for p in [fighters_path, events_path, fights_path]:
        if not os.path.exists(p):
            print(f"ERROR: Missing required file: {p}")
            sys.exit(1)

    print("\nLoading CSVs...")
    fighters_df = pd.read_csv(fighters_path)
    events_df   = pd.read_csv(events_path)
    fights_df   = pd.read_csv(fights_path)
    glicko_df   = pd.read_csv(glicko_path) if os.path.exists(glicko_path) else None

    print(f"  Fighters: {len(fighters_df):,}")
    print(f"  Events:   {len(events_df):,}")
    print(f"  Fights:   {len(fights_df):,}")
    if glicko_df is not None:
        print(f"  Glicko:   {len(glicko_df):,}")

    print("\nConnecting to database...")
    conn = psycopg2.connect(DATABASE_URL)

    print("\nSeeding tables...")
    seed_fighters(conn, fighters_df, glicko_df)
    seed_events(conn, events_df)
    seed_fights(conn, fights_df, events_df)

    conn.close()
    print("\n✓ Seed complete! MMA tables populated.")
    print("  Next: Run mma_sync.py to pull upcoming events and generate predictions.")


if __name__ == '__main__':
    main()
