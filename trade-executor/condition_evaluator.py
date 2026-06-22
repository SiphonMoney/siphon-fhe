from fhe_client import get_encrypted_tree_result

# Boolean composition (AND/OR/NOT) used to happen here over plaintext booleans. Now that the
# engine returns encrypted results, the whole tree is folded homomorphically inside the engine
# (/evaluateTree) and we only forward it + the live prices.


def evaluate_tree_encrypted(condition_tree: dict, live_prices: dict, server_key: str):
    """Return the encrypted result hex for a condition tree (or None on error).

    live_prices: { price_feed_id -> float price }. Converted to integer cents for the engine,
    which matches how bounds are encrypted client-side (price * 100).
    """
    prices_cents = {feed: int(round(price * 100)) for feed, price in live_prices.items()}
    return get_encrypted_tree_result(condition_tree, prices_cents, server_key)
