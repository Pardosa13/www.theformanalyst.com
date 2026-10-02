#!/usr/bin/env python3
"""
Safe, interactive script to add nullable Betfair result columns to your horses table.
"""
import os, sys
from sqlalchemy import create_engine, inspect, text

SQLALCHEMY_DATABASE_URI = os.getenv("SQLALCHEMY_DATABASE_URI")
if not SQLALCHEMY_DATABASE_URI:
    print("Please set SQLALCHEMY_DATABASE_URI environment variable (e.g. postgresql://user:pass@host/db)")
    sys.exit(1)

engine = create_engine(SQLALCHEMY_DATABASE_URI)
inspector = inspect(engine)
candidates = ['horses', 'horse']
table_name = None
for t in candidates:
    if t in inspector.get_table_names():
        table_name = t
        break

if not table_name:
    print("Could not find 'horses' or 'horse' table in the database. Please verify the table name and update this script if needed.")
    sys.exit(1)

existing_cols = [c['name'] for c in inspector.get_columns(table_name)]

desired = {
    'betfair_selection_id': "INTEGER",
    'final_position': "INTEGER",
    'final_odds': "FLOAT",
    'result_settled_at': "TIMESTAMP",
    'result_source': "VARCHAR(50)"
}

to_add = {k:v for k,v in desired.items() if k not in existing_cols}
if not to_add:
    print("No columns to add. Table '{}' already has all desired columns.".format(table_name))
    sys.exit(0)

print("Table found:", table_name)
print("Existing columns:", existing_cols)
print("\nColumns to add:")
for k, v in to_add.items():
    print(f" - {k} ({v})")

confirm = input("\nThis will ALTER TABLE {} and add the above nullable columns. Proceed? (yes/no): ".format(table_name)).strip().lower()
if confirm not in ('yes', 'y'):
    print("Aborted.")
    sys.exit(0)

with engine.begin() as conn:
    for k, v in to_add.items():
        sql = f'ALTER TABLE "{table_name}" ADD COLUMN "{k}" {v};'
        print("Executing:", sql)
        conn.execute(text(sql))

print("Done. Added columns to table", table_name)
