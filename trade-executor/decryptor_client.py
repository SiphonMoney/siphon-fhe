"""HTTP client for the confidential-vm siphon-decryptor service.

The decryptor holds each user's tfhe ClientKey and returns only the decrypted trigger bit.
Called from the scheduler (internal VPC); never exposes decrypted strategy inputs.
"""
import os
from typing import Optional, Tuple

import requests

DECRYPTOR_URL = (os.getenv("DECRYPTOR_URL") or "").rstrip("/")
DECRYPTOR_TIMEOUT = int(os.getenv("DECRYPTOR_TIMEOUT", "60"))


def decryptor_enabled() -> bool:
    return bool(DECRYPTOR_URL)


# EVM addresses are case-insensitive; normalise the decryptor key (in-memory HashMap, case
# sensitive) so an uploaded key always matches the decrypt lookup regardless of checksum casing.
def _norm_user(user_id: str) -> str:
    return (user_id or "").strip().lower()


def upload_client_key(user_id: str, client_key_hex: str) -> dict:
    """Forward a (dev: plaintext hex) ClientKey to the decryptor. Used by the browser proxy."""
    if not DECRYPTOR_URL:
        return {"ok": False, "error": "DECRYPTOR_URL not configured"}
    r = requests.post(
        f"{DECRYPTOR_URL}/clientKey",
        json={"user_id": _norm_user(user_id), "client_key": client_key_hex},
        timeout=DECRYPTOR_TIMEOUT,
    )
    try:
        body = r.json()
    except Exception:
        body = {"ok": False, "error": r.text[:500]}
    if not r.ok:
        body.setdefault("error", f"HTTP {r.status_code}")
    return body


def has_client_key(user_id: str) -> bool:
    if not DECRYPTOR_URL:
        return False
    try:
        r = requests.get(
            f"{DECRYPTOR_URL}/hasClientKey/{_norm_user(user_id)}",
            timeout=10,
        )
        if r.ok:
            return bool(r.json().get("has_key"))
    except Exception as e:
        print(f"[Decryptor] hasClientKey failed for {user_id[:10]}…: {e}")
    return False


def decrypt_trigger(user_id: str, encrypted_result_hex: str) -> Tuple[Optional[bool], Optional[str]]:
    """Decrypt the FHE engine's encrypted 0/1 result. Returns (triggered, error)."""
    if not DECRYPTOR_URL:
        return None, "DECRYPTOR_URL not configured"
    try:
        r = requests.post(
            f"{DECRYPTOR_URL}/decrypt",
            json={"user_id": _norm_user(user_id), "encrypted_result": encrypted_result_hex},
            timeout=DECRYPTOR_TIMEOUT,
        )
        body = r.json()
        if not r.ok:
            return None, body.get("error") or f"HTTP {r.status_code}"
        if body.get("error"):
            return None, body["error"]
        triggered = body.get("triggered")
        if triggered is None:
            return None, "missing triggered field"
        return bool(triggered), None
    except Exception as e:
        return None, str(e)
