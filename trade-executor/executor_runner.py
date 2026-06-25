"""Shared on-chain execution path.

With confidential-vm decryption (`DECRYPTOR_URL`), the scheduler decrypts the FHE result bit
inside the TEE and calls `run_execution()` directly. Without a decryptor, the browser decrypts
locally and authorizes via POST /executeStrategy.
"""
import json
import time
from datetime import datetime

from database import db, Strategy, Note
from trade_executor import execute_trade, execute_private_withdrawal
from evm_executor import FatalExecutionError, NullifierSpentSwapFailed


def _find_note(strategy_dict):
    """Locate the privacy-pool Note for this strategy via its zkp nullifierHash, if any."""
    zkp = strategy_dict.get('zkp_data')
    if not zkp:
        return None
    try:
        zk = zkp if isinstance(zkp, dict) else json.loads(zkp)
        nullifier_hash = str(zk.get('nullifierHash', ''))
        if not nullifier_hash:
            return None
        return Note.query.filter_by(nullifier_hash=nullifier_hash).first()
    except Exception as e:
        print(f"[Executor] ⚠️ Could not resolve note: {e}")
        return None


def run_execution(strategy_dict, current_price):
    """Execute a (browser-authorized) strategy. Returns a result dict; updates DB + nullifier.

    Returns: { "status": "EXECUTED"|"FAILED", "tx_hash": str|None }
    """
    sid = strategy_dict['id']
    is_private = strategy_dict.get('is_private', False)

    # ── Mark nullifier pending to block double-spend during execution ──
    note = _find_note(strategy_dict)
    if note:
        note.spent = 'pending'
        db.session.commit()
        print(f"[Executor] Note for strategy {sid} marked spent=pending")

    t_exec = time.monotonic()
    try:
        if is_private:
            tx_hash = execute_private_withdrawal(strategy_dict, current_price)
        else:
            tx_hash = execute_trade(strategy_dict, current_price)
    except NullifierSpentSwapFailed as swap_fatal:
        print(f"[Executor] ⚠️  ZK withdraw confirmed but swap failed for {sid}: {swap_fatal}")
        _mark_failed(sid)
        # Nullifier IS spent on-chain — mark true so it's never reused
        if note:
            note.spent = 'true'
            db.session.commit()
            print("[Executor] Note marked spent=true (nullifier spent, swap failed)")
        return {"status": "FAILED", "tx_hash": None}
    except FatalExecutionError as fatal:
        print(f"[Executor] ❌ Fatal error for {sid}: {fatal}")
        _mark_failed(sid)
        if note:
            if "NullifierAlreadySpent" in str(fatal):
                note.spent = 'true'
                print("[Executor] Note marked spent=true (nullifier already spent on-chain)")
            else:
                note.spent = 'false'
                print("[Executor] Note reverted to spent=false (fatal error)")
            db.session.commit()
        return {"status": "FAILED", "tx_hash": None}

    exec_ms = (time.monotonic() - t_exec) * 1000
    print(f"[Executor] Execution took {exec_ms:.0f}ms | tx_hash={tx_hash}")

    if tx_hash:
        strat = Strategy.query.get(sid)
        if strat:
            strat.status = 'EXECUTED'
            strat.tx_hash = tx_hash
            strat.executed_at = datetime.utcnow()
            db.session.commit()
            print(f"[Executor] Strategy {sid} EXECUTED: {tx_hash}")
        if note:
            note.spent = 'true'
            db.session.commit()
            print("[Executor] Note marked spent=true")
        return {"status": "EXECUTED", "tx_hash": tx_hash}

    # No tx hash — revert note so it can be retried
    if note:
        note.spent = 'false'
        db.session.commit()
        print("[Executor] Note reverted to spent=false (tx failed, will retry)")
    return {"status": "FAILED", "tx_hash": None}


def _mark_failed(sid):
    strat = Strategy.query.get(sid)
    if strat:
        strat.status = 'FAILED'
        db.session.commit()
