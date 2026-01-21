import os
import json
import sys
from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import geth_poa_middleware

# --- LOAD ENVIRONMENT VARIABLES ---
load_dotenv(override=True)

SEPOLIA_RPC_URL = os.getenv("SEPOLIA_RPC_URL")
ENTRYPOINT_CONTRACT_ADDRESS = os.getenv("ENTRYPOINT_CONTRACT_ADDRESS")
EXECUTOR_PRIVATE_KEY = os.getenv("EXECUTOR_PRIVATE_KEY")
ABI_PATH = os.getenv("ABI_PATH", "Entrypoint.abi.json") # Default to local file if not set

# --- TOKEN ADDRESSES (Sepolia) ---
TOKEN_ADDRESSES = {
    "ETH": Web3.to_checksum_address("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"),
    "USDC": Web3.to_checksum_address("0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"),
    "WETH": Web3.to_checksum_address("0x7b79995e5f793A07Bc00c21412e50Eaae098E7f9")
}

# --- LOAD ABI FUNCTION ---
def load_contract_abi(path):
    try:
        if not os.path.exists(path):
            # Try looking in an 'abis' folder if not found in root
            alt_path = os.path.join("abis", path)
            if os.path.exists(alt_path):
                path = alt_path
            else:
                raise FileNotFoundError(f"Path does not exist: {path}")

        with open(path, "r") as f:
            loaded_json = json.load(f)
            # Handle Hardhat/Foundry artifacts
            if isinstance(loaded_json, dict) and "abi" in loaded_json:
                return loaded_json["abi"]
            return loaded_json
    except Exception as e:
        print(f"❌ [Executor] Error loading ABI from {path}: {e}")
        return None

# Load ABI globally once
CONTRACT_ABI = load_contract_abi(ABI_PATH)

# --- HELPERS ---
def safe_int(val):
    if val is None or val == "":
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0

def format_proof_to_uint_array(proof_list):
    """Converts a list of hex/decimal proof strings to a list of Integers."""
    if not isinstance(proof_list, list):
        return []
    formatted = []
    for item in proof_list:
        try:
            if isinstance(item, int):
                formatted.append(item)
            elif isinstance(item, str):
                if item.startswith("0x"):
                    formatted.append(int(item, 16))
                else:
                    formatted.append(int(item))
        except (ValueError, TypeError):
            continue
    return formatted

def execute_trade(strategy, current_price):
    print("\n" + "="*60)
    print(f"✅ EXECUTION: Trigger met for strategy '{strategy['id']}'")

    # 1. Validation Checks
    missing_vars = []
    if not SEPOLIA_RPC_URL: missing_vars.append("SEPOLIA_RPC_URL")
    if not ENTRYPOINT_CONTRACT_ADDRESS: missing_vars.append("ENTRYPOINT_CONTRACT_ADDRESS")
    if not EXECUTOR_PRIVATE_KEY: missing_vars.append("EXECUTOR_PRIVATE_KEY")
    if not CONTRACT_ABI: missing_vars.append("ABI (Check ABI_PATH)")

    if missing_vars:
        print(f"   ❌ [Executor] CRITICAL ERROR: Missing config: {', '.join(missing_vars)}")
        return

    try:
        # 2. Connect to Blockchain
        w3 = Web3(Web3.HTTPProvider(SEPOLIA_RPC_URL))
        # Inject PoA middleware for Sepolia/testnets
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        if not w3.is_connected():
            print("   ❌ [Executor] Could not connect to RPC.")
            return

        executor_account = w3.eth.account.from_key(EXECUTOR_PRIVATE_KEY)
        entrypoint_contract = w3.eth.contract(address=ENTRYPOINT_CONTRACT_ADDRESS, abi=CONTRACT_ABI)

        # 3. Parse ZK Data
        try:
            zk_payload = json.loads(strategy['zkp_data'])
            inputs = zk_payload.get('publicInputs', {})

            raw_proof = zk_payload.get('proof', [])
            proof_array = format_proof_to_uint_array(raw_proof)

            zk_proof_struct = {
                "stateRoot": safe_int(inputs.get('root')),
                "nullifier": safe_int(inputs.get('nullifier')),
                "newCommitment": safe_int(inputs.get('newCommitment')),
                "proof": proof_array
            }

            _amountIn = safe_int(inputs.get('amount'))

            raw_asset_in = inputs.get('asset', '')
            if raw_asset_in in TOKEN_ADDRESSES:
                _srcToken = TOKEN_ADDRESSES[raw_asset_in]
            elif w3.is_address(raw_asset_in):
                _srcToken = Web3.to_checksum_address(raw_asset_in)
            else:
                print(f"   ❌ [Executor] Invalid asset_in: '{raw_asset_in}'")
                return

        except Exception as e:
            print(f"   ❌ [Executor] Failed to parse zkp_data: {e}")
            return

        # 4. BUILD TRANSACTION PARAMS
        raw_asset_out = strategy.get('asset_out', 'ETH')
        if raw_asset_out in TOKEN_ADDRESSES:
            _dstToken = TOKEN_ADDRESSES[raw_asset_out]
        elif w3.is_address(raw_asset_out):
            _dstToken = Web3.to_checksum_address(raw_asset_out)
        else:
            print(f"   ❌ [Executor] Invalid asset_out: '{raw_asset_out}'")
            return

        # Use Pool Address from Strategy or Fallback
        _pool = strategy.get('pool_address')
        if not _pool or not w3.is_address(_pool):
            # Fallback WETH/USDC Pool on Sepolia (Example)
            _pool = Web3.to_checksum_address("0x3289680dD4d6C10bb19b899729cda5eEF58AEfF1")
            print(f"   ⚠️ [Executor] Missing strategy pool_address. Using Fallback: {_pool}")
        else:
            _pool = Web3.to_checksum_address(_pool)


        _recipient = Web3.to_checksum_address(strategy['recipient_address'])
        _minAmountOut = 0
        _fee = 500  # 0.05% fee tier

        print(f"   [Executor] Building transaction for Entrypoint...")
        print(f"     -> Amount In: {_amountIn}")
        print(f"     -> Asset Path: {_srcToken} -> {_dstToken}")

        # 5. Build & Send (EIP-1559 Compatible)
        tx_func = entrypoint_contract.functions.swap(
            _pool,
            _srcToken,
            _dstToken,
            _recipient,
            _amountIn,
            _minAmountOut,
            _fee,
            zk_proof_struct
        )

        # Estimate gas to prevent under-gassing failures
        try:
            estimated_gas = tx_func.estimate_gas({'from': executor_account.address})
            gas_limit = int(estimated_gas * 1.2) # Add 20% buffer
        except Exception as gas_err:
            print(f"   ⚠️ Gas estimation failed ({gas_err}), using default.")
            gas_limit = 3000000

        tx = tx_func.build_transaction({
            'from': executor_account.address,
            'nonce': w3.eth.get_transaction_count(executor_account.address),
            'gas': gas_limit,
            'maxFeePerGas': w3.to_wei('50', 'gwei'),
            'maxPriorityFeePerGas': w3.to_wei('2', 'gwei'),
            'chainId': 11155111 # Sepolia
        })

        signed_tx = w3.eth.account.sign_transaction(tx, private_key=EXECUTOR_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        
        print(f"   ✅ Transaction sent! Hash: {tx_hash.hex()}")
        print(f"   🔗 https://sepolia.etherscan.io/tx/{tx_hash.hex()}")
        print("="*60)

    except Exception as e:
        print(f"   ❌ [Executor] On-chain error: {e}")
        import traceback
        traceback.print_exc()
        print("="*60)