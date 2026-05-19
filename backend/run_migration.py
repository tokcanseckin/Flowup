#!/usr/bin/env python3
"""
Run database migration from SQL file.
Usage: python run_migration.py <migration_file.sql>
"""
import sys
import os
from pathlib import Path

# Add backend directory to path
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import text
from database import engine

def run_migration(sql_file: str):
    """Execute SQL migration file against the database."""
    if not os.path.exists(sql_file):
        print(f"Error: Migration file '{sql_file}' not found")
        sys.exit(1)
    
    with open(sql_file, 'r') as f:
        sql_content = f.read()
    
    print(f"Running migration: {sql_file}")
    print("=" * 60)
    
    # Split by semicolon to handle multiple statements
    statements = [stmt.strip() for stmt in sql_content.split(';') if stmt.strip()]
    
    with engine.begin() as conn:
        for i, statement in enumerate(statements, 1):
            # Remove comment lines from statement
            lines = [line for line in statement.split('\n') if not line.strip().startswith('--')]
            cleaned_statement = '\n'.join(lines).strip()
            
            # Skip if empty after removing comments
            if not cleaned_statement:
                continue
            
            try:
                print(f"\nExecuting statement {i}/{len(statements)}...")
                print(cleaned_statement[:100] + ('...' if len(cleaned_statement) > 100 else ''))
                result = conn.execute(text(cleaned_statement))
                
                # Print results if it's a SELECT
                if cleaned_statement.strip().upper().startswith('SELECT'):
                    rows = result.fetchall()
                    if rows:
                        print("\nResults:")
                        for row in rows:
                            print(f"  {row}")
                
                print("✓ Success")
                
            except Exception as e:
                print(f"✗ Error: {e}")
                # Continue with other statements (many are IF NOT EXISTS)
                continue
    
    print("\n" + "=" * 60)
    print("Migration complete!")

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python run_migration.py <migration_file.sql>")
        sys.exit(1)
    
    run_migration(sys.argv[1])
