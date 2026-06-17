from functools import wraps
from flask import request, jsonify
from eth_account import Account
from eth_account.messages import encode_defunct
import os
import time

SIGN_MESSAGE = "Siphon note encryption v1"
MAX_AGE_SECONDS = 300

def _check_wallet_sig():
    """Returns lowercased wallet address if sig valid, else None."""
    wallet    = (request.headers.get('X-Wallet-Address') or '').lower()
    signature = request.headers.get('X-Signature') or ''
    timestamp = request.headers.get('X-Timestamp') or ''

    if not wallet or not signature or not timestamp:
        return None
    try:
        ts = int(timestamp)
    except ValueError:
        return None
    if abs(time.time() - ts) > MAX_AGE_SECONDS:
        return None

    message = f"{SIGN_MESSAGE}:{timestamp}"
    msg = encode_defunct(text=message)
    try:
        recovered = Account.recover_message(msg, signature=signature).lower()
    except Exception:
        return None
    return recovered if recovered == wallet else None

def verify_wallet_sig(f):
    """Requires valid wallet signature. Injects wallet= kwarg."""
    @wraps(f)
    def decorated(*args, **kwargs):
        wallet = _check_wallet_sig()
        if not wallet:
            return jsonify({"error": "Invalid or missing wallet signature"}), 401
        return f(*args, wallet=wallet, **kwargs)
    return decorated

def require_api_token_or_wallet(f):
    """Accepts either API token (executor) or wallet sig (user).
    Injects wallet= kwarg; None when called via API token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Path 1: API token (executor backend)
        token = request.headers.get('X-API-TOKEN') or request.headers.get('Authorization', '').replace('Bearer ', '')
        expected = os.getenv('API_TOKEN', '')
        if expected and token == expected:
            return f(*args, wallet=None, **kwargs)

        # Path 2: Wallet signature (user)
        wallet = _check_wallet_sig()
        if wallet:
            return f(*args, wallet=wallet, **kwargs)

        return jsonify({"error": "Authentication required"}), 401
    return decorated
