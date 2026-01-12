"""
Database Migration Script
Migrates existing database to use compression and encryption
Also optimizes database size
"""
import os
import sys
from app import app, db
from database import Strategy
from encryption import db_encryption, data_compression
import json
from sqlalchemy import text
from datetime import datetime

def migrate_database():
    """Migrate existing database to new compressed/encrypted format"""
    with app.app_context():
        print("🔄 Starting database migration...")
        
        # Ensure database directory exists
        import os
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        if 'sqlite' in db_uri and 'instance' in db_uri:
            instance_dir = os.path.join(os.path.dirname(__file__), 'instance')
            os.makedirs(instance_dir, exist_ok=True)
            print(f"📁 Ensured instance directory exists: {instance_dir}")
        
        try:
            # Create tables if they don't exist
            db.create_all()
            
            # Get all strategies
            strategies = Strategy.query.all()
            total = len(strategies)
            print(f"📊 Found {total} strategies to migrate")
            
            if total == 0:
                print("✅ No strategies to migrate")
                return
        except Exception as e:
            print(f"⚠️  Could not query database: {e}")
            print("   Database may not exist yet or schema mismatch")
            print("   Creating database tables...")
            try:
                db.create_all()
                print("✅ Database tables created")
            except Exception as create_error:
                print(f"❌ Failed to create tables: {create_error}")
            return
        
        migrated = 0
        errors = 0
        
        for i, strategy in enumerate(strategies, 1):
            try:
                # Check if already migrated (compressed data starts with specific pattern)
                # For now, we'll migrate all records
                
                # Get original data (might be plaintext or old format)
                original_client_key = strategy.encrypted_client_key if hasattr(strategy, 'encrypted_client_key') else None
                original_server_key = strategy.server_key if hasattr(strategy, 'server_key') else None
                
                # Re-save with new format (compression + encryption)
                # The database model will handle compression/encryption automatically
                if original_client_key:
                    strategy.encrypted_client_key = original_client_key
                if original_server_key:
                    strategy.server_key = original_server_key
                
                # Compress other large fields
                if hasattr(strategy, 'encrypted_upper_bound') and strategy.encrypted_upper_bound:
                    if isinstance(strategy.encrypted_upper_bound, str):
                        strategy.encrypted_upper_bound = strategy.encrypted_upper_bound
                
                if hasattr(strategy, 'encrypted_lower_bound') and strategy.encrypted_lower_bound:
                    if isinstance(strategy.encrypted_lower_bound, str):
                        strategy.encrypted_lower_bound = strategy.encrypted_lower_bound
                
                migrated += 1
                if i % 10 == 0:
                    print(f"  Progress: {i}/{total} ({i*100//total}%)")
                    db.session.commit()  # Commit in batches
                    
            except Exception as e:
                errors += 1
                print(f"❌ Error migrating strategy {strategy.id}: {e}")
                db.session.rollback()
        
        # Final commit
        db.session.commit()
        
        print(f"\n✅ Migration complete!")
        print(f"   Migrated: {migrated}")
        print(f"   Errors: {errors}")
        
        # Optimize database
        print("\n🔧 Optimizing database...")
        try:
            # Vacuum SQLite database to reclaim space
            db.session.execute(text("VACUUM"))
            db.session.commit()
            print("✅ Database optimized")
        except Exception as e:
            print(f"⚠️  Could not optimize database: {e}")

def check_database_size():
    """Check database size before and after migration"""
    db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
    if os.path.exists(db_path):
        size_mb = os.path.getsize(db_path) / (1024 * 1024)
        print(f"📦 Database size: {size_mb:.2f} MB")
        return size_mb
    return 0

if __name__ == '__main__':
    print("=" * 60)
    print("Database Migration Tool")
    print("=" * 60)
    
    size_before = check_database_size()
    print(f"\n📊 Size before migration: {size_before:.2f} MB\n")
    
    migrate_database()
    
    size_after = check_database_size()
    print(f"\n📊 Size after migration: {size_after:.2f} MB")
    
    if size_before > 0:
        reduction = ((size_before - size_after) / size_before) * 100
        print(f"📉 Size reduction: {reduction:.1f}%")
    
    print("\n✅ Migration complete!")
