from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import os
import json
from database import db, Strategy, UserFheKey, get_server_key
from scheduler import worker_loop
from config import DATABASE_URI, PYTH_PRICE_FEED_IDS
from auth import rate_limit
from address_validator import validate_recipient


app = Flask(__name__)
CORS(
    app,
    resources={r"/*": {
        "origins": [
            "https://siphon-app.vercel.app",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        "allow_headers": [
            "Content-Type",
            "X-Wallet-Address",
            "X-Signature",
            "X-Timestamp",
            "X-API-TOKEN",
            "Authorization",
        ],
        "methods": ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    }},
    supports_credentials=False,
)

# Ensure instance directory exists before setting database URI
import os
if DATABASE_URI and 'sqlite' in DATABASE_URI and 'instance' in DATABASE_URI:
    instance_dir = os.path.join(os.path.dirname(__file__), 'instance')
    os.makedirs(instance_dir, exist_ok=True)
    # Convert relative path to absolute path for SQLite
    if DATABASE_URI.startswith('sqlite:///instance'):
        db_path = os.path.join(instance_dir, 'strategies.db')
        # Extract timeout if present
        timeout = '?timeout=20000'
        if 'timeout' in DATABASE_URI:
            timeout = '?' + DATABASE_URI.split('?')[1] if '?' in DATABASE_URI else ''
        DATABASE_URI = f'sqlite:///{db_path}{timeout}'

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# SQLite concurrency fix: use NullPool to avoid connection sharing issues
# and set busy_timeout to wait instead of failing on lock
if DATABASE_URI and 'sqlite' in DATABASE_URI:
    from sqlalchemy.pool import NullPool
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'poolclass': NullPool,
        'connect_args': {'timeout': 30, 'check_same_thread': False}
    }

db.init_app(app)

from notes import notes_bp
app.register_blueprint(notes_bp)

# Enable WAL mode for better SQLite concurrency (deferred until first request)
if DATABASE_URI and 'sqlite' in DATABASE_URI:
    from sqlalchemy import text
    with app.app_context():
        # Create tables first if they don't exist
        db.create_all()
        # Apply migrations for new columns if they don't exist
        try:
            with db.engine.connect() as conn:
                for col, typedef in [
                    ("condition_tree", "TEXT"),
                    ("to_chain", "VARCHAR(20)"),
                    ("from_chain", "VARCHAR(20)"),
                    ("encrypted_result", "BLOB"),
                    ("result_updated_at", "DATETIME"),
                ]:
                    try:
                        conn.execute(text(f"ALTER TABLE strategy ADD COLUMN {col} {typedef} DEFAULT ''"))
                        conn.commit()
                    except Exception:
                        pass  # column already exists

                # Migrate notes.spent from boolean (0/1) to string ('false'/'true'/'pending')
                try:
                    conn.execute(text("UPDATE notes SET spent = 'true' WHERE spent = '1' OR spent = 'True'"))
                    conn.execute(text("UPDATE notes SET spent = 'false' WHERE spent = '0' OR spent = 'False' OR spent = ''"))
                    conn.commit()
                except Exception:
                    pass
        except Exception as e:
            print(f"Warning: Could not apply SQLite migrations: {e}")
            
        # DELETE journal mode — WAL's -wal/-shm sidecar files are unreliable on
        # this Docker volume and cause "disk I/O error" / "database disk image is
        # malformed" corruption under concurrent access. Do NOT switch to WAL here.
        #
        # The real fix for the "database is locked" 500s is a reliable PER-CONNECTION
        # busy_timeout: NullPool opens a fresh connection on every checkout, so a
        # one-time startup PRAGMA doesn't stick. We set it via a connect-event
        # listener so every connection waits (up to 30s) for a lock instead of
        # failing immediately. DELETE mode + per-connection busy_timeout handles the
        # scheduler-thread vs web-worker contention without WAL's corruption risk.
        from sqlalchemy import event

        @event.listens_for(db.engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=DELETE")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        try:
            with db.engine.connect() as conn:
                conn.execute(text("PRAGMA journal_mode=DELETE"))
                conn.execute(text("PRAGMA busy_timeout=30000"))
                conn.execute(text("PRAGMA synchronous=NORMAL"))
                conn.commit()
        except Exception as e:
            print(f"Warning: Could not set SQLite PRAGMA: {e}")

# Health check endpoint (no auth required)
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "service": "trade-executor"}), 200

_scheduler_started = threading.Lock()
_scheduler_running = {"started": False}


def start_scheduler():
    """Start the background scheduler thread EXACTLY ONCE per process.

    The scheduler must run in exactly one place. Two concurrent scheduler loops
    will both pick up the same ARMED+triggered strategy, both call run_execution(),
    and both attempt the same ZK withdraw with the same nullifier -> one succeeds
    on-chain, the other reverts NullifierAlreadySpent().

    Why this used to start twice:
      1. entrypoint.sh runs `python init_db.py`, which does `from app import app`,
         executing this module top-to-bottom and starting a scheduler thread in the
         (short-lived) init process.
      2. gunicorn `--preload app:app` imports this module again in the MASTER process,
         starting another scheduler thread in the master.
    Starting the scheduler at import time means every importer (init_db, the gunicorn
    master, any worker) gets its own loop.

    Fix: never start at import time. We start the scheduler from gunicorn's
    `post_fork` hook (see gunicorn.conf.py) so it runs once, inside the single sync
    worker that actually owns the shared DB session. For the dev `python app.py`
    path we start it from the __main__ block. A per-process guard + lock makes a
    double-call a no-op.
    """
    with _scheduler_started:
        if _scheduler_running["started"]:
            print("--- Scheduler already running in this process; skipping ---")
            return
        _scheduler_running["started"] = True
        print("--- Starting the background scheduler thread ---")
        scheduler_thread = threading.Thread(target=worker_loop, args=(app,), daemon=True)
        scheduler_thread.start()

@app.route('/createStrategy', methods=['POST'])
@rate_limit(max_requests=50, window_seconds=60)
def create_strategy():
    data = request.json
    if not data: 
        return jsonify({"error": "Invalid JSON"}), 400

    try:
        strategy_type = data.get("strategy_type", "")
        token_symbol = data.get('asset_in') if "LONG" in strategy_type or "SELL" in strategy_type else data.get('asset_out')

        # Validate recipient address matches destination chain
        to_chain = data.get('to_chain', '11155111')
        recipient = data.get('recipient_address', '')
        valid, err = validate_recipient(recipient, str(to_chain))
        if not valid:
            return jsonify({"error": err}), 400

        # Client-side FHE: bounds are encrypted in the browser; the user's server key must have
        # been uploaded once via /uploadServerKey. The client key never reaches us.
        if not get_server_key(data['user_id']):
            return jsonify({"error": "No FHE server key on file for this user. Upload it via /uploadServerKey first."}), 400

        # Support condition_tree (new) OR legacy upper/lower bound (old)
        condition_tree = data.get('condition_tree')

        new_strategy = Strategy(
            user_id=data['user_id'],
            strategy_type=strategy_type,
            asset_in=data['asset_in'],
            asset_out=data['asset_out'],
            amount=data['amount'],
            recipient_address=recipient,
            encrypted_upper_bound=data.get('encrypted_upper_bound'),
            encrypted_lower_bound=data.get('encrypted_lower_bound'),
            zkp_data=json.dumps(data.get('zkp_data') or data.get('zk_proof')) if (data.get('zkp_data') or data.get('zk_proof')) else None,
            condition_tree=json.dumps(condition_tree) if condition_tree else None,
            to_chain=str(to_chain),
            from_chain=str(data.get('from_chain', '11155111')),
        )
        
        db.session.add(new_strategy)
        db.session.commit()
        return jsonify({"status": "success", "strategy_id": new_strategy.id}), 201

    except Exception as e:
        print(f"Error creating strategy: {e}") 
        db.session.rollback()
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500

@app.route('/strategies/<user_id>', methods=['GET'])
def get_user_strategies(user_id):
    """Get all strategies for a user with their execution status and tx_hash."""
    try:
        strategies = Strategy.query.filter_by(user_id=user_id).order_by(Strategy.created_at.desc()).all()
        
        return jsonify({
            "status": "success",
            "strategies": [{
                "id": s.id,
                "strategy_type": s.strategy_type,
                "asset_in": s.asset_in,
                "asset_out": s.asset_out,
                "amount": s.amount,
                "status": s.status,
                "tx_hash": s.tx_hash,
                "executed_at": s.executed_at.isoformat() if s.executed_at else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "has_encrypted_result": bool(s.encrypted_result),
                "result_updated_at": s.result_updated_at.isoformat() if s.result_updated_at else None,
            } for s in strategies]
        }), 200

    except Exception as e:
        print(f"Error fetching strategies: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/uploadServerKey', methods=['POST'])
@rate_limit(max_requests=20, window_seconds=60)
def upload_server_key():
    """Store a user's FHE server (public) key once. Idempotent upsert.

    The server key is large (~100 MB hex) but identical across all of a user's strategies, so
    it is uploaded a single time and reused. The client (secret) key is never uploaded."""
    data = request.json
    if not data or not data.get('user_id') or not data.get('server_key'):
        return jsonify({"error": "user_id and server_key are required"}), 400
    try:
        existing = UserFheKey.query.get(data['user_id'])
        if existing:
            existing.server_key = data['server_key']
        else:
            db.session.add(UserFheKey(user_id=data['user_id'], server_key=data['server_key']))
        db.session.commit()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error storing server key: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/hasServerKey/<user_id>', methods=['GET'])
def has_server_key(user_id):
    """Lets the browser know whether it still needs to upload its server key."""
    return jsonify({"has_key": get_server_key(user_id) is not None}), 200


@app.route('/uploadClientKey', methods=['POST'])
@rate_limit(max_requests=20, window_seconds=60)
def upload_client_key():
    """Forward the user's FHE client key to the confidential-vm decryptor.

    The browser never talks to the decryptor directly (private VPC). The key is forwarded
  once and held only in the decryptor's memory; decryption of strategy inputs never happens."""
    from decryptor_client import upload_client_key as forward_client_key, decryptor_enabled

    if not decryptor_enabled():
        return jsonify({"error": "Confidential decryptor not configured (DECRYPTOR_URL)"}), 503
    data = request.json
    if not data or not data.get('user_id') or not data.get('client_key'):
        return jsonify({"error": "user_id and client_key are required"}), 400
    result = forward_client_key(data['user_id'], data['client_key'])
    if not result.get('ok'):
        return jsonify({"error": result.get('error', 'upload failed')}), 502
    return jsonify({"status": "success"}), 200


@app.route('/hasClientKey/<user_id>', methods=['GET'])
def has_client_key(user_id):
    """Whether the confidential decryptor already holds this user's client key."""
    from decryptor_client import has_client_key as decryptor_has_key, decryptor_enabled

    if not decryptor_enabled():
        return jsonify({"has_key": False, "decryptor": False}), 200
    return jsonify({"has_key": decryptor_has_key(user_id), "decryptor": True}), 200


@app.route('/clientMetrics', methods=['POST'])
@rate_limit(max_requests=60, window_seconds=60)
def client_metrics():
    """Receive in-browser client-side-FHE timings and log them here, so the encryption
    performance is observable in the trade-executor terminal (it happens in the browser,
    not on the server). Best-effort; never affects the strategy flow."""
    d = request.json or {}
    try:
        print(
            f"[ClientMetrics] strategy={d.get('strategy_id', '?')} user={str(d.get('user_id', '?'))[:10]}… | "
            f"keygen={float(d.get('keygenMs', 0)):.0f}ms  "
            f"serverKey(derive+upload)={float(d.get('serverKeyMs', 0)):.0f}ms  "
            f"encrypt={float(d.get('encryptMs', 0)):.0f}ms  "
            f"submit={float(d.get('submitMs', 0)):.0f}ms  "
            f"total={float(d.get('totalMs', 0)):.0f}ms"
        )
    except Exception as e:
        print(f"[ClientMetrics] received (unparseable): {d} ({e})")
    return jsonify({"ok": True}), 200


@app.route('/strategy/<strategy_id>/result', methods=['GET'])
def get_strategy_result(strategy_id):
    """Return the latest encrypted evaluation result for a strategy. The browser decrypts it
    locally to decide whether to authorize execution."""
    s = Strategy.query.get(strategy_id)
    if not s:
        return jsonify({"error": "strategy not found"}), 404
    return jsonify({
        "status": s.status,
        "encrypted_result": s.encrypted_result,
        "result_updated_at": s.result_updated_at.isoformat() if s.result_updated_at else None,
    }), 200


@app.route('/executeStrategy', methods=['POST'])
@rate_limit(max_requests=30, window_seconds=60)
def execute_strategy_endpoint():
    """Browser-authorized execution. After the browser decrypts the result and sees it is
    triggered, it calls this to run the on-chain trade. We fetch a fresh price and execute."""
    from oracle import get_live_prices
    from executor_runner import run_execution

    data = request.json or {}
    strategy_id = data.get('strategy_id')
    user_id = data.get('user_id')
    if not strategy_id or not user_id:
        return jsonify({"error": "strategy_id and user_id are required"}), 400

    s = Strategy.query.get(strategy_id)
    if not s:
        return jsonify({"error": "strategy not found"}), 404
    if s.user_id != user_id:
        return jsonify({"error": "not authorized for this strategy"}), 403
    if s.status in ('EXECUTED',):
        return jsonify({"status": "already_executed", "tx_hash": s.tx_hash}), 200
    if s.status not in ('PENDING', 'ARMED'):
        return jsonify({"error": f"strategy not executable (status={s.status})"}), 409

    strategy_dict = s.to_dict()

    # Fresh price using the same selection logic as the scheduler.
    feed_ids = [fid for fid in PYTH_PRICE_FEED_IDS.values() if fid]
    live_prices = get_live_prices(feed_ids)
    eth_feed_id = PYTH_PRICE_FEED_IDS.get("ETH")
    sol_feed_id = PYTH_PRICE_FEED_IDS.get("SOL")
    asset_in = strategy_dict.get('asset_in', 'ETH').upper()
    feed = strategy_dict.get('price_feed_id') or PYTH_PRICE_FEED_IDS.get(asset_in)
    if feed and feed in live_prices:
        current_price = live_prices[feed]
    elif eth_feed_id in live_prices:
        current_price = live_prices.get(eth_feed_id, 0)
    else:
        current_price = live_prices.get(sol_feed_id, 0)

    try:
        result = run_execution(strategy_dict, current_price)
        return jsonify({"status": "success", **result}), 200
    except Exception as e:
        print(f"Error executing strategy {strategy_id}: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    # Dev path: gunicorn isn't involved, so start the scheduler here (once).
    start_scheduler()
    app.run(port=5005, debug=False)
