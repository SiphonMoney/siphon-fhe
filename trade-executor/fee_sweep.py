"""Fee-vault sweep — accrue-then-sweep.

Accrued protocol fees (Part A + Part B) sit in the executor wallet in the input asset. When the
unswept total for a (chain, asset) crosses FEE_SWEEP_MIN_USD, deposit them into the Siphon vault as
ONE protocol-owned private note, consuming a pre-generated precommitment from the protocol pool.
The protocol later withdraws these notes itself (it holds the nullifier/secret).

Manual/admin-triggered until validated; not wired into the auto-scheduler.
"""
from __future__ import annotations
import fee_config as fc

TOKEN_DECIMALS = {"ETH": 18, "USDC": 6, "USDT": 6, "WBTC": 8}


def sweep_fees(chain_id: int, asset: str, asset_usd_price: float | None = None) -> dict:
    """Sweep unswept fees for (chain_id, asset) into the fee-vault. Returns a summary dict."""
    from database import db, FeeAccrual, ProtocolFeeNote
    from evm_chain_config import get_evm_chain_config
    from evm_executor import (
        get_web3, get_executor_account, deposit_native_to_vault, deposit_to_vault,
    )

    asset = (asset or "ETH").upper()
    chain_id = int(chain_id)

    accruals = FeeAccrual.query.filter_by(chain_id=chain_id, asset=asset, swept=False).all()
    total = sum(int(a.amount_wei) for a in accruals if a.amount_wei)
    if total <= 0:
        return {"swept": False, "reason": "nothing to sweep", "total_wei": "0"}

    decimals = TOKEN_DECIMALS.get(asset, 18)
    if asset_usd_price and asset_usd_price > 0:
        usd = total / (10 ** decimals) * asset_usd_price
        if usd < fc.FEE_SWEEP_MIN_USD:
            return {"swept": False, "reason": f"below threshold (${usd:.2f} < ${fc.FEE_SWEEP_MIN_USD})",
                    "total_wei": str(total)}

    note = (ProtocolFeeNote.query
            .filter_by(chain_id=chain_id, asset=asset, status='available')
            .order_by(ProtocolFeeNote.created_at).first())
    if not note:
        return {"swept": False, "reason": "no available protocol precommitment — generate the fee pool",
                "total_wei": str(total)}

    chain = get_evm_chain_config(chain_id)
    w3 = get_web3(chain)
    account = get_executor_account(w3, chain_id)

    try:
        if asset == "ETH":
            tx = deposit_native_to_vault(w3, account, chain, total, int(note.precommitment))
        else:
            token_addr = chain.token_address(asset)
            tx = deposit_to_vault(w3, account, chain, token_addr, total, int(note.precommitment))
    except Exception as e:
        db.session.rollback()
        return {"swept": False, "reason": f"deposit failed: {e}", "total_wei": str(total)}

    from datetime import datetime
    note.status = 'used'
    note.amount_wei = str(total)
    note.sweep_tx = tx
    note.used_at = datetime.utcnow()
    for a in accruals:
        a.swept = True
        a.sweep_tx = tx
    db.session.commit()
    print(f"[Fee] Swept {total} wei {asset} ({len(accruals)} accruals) → fee-vault note "
          f"precommitment={note.precommitment} tx={tx}")
    return {"swept": True, "tx": tx, "total_wei": str(total), "count": len(accruals),
            "precommitment": note.precommitment}
