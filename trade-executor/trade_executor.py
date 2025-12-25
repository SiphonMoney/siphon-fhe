import os
import json
from web3 import Web3
from config import (
    SEPOLIA_RPC_URL, 
    SYPHON_VAULT_CONTRACT_ADDRESS, 
    EXECUTOR_PRIVATE_KEY
)

TOKEN_ADDRESSES = {
    "ETH": Web3.to_checksum_address("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"), 
    "USDC": Web3.to_checksum_address("0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"), 
    "WETH": Web3.to_checksum_address("0x7b79995e5f793A07Bc00c21412e50Eaae098E7f9")  
}

try:
    with open("SyphonVault.abi.json", "r") as f:
        CONTRACT_ABI = json.load(f)
except FileNotFoundError:
    print("CRITICAL ERROR: SyphonVault.abi.json not found.")
    CONTRACT_ABI = None

def encode_proof_to_bytes(proof_list):
    """
    Converts a list of 24 strings/ints into a single 32-byte-aligned bytes object.
    Handles Decimal strings (standard SnarkJS) and Hex strings.
    """
    if not isinstance(proof_list, list):
        return b''
    
    encoded = b''
    for item in proof_list:
        try:
            val = 0
            if isinstance(item, int):
                val = item
            elif isinstance(item, str):
                if item.startswith("0x"):
                    val = int(item, 16)
                else:
                    val = int(item, 10) 
            
            encoded += val.to_bytes(32, 'big')
        except Exception as e:
            print(f"   [Encode Error] Failed to process proof item '{item}': {e}")
            raise e
            
    return encoded

def execute_trade(strategy, current_price):
    print("\n" + "="*60)
    print(f"✅ EXECUTION: Trigger met for strategy '{strategy['id']}'")
    print(f"   Strategy Type: {strategy['strategy_type']}")
    print(f"   Current Price: ${current_price:,.2f}")
    
    if not all([SEPOLIA_RPC_URL, SYPHON_VAULT_CONTRACT_ADDRESS, CONTRACT_ABI, EXECUTOR_PRIVATE_KEY]):
        print("   ❌ [Executor] CRITICAL ERROR: Missing .env config.")
        return

    try:
        w3 = Web3(Web3.HTTPProvider(SEPOLIA_RPC_URL))
        executor_account = w3.eth.account.from_key(EXECUTOR_PRIVATE_KEY)
        w3.eth.default_account = executor_account.address
        print(f"   [Executor] Wallet loaded: {executor_account.address}")

        vault_contract = w3.eth.contract(
            address=SYPHON_VAULT_CONTRACT_ADDRESS, 
            abi=CONTRACT_ABI
        )

        try:
            zk_payload = json.loads(strategy['zkp_data'])
            inputs = zk_payload.get('publicInputs', {})
            
            raw_proof_list = zk_payload.get('proof', [])
            _proof_bytes = encode_proof_to_bytes(raw_proof_list)

            _nullifier = int(inputs.get('nullifier', 0))
            _newCommitment = int(inputs.get('newCommitment', 0))
            _amountIn = int(inputs.get('amount', 0))
            
            raw_asset = inputs.get('asset', '')
            print(f"   [Executor] Processing Asset: '{raw_asset}'")

            if raw_asset in TOKEN_ADDRESSES:
                _asset_in_address = TOKEN_ADDRESSES[raw_asset]
            elif w3.is_address(raw_asset):
                _asset_in_address = Web3.to_checksum_address(raw_asset)
            else:
                raise ValueError(f"Asset '{raw_asset}' is not a valid token symbol or address.")

        except Exception as e:
            print(f"   ❌ [Executor] Failed to parse zkp_data: {e}")
            return

        _asset_out_symbol = strategy.get('asset_out', 'ETH')
        _asset_out_address = TOKEN_ADDRESSES.get(_asset_out_symbol, TOKEN_ADDRESSES['ETH'])
        _recipient = Web3.to_checksum_address(strategy['recipient_address'])
        _minAmountOut = 0 
        _fee = 3000 
        
        print(f"   [Executor] Building swap transaction...")
        print(f"     -> Asset In: {_asset_in_address}")
        print(f"     -> Asset Out: {_asset_out_address}")
        print(f"     -> Amount: {_amountIn}")
        print(f"     -> Proof Length: {len(_proof_bytes)} bytes")

        tx = vault_contract.functions.swap(
            _asset_in_address,
            _asset_out_address,
            _recipient,
            _amountIn,
            _minAmountOut,
            _fee,
            _nullifier,
            _newCommitment,
            _proof_bytes
        ).build_transaction({
            'from': executor_account.address,
            'nonce': w3.eth.get_transaction_count(executor_account.address),
            'gas': 3000000,
            'gasPrice': w3.eth.gas_price
        })

        signed_tx = w3.eth.account.sign_transaction(tx, private_key=EXECUTOR_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        
        print(f"   [Executor] ✅ Swap transaction sent! Hash: {tx_hash.hex()}")
        print("="*60)

    except Exception as e:
        print(f"   ❌ [Executor] An error occurred during on-chain execution: {e}")
        print("="*60)