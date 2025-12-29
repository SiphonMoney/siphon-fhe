import os
import json
from web3 import Web3
from config import (
    SEPOLIA_RPC_URL,
    ENTRYPOINT_CONTRACT_ADDRESS,
    EXECUTOR_PRIVATE_KEY
)

# --- TOKEN ADDRESSES (Sepolia) ---
TOKEN_ADDRESSES = {
    "ETH": Web3.to_checksum_address("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"),
    "USDC": Web3.to_checksum_address("0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"),
    "WETH": Web3.to_checksum_address("0x7b79995e5f793A07Bc00c21412e50Eaae098E7f9")
}

# --- LOAD ABI ---
try:
    with open("Entrypoint.abi.json", "r") as f:
        CONTRACT_ABI = json.load(f)
except FileNotFoundError:
    print("CRITICAL ERROR: Entrypoint.abi.json not found.")
    CONTRACT_ABI = None

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

    if not all([SEPOLIA_RPC_URL, ENTRYPOINT_CONTRACT_ADDRESS, CONTRACT_ABI, EXECUTOR_PRIVATE_KEY]):
        print("   ❌ [Executor] CRITICAL ERROR: Missing .env config or ABI.")
        return

    try:
        # 1. Connect
        w3 = Web3(Web3.HTTPProvider(SEPOLIA_RPC_URL))
        executor_account = w3.eth.account.from_key(EXECUTOR_PRIVATE_KEY)
        w3.eth.default_account = executor_account.address

        entrypoint_contract = w3.eth.contract(address=ENTRYPOINT_CONTRACT_ADDRESS, abi=CONTRACT_ABI)

        # 2. Parse ZK Data
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

        # 3. BUILD TRANSACTION PARAMS
        raw_asset_out = strategy.get('asset_out', 'ETH')
        if raw_asset_out in TOKEN_ADDRESSES:
            _dstToken = TOKEN_ADDRESSES[raw_asset_out]
        else:
            print(f"   ❌ [Executor] Invalid asset_out: '{raw_asset_out}'")
            return

        # TODO: The pool address must be part of the strategy data.
        # Assuming it's passed in the strategy object for now.
        _pool = strategy.get('pool_address') # This is an assumption
        if not _pool or not w3.is_address(_pool):
            print(f"   ❌ [Executor] Missing or invalid pool_address in strategy data.")
            # Fallback for now - THIS NEEDS TO BE PROVIDED IN STRATEGY
            _pool = Web3.to_checksum_address("0x3289680dD4d6C10bb19b899729cda5eEF58AEfF1") # WETH/USDC 0.05% on Sepolia, for testing
            print(f"   ⚠️ [Executor] USING FALLBACK POOL ADDRESS: {_pool}")


        _recipient = Web3.to_checksum_address(strategy['recipient_address'])
        _minAmountOut = 0
        _fee = 500  # 0.05% fee tier

        print(f"   [Executor] Building transaction for Entrypoint...")
        print(f"     -> Pool: {_pool}")
        print(f"     -> SrcToken: {_srcToken}")
        print(f"     -> DstToken: {_dstToken}")
        print(f"     -> AmountIn: {_amountIn}")
        print(f"     -> Recipient: {_recipient}")
        print(f"     -> ZKProof (root): {zk_proof_struct['stateRoot']}")
        print(f"     -> ZKProof (nullifier): {zk_proof_struct['nullifier']}")


        # 4. Build & Send
        tx = entrypoint_contract.functions.swap(
            _pool,
            _srcToken,
            _dstToken,
            _recipient,
            _amountIn,
            _minAmountOut,
            _fee,
            zk_proof_struct
        ).build_transaction({
            'from': executor_account.address,
            'nonce': w3.eth.get_transaction_count(executor_account.address),
            'gas': 3000000, # May need adjustment
            'gasPrice': w3.eth.gas_price
        })

        signed_tx = w3.eth.account.sign_transaction(tx, private_key=EXECUTOR_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        print(f"   [Executor] ✅ Transaction sent! Hash: {tx_hash.hex()}")
        print("="*60)

    except Exception as e:
        print(f"   ❌ [Executor] On-chain error: {e}")
        print("="*60)