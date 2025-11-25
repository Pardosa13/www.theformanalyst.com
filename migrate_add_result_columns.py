#!/usr/bin/env python3
"""
Migration script to add Betfair result columns to existing database.

This script safely adds nullable columns for Betfair integration:
- Horse: betfair_selection_id, final_position, final_odds, result_settled_at, result_source
- Race: betfair_market_id, betfair_mapping_confidence, betfair_mapped_at

Usage:
    python migrate_add_result_columns.py [--confirm]
    
Options:
    --confirm   Skip confirmation prompt and apply changes directly
"""

import os
import sys


def get_db_engine():
    """Create database engine from environment or default"""
    from sqlalchemy import create_engine
    
    database_url = os.environ.get('DATABASE_URL', 'sqlite:///formanalyst.db')
    
    # Fix for postgres:// vs postgresql:// (Railway uses postgres://)
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    
    return create_engine(database_url)


def check_column_exists(engine, table_name, column_name):
    """Check if a column exists in a table"""
    from sqlalchemy import inspect
    
    inspector = inspect(engine)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def add_column_if_not_exists(engine, table_name, column_name, column_type, dry_run=True):
    """Add a column to a table if it doesn't exist"""
    from sqlalchemy import text
    
    if check_column_exists(engine, table_name, column_name):
        print(f"  ✓ Column '{column_name}' already exists in '{table_name}'")
        return False
    
    # Map Python/SQLAlchemy types to SQL types
    sql_type_map = {
        'Integer': 'INTEGER',
        'Float': 'REAL',
        'String': 'VARCHAR(50)',
        'DateTime': 'TIMESTAMP',
    }
    
    sql_type = sql_type_map.get(column_type, column_type)
    
    if dry_run:
        print(f"  → Will add column '{column_name}' ({sql_type}) to '{table_name}'")
        return True
    else:
        # Execute the ALTER TABLE statement
        with engine.connect() as conn:
            sql = f'ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_type}'
            conn.execute(text(sql))
            conn.commit()
        print(f"  ✓ Added column '{column_name}' ({sql_type}) to '{table_name}'")
        return True


def create_index_if_not_exists(engine, table_name, column_name, dry_run=True):
    """Create an index on a column if it doesn't exist"""
    from sqlalchemy import text, inspect
    
    inspector = inspect(engine)
    indexes = inspector.get_indexes(table_name)
    index_name = f'ix_{table_name}_{column_name}'
    
    existing_index_names = [idx['name'] for idx in indexes]
    if index_name in existing_index_names:
        print(f"  ✓ Index '{index_name}' already exists")
        return False
    
    if dry_run:
        print(f"  → Will create index '{index_name}' on '{table_name}.{column_name}'")
        return True
    else:
        with engine.connect() as conn:
            sql = f'CREATE INDEX {index_name} ON {table_name} ({column_name})'
            conn.execute(text(sql))
            conn.commit()
        print(f"  ✓ Created index '{index_name}' on '{table_name}.{column_name}'")
        return True


def run_migration(dry_run=True):
    """Run the migration"""
    print("\n" + "=" * 60)
    print("Betfair Integration - Database Migration")
    print("=" * 60)
    
    if dry_run:
        print("\n[DRY RUN] - No changes will be made\n")
    else:
        print("\n[APPLYING CHANGES]\n")
    
    engine = get_db_engine()
    changes_needed = []
    
    # Horse table columns
    print("Checking 'horses' table:")
    horse_columns = [
        ('betfair_selection_id', 'Integer'),
        ('final_position', 'Integer'),
        ('final_odds', 'Float'),
        ('result_settled_at', 'DateTime'),
        ('result_source', 'String'),
    ]
    
    for col_name, col_type in horse_columns:
        if add_column_if_not_exists(engine, 'horses', col_name, col_type, dry_run):
            changes_needed.append(f"horses.{col_name}")
    
    # Index for betfair_selection_id
    if create_index_if_not_exists(engine, 'horses', 'betfair_selection_id', dry_run):
        changes_needed.append("index: horses.betfair_selection_id")
    
    # Race table columns
    print("\nChecking 'races' table:")
    race_columns = [
        ('betfair_market_id', 'String'),
        ('betfair_mapping_confidence', 'Float'),
        ('betfair_mapped_at', 'DateTime'),
    ]
    
    for col_name, col_type in race_columns:
        if add_column_if_not_exists(engine, 'races', col_name, col_type, dry_run):
            changes_needed.append(f"races.{col_name}")
    
    # Index for betfair_market_id
    if create_index_if_not_exists(engine, 'races', 'betfair_market_id', dry_run):
        changes_needed.append("index: races.betfair_market_id")
    
    print("\n" + "-" * 60)
    
    if not changes_needed:
        print("\n✓ All columns and indexes already exist. No migration needed.")
        return True
    
    if dry_run:
        print(f"\n{len(changes_needed)} change(s) will be applied:")
        for change in changes_needed:
            print(f"  - {change}")
        return False
    else:
        print(f"\n✓ {len(changes_needed)} change(s) applied successfully.")
        return True


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate database for Betfair integration')
    parser.add_argument('--confirm', action='store_true', 
                        help='Skip confirmation prompt and apply changes')
    args = parser.parse_args()
    
    # First, do a dry run to see what changes are needed
    all_up_to_date = run_migration(dry_run=True)
    
    if all_up_to_date:
        sys.exit(0)
    
    # Ask for confirmation unless --confirm was passed
    if not args.confirm:
        print("\n" + "=" * 60)
        response = input("\nApply these changes? [y/N]: ").strip().lower()
        if response not in ('y', 'yes'):
            print("Migration cancelled.")
            sys.exit(0)
    
    # Apply the changes
    run_migration(dry_run=False)
    print("\n✓ Migration complete!")


if __name__ == '__main__':
    main()
