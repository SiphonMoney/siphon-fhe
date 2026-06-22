import requests
from config import FHE_ENGINE_URL, FHE_ENGINE_TREE_URL

# The FHE engine no longer decrypts. These helpers return the *encrypted* result ciphertext
# (hex) that the scheduler stores on the strategy; the user's browser decrypts it locally.


def get_encrypted_result(strategy, current_price, server_key):
    """Call /evaluateStrategy and return the encrypted result hex (or None on error)."""
    print(f"   -> [FHE Client] Evaluating strategy '{strategy['id']}' on the FHE engine...")
    try:
        payload = {
            "strategy_type": strategy["strategy_type"],
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
