import time
from flask import Flask, request, jsonify
from flask_cors import CORS
from wallet_auth import verify_wallet_sig
from prover import generate_proof
from config import PORT, VALID_CIRCUITS

app = Flask(__name__)
CORS(app)


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy', 'service': 'proving-relayer'}), 200


@app.route('/prove', methods=['POST'])
@verify_wallet_sig
def prove(wallet):
    data    = request.json or {}
    inputs  = data.get('inputs')
    circuit = data.get('circuit', '').lower()

    if not inputs or not isinstance(inputs, dict):
        return jsonify({'error': 'Missing or invalid inputs'}), 400
    if circuit not in VALID_CIRCUITS:
        return jsonify({'error': f"Invalid circuit '{circuit}'. Valid: {sorted(VALID_CIRCUITS)}"}), 400

    t0 = time.monotonic()
    try:
        result = generate_proof(inputs, circuit)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500

    elapsed_ms = (time.monotonic() - t0) * 1000
    print(f"[Benchmark] circuit={circuit} proof={elapsed_ms:.1f}ms wallet={wallet[:10]}...")
    return jsonify({**result, 'elapsed_ms': elapsed_ms}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False)
