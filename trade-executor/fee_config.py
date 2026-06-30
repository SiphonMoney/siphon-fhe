"""Siphon dynamic fee model — central, env-overridable parameters.

Two-part model:
  Part A  Arming fee   (upfront, non-refundable): BASE_ARM + PER_HOUR_ARM * window_hours, capped.
          Covers FHE compute + anti-spam — pays even if the strategy never triggers.
  Part B  Execution fee (per successful trigger, taken from the trade):
          max(MIN_EXEC, EXEC_BPS * notional) + gas_reimbursement, in the input asset.

Fees are deducted EXECUTOR-SIDE (the swap amounts are bound into the ZK circuit, so an on-chain
skim would need circuit changes). The deducted amount simply stays in the executor wallet
(zero extra gas) and is recorded as a FeeAccrual; a periodic sweep deposits accrued fees into the
Siphon fee-vault as one protocol-owned private note (accrue-then-sweep).
"""
import os


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _b(name: str, default: bool) -> bool:
    return str(os.environ.get(name, str(default))).strip().lower() in ("1", "true", "yes", "on")


# Master switch — fees are OFF unless explicitly enabled, so nothing changes until rolled out.
FEE_ENABLED = _b("FEE_ENABLED", False)

# ── Part B: execution fee ────────────────────────────────────────────────────
EXEC_BPS = _f("FEE_EXEC_BPS", 20)           # 20 bps = 0.20% of notional
MIN_EXEC_USD = _f("FEE_MIN_EXEC_USD", 0.20)  # floor so tiny trades still cover base cost
GAS_REIMBURSE_USD = _f("FEE_GAS_REIMBURSE_USD", 0.10)  # flat gas buffer (v1; refine to actual)

# ── Part A: arming fee ───────────────────────────────────────────────────────
BASE_ARM_USD = _f("FEE_BASE_ARM_USD", 0.20)
PER_HOUR_ARM_USD = _f("FEE_PER_HOUR_ARM_USD", 0.05)
ARM_CAP_USD = _f("FEE_ARM_CAP_USD", 5.00)

# ── Fee-vault destination (shielded). Per-chain asset whose vault collects swept fees.
# The execution fee is taken in the INPUT asset, so fees accrue per (chain, asset_in).
FEE_SWEEP_MIN_USD = _f("FEE_SWEEP_MIN_USD", 5.00)   # only sweep once accrued >= this (gas efficiency)

# Stablecoins are treated as $1 for USD<->asset conversion.
STABLE_SYMBOLS = {"USDC", "USDT", "DAI"}
