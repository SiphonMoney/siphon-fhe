import os
import json
from web3 import Web3
from config import (
    SEPOLIA_RPC_URL, 
    SYPHON_VAULT_CONTRACT_ADDRESS, 
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
    with open("SyphonVault.abi.json", "r") as f:
        data = json.load(f)
        # Handle both raw list and artifact format ({"abi": [...]})
        CONTRACT_ABI = data["abi"] if "abi" in data else data
except FileNotFoundError:
    print("CRITICAL ERROR: SyphonVault.abi.json not found.")
    CONTRACT_ABI = None

# --- HELPERS ---
def safe_int(val):
    if val is None or val == "":
        return 0
    try:
        return int(val)
    except ValueError:
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
                    formatted.append(int(item, 10))
        except:
            continue
    return formatted

def execute_trade(strategy, current_price):
    print("\n" + "="*60)
    print(f"✅ EXECUTION: Trigger met for strategy '{strategy['id']}'")
    
    if not all([SEPOLIA_RPC_URL, SYPHON_VAULT_CONTRACT_ADDRESS, CONTRACT_ABI, EXECUTOR_PRIVATE_KEY]):
        print("   ❌ [Executor] CRITICAL ERROR: Missing .env config.")
        return

    try:
        # 1. Connect
        w3 = Web3(Web3.HTTPProvider(SEPOLIA_RPC_URL))
        executor_account = w3.eth.account.from_key(EXECUTOR_PRIVATE_KEY)
        w3.eth.default_account = executor_account.address
        
        vault_contract = w3.eth.contract(address=SYPHON_VAULT_CONTRACT_ADDRESS, abi=CONTRACT_ABI)

        # 2. Parse ZK Data
        try:
            zk_payload = json.loads(strategy['zkp_data'])
            inputs = zk_payload.get('publicInputs', {})
            
            raw_proof = zk_payload.get('proof', [])
            proof_array = format_proof_to_uint_array(raw_proof)

            state_root = safe_int(inputs.get('root')) 
            _nullifier = safe_int(inputs.get('nullifier'))
            _newCommitment = safe_int(inputs.get('newCommitment'))
            _amountIn = safe_int(inputs.get('amount'))
            
            # Asset In Lookup
            raw_asset = inputs.get('asset', '')
            if raw_asset in TOKEN_ADDRESSES:
                _asset_in_address = TOKEN_ADDRESSES[raw_asset]
            elif w3.is_address(raw_asset):
                _asset_in_address = Web3.to_checksum_address(raw_asset)
            else:
                print(f"   ❌ [Executor] Invalid asset: '{raw_asset}'")
                return

        except Exception as e:
            print(f"   ❌ [Executor] Failed to parse zkp_data: {e}")
            return

        # 3. BUILD TRANSACTION PARAMS
        
        # --- FIX 1: Map Input ETH -> WETH ---
        if _asset_in_address == TOKEN_ADDRESSES["ETH"]:
             print("   [Executor] Mapping Input ETH -> WETH")
             _asset_in_address = TOKEN_ADDRESSES["WETH"]

        # --- FIX 2: Map Output ETH -> WETH ---
        _asset_out_symbol = strategy.get('asset_out', 'ETH')
        
        if _asset_out_symbol == "ETH":
            print("   [Executor] Mapping Output ETH -> WETH")
            _asset_out_address = TOKEN_ADDRESSES["WETH"]
        else:
            _asset_out_address = TOKEN_ADDRESSES.get(_asset_out_symbol, TOKEN_ADDRESSES["WETH"])

        _recipient = Web3.to_checksum_address(strategy['recipient_address'])
        _minAmountOut = 0 
        _fee = 500  # 0.05% fee tier for better testnet liquidity
        _router = "0xE592427A0AEce92De3Edee1F18E0157C05861564" # Uniswap Router

        # --- FIX 3: Correct Struct Size (6 items) ---
        swap_param_struct = (
            _asset_in_address,   # srcToken
            _asset_out_address,  # dstToken
            _recipient,          # recipient
            _amountIn,           # amountIn
            _minAmountOut,       # minAmountOut
            _fee                 # fee
        )

        print(f"   [Executor] Building transaction...")
        print(f"     -> Struct: {swap_param_struct}")
        print(f"     -> Root: {state_root}")
        print(f"     -> Nullifier: {_nullifier}")

        # 4. Build & Send
        tx = vault_contract.functions.swap(
            swap_param_struct,  
            _router,            
            state_root,         
            _nullifier,        
            _newCommitment,     
            proof_array         
        ).build_transaction({
            'from': executor_account.address,
            'nonce': w3.eth.get_transaction_count(executor_account.address),
            'gas': 3000000,
            'gasPrice': w3.eth.gas_price
        })

        signed_tx = w3.eth.account.sign_transaction(tx, private_key=EXECUTOR_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        print(f"   [Executor] ✅ Transaction sent! Hash: {tx_hash.hex()}")
        print("="*60)

    except Exception as e:
        print(f"   ❌ [Executor] On-chain error: {e}")
        print("="*60)