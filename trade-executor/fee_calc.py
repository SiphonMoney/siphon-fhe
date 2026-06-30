"""Pure fee math for the Siphon dynamic fee model. No I/O — easy to unit-test.

The execution fee is taken in the INPUT asset (the executor holds the withdrawn amount_wei in
asset_in before swapping). `asset_usd_price` is the USD price of asset_in (the scheduler's
current_price == PYTH price of asset_in; stablecoins ~= $1).
"""
import fee_config as fc


def compute_execution_fee_wei(amount_wei: int, decimals: int, asset_usd_price: float) -> int:
    """Part B: max(MIN_EXEC, EXEC_BPS * notional) + gas_reimbursement, expressed in input-asset wei.

    Returns 0 when fees are disabled. Never exceeds 50% of the trade (safety clamp)."""
    if not fc.FEE_ENABLED or amount_wei <= 0:
        return 0

    # BPS cut is a direct fraction of the input amount — no USD round-trip needed.
    bps_wei = amount_wei * int(fc.EXEC_BPS) // 10000

    # USD floor + gas buffer, converted to input-asset wei via the asset price.
    scale = 10 ** decimals
    if asset_usd_price and asset_usd_price > 0:
        min_wei = int(fc.MIN_EXEC_USD / asset_usd_price * scale)
        gas_wei = int(fc.GAS_REIMBURSE_USD / asset_usd_price * scale)
    else:
        # No price → fall back to the BPS cut only (can't convert USD amounts).
        min_wei = 0
        gas_wei = 0

    fee_wei = max(bps_wei, min_wei) + gas_wei

    # Never take more than half the trade — guards against a bad/zero price blowing up the fee.
    return min(fee_wei, amount_wei // 2)


def compute_arming_fee_usd(window_hours: float) -> float:
    """Part A: BASE_ARM + PER_HOUR_ARM * window_hours, capped at ARM_CAP. USD."""
    if not fc.FEE_ENABLED:
        return 0.0
    hours = max(0.0, float(window_hours or 0.0))
    fee = fc.BASE_ARM_USD + fc.PER_HOUR_ARM_USD * hours
    return round(min(fee, fc.ARM_CAP_USD), 6)


def usd_to_wei(usd: float, decimals: int, asset_usd_price: float) -> int:
    """Convert a USD amount to input-asset wei (for arming-fee collection in-asset)."""
    if not usd or not asset_usd_price or asset_usd_price <= 0:
        return 0
    return int(usd / asset_usd_price * (10 ** decimals))
