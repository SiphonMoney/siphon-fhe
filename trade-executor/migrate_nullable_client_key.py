"""
Migration script to make encrypted_client_key nullable
This allows storing strategies with MPC shares (where full key is not stored)
"""
import os
import sys
import sqlite3
from app import app, db
from database import Strategy

def make_client_key_nullable():
    """Make encrypted_client_key column nullable using SQLite table recreation"""
    with app.app_context():
        db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '').split('?')[0]
        
        if not os.path.exists(db_path):
            print(f"⚠️  Database file not found: {db_path}")
            print("   Creating new database with correct schema...")
            db.create_all()
            print("✅ Database created with nullable encrypted_client_key")
            return
        
        print(f"🔄 Migrating database: {db_path}")
        
        # Connect directly to SQLite
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        try:
            # Check current schema
            cursor.execute("PRAGMA table_info(strategy)")
            columns = cursor.fetchall()
            
            # Check if encrypted_client_key is already nullable
            client_key_nullable = False
            for col in columns:
                if col[1] == 'encrypted_client_key':
                    client_key_nullable = col[3] == 0  # 0 = nullable, 1 = NOT NULL
                    break
            
            if client_key_nullable:
                print("✅ encrypted_client_key is already nullable, no migration needed")
                conn.close()
                return
            
            print("📋 Current schema: encrypted_client_key is NOT NULL")
            print("   Migrating to allow NULL...")
            
            # SQLite doesn't support ALTER COLUMN directly, so we need to recreate the table
            # Step 1: Get all existing data
            cursor.execute("SELECT * FROM strategy")
            rows = cursor.fetchall()
            column_names = [desc[0] for desc in cursor.description]
            
            print(f"   Found {len(rows)} strategies to migrate")
            
            # Step 2: Create new table with nullable encrypted_client_key
            # We'll use SQLAlchemy to create the new table schema
            # First, drop and recreate with new schema
            cursor.execute("DROP TABLE IF EXISTS strategy_backup")
            cursor.execute("CREATE TABLE strategy_backup AS SELECT * FROM strategy")
            
            # Step 3: Drop old table
            cursor.execute("DROP TABLE strategy")
            
            # Step 4: Create new table with correct schema (using SQLAlchemy)
            db.create_all()
            
            # Step 5: Copy data back (handle NULL encrypted_client_key)
            if len(rows) > 0:
                # Get column names for insert
                placeholders = ','.join(['?' for _ in column_names])
                insert_sql = f"INSERT INTO strategy ({','.join(column_names)}) VALUES ({placeholders})"
                
                for row in rows:
                    # Convert row to dict for easier handling
                    row_dict = dict(zip(column_names, row))
                    # Convert to list, preserving NULL values
                    row_list = list(row)
                    cursor.execute(insert_sql, row_list)
            
            # Step 6: Drop backup table
            cursor.execute("DROP TABLE IF EXISTS strategy_backup")
            
            conn.commit()
            print("✅ Migration complete: encrypted_client_key is now nullable")
            
            # Verify
            cursor.execute("PRAGMA table_info(strategy)")
            columns = cursor.fetchall()
            for col in columns:
                if col[1] == 'encrypted_client_key':
                    if col[3] == 0:
                        print(f"✅ Verified: encrypted_client_key is nullable")
                    else:
                        print(f"⚠️  Warning: encrypted_client_key is still NOT NULL")
                    break
            
        except Exception as e:
            conn.rollback()
            print(f"❌ Migration failed: {e}")
            print("   Attempting to restore from backup...")
            # Try to restore from backup if it exists
            try:
                cursor.execute("DROP TABLE IF EXISTS strategy")
                cursor.execute("ALTER TABLE strategy_backup RENAME TO strategy")
                conn.commit()
                print("✅ Restored from backup")
            except Exception as restore_error:
                print(f"❌ Could not restore from backup: {restore_error}")
                print("   You may need to recreate the database manually")
            raise
        finally:
            conn.close()

if __name__ == '__main__':
    print("=" * 60)
    print("Migration: Make encrypted_client_key Nullable")
    print("=" * 60)
    print()
    
    make_client_key_nullable()
    
    print()
    print("✅ Migration script complete!")
