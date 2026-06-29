#!/usr/bin/env python3
"""
End-to-end test for all DB endpoints in the trade-executor.

Coverage:
  Precommitments:
    POST   /precommitments          — create
    GET    /precommitments          — list (optionally filtered by status)
    GET    /precommitments/pool-count
    PATCH  /precommitments/<id>/claim
    PATCH  /precommitments/<id>/release
    PATCH  /precommitments/<id>/resolve

  Commitments:
    POST   /commitments             — create
    GET    /commitments             — list
    PATCH  /commitments/<id>/spent  — mark spent
    GET    /commitments/export      — export all
    PATCH  /commitments/<id>/executor-update  — executor replaces blob

  Nullifier registry (API token auth):
    POST   /nullifier-registry            — claim
    PATCH  /nullifier-registry/<h>/spent  — mark spent (also flips commitment)
    PATCH  /nullifier-registry/<h>/release— release pending claim

Run: python test_db.py
"""

import os, sys, time, subprocess, json, secrets, requests
from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_defunct

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Override NOTE_DB_URI with local SQLite so tests run without Supabase connectivity.
# The note DB models are SQLAlchemy and fully compatible with SQLite.
_TEST_DB = os.path.join(os.path.dirname(__file__), 'test_notes.db')
os.environ['NOTE_DB_URI'] = f'sqlite:///{_TEST_DB}'

BASE = "http://localhost:5005"
API_TOKEN = os.environ.get("API_TOKEN", "testtoken123")

# ── Signing helpers ────────────────────────────────────────────────────────────

SIGN_MESSAGE_NOTES = "Siphon notes auth v1"

def _tag_headers(wallet: Account, tag: str) -> dict:
    ts   = str(int(time.time()))
    addr = wallet.address.lower()
    msg  = f"{SIGN_MESSAGE_NOTES}\nwallet:{addr}\ntag:{tag}\nts:{ts}"
    sig  = wallet.sign_message(encode_defunct(text=msg)).signature.hex()
    if not sig.startswith("0x"):
        sig = "0x" + sig
    return {
        "X-Tag":            tag,
        "X-Wallet-Address": addr,
        "X-Signature":      sig,
        "X-Timestamp":      ts,
        "Content-Type":     "application/json",
    }

def _api_headers() -> dict:
    return {"X-API-TOKEN": API_TOKEN, "Content-Type": "application/json"}

# ── Helpers ────────────────────────────────────────────────────────────────────

def ok(label: str, r: requests.Response, expected: int = 200):
    if r.status_code != expected:
        print(f"  FAIL  {label}  HTTP {r.status_code}: {r.text}")
        sys.exit(1)
    print(f"  ✅  {label}")
    return r.json()

# ── Server lifecycle ────────────────────────────────────────────────────────────

def start_server():
    env = {**os.environ}
    proc = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=os.path.dirname(__file__),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    # Wait until /health responds
    for _ in range(30):
        time.sleep(1)
        try:
            r = requests.get(f"{BASE}/health", timeout=2)
            if r.status_code == 200:
                print("  Server started ✅\n")
                return proc
        except Exception:
            pass
    proc.kill()
    out, _ = proc.communicate()
    print("Server failed to start:\n", out.decode())
    sys.exit(1)


def stop_server(proc: subprocess.Popen):
    proc.terminate()
    proc.wait(timeout=5)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n══════════════════════════════════════════════════════════")
    print("  Siphon DB — full endpoint test suite")
    print("══════════════════════════════════════════════════════════\n")

    print("[0] Starting trade-executor (port 5005)...")
    proc = start_server()

    wallet = Account.create()
    tag    = "0x" + secrets.token_hex(20)   # pseudonymous tag
    h      = _tag_headers

    try:
        # ── Precommitments ─────────────────────────────────────────────────────
        print("── Precommitments ─────────────────────────────────────────────")

        r = requests.post(f"{BASE}/precommitments",
            headers=h(wallet, tag),
            json={"enc_blob": "aabbcc", "iv": "ddeeff"})
        body = ok("POST /precommitments (create)", r, 201)
        pc_id = body["id"]
        print(f"     id: {pc_id}")

        r = requests.get(f"{BASE}/precommitments", headers=h(wallet, tag))
        body = ok("GET  /precommitments (list all)", r, 200)
        assert any(p["id"] == pc_id for p in body["precommitments"]), "created id not in list"
        print(f"     count: {len(body['precommitments'])}")

        r = requests.get(f"{BASE}/precommitments?status=pending", headers=h(wallet, tag))
        body = ok("GET  /precommitments?status=pending", r, 200)
        assert any(p["id"] == pc_id for p in body["precommitments"]), "not in pending list"

        r = requests.get(f"{BASE}/precommitments/pool-count", headers=h(wallet, tag))
        body = ok("GET  /precommitments/pool-count", r, 200)
        print(f"     pool count: {body['count']}")

        r = requests.patch(f"{BASE}/precommitments/{pc_id}/claim", headers=h(wallet, tag))
        ok("PATCH /precommitments/<id>/claim", r, 200)

        # Double-claim should 409
        r2 = requests.patch(f"{BASE}/precommitments/{pc_id}/claim", headers=h(wallet, tag))
        if r2.status_code != 409:
            print(f"  FAIL  double-claim should be 409, got {r2.status_code}")
            sys.exit(1)
        print("  ✅  double-claim correctly rejected (409)")

        r = requests.patch(f"{BASE}/precommitments/{pc_id}/release", headers=h(wallet, tag))
        ok("PATCH /precommitments/<id>/release (back to pending)", r, 200)

        # Claim again, then resolve
        requests.patch(f"{BASE}/precommitments/{pc_id}/claim", headers=h(wallet, tag))
        r = requests.patch(f"{BASE}/precommitments/{pc_id}/resolve", headers=h(wallet, tag))
        ok("PATCH /precommitments/<id>/resolve", r, 200)

        # Resolved — further claim/resolve should 409
        r = requests.patch(f"{BASE}/precommitments/{pc_id}/claim", headers=h(wallet, tag))
        if r.status_code != 409:
            print(f"  FAIL  claim on resolved should be 409, got {r.status_code}")
            sys.exit(1)
        print("  ✅  claim on resolved correctly rejected (409)")

        print()

        # ── Commitments ────────────────────────────────────────────────────────
        print("── Commitments ────────────────────────────────────────────────")

        r = requests.post(f"{BASE}/commitments",
            headers=h(wallet, tag),
            json={"enc_blob": "deadbeef", "iv": "cafebabe", "asset": "ETH", "source": "deposit"})
        body = ok("POST /commitments (create)", r, 201)
        comm_id = body["id"]
        print(f"     id: {comm_id}")

        r = requests.get(f"{BASE}/commitments", headers=h(wallet, tag))
        body = ok("GET  /commitments (list)", r, 200)
        assert any(c["id"] == comm_id for c in body["commitments"]), "commitment not in list"
        assert body["commitments"][0]["spent"] == "false"
        print(f"     count: {len(body['commitments'])}, spent=false ✅")

        r = requests.patch(f"{BASE}/commitments/{comm_id}/spent",
            headers=h(wallet, tag),
            json={"status": "pending"})
        ok("PATCH /commitments/<id>/spent → pending", r, 200)

        r = requests.get(f"{BASE}/commitments", headers=h(wallet, tag))
        body = ok("GET  /commitments (verify pending)", r, 200)
        match = next(c for c in body["commitments"] if c["id"] == comm_id)
        assert match["spent"] == "pending", f"expected pending, got {match['spent']}"
        print("  ✅  spent=pending verified")

        r = requests.get(f"{BASE}/commitments/export", headers=h(wallet, tag))
        body = ok("GET  /commitments/export", r, 200)
        assert any(c["id"] == comm_id for c in body["commitments"])
        print(f"     export count: {len(body['commitments'])}")

        r = requests.patch(f"{BASE}/commitments/{comm_id}/executor-update",
            headers=_api_headers(),
            json={"enc_blob": "newblob99", "iv": "newiv00", "spent": "false"})
        ok("PATCH /commitments/<id>/executor-update (change note)", r, 200)

        r = requests.get(f"{BASE}/commitments", headers=h(wallet, tag))
        body = ok("GET  /commitments (verify executor-update)", r, 200)
        match = next(c for c in body["commitments"] if c["id"] == comm_id)
        assert match["spent"] == "false", f"expected false after update, got {match['spent']}"
        print("  ✅  executor-update blob replaced, spent reset to false")

        print()

        # ── Nullifier registry ─────────────────────────────────────────────────
        print("── Nullifier registry ─────────────────────────────────────────")

        nullifier_hash = "0x" + secrets.token_hex(32)

        r = requests.post(f"{BASE}/nullifier-registry",
            headers=_api_headers(),
            json={"nullifier_hash": nullifier_hash, "commitment_id": comm_id})
        ok("POST /nullifier-registry (claim)", r, 201)

        # Double-claim should 409
        r2 = requests.post(f"{BASE}/nullifier-registry",
            headers=_api_headers(),
            json={"nullifier_hash": nullifier_hash, "commitment_id": comm_id})
        if r2.status_code != 409:
            print(f"  FAIL  double nullifier claim should be 409, got {r2.status_code}")
            sys.exit(1)
        print("  ✅  double nullifier claim correctly rejected (409)")

        # Release it back
        r = requests.patch(f"{BASE}/nullifier-registry/{nullifier_hash}/release",
            headers=_api_headers())
        ok("PATCH /nullifier-registry/<h>/release", r, 200)

        # Claim again, then mark spent — should flip commitment.spent to true
        requests.post(f"{BASE}/nullifier-registry",
            headers=_api_headers(),
            json={"nullifier_hash": nullifier_hash, "commitment_id": comm_id})

        r = requests.patch(f"{BASE}/nullifier-registry/{nullifier_hash}/spent",
            headers=_api_headers())
        ok("PATCH /nullifier-registry/<h>/spent", r, 200)

        # Verify commitment.spent flipped to true
        r = requests.get(f"{BASE}/commitments", headers=h(wallet, tag))
        body = ok("GET  /commitments (verify nullifier→commitment flip)", r, 200)
        match = next(c for c in body["commitments"] if c["id"] == comm_id)
        assert match["spent"] == "true", f"expected true after nullifier spent, got {match['spent']}"
        print("  ✅  commitment.spent flipped to true by nullifier-registry/spent")

        # Release on already-spent should 404
        r = requests.patch(f"{BASE}/nullifier-registry/{nullifier_hash}/release",
            headers=_api_headers())
        if r.status_code != 404:
            print(f"  FAIL  release on spent nullifier should be 404, got {r.status_code}")
            sys.exit(1)
        print("  ✅  release on spent nullifier correctly rejected (404)")

        print()
        print("══════════════════════════════════════════════════════════")
        print("  ✅  All DB endpoints passed.")
        print("══════════════════════════════════════════════════════════\n")

    finally:
        stop_server(proc)


if __name__ == "__main__":
    main()
