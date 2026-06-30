from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import os
import json
from database import db, Strategy, StrategyLeg, UserFheKey, get_server_key
from note_db import init_note_db
from scheduler import worker_loop
from config import DATABASE_URI, PYTH_PRICE_FEED_IDS
from auth import rate_limit, require_admin
from address_validator import validate_recipient
from evm_chain_config import SUPPORTED_EXECUTOR_CHAIN_IDS
from admin_wallets import (
    chain_is_gated, is_wallet_allowed, add_wallet, remove_wallet, list_wallets,
)


app = Flask(__name__)
# Allowed browser origins. Extra origins can be added without a code change via the
# CORS_ALLOWED_ORIGINS env var (comma-separated).
_DEFAULT_CORS_ORIGINS = [
    "https://siphon.money",
    "https://www.siphon.money",
    "https://siphon-app.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
_CORS_ORIGINS = _DEFAULT_CORS_ORIGINS + [
    o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
CORS(
    app,
    resources={r"/*": {
        "origins": _CORS_ORIGINS,
        "allow_headers": [
            "Content-Type",
            "X-Wallet-Address",
            "X-Tag",
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

# ── Supabase (note DB) — standalone SQLAlchemy engine, separate from main SQLite ──
# init_note_db() creates tables on Supabase if they don't exist. Safe every startup.
init_note_db()

from precommitments import precommitments_bp
from commitments import commitments_bp
from notes import notes_bp
app.register_blueprint(precommitments_bp)
app.register_blueprint(commitments_bp)
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

                # Multi-leg (TWAP / RANGE_GRID) columns — proper NULL/0 defaults (not '').
                for col, typedef in [
                    ("leg_count", "INTEGER"),
                    ("interval_sec", "INTEGER"),
                    ("grid_levels", "INTEGER"),
                    ("max_slippage_bps", "INTEGER"),
                    ("start_delay_sec", "INTEGER"),
                    ("executed_count", "INTEGER DEFAULT 0"),
                    ("eval_mode", "VARCHAR(10)"),
                    ("schedule_anchor", "INTEGER"),
                ]:
                    try:
                        conn.execute(text(f"ALTER TABLE strategy ADD COLUMN {col} {typedef}"))
                        conn.commit()
                    except Exception:
                        pass  # column already exists

                # Per-leg vault-output precommitment (output_mode='vault').
                try:
                    conn.execute(text("ALTER TABLE strategy_legs ADD COLUMN output_precommitment VARCHAR"))
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
        to_chain = data.get('to_chain', '8453')
        from_chain = str(data.get('from_chain', '8453'))
        recipient = data.get('recipient_address', '')
        valid, err = validate_recipient(recipient, str(to_chain))
        if not valid:
            return jsonify({"error": err}), 400

        try:
            if int(from_chain) not in SUPPORTED_EXECUTOR_CHAIN_IDS:
                return jsonify({
                    "error": f"from_chain {from_chain} is not supported by the executor. "
                             f"Supported: {sorted(SUPPORTED_EXECUTOR_CHAIN_IDS)}"
                }), 400
        except (TypeError, ValueError):
            return jsonify({"error": f"Invalid from_chain: {from_chain}"}), 400

        # Admin allow-list gate: on gated chains (e.g. ETH Sepolia) only allow-listed wallets
        # may create strategies. The wallet is the strategy owner (user_id), falling back to the
        # recipient address. Applies if either the source or destination chain is gated.
        gate_wallet = (data.get('user_id') or recipient or '')
        for gated_cid in {c for c in (from_chain, to_chain) if chain_is_gated(c)}:
            if not is_wallet_allowed(gate_wallet, gated_cid):
                return jsonify({
                    "error": f"Wallet {gate_wallet or '(none)'} is not allow-listed for chain "
                             f"{int(gated_cid)}. This chain is restricted to admin wallets."
                }), 403

        # Client-side FHE: bounds are encrypted in the browser; the user's server key must have
        # been uploaded once via /uploadServerKey. The client key never reaches us.
        if not get_server_key(data['user_id']):
            return jsonify({"error": "No FHE server key on file for this user. Upload it via /uploadServerKey first."}), 400

        # Support condition_tree (new) OR legacy upper/lower bound (old)
        condition_tree = data.get('condition_tree')

        # Multi-leg strategies (TWAP / RANGE_GRID) carry a `legs` array: each leg is an
        # independent shielded sub-trade with its own slice-note ZK proof + encrypted trigger.
        legs = data.get('legs') or []
        is_multi_leg = strategy_type in ('TWAP', 'RANGE_GRID') and len(legs) > 0
        # 'time' for TWAP (encrypted fire-time triggers), 'price' for grid (encrypted price band).
        eval_mode = 'time' if strategy_type == 'TWAP' else ('price' if strategy_type == 'RANGE_GRID' else None)

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
            from_chain=from_chain,
            output_mode=data.get('output_mode', 'address'),
            output_precommitment=data.get('output_precommitment'),
            # multi-leg schedule/ladder metadata
            leg_count=(len(legs) if is_multi_leg else None),
            interval_sec=data.get('interval_sec'),
            grid_levels=data.get('grid_levels'),
            max_slippage_bps=data.get('max_slippage_bps'),
            start_delay_sec=data.get('start_delay_sec'),
            executed_count=0,
            eval_mode=eval_mode,
            schedule_anchor=data.get('schedule_anchor'),
        )

        db.session.add(new_strategy)
        db.session.flush()  # assign new_strategy.id before inserting child legs

        # Persist each leg as a StrategyLeg row. Each leg carries its own per-leg withdrawal
        # proof (zk_proof) and encrypted trigger bound. Missing/partial legs are rejected so a
        # multi-leg order can never silently degrade to fewer legs.
        if is_multi_leg:
            for i, leg in enumerate(legs):
                leg_proof = leg.get('zk_proof') or leg.get('zkp_data')
                nh = None
                if isinstance(leg_proof, dict):
                    nhs = leg_proof.get('nullifierHashes') or leg_proof.get('nullifier_hashes')
                    if isinstance(nhs, list) and nhs:
                        nh = str(nhs[0])
                    elif leg_proof.get('nullifierHash') is not None:
                        nh = str(leg_proof.get('nullifierHash'))
                db.session.add(StrategyLeg(
                    strategy_id=new_strategy.id,
                    leg_index=leg.get('leg_index', i),
                    amount=leg['amount'],
                    side=leg.get('side'),
                    eval_mode=leg.get('eval_mode', eval_mode or 'price'),
                    target_price=leg.get('target_price'),
                    encrypted_upper_bound=leg.get('encrypted_upper_bound'),
                    encrypted_lower_bound=leg.get('encrypted_lower_bound'),
                    zkp_data=json.dumps(leg_proof) if leg_proof else None,
                    nullifier_hash=nh,
                    output_precommitment=leg.get('output_precommitment'),
                    status='PENDING',
                ))

        db.session.commit()
        return jsonify({
            "status": "success",
            "strategy_id": new_strategy.id,
            "leg_count": new_strategy.leg_count,
        }), 201

    except Exception as e:
        print(f"Error creating strategy: {e}")
        db.session.rollback()
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500


# ----------------------- Admin wallet allow-list (gated chains) -----------------------
# All endpoints require the X-ADMIN-TOKEN header (ADMIN_API_TOKEN). Used to restrict gated
# chains (e.g. ETH Sepolia) to a managed set of wallets that may create/run strategies.

@app.route('/admin/wallets', methods=['GET'])
@require_admin
def admin_list_wallets():
    chain_id = request.args.get('chain_id')
    try:
        return jsonify({"wallets": list_wallets(chain_id)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/admin/wallets', methods=['POST'])
@require_admin
def admin_add_wallet():
    data = request.json or {}
    address = data.get('address')
    chain_id = data.get('chain_id', 11155111)
    label = data.get('label')
    if not address:
        return jsonify({"error": "address is required"}), 400
    try:
        row = add_wallet(address, chain_id, label)
        return jsonify({"status": "success", "wallet": row}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/admin/wallets', methods=['DELETE'])
@require_admin
def admin_remove_wallet():
    data = request.json or {}
    address = data.get('address')
    chain_id = data.get('chain_id', 11155111)
    if not address:
        return jsonify({"error": "address is required"}), 400
    try:
        removed = remove_wallet(address, chain_id)
        return jsonify({"status": "success", "removed": removed}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/admin/fee-pool', methods=['POST'])
@require_admin
def admin_fee_pool():
    """Upload protocol fee-note precommitments (frontend-generated) for the fee-vault sweep.
    Body: { chain_id, asset, precommitments: [str,...] }. Idempotent on precommitment uniqueness."""
    from database import ProtocolFeeNote
    data = request.json or {}
    chain_id = data.get('chain_id', 11155111)
    asset = (data.get('asset') or 'ETH').upper()
    precs = data.get('precommitments') or []
    if not isinstance(precs, list) or not precs:
        return jsonify({"error": "precommitments[] required"}), 400
    added = 0
    for p in precs:
        try:
            if ProtocolFeeNote.query.filter_by(precommitment=str(p)).first():
                continue
            db.session.add(ProtocolFeeNote(chain_id=int(chain_id), asset=asset, precommitment=str(p)))
            added += 1
        except Exception:
            continue
    db.session.commit()
    avail = ProtocolFeeNote.query.filter_by(chain_id=int(chain_id), asset=asset, status='available').count()
    return jsonify({"status": "success", "added": added, "available": avail}), 200


@app.route('/admin/sweep-fees', methods=['POST'])
@require_admin
def admin_sweep_fees():
    """Manually trigger a fee-vault sweep for (chain_id, asset). Body: { chain_id, asset, price }."""
    from fee_sweep import sweep_fees
    data = request.json or {}
    chain_id = data.get('chain_id', 11155111)
    asset = (data.get('asset') or 'ETH').upper()
    price = data.get('price')
    try:
        result = sweep_fees(int(chain_id), asset, float(price) if price else None)
        return jsonify(result), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


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


@app.route('/debugEval', methods=['POST'])
@rate_limit(max_requests=30, window_seconds=60)
def debug_eval():
    """DEV self-test: given a FRESH wasm (server_key, client_key) and a TWAP fire-time ciphertext,
    evaluate via the engine and decrypt via the decryptor under a throwaway user id. Isolates
    whether wasm-generated keys are compatible with the native engine."""
    import requests as _rq
    from decryptor_client import upload_client_key, DECRYPTOR_URL
    import fhe_client
    data = request.json or {}
    for f in ('server_key', 'client_key', 'encrypted_lower_bound'):
        if not data.get(f):
            return jsonify({"error": f"{f} required"}), 400
    uid = (data.get('user_id') or '__selftest').lower()
    ctime = int(data.get('current_time', 100))
    up = upload_client_key(uid, data['client_key'])
    payload = {
        "strategy_type": "TWAP_SLICE",
        "encrypted_upper_bound": "",
        "encrypted_lower_bound": data['encrypted_lower_bound'],
        "server_key": data['server_key'],
        "current_price_cents": 0,
        "current_time": ctime,
    }
    er = _rq.post(fhe_client.FHE_ENGINE_URL, json=payload, timeout=180)
    if not er.ok:
        return jsonify({"stage": "engine", "status": er.status_code, "body": er.text[:300]}), 200
    enc = (er.json() or {}).get("encrypted_result")
    if not enc:
        return jsonify({"stage": "engine", "error": er.json()}), 200
    dr = _rq.post(f"{DECRYPTOR_URL}/decrypt",
                  json={"user_id": uid, "encrypted_result": enc}, timeout=120)
    return jsonify({"client_key_upload": up, "decrypt": dr.json(),
                    "current_time": ctime, "expect": "triggered=true (0<=%d)" % ctime}), 200


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
