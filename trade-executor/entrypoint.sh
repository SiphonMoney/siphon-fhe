#!/bin/bash
# Docker entrypoint script for DB init + server start

set -e

echo "🚀 Starting Siphon Trade Executor..."

# Initialize database if needed
echo "📦 Initializing database..."
python3 init_db.py || {
    echo "⚠️  Database initialization warning (may already exist)"
}

# Run database migration if needed (only if database exists and has data)
echo "🔄 Checking if migration is needed..."
if [ -f "instance/strategies.db" ]; then
    python3 migrate_database.py || {
        echo "⚠️  Migration warning (may have already run or no data to migrate)"
    }
else
    echo "   No existing database, migration will run on first data insertion"
fi

# Start the application
echo "✅ Starting Gunicorn server..."
exec gunicorn --bind 0.0.0.0:5005 --workers 1 --timeout 3000 'app:app'
