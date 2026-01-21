import requests
import json
import os
from config import FHE_ENGINE_URL

# Get API token from environment
API_TOKEN = os.getenv('API_TOKEN', '')

def is_condition_met(strategy, current_price):
    print(f"   -> [FHE Client] Consulting REAL Rust FHE Engine for strategy '{strategy['id']}'...")
    try:
        payload = {
            "strategy_type": strategy["strategy_type"],
            "encrypted_upper_bound": json.loads(strategy["encrypted_upper_bound"]),
            "encrypted_lower_bound": json.loads(strategy["encrypted_lower_bound"]),
            "server_key": json.loads(strategy["server_key"]),
            "current_price_cents": int(current_price * 100),
        }
        
        # Debug: Check what MPC fields are available
        has_fhe_key_id = strategy.get('fhe_key_id') is not None
        has_mpc_pubkey = strategy.get('mpc_public_key_set') is not None
        has_client_key = strategy.get('encrypted_client_key') is not None
        
        print(f"   -> [FHE Client] Strategy MPC status:")
        print(f"      - fhe_key_id: {'✅' if has_fhe_key_id else '❌'} ({strategy.get('fhe_key_id', 'None')})")
        print(f"      - mpc_public_key_set: {'✅' if has_mpc_pubkey else '❌'}")
        print(f"      - encrypted_client_key: {'✅' if has_client_key else '❌'} (legacy)")
        
        # Add client key only if NOT using MPC shares (legacy support)
        if strategy.get('encrypted_client_key'):
            payload["encrypted_client_key"] = json.loads(strategy['encrypted_client_key'])
        
        # Add MPC fields (preferred - uses threshold decryption)
        if strategy.get('mpc_public_key_set'):
            payload["mpc_public_key_set"] = strategy['mpc_public_key_set']
        if strategy.get('mpc_share_indices'):
            payload["mpc_share_indices"] = json.loads(strategy['mpc_share_indices']) if isinstance(strategy['mpc_share_indices'], str) else strategy['mpc_share_indices']
        if strategy.get('fhe_key_id'):
            payload["fhe_key_id"] = strategy['fhe_key_id']  # Key ID for threshold decryption
            print(f"   -> [FHE Client] ✅ Using MPC threshold decryption (key_id: {strategy['fhe_key_id']})")
        else:
            print(f"   -> [FHE Client] ⚠️  No fhe_key_id found - will use legacy direct decryption")
        
        headers = {}
        if API_TOKEN:
            headers['X-API-TOKEN'] = API_TOKEN
        
        response = requests.post(FHE_ENGINE_URL, json=payload, headers=headers, timeout=3000)
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