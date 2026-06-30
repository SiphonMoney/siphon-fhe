"""Fee accrual ledger. Records each deducted fee (kept in the executor wallet) for a later sweep
into the Siphon fee-vault. Sweep is implemented separately (accrue-then-sweep)."""


def record_fee(chain_id, asset, fee_wei, kind='execution', strategy_id=None, leg_id=None, tx_hash=None):
    """Persist a FeeAccrual. Best-effort — never raises into the on-chain execution path."""
    try:
        fee_wei = int(fee_wei)
    except (TypeError, ValueError):
        return None
    if fee_wei <= 0:
        return None
    try:
        from database import db, FeeAccrual
        row = FeeAccrual(
            chain_id=int(chain_id), asset=(asset or '').upper(), amount_wei=str(fee_wei),
            kind=kind, strategy_id=strategy_id, leg_id=leg_id, tx_hash=tx_hash,
        )
        db.session.add(row)
        db.session.commit()
        return row.id
    except Exception as e:
        try:
            from database import db
            db.session.rollback()
        except Exception:
            pass
        print(f"[Fee] record_fee failed: {e}")
        return None
