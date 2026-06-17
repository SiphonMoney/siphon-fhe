from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import os
import json
from database import db, Strategy
from scheduler import worker_loop
from config import DATABASE_URI, PYTH_PRICE_FEED_IDS
from auth import rate_limit
from address_validator import validate_recipient


app = Flask(__name__)
CORS(app)

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
            
        # Then set PRAGMA settings
        try:
            with db.engine.connect() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.execute(text("PRAGMA busy_timeout=30000"))
                conn.commit()
        except Exception as e:
            print(f"Warning: Could not set SQLite PRAGMA: {e}")

# Health check endpoint (no auth required)
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "service": "trade-executor"}), 200

if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
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
            server_key=data.get('server_key'),
            encrypted_client_key=data.get('encrypted_client_key'),
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
            } for s in strategies]
        }), 200
        
    except Exception as e:
        print(f"Error fetching strategies: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(port=5005, debug=False)
