import time
import requests
from config import FHE_ENGINE_URL, FHE_ENGINE_TREE_URL

# The FHE engine no longer decrypts. These helpers return the *encrypted* result ciphertext
# (hex) that the scheduler stores on the strategy; the user's browser decrypts it locally.


# The frontend labels simple limit orders LIMIT_BUY / LIMIT_SELL, but the FHE engine's
# /evaluateStrategy only knows LIMIT_BUY_DIP (price <= lower) / LIMIT_SELL_RALLY (price >= upper).
# The semantics are identical, so translate at the boundary.
ENGINE_STRATEGY_TYPE = {
    "LIMIT_BUY": "LIMIT_BUY_DIP",
    "LIMIT_SELL": "LIMIT_SELL_RALLY",
}


def get_encrypted_result(strategy, current_price, server_key):
    """Call /evaluateStrategy and return the encrypted result hex (or None on error)."""
    print(f"   -> [FHE Client] Evaluating strategy '{strategy['id']}' on the FHE engine...")
    try:
        strategy_type = ENGINE_STRATEGY_TYPE.get(
            strategy["strategy_type"], strategy["strategy_type"]
        )
        payload = {
            "strategy_type": strategy_type,
            "encrypted_upper_bound": strategy.get("encrypted_upper_bound"),
            "encrypted_lower_bound": strategy.get("encrypted_lower_bound"),
            "server_key": server_key,
            "current_price_cents": int(current_price * 100),
        }
        response = requests.post(FHE_ENGINE_URL, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        if result.get("error"):
            print(f"   <- [FHE Client] ❌ engine error: {result['error']}")
            return None
        return result.get("encrypted_result")
    except Exception as e:
        print(f"   <- [FHE Client] ❌ An error occurred: {e}")
        return None


# Map a grid leg's side to the engine's one-sided price arm:
#   buy rung  fires when price <= rung  -> LIMIT_BUY_DIP   (encrypted_lower_bound)
#   sell rung fires when price >= rung  -> LIMIT_SELL_RALLY (encrypted_upper_bound)
_GRID_SIDE_ENGINE_TYPE = {
    "LIMIT_BUY": "LIMIT_BUY_DIP",
    "LIMIT_SELL": "LIMIT_SELL_RALLY",
    "BUY": "LIMIT_BUY_DIP",
    "SELL": "LIMIT_SELL_RALLY",
}


def get_encrypted_leg_result(leg, current_price, server_key, now_ts=None):
    """Evaluate ONE leg of a multi-leg strategy and return the encrypted trigger hex.

    A TWAP slice (eval_mode 'time') compares an encrypted fire-time against the public current
    unix time (TWAP_SLICE). A grid rung (eval_mode 'price') is a one-sided price trigger keyed
    by the leg's side. The encrypted bound never leaves ciphertext on the server.

    `leg` is a StrategyLeg.to_dict() (or any dict with eval_mode/side/encrypted_* fields).
    """
    try:
        eval_mode = (leg.get("eval_mode") or "price").lower()
        if eval_mode == "time":
            engine_type = "TWAP_SLICE"
        else:
            side = (leg.get("side") or "").upper()
            engine_type = _GRID_SIDE_ENGINE_TYPE.get(side)
            if not engine_type:
                print(f"   <- [FHE Client] ❌ grid leg missing/unknown side '{side}'")
                return None

        payload = {
            "strategy_type": engine_type,
            "encrypted_upper_bound": leg.get("encrypted_upper_bound"),
            "encrypted_lower_bound": leg.get("encrypted_lower_bound"),
            "server_key": server_key,
            "current_price_cents": int(current_price * 100),
            "current_time": int(now_ts if now_ts is not None else time.time()),
        }
        response = requests.post(FHE_ENGINE_URL, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        if result.get("error"):
            print(f"   <- [FHE Client] ❌ engine error (leg {leg.get('leg_index')}): {result['error']}")
            return None
        return result.get("encrypted_result")
    except Exception as e:
        print(f"   <- [FHE Client] ❌ leg eval error: {e}")
        return None


def get_encrypted_tree_result(condition_tree, prices_cents, server_key):
    """Call /evaluateTree (homomorphic AND/OR/NOT) and return the encrypted result hex.

    prices_cents: { price_feed_id -> int price in cents }
    """
    try:
        payload = {
            "tree": condition_tree,
            "server_key": server_key,
            "prices": prices_cents,
        }
        resp = requests.post(FHE_ENGINE_TREE_URL, json=payload, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        if result.get("error"):
            print(f"[FHE Client] ❌ evaluateTree error: {result['error']}")
            return None
        return result.get("encrypted_result")
    except Exception as e:
        print(f"[FHE Client] ❌ get_encrypted_tree_result error: {e}")
        return None
