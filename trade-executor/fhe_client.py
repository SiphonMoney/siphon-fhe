import requests
import os
from config import FHE_ENGINE_URL, FHE_ENGINE_CONDITION_URL

def is_condition_met(strategy, current_price):
    print(f"   -> [FHE Client] Consulting Rust FHE Engine for strategy '{strategy['id']}'...")
    try:
        payload = {
            "strategy_type": strategy["strategy_type"],
            "encrypted_upper_bound": strategy["encrypted_upper_bound"],
            "encrypted_lower_bound": strategy["encrypted_lower_bound"],
            "server_key": strategy["server_key"],
            "current_price_cents": int(current_price * 100),
            "encrypted_client_key": strategy["encrypted_client_key"],
        }
        
        response = requests.post(FHE_ENGINE_URL, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        if result.get("is_triggered", False):
            print(f"   <- [FHE Client] Response from Rust: Condition MET.")
            return True
        else:
            print(f"   <- [FHE Client] Response from Rust: Condition NOT met.")
            return False
    except Exception as e:
        print(f"   <- [FHE Client] ❌ An error occurred: {e}")
        return False

def evaluate_leaf(encrypted_bound: str, condition: str, current_price: float,
                  server_key: str, encrypted_client_key: str) -> bool:
    """Call /evaluateCondition for a single LEAF node."""
    try:
        payload = {
            "encrypted_bound": encrypted_bound,
            "condition": condition,
            "current_price_cents": int(current_price * 100),
            "server_key": server_key,
            "encrypted_client_key": encrypted_client_key,
        }
        resp = requests.post(FHE_ENGINE_CONDITION_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json().get("is_triggered", False)
    except Exception as e:
        print(f"[FHE Client] ❌ evaluate_leaf error: {e}")
        return False