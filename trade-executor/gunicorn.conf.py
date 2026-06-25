"""Gunicorn config for the trade-executor.

The background scheduler must run in EXACTLY ONE process. We deliberately do NOT
start it at module import time (that fired once in `init_db.py`'s import of `app`
and again in the gunicorn master under `--preload`, yielding two concurrent
scheduler loops that race on the same nullifier -> NullifierAlreadySpent()).

Instead we start it from `post_fork`, which runs once inside each worker after the
fork. With WORKERS=1 (required for SQLite) this means exactly one scheduler, living
in the single worker that owns the shared DB session. The gunicorn master never runs
app logic, so no orphaned scheduler there.
"""


def post_fork(server, worker):
    # Imported lazily so the master's preload import does not trigger any start.
    from app import start_scheduler
    server.log.info("[gunicorn post_fork] worker pid=%s: starting scheduler", worker.pid)
    start_scheduler()
