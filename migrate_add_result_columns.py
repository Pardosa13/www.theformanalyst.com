#!/usr/bin/env python3
"""
Migration helper script to add Betfair result columns to the database.

This script safely adds nullable columns for Betfair integration:
- Race.betfair_market_id
- Race.betfair_mapped
- Horse.betfair_selection_id
- Horse.final_position
- Horse.final_odds
- Horse.result_settled_at
- Horse.result_source

Usage:
    python migrate_add_result_columns.py

The script will:
1. Show what columns will be added
2. Ask for confirmation
3. Apply the changes if confirmed

This script is idempotent - it will skip columns that already exist.
"""

import os
import sys
from sqlalchemy import create_engine, inspect, text

def get_database_url():
    """Get database URL from environment or default to SQLite."""
    db_url = os.environ.get('DATABASE_URL', 'sqlite:///formanalyst.db')
    # Fix for postgres:// vs postgresql:// (Railway uses postgres://)
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    return db_url

def get_existing_columns(inspector, table_name):
    """Get list of existing column names for a table."""
    try:
        columns = inspector.get_columns(table_name)
        return [col['name'] for col in columns]
    except Exception:
        return []

def main():
    db_url = get_database_url()
    print(f"Database URL: {db_url[:50]}...")
    
    engine = create_engine(db_url)
    inspector = inspect(engine)
    
    # Define columns to add
    race_columns = [
        ('betfair_market_id', 'VARCHAR(50)', 'Betfair market ID for this race'),
        ('betfair_mapped', 'BOOLEAN DEFAULT FALSE', 'Whether race is mapped to Betfair'),
    ]
    
    horse_columns = [
        ('betfair_selection_id', 'INTEGER', 'Betfair selection ID for this horse'),
        ('final_position', 'INTEGER', 'Final finishing position'),
        ('final_odds', 'FLOAT', 'Final Betfair odds'),
        ('result_settled_at', 'TIMESTAMP', 'When the result was settled'),
        ('result_source', 'VARCHAR(50)', 'Source of the result (e.g., betfair)'),
    ]
    
    # Check existing columns
    existing_race_cols = get_existing_columns(inspector, 'races')
    existing_horse_cols = get_existing_columns(inspector, 'horses')
    
    # Determine which columns need to be added
    race_cols_to_add = [(name, dtype, desc) for name, dtype, desc in race_columns 
                        if name not in existing_race_cols]
    horse_cols_to_add = [(name, dtype, desc) for name, dtype, desc in horse_columns 
                         if name not in existing_horse_cols]
    
    if not race_cols_to_add and not horse_cols_to_add:
        print("\n✓ All Betfair columns already exist. No migration needed.")
        return 0
    
    # Show what will be added
    print("\n" + "=" * 60)
    print("BETFAIR INTEGRATION MIGRATION")
    print("=" * 60)
    
    if race_cols_to_add:
        print("\nColumns to add to 'races' table:")
        for name, dtype, desc in race_cols_to_add:
            print(f"  - {name} ({dtype}): {desc}")
    
    if horse_cols_to_add:
        print("\nColumns to add to 'horses' table:")
        for name, dtype, desc in horse_cols_to_add:
            print(f"  - {name} ({dtype}): {desc}")
    
    print("\n" + "-" * 60)
    
    # Ask for confirmation
    response = input("Do you want to apply these changes? (yes/no): ").strip().lower()
    
    if response != 'yes':
        print("Migration cancelled.")
        return 1
    
    # Apply migrations
    print("\nApplying migrations...")
    
    with engine.connect() as conn:
        # Handle PostgreSQL vs SQLite differences
        is_postgres = 'postgresql' in db_url
        
        for name, dtype, desc in race_cols_to_add:
            try:
                if is_postgres:
                    # PostgreSQL syntax
                    if 'BOOLEAN' in dtype:
                        sql = f"ALTER TABLE races ADD COLUMN IF NOT EXISTS {name} BOOLEAN DEFAULT FALSE"
                    else:
                        sql = f"ALTER TABLE races ADD COLUMN IF NOT EXISTS {name} {dtype.split()[0]}"
                else:
                    # SQLite syntax (doesn't support IF NOT EXISTS for columns)
                    if 'BOOLEAN' in dtype:
                        sql = f"ALTER TABLE races ADD COLUMN {name} INTEGER DEFAULT 0"
                    else:
                        sql = f"ALTER TABLE races ADD COLUMN {name} {dtype.split()[0]}"
                
                conn.execute(text(sql))
                conn.commit()
                print(f"  ✓ Added races.{name}")
            except Exception as e:
                if 'duplicate column' in str(e).lower() or 'already exists' in str(e).lower():
                    print(f"  ⚠ races.{name} already exists, skipping")
                else:
                    print(f"  ✗ Failed to add races.{name}: {e}")
        
        for name, dtype, desc in horse_cols_to_add:
            try:
                if is_postgres:
                    sql = f"ALTER TABLE horses ADD COLUMN IF NOT EXISTS {name} {dtype.split()[0]}"
                else:
                    sql = f"ALTER TABLE horses ADD COLUMN {name} {dtype.split()[0]}"
                
                conn.execute(text(sql))
                conn.commit()
                print(f"  ✓ Added horses.{name}")
            except Exception as e:
                if 'duplicate column' in str(e).lower() or 'already exists' in str(e).lower():
                    print(f"  ⚠ horses.{name} already exists, skipping")
                else:
                    print(f"  ✗ Failed to add horses.{name}: {e}")
        
        # Create indexes for Betfair columns
        try:
            if is_postgres:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_races_betfair_market_id ON races (betfair_market_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_horses_betfair_selection_id ON horses (betfair_selection_id)"))
            else:
                # SQLite: check if index exists first
                try:
                    conn.execute(text("CREATE INDEX ix_races_betfair_market_id ON races (betfair_market_id)"))
                except Exception:
                    pass
                try:
                    conn.execute(text("CREATE INDEX ix_horses_betfair_selection_id ON horses (betfair_selection_id)"))
                except Exception:
                    pass
            conn.commit()
            print("  ✓ Created indexes")
        except Exception as e:
            print(f"  ⚠ Index creation note: {e}")
    
    print("\n✓ Migration completed successfully!")
    print("\nNext steps:")
    print("  1. Set BETFAIR_ENABLED=true in your environment")
    print("  2. Configure Betfair credentials (see BETFAIR_README.md)")
    print("  3. Restart the application")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
