#!/bin/bash
set -e

echo "=== Siphon Trade Executor Starting ==="

# Run database initialization if needed
echo "Initializing database..."
python init_db.py || echo "Database already initialized or init_db.py not found"

# SQLite requires single worker to avoid locking issues
# Also use --preload to ensure scheduler starts only once
WORKERS=${GUNICORN_WORKERS:-1}
echo "Starting gunicorn with $WORKERS worker (SQLite mode)..."

# Start gunicorn with production settings.
# The scheduler is started from gunicorn.conf.py's post_fork hook (exactly once,
# inside the single worker) — NOT at import time. Keep --preload for fast worker
# boot; the master's preload import no longer starts a scheduler.
exec gunicorn \
    --bind 0.0.0.0:5005 \
    --workers $WORKERS \
    --timeout 120 \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    --preload \
    --config gunicorn.conf.py \
    app:app
