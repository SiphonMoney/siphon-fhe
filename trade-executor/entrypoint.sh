#!/bin/bash
set -e

echo "=== Siphon Trade Executor Starting ==="

# Run database initialization if needed
echo "Initializing database..."
python init_db.py || echo "Database already initialized or init_db.py not found"

# Calculate workers based on CPU cores (2 * cores + 1)
WORKERS=${GUNICORN_WORKERS:-$(( 2 * $(nproc) + 1 ))}
echo "Starting gunicorn with $WORKERS workers..."

# Start gunicorn with production settings
exec gunicorn \
    --bind 0.0.0.0:5005 \
    --workers $WORKERS \
    --timeout 120 \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    app:app
