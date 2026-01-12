from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import os
import json
from database import db, Strategy
from scheduler import worker_loop
from config import DATABASE_URI, PYTH_PRICE_FEED_IDS
from auth import require_auth, rate_limit


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
db.init_app(app)

# Health check endpoint (no auth required)
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "service": "trade-executor"}), 200

if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    print("--- Starting the background scheduler thread ---")
    scheduler_thread = threading.Thread(target=worker_loop, args=(app,), daemon=True)
    scheduler_thread.start()

@app.route('/createStrategy', methods=['POST'])
@require_auth
@rate_limit(max_requests=50, window_seconds=60)  # 50 requests per minute
def create_strategy():
    data = request.json
    if not data: 
        return jsonify({"error": "Invalid JSON"}), 400


    try:
        strategy_type = data.get("strategy_type", "")
        token_symbol = data.get('asset_in') if "LONG" in strategy_type or "SELL" in strategy_type else data.get('asset_out')

        # Data is automatically compressed and encrypted by the database model
        new_strategy = Strategy(
            user_id=data['user_id'],
            strategy_type=strategy_type,
            asset_in=data['asset_in'],
            asset_out=data['asset_out'],
            amount=data['amount'],
            recipient_address=data['recipient_address'],
            encrypted_upper_bound=json.dumps(data.get('encrypted_upper_bound')),
            encrypted_lower_bound=json.dumps(data.get('encrypted_lower_bound')),
            server_key=json.dumps(data.get('server_key')),  # Will be compressed + encrypted
            encrypted_client_key=json.dumps(data.get('encrypted_client_key')) if data.get('encrypted_client_key') else None,  # Optional - None if using MPC shares
            mpc_public_key_set=data.get('mpc_public_key_set'),
            mpc_share_indices=json.dumps(data.get('mpc_share_indices')) if data.get('mpc_share_indices') else None,
            fhe_key_id=data.get('fhe_key_id'),  # Key ID when shares stored on MPC
            zkp_data=json.dumps(data.get('zkp_data') or data.get('zk_proof')) if (data.get('zkp_data') or data.get('zk_proof')) else None
        )
        
        db.session.add(new_strategy)
        db.session.commit()
        return jsonify({"status": "success", "strategy_id": new_strategy.id}), 201

    except Exception as e:
        print(f"Error creating strategy: {e}") 
        db.session.rollback()
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500

if __name__ == '__main__':
    # Local dev (prefer gunicorn for production-like behavior)
    app.run(port=5005, debug=True)
