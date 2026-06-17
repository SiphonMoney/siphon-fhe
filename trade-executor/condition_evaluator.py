from fhe_client import evaluate_leaf

def evaluate_tree(node: dict, live_prices: dict, strategy: dict) -> bool:
    """
    Recursively evaluate a condition tree node against live prices.
    live_prices: { price_feed_id -> float_price }
    strategy: full strategy dict (for server_key, encrypted_client_key)
    """
    if not node:
        return False
        
    op = node.get("op")

    if op == "LEAF":
        price_feed_id = node.get("price_feed_id")
        price = live_prices.get(price_feed_id)
        if price is None:
            print(f"[ConditionEval] ⚠️ No price for feed {price_feed_id}, skipping leaf → False")
            return False
        return evaluate_leaf(
            encrypted_bound=node["encrypted_bound"],
            condition=node["condition"],
            current_price=price,
            server_key=strategy["server_key"],
            encrypted_client_key=strategy["encrypted_client_key"],
        )

    children = node.get("conditions", [])

    if op == "AND":
        if not children:
            return False
        return all(evaluate_tree(c, live_prices, strategy) for c in children)

    if op == "OR":
        if not children:
            return False
        return any(evaluate_tree(c, live_prices, strategy) for c in children)

    if op == "NOT":
        if not children:
            return False
        return not evaluate_tree(children[0], live_prices, strategy)

    print(f"[ConditionEval] ❌ Unknown op '{op}'")
    return False
