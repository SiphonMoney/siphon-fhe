"""Shared on-chain execution path.

With confidential-vm decryption (`DECRYPTOR_URL`), the scheduler decrypts the FHE result bit
inside the TEE and calls `run_execution()` directly. Without a decryptor, the browser decrypts
locally and authorizes via POST /executeStrategy.
"""
import json
import time
from datetime import datetime

from sqlalchemy import text

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


def _claim_note(note_id):
    """Atomically move a note 'false' -> 'pending'. Returns True iff THIS caller won the claim.

    Conditional UPDATE: only one caller can flip a given note out of 'false'. Under SQLite +
    NullPool + DELETE journal mode with busy_timeout, the UPDATE takes a write lock so two
    concurrent claimers serialize; the loser sees rowcount==0 because the row is no longer
    'false'. We commit immediately so the 'pending' state is visible to any other process/thread
    before we start the (slow) on-chain withdraw.
    """
    result = db.session.execute(
        text("UPDATE notes SET spent='pending' WHERE id=:id AND spent='false'"),
        {"id": note_id},
    )
    db.session.commit()
    return result.rowcount == 1


def _claim_strategy(sid):
    """Atomically move a strategy ARMED/PENDING -> EXECUTING. Returns True iff THIS caller won.

    Guards against two schedulers both executing the same ARMED strategy even before any note
    lookup. Loser sees rowcount==0 (already EXECUTING/EXECUTED/FAILED) and aborts.
    """
    result = db.session.execute(
        text("UPDATE strategy SET status='EXECUTING' WHERE id=:id AND status IN ('PENDING','ARMED')"),
        {"id": sid},
    )
    db.session.commit()
    return result.rowcount == 1


def _mark_note_spent(note_id):
    """Commit note.spent='true' atomically. Idempotent; safe to call from a withdraw-confirm
    callback. The withdraw is what spends the nullifier on-chain, so once its receipt confirms
    the note is genuinely spent and must NEVER revert to spendable."""
    db.session.execute(
        text("UPDATE notes SET spent='true' WHERE id=:id"),
        {"id": note_id},
    )
    db.session.commit()
    print(f"[Executor] Note {note_id} marked spent=true (ZK withdraw confirmed on-chain)")


def run_execution(strategy_dict, current_price):
    """Execute a (browser-authorized) strategy. Returns a result dict; updates DB + nullifier.

    Returns: { "status": "EXECUTED"|"FAILED", "tx_hash": str|None }
    """
    sid = strategy_dict['id']
    is_private = strategy_dict.get('is_private', False)

    # ── Strategy-level atomic guard ──
    # Flip ARMED/PENDING -> EXECUTING before doing anything. If we don't win, another
    # caller is already executing this strategy; abort immediately.
    if not _claim_strategy(sid):
        cur = Strategy.query.get(sid)
        cur_status = cur.status if cur else '<gone>'
        print(f"[Executor] Strategy {sid} not claimable (status={cur_status}) — another executor owns it; aborting")
        return {"status": "FAILED", "tx_hash": None}

    # ── Pre-flight: a spent nullifier can never be withdrawn again ──
    note = _find_note(strategy_dict)
    if note and str(note.spent) == 'true':
        print(f"[Executor] Note for strategy {sid} already spent — skipping (no retry possible)")
        _mark_failed(sid)
        return {"status": "FAILED", "tx_hash": None}

    # ── Atomic nullifier claim: only ONE caller may move the note 'false' -> 'pending' ──
    note_id = note.id if note else None
    if note:
        if not _claim_note(note_id):
            # Lost the claim: note is 'pending' or 'true'. Another execution owns this
            # nullifier — do NOT touch the on-chain withdraw.
            fresh = Note.query.get(note_id)
            print(f"[Executor] Note for strategy {sid} not claimable (spent={fresh.spent if fresh else '?'}) — aborting to avoid double-spend")
            _mark_failed(sid)
            return {"status": "FAILED", "tx_hash": None}
        print(f"[Executor] Note for strategy {sid} claimed (spent=pending)")

    # Callback fired the instant the ZK withdraw receipt confirms on-chain. From this
    # point the nullifier is genuinely spent; a later swap failure must NOT revert it.
    def _on_withdraw_confirmed(_tx_hash):
        if note_id:
            _mark_note_spent(note_id)

    t_exec = time.monotonic()
    try:
        if is_private:
            tx_hash = execute_private_withdrawal(
                strategy_dict, current_price, on_withdraw_confirmed=_on_withdraw_confirmed
            )
        else:
            tx_hash = execute_trade(strategy_dict, current_price)
    except NullifierSpentSwapFailed as swap_fatal:
        print(f"[Executor] ⚠️  ZK withdraw confirmed but swap failed for {sid}: {swap_fatal}")
        _mark_failed(sid)
        # Nullifier IS spent on-chain — ensure it's marked true (idempotent with the
        # confirm-time callback above) so it's never reused.
        if note_id:
            _mark_note_spent(note_id)
        return {"status": "FAILED", "tx_hash": None}
    except FatalExecutionError as fatal:
        print(f"[Executor] ❌ Fatal error for {sid}: {fatal}")
        _mark_failed(sid)
        if note_id:
            if "NullifierAlreadySpent" in str(fatal):
                # Spent on-chain (by someone) — mark true, never reuse.
                _mark_note_spent(note_id)
            else:
                # Withdraw never confirmed (e.g. InvalidZKProof on dry-run) — release
                # the claim so the note is spendable again. The confirm callback did
                # NOT fire, so the nullifier is NOT spent on-chain.
                db.session.execute(
                    text("UPDATE notes SET spent='false' WHERE id=:id AND spent='pending'"),
                    {"id": note_id},
                )
                db.session.commit()
                print("[Executor] Note reverted to spent=false (fatal error, withdraw not confirmed)")
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
        # Note was already marked 'true' by the confirm callback; ensure it for the
        # non-ZK path (no callback fires) and as a belt-and-suspenders idempotent write.
        if note_id:
            _mark_note_spent(note_id)
        return {"status": "EXECUTED", "tx_hash": tx_hash}

    # No tx hash and no withdraw confirmed — release the claim only if the withdraw
    # never spent the nullifier (still 'pending'). If the callback already set 'true',
    # this conditional UPDATE is a no-op and the note stays spent.
    if note_id:
        db.session.execute(
            text("UPDATE notes SET spent='false' WHERE id=:id AND spent='pending'"),
            {"id": note_id},
        )
        db.session.commit()
        print("[Executor] Note released to spent=false if not yet withdrawn (tx failed, retryable)")
    return {"status": "FAILED", "tx_hash": None}


def _mark_failed(sid):
    strat = Strategy.query.get(sid)
    if strat:
        strat.status = 'FAILED'
        db.session.commit()
