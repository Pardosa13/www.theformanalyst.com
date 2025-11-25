#!/usr/bin/env python3
"""
Migration Helper: Add Betfair Result Columns

This script safely adds nullable columns to the database for Betfair integration.
It must be run manually and will:
1. Print planned actions
2. Ask for confirmation
3. Apply changes using SQL ALTER TABLE commands

Usage:
    python migrate_add_result_columns.py

For Railway deployment:
    railway run python migrate_add_result_columns.py

Environment Variables Required:
    DATABASE_URL - Database connection string

DO NOT run this script automatically. It should only be run once after 
reviewing the planned changes.
"""

import os
import sys

# Check if running in automated mode (skip confirmation)
AUTOMATED = os.environ.get('MIGRATE_AUTO_CONFIRM', 'false').lower() == 'true'


def get_database_url():
    """Get database URL from environment."""
    url = os.environ.get('DATABASE_URL', 'sqlite:///formanalyst.db')
    # Fix for postgres:// vs postgresql:// (Railway uses postgres://)
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return url


def column_exists(cursor, table, column, db_type):
    """Check if a column exists in a table."""
    if db_type == 'sqlite':
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
        return column in columns
    else:  # PostgreSQL
        cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = %s AND column_name = %s
            )
        """, (table, column))
        return cursor.fetchone()[0]


def print_plan(columns_to_add):
    """Print the migration plan."""
    print("\n" + "=" * 60)
    print("BETFAIR INTEGRATION - DATABASE MIGRATION PLAN")
    print("=" * 60)
    
    if not columns_to_add:
        print("\n✓ All columns already exist. No changes needed.")
        return False
    
    print("\nThe following columns will be added:\n")
    
    for table, column, col_type, description in columns_to_add:
        print(f"  • {table}.{column}")
        print(f"    Type: {col_type}")
        print(f"    Description: {description}")
        print()
    
    print("=" * 60)
    print("IMPORTANT:")
    print("  - All columns are NULLABLE (no data loss risk)")
    print("  - Existing data will be preserved")
    print("  - Indexes will be created for lookup columns")
    print("=" * 60)
    
    return True


def confirm_migration():
    """Ask user for confirmation."""
    if AUTOMATED:
        print("\n[AUTOMATED MODE] Skipping confirmation...")
        return True
    
    print("\nDo you want to proceed with the migration?")
    response = input("Type 'yes' to confirm: ").strip().lower()
    return response == 'yes'


def run_migration():
    """Run the database migration."""
    
    # Define columns to add
    COLUMNS_SPEC = [
        # (table, column, sqlite_type, pg_type, description, create_index)
        ('races', 'market_id', 'VARCHAR(50)', 'VARCHAR(50)', 
         'Betfair market ID for the race', True),
        ('horses', 'betfair_selection_id', 'INTEGER', 'INTEGER', 
         'Betfair selection ID for the horse', True),
        ('horses', 'final_position', 'INTEGER', 'INTEGER', 
         'Final race position (1, 2, 3, etc.)', False),
        ('horses', 'final_odds', 'FLOAT', 'FLOAT', 
         'Final traded odds at race close', False),
        ('horses', 'result_settled_at', 'DATETIME', 'TIMESTAMP', 
         'Timestamp when result was settled', False),
        ('horses', 'result_source', 'VARCHAR(50)', 'VARCHAR(50)', 
         'Source of result data (e.g., "betfair")', False),
    ]
    
    db_url = get_database_url()
    is_sqlite = db_url.startswith('sqlite')
    
    print(f"\nDatabase: {'SQLite' if is_sqlite else 'PostgreSQL'}")
    print(f"URL: {db_url[:50]}..." if len(db_url) > 50 else f"URL: {db_url}")
    
    # Connect to database
    if is_sqlite:
        import sqlite3
        db_path = db_url.replace('sqlite:///', '')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        placeholder = '?'
    else:
        try:
            import psycopg2
        except ImportError:
            print("\nERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
            sys.exit(1)
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        placeholder = '%s'
    
    # Check which columns need to be added
    columns_to_add = []
    for table, column, sqlite_type, pg_type, description, create_index in COLUMNS_SPEC:
        if not column_exists(cursor, table, column, 'sqlite' if is_sqlite else 'pg'):
            col_type = sqlite_type if is_sqlite else pg_type
            columns_to_add.append((table, column, col_type, description, create_index))
    
    # Print plan
    display_columns = [(t, c, ct, d) for t, c, ct, d, _ in columns_to_add]
    if not print_plan(display_columns):
        cursor.close()
        conn.close()
        return
    
    # Confirm
    if not confirm_migration():
        print("\nMigration cancelled.")
        cursor.close()
        conn.close()
        return
    
    # Execute migration
    print("\nApplying migration...")
    
    try:
        for table, column, col_type, description, create_index in columns_to_add:
            print(f"  Adding {table}.{column}...", end=" ")
            
            # Add column
            sql = f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
            cursor.execute(sql)
            
            # Create index if needed
            if create_index:
                index_name = f"ix_{table}_{column}"
                if is_sqlite:
                    cursor.execute(
                        f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})"
                    )
                else:
                    cursor.execute(
                        f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})"
                    )
            
            print("✓")
        
        conn.commit()
        print("\n✓ Migration completed successfully!")
        
    except Exception as e:
        conn.rollback()
        print(f"\n✗ Migration failed: {e}")
        sys.exit(1)
    
    finally:
        cursor.close()
        conn.close()


def main():
    """Main entry point."""
    print("\n" + "=" * 60)
    print("BETFAIR INTEGRATION - DATABASE MIGRATION")
    print("=" * 60)
    print("\nThis script will add columns to support Betfair integration.")
    print("It is safe to run multiple times (idempotent).")
    
    run_migration()


if __name__ == '__main__':
    main()
