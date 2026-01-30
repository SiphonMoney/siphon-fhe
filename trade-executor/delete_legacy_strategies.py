#!/usr/bin/env python3
"""
Delete legacy strategies that don't have MPC key_id
These were created before MPC servers were fixed
"""
import sqlite3
import os

# Find the database file
db_path = 'instance/strategies.db'
if not os.path.exists(db_path):
    print('❌ Database not found at:', db_path)
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get all strategies without fhe_key_id (legacy strategies)
cursor.execute('''
    SELECT id, user_id, status, fhe_key_id, created_at 
    FROM strategy 
    WHERE fhe_key_id IS NULL OR fhe_key_id = ''
''')
legacy_strategies = cursor.fetchall()

print(f'📊 Found {len(legacy_strategies)} legacy strategies (no fhe_key_id)')
print()

if len(legacy_strategies) == 0:
    print('✅ No legacy strategies to delete')
    conn.close()
    exit(0)

# Show what will be deleted
print('🗑️  Strategies to be deleted:')
for s in legacy_strategies:
    strategy_id, user_id, status, fhe_key_id, created = s
    print(f'  - {strategy_id[:16]}... (User: {user_id}, Status: {status}, Created: {created})')
print()

# Confirm deletion
response = input('⚠️  Delete these legacy strategies? (yes/no): ')
if response.lower() != 'yes':
    print('❌ Deletion cancelled')
    conn.close()
    exit(0)

# Delete legacy strategies
cursor.execute('''
    DELETE FROM strategy 
    WHERE fhe_key_id IS NULL OR fhe_key_id = ''
''')
deleted_count = cursor.rowcount
conn.commit()

print(f'✅ Deleted {deleted_count} legacy strategies')
print()

# Show remaining strategies
cursor.execute('SELECT COUNT(*) FROM strategy')
remaining = cursor.fetchone()[0]
print(f'📊 Remaining strategies: {remaining}')

conn.close()
