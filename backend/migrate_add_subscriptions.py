"""
Migration script to add subscription columns to users table.
Run this on production: python3 migrate_add_subscriptions.py
"""
import sqlite3
import sys

DB_PATH = '/opt/flowup/backend/flowup.db'

def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if columns already exist
    cursor.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cursor.fetchall()]
    
    migrations = []
    
    if 'subscription_tier' not in columns:
        migrations.append("ALTER TABLE users ADD COLUMN subscription_tier TEXT NOT NULL DEFAULT 'free'")
    
    if 'subscription_status' not in columns:
        migrations.append("ALTER TABLE users ADD COLUMN subscription_status TEXT DEFAULT NULL")
    
    if 'subscription_platform' not in columns:
        migrations.append("ALTER TABLE users ADD COLUMN subscription_platform TEXT DEFAULT NULL")
    
    if 'subscription_external_id' not in columns:
        migrations.append("ALTER TABLE users ADD COLUMN subscription_external_id TEXT DEFAULT NULL")
    
    if 'subscription_started_at' not in columns:
        migrations.append("ALTER TABLE users ADD COLUMN subscription_started_at DATETIME DEFAULT NULL")
    
    if 'subscription_expires_at' not in columns:
        migrations.append("ALTER TABLE users ADD COLUMN subscription_expires_at DATETIME DEFAULT NULL")
    
    if 'subscription_cancel_at_period_end' not in columns:
        migrations.append("ALTER TABLE users ADD COLUMN subscription_cancel_at_period_end INTEGER NOT NULL DEFAULT 0")
    
    if 'original_platform' not in columns:
        migrations.append("ALTER TABLE users ADD COLUMN original_platform TEXT DEFAULT NULL")
    
    if not migrations:
        print("All subscription columns already exist. No migration needed.")
        conn.close()
        return
    
    print(f"Running {len(migrations)} migrations...")
    
    for migration in migrations:
        print(f"  {migration}")
        try:
            cursor.execute(migration)
        except Exception as e:
            print(f"Error: {e}")
            conn.rollback()
            conn.close()
            sys.exit(1)
    
    conn.commit()
    conn.close()
    print("✓ Migration completed successfully!")

if __name__ == '__main__':
    main()
