"""Shared on-chain execution path.

With confidential-vm decryption (`DECRYPTOR_URL`), the scheduler decrypts the FHE result bit
inside the TEE and calls `run_execution()` directly. Without a decryptor, the browser decrypts
locally and authorizes via POST /executeStrategy.
"""
from __future__ import annotations  # allow `str | None` annotations on Python 3.9 (Docker base)
import json
import time
import hmac as hmac_lib
import hashlib
import os
import requests as http
from datetime import datetime

from sqlalchemy import text

from database import db, Strategy, StrategyLeg
from trade_executor import execute_private_withdrawal
from evm_executor import execute_evm_trade, execute_evm_leg_swap
from evm_executor import FatalExecutionError, NullifierSpentSwapFailed

_HMAC_SECRET    = os.environ.get('SERVER_HMAC_SECRET', '').encode()
_API_TOKEN      = os.environ.get('API_TOKEN', '')
_TRADE_EXECUTOR = os.environ.get('TRADE_EXECUTOR_BASE_URL', 'http://localhost:5002')


def _nullifier_hmac(nullifier_hash: str) -> str:
    return hmac_lib.new(_HMAC_SECRET, nullifier_hash.encode(), hashlib.sha256).hexdigest()


def _executor_headers():
    return {'X-API-TOKEN': _API_TOKEN, 'Content-Type': 'application/json'}


def _get_nullifier_hash(strategy_dict) -> str | None:
    """Extract the primary nullifier hash from zkp_data for the double-spend guard.

    Frontend sends nullifierHashes[] (array) — use [0]. Falls back to the singular
    nullifierHash field for backward compatibility.
    """
    zkp = strategy_dict.get('zkp_data')
    if not zkp:
        return None
    try:
        zk = zkp if isinstance(zkp, dict) else json.loads(zkp)
        hashes = zk.get('nullifierHashes')
        if hashes and isinstance(hashes, list) and len(hashes) > 0:
            nh = str(hashes[0])
            return nh if nh else None
        nh = str(zk.get('nullifierHash', ''))
        return nh if nh else None
    except Exception as e:
        print(f"[Executor] ⚠️ Could not parse nullifierHash: {e}")
        return None


def _claim_nullifier(nullifier_hash: str, commitment_id: str | None) -> bool:
    """Atomically claim a nullifier in nullifier_registry. Returns True iff THIS caller won."""
    try:
        resp = http.post(
            f"{_TRADE_EXECUTOR}/nullifier-registry",
            json={'nullifier_hash': nullifier_hash, 'commitment_id': commitment_id},
            headers=_executor_headers(),
            timeout=10,
        )
        if resp.status_code == 201:
            print(f"[Executor] Nullifier claimed: {nullifier_hash[:16]}...")
            return True
        if resp.status_code == 409:
            print(f"[Executor] Nullifier already claimed (double-spend guard): {nullifier_hash[:16]}...")
            return False
        print(f"[Executor] ⚠️ Unexpected claim response {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        print(f"[Executor] ⚠️ Nullifier claim request failed: {e}")
        return False


def _mark_nullifier_spent(nullifier_hash: str):
    """Mark nullifier spent after on-chain withdraw confirms. Also flips commitment.spent."""
    try:
        resp = http.patch(
            f"{_TRADE_EXECUTOR}/nullifier-registry/{nullifier_hash}/spent",
            headers=_executor_headers(),
            timeout=10,
        )
        if resp.status_code == 200:
            print(f"[Executor] Nullifier marked spent: {nullifier_hash[:16]}...")
        else:
            print(f"[Executor] ⚠️ mark-spent response {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[Executor] ⚠️ mark-spent request failed: {e}")


def _release_nullifier(nullifier_hash: str):
    """Release pending claim back to false when withdraw fails/reverts."""
    try:
        resp = http.patch(
            f"{_TRADE_EXECUTOR}/nullifier-registry/{nullifier_hash}/release",
            headers=_executor_headers(),
            timeout=10,
        )
        if resp.status_code == 200:
            print(f"[Executor] Nullifier released to false: {nullifier_hash[:16]}...")
        else:
            print(f"[Executor] ⚠️ release response {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[Executor] ⚠️ release request failed: {e}")


def _claim_strategy(sid):
    """Atomically move a strategy ARMED/PENDING -> EXECUTING. Returns True iff THIS caller won."""
    result = db.session.execute(
        text("UPDATE strategy SET status='EXECUTING' WHERE id=:id AND status IN ('PENDING','ARMED')"),
        {"id": sid},
    )
    db.session.commit()
    return result.rowcount == 1


def run_execution(strategy_dict, current_price):
    """Execute a (browser-authorized) strategy. Returns a result dict; updates DB + nullifier.

    Returns: { "status": "EXECUTED"|"FAILED", "tx_hash": str|None }
    """
    sid        = strategy_dict['id']
    is_private = strategy_dict.get('is_private', False)

    # commitment_id is included by the browser in the strategy payload so the executor
    # can mark the right commitments row spent after confirmation.
    commitment_id   = strategy_dict.get('commitment_id')
    nullifier_hash  = _get_nullifier_hash(strategy_dict) if is_private else None

    # ── Strategy-level atomic guard ──
    if not _claim_strategy(sid):
        cur = Strategy.query.get(sid)
        cur_status = cur.status if cur else '<gone>'
        print(f"[Executor] Strategy {sid} not claimable (status={cur_status}) — another executor owns it; aborting")
        return {"status": "FAILED", "tx_hash": None}

    # ── Atomic nullifier claim via nullifier_registry ──
    if nullifier_hash:
        if not _claim_nullifier(nullifier_hash, commitment_id):
            print(f"[Executor] Strategy {sid} — nullifier already claimed, aborting double-spend")
            _mark_failed(sid)
            return {"status": "FAILED", "tx_hash": None}

    # Callback fired the instant the ZK withdraw receipt confirms on-chain.
    def _on_withdraw_confirmed(_tx_hash):
        if nullifier_hash:
            _mark_nullifier_spent(nullifier_hash)

    t_exec = time.monotonic()
    try:
        if is_private:
            tx_hash = execute_private_withdrawal(
                strategy_dict, current_price, on_withdraw_confirmed=_on_withdraw_confirmed
            )
        else:
            # EVM-only: non-private strategies execute directly from the executor wallet on the
            # strategy's EVM chain (no Solana path).
            tx_hash = execute_evm_trade(strategy_dict, current_price)

    except NullifierSpentSwapFailed as swap_fatal:
        print(f"[Executor] ⚠️  ZK withdraw confirmed but swap failed for {sid}: {swap_fatal}")
        _mark_failed(sid)
        # Nullifier IS spent on-chain — mark it; do NOT release.
        if nullifier_hash:
            _mark_nullifier_spent(nullifier_hash)
        return {"status": "FAILED", "tx_hash": None}

    except FatalExecutionError as fatal:
        print(f"[Executor] ❌ Fatal error for {sid}: {fatal}")
        _mark_failed(sid)
        if nullifier_hash:
            if "NullifierAlreadySpent" in str(fatal):
                # Spent on-chain by someone — mark true, never reuse.
                _mark_nullifier_spent(nullifier_hash)
            else:
                # Withdraw never confirmed — release claim so note is spendable again.
                _release_nullifier(nullifier_hash)
        return {"status": "FAILED", "tx_hash": None}

    exec_ms = (time.monotonic() - t_exec) * 1000
    print(f"[Executor] Execution took {exec_ms:.0f}ms | tx_hash={tx_hash}")

    if tx_hash:
        strat = Strategy.query.get(sid)
        if strat:
            strat.status     = 'EXECUTED'
            strat.tx_hash    = tx_hash
            strat.executed_at = datetime.utcnow()
            db.session.commit()
            print(f"[Executor] Strategy {sid} EXECUTED: {tx_hash}")
        # Belt-and-suspenders: ensure spent for non-ZK path (confirm callback didn't fire).
        if nullifier_hash:
            _mark_nullifier_spent(nullifier_hash)
        return {"status": "EXECUTED", "tx_hash": tx_hash}

    # No tx hash and withdraw never confirmed — release nullifier so user can retry.
    if nullifier_hash:
        _release_nullifier(nullifier_hash)
    return {"status": "FAILED", "tx_hash": None}


def _mark_failed(sid):
    strat = Strategy.query.get(sid)
    if strat:
        strat.status = 'FAILED'
        db.session.commit()


# ---------------------------------------------------------------------------------------------
#  Per-leg execution (TWAP slice / grid rung)
# ---------------------------------------------------------------------------------------------

def _claim_leg(leg_id):
    """Atomically move a leg ARMED/PENDING -> EXECUTING. Returns True iff THIS caller won."""
    result = db.session.execute(
        text("UPDATE strategy_legs SET status='EXECUTING' WHERE id=:id AND status IN ('PENDING','ARMED')"),
        {"id": leg_id},
    )
    db.session.commit()
    return result.rowcount == 1


def _mark_leg(leg_id, status, tx_hash=None):
    leg = StrategyLeg.query.get(leg_id)
    if leg:
        leg.status = status
        if tx_hash:
            leg.tx_hash = tx_hash
        if status == 'EXECUTED':
            leg.executed_at = datetime.utcnow()
        db.session.commit()


def run_leg_execution(leg_dict, strategy_dict, current_price):
    """Execute ONE leg of a multi-leg strategy on-chain.

    A leg is structurally identical to a single-strategy trade — spend exactly one (slice) note
    and swap — so it reuses the full `execute_evm_trade` withdraw→swap→(vault re-deposit) path,
    but with the LEG's own amount, ZK proof, and nullifier. N legs therefore yield N independent
    withdraw+swap txs with N distinct nullifiers; one note is never spent twice.

    Returns { "status": "EXECUTED"|"FAILED", "tx_hash": str|None }.
    """
    leg_id = leg_dict['id']
    sid    = strategy_dict['id']

    # Per-leg atomic guard (prevents two cycles firing the same leg).
    if not _claim_leg(leg_id):
        print(f"[Executor] Leg {leg_id} not claimable — another cycle owns it; aborting")
        return {"status": "FAILED", "tx_hash": None}

    nullifier_hash = _get_nullifier_hash(leg_dict)  # reads leg_dict['zkp_data']

    # Atomic nullifier claim (double-spend guard, same registry as single-strategy path).
    if nullifier_hash:
        if not _claim_nullifier(nullifier_hash, leg_dict.get('commitment_id')):
            print(f"[Executor] Leg {leg_id} — nullifier already claimed, aborting double-spend")
            _mark_leg(leg_id, 'FAILED')
            return {"status": "FAILED", "tx_hash": None}

    def _on_withdraw_confirmed(_tx_hash):
        if nullifier_hash:
            _mark_nullifier_spent(nullifier_hash)

    # Synthesize a single-trade strategy dict from parent + this leg, then run the SAME proven
    # single-strategy flow (execute_evm_trade): the leg's zkp_data is a single-slice WITHDRAW proof,
    # so the executor withdraws that slice → swaps asset_in→asset_out → re-deposits the output into
    # the asset_out vault as a private note (output_mode='vault', per-leg output_precommitment).
    # N legs ⇒ N withdraw+swap+deposit chains with N distinct nullifiers; one note is never spent twice.
    leg_strategy = dict(strategy_dict)
    leg_strategy.update({
        'amount':   leg_dict['amount'],
        'zkp_data': leg_dict.get('zkp_data'),
        'output_mode': (strategy_dict.get('output_mode') or 'vault'),
        'output_precommitment': leg_dict.get('output_precommitment'),
        'is_private': True,
    })

    t_exec = time.monotonic()
    try:
        tx_hash = execute_evm_trade(
            leg_strategy, current_price, on_withdraw_confirmed=_on_withdraw_confirmed
        )
    except NullifierSpentSwapFailed as swap_fatal:
        print(f"[Executor] ⚠️  leg {leg_id}: withdraw confirmed but swap failed: {swap_fatal}")
        _mark_leg(leg_id, 'FAILED')
        if nullifier_hash:
            _mark_nullifier_spent(nullifier_hash)  # spent on-chain — never reuse
        return {"status": "FAILED", "tx_hash": None}
    except FatalExecutionError as fatal:
        print(f"[Executor] ❌ leg {leg_id} fatal: {fatal}")
        _mark_leg(leg_id, 'FAILED')
        if nullifier_hash:
            if "NullifierAlreadySpent" in str(fatal):
                _mark_nullifier_spent(nullifier_hash)
            else:
                _release_nullifier(nullifier_hash)
        return {"status": "FAILED", "tx_hash": None}

    exec_ms = (time.monotonic() - t_exec) * 1000
    print(f"[Executor] Leg {leg_id} took {exec_ms:.0f}ms | tx_hash={tx_hash}")

    if tx_hash:
        _mark_leg(leg_id, 'EXECUTED', tx_hash=tx_hash)
        if nullifier_hash:
            _mark_nullifier_spent(nullifier_hash)
        return {"status": "EXECUTED", "tx_hash": tx_hash}

    _mark_leg(leg_id, 'FAILED')
    if nullifier_hash:
        _release_nullifier(nullifier_hash)
    return {"status": "FAILED", "tx_hash": None}
