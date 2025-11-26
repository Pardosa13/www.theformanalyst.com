#!/usr/bin/env python3
"""
Non-interactive, idempotent script to ensure required database columns exist.

This script is a fallback for environments where Alembic migrations cannot be run.
It uses ALTER TABLE ... IF NOT EXISTS statements (PostgreSQL) to safely add missing columns.

Usage:
    python scripts/ensure_db_columns.py

Environment:
    DATABASE_URL or SQLALCHEMY_DATABASE_URI must be set.
"""
import os
import sys
from sqlalchemy import create_engine, inspect, text


def get_database_url():
    """Get database URL from environment variables."""
    url = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI")
    if not url:
        print("ERROR: DATABASE_URL or SQLALCHEMY_DATABASE_URI environment variable not set.")
        sys.exit(1)

    # Fix for postgres:// vs postgresql:// (Railway uses postgres://)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    return url


def ensure_column(conn, table_name, column_name, column_type):
    """Add a column if it doesn't exist (PostgreSQL specific with IF NOT EXISTS)."""
    sql = f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS "{column_name}" {column_type};'
    print(f"  Executing: {sql}")
    conn.execute(text(sql))


def main():
    """Main function to ensure all required columns exist."""
    print("=" * 60)
    print("Ensure Database Columns Script")
    print("=" * 60)

    database_url = get_database_url()
    print(f"\nConnecting to database...")

    try:
        engine = create_engine(database_url)
        inspector = inspect(engine)
        table_names = inspector.get_table_names()
        print(f"Found tables: {table_names}")
    except Exception as e:
        print(f"ERROR: Failed to connect to database: {e}")
        sys.exit(1)

    # Check if required tables exist
    if "races" not in table_names:
        print("WARNING: 'races' table not found. Skipping races columns.")
    if "horses" not in table_names:
        print("WARNING: 'horses' table not found. Skipping horses columns.")

    # Define columns to add
    races_columns = {
        "market_id": "VARCHAR(255)"
    }

    horses_columns = {
        "betfair_selection_id": "INTEGER",
        "final_position": "INTEGER",
        "final_odds": "FLOAT",
        "result_settled_at": "TIMESTAMP",
        "result_source": "VARCHAR(50)"
    }

    with engine.begin() as conn:
        # Add races.market_id
        if "races" in table_names:
            print("\nProcessing 'races' table:")
            existing_cols = [c["name"] for c in inspector.get_columns("races")]
            for col_name, col_type in races_columns.items():
                if col_name in existing_cols:
                    print(f"  ✓ Column '{col_name}' already exists.")
                else:
                    ensure_column(conn, "races", col_name, col_type)
                    print(f"  ✓ Added column '{col_name}'.")

        # Add horses Betfair columns
        if "horses" in table_names:
            print("\nProcessing 'horses' table:")
            existing_cols = [c["name"] for c in inspector.get_columns("horses")]
            for col_name, col_type in horses_columns.items():
                if col_name in existing_cols:
                    print(f"  ✓ Column '{col_name}' already exists.")
                else:
                    ensure_column(conn, "horses", col_name, col_type)
                    print(f"  ✓ Added column '{col_name}'.")

            # Create index on betfair_selection_id if column was added
            if "betfair_selection_id" in [c["name"] for c in inspector.get_columns("horses")]:
                index_sql = 'CREATE INDEX IF NOT EXISTS "ix_horses_betfair_selection_id" ON "horses" ("betfair_selection_id");'
                print(f"  Executing: {index_sql}")
                conn.execute(text(index_sql))
                print("  ✓ Index on 'betfair_selection_id' verified/created.")

    print("\n" + "=" * 60)
    print("Done. All required columns verified/added.")
    print("=" * 60)


if __name__ == "__main__":
    main()
