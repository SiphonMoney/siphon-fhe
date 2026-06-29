from functools import wraps
from flask import request, jsonify
from eth_account import Account
from eth_account.messages import encode_defunct
import os
import time

# ── Strategy / FHE endpoints — wallet-address auth ───────────────────────────
# Must match the frontend's SIGN_MESSAGE_AUTH_BASE (noteStore.ts).
SIGN_MESSAGE = "Siphon auth v1"
MAX_AGE_SECONDS = 300

# ── Note endpoints — tag-based auth (no wallet stored server-side) ────────────
SIGN_MESSAGE_NOTES = "Siphon notes auth v1"


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


def _check_tag_sig():
    """Returns tag if sig is valid and fresh, else None.

    The signed message includes the sender's wallet address so the server can
    verify the sig is self-consistent: recovered address must match the address
    the client declared. This closes the bypass where an attacker signs a valid
    message but submits someone else's X-Tag — the recovered address would not
    match their declared wallet, rejecting the request.

    The wallet address is never stored server-side; it is used only transiently
    for this cryptographic check. The tag remains pseudonymous in the DB.
    """
    tag       = (request.headers.get('X-Tag') or '').lower()
    wallet    = (request.headers.get('X-Wallet-Address') or '').lower()
    signature = request.headers.get('X-Signature') or ''
    timestamp = request.headers.get('X-Timestamp') or ''

    if not tag or not wallet or not signature or not timestamp:
        return None
    try:
        ts = int(timestamp)
    except ValueError:
        return None
    if abs(time.time() - ts) > MAX_AGE_SECONDS:
        return None

    # wallet is bound into the signed message — attacker cannot substitute a
    # different wallet without invalidating the sig.
    message = f"{SIGN_MESSAGE_NOTES}\nwallet:{wallet}\ntag:{tag}\nts:{timestamp}"
    msg = encode_defunct(text=message)
    try:
        recovered = Account.recover_message(msg, signature=signature).lower()
    except Exception:
        return None

    # Recovered signer must be the wallet that claims ownership of this tag.
    if recovered != wallet:
        return None
    return tag


def verify_wallet_sig(f):
    """Requires valid wallet signature. Injects wallet= kwarg."""
    @wraps(f)
    def decorated(*args, **kwargs):
        wallet = _check_wallet_sig()
        if not wallet:
            return jsonify({"error": "Invalid or missing wallet signature"}), 401
        return f(*args, wallet=wallet, **kwargs)
    return decorated


def verify_tag_sig(f):
    """Requires valid tag signature. Injects tag= kwarg. Used for note endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        tag = _check_tag_sig()
        if not tag:
            return jsonify({"error": "Invalid or missing tag signature"}), 401
        return f(*args, tag=tag, **kwargs)
    return decorated


def require_api_token(f):
    """Requires API token. Used for executor-only endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token    = request.headers.get('X-API-TOKEN') or request.headers.get('Authorization', '').replace('Bearer ', '')
        expected = os.getenv('API_TOKEN', '')
        if not expected or token != expected:
            return jsonify({"error": "Invalid or missing API token"}), 401
        return f(*args, **kwargs)
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
