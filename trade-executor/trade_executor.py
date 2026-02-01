import os
import json
import base58
import base64
import requests
from dotenv import load_dotenv
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM_ID
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed
from solana.transaction import Transaction
from solders.instruction import Instruction, AccountMeta

# --- LOAD ENVIRONMENT VARIABLES ---
load_dotenv(override=True)

from config import (
    SOLANA_RPC_URL,
    SOLANA_NETWORK,
    SIPHON_PROGRAM_ID,
    EXECUTOR_PRIVATE_KEY,
    SOLANA_TOKEN_MINTS,
    MAINNET_TOKEN_MINTS,
    JUPITER_API_URL,
    RANGE_API_URL,
    RANGE_API_KEY,
)

# --- HELPERS ---
def safe_int(val):
    if val is None or val == "":
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0

# Token decimals for conversion
TOKEN_DECIMALS = {
    'SOL': 9,
    'USDC': 6,
    'USDT': 6,
    'USD': 6,  # USD Dev token on devnet
}

# Devnet USD Dev token for mock swaps
USD_DEV_MINT = "Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr"

def amount_to_lamports(amount, asset='SOL'):
    """Convert float amount to smallest units (lamports for SOL, micro units for SPL)."""
    if amount is None or amount == "":
        return 0
    try:
        amount_float = float(amount)
        decimals = TOKEN_DECIMALS.get(asset.upper(), 9)
        return int(amount_float * (10 ** decimals))
    except (ValueError, TypeError):
        return 0

def get_executor_keypair():
    """Load executor keypair from private key (base58 encoded)."""
    if not EXECUTOR_PRIVATE_KEY:
        return None
    try:
        secret_key = base58.b58decode(EXECUTOR_PRIVATE_KEY)
        return Keypair.from_bytes(secret_key)
    except Exception as e:
        print(f"   ❌ [Executor] Failed to load keypair: {e}")
        return None

def get_token_mint(token_symbol):
    """Get Solana token mint address from symbol."""
    mint_str = SOLANA_TOKEN_MINTS.get(token_symbol.upper())
    if mint_str:
        return Pubkey.from_string(mint_str)
    return None

# --- RANGE COMPLIANCE ---
def check_range_compliance(address: str) -> tuple[bool, str]:
    """Check if address passes Range compliance screening."""
    if not RANGE_API_KEY or not RANGE_API_URL:
        print("   ⚠️ [Executor] Range API not configured, skipping compliance check")
        return True, "Compliance check skipped"

    try:
        response = requests.post(
            f"{RANGE_API_URL}/screen",
            json={"address": address, "chain": "solana"},
            headers={"Authorization": f"Bearer {RANGE_API_KEY}"},
            timeout=10
        )
        result = response.json()

        if result.get("allowed", True):
            return True, "Address cleared"
        else:
            return False, result.get("reason", "Address blocked by compliance")
    except Exception as e:
        print(f"   ⚠️ [Executor] Range API error: {e}, allowing by default")
        return True, "Compliance check failed, allowing by default"

# --- JUPITER SWAP ---
def get_jupiter_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 50):
    """Get swap quote from Jupiter aggregator (new v1 API)."""
    try:
        response = requests.get(
            f"{JUPITER_API_URL}/quote",
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "slippageBps": slippage_bps,
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"   ❌ [Executor] Jupiter quote error: {e}")
        return None

def get_jupiter_swap_transaction(quote: dict, user_pubkey: str):
    """Get serialized swap transaction from Jupiter (new v1 API)."""
    try:
        response = requests.post(
            f"{JUPITER_API_URL}/swap",
            json={
                "quoteResponse": quote,
                "userPublicKey": user_pubkey,
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto",
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"   ❌ [Executor] Jupiter swap error: {e}")
        return None

# --- MAIN EXECUTION ---
def execute_trade(strategy, current_price):
    """Execute a strategy trade: direct transfer or Jupiter swap."""
    print("\n" + "="*60)
    print(f"✅ EXECUTION: Trigger met for strategy '{strategy['id']}'")

    # 1. Validation
    if not SOLANA_RPC_URL or not EXECUTOR_PRIVATE_KEY:
        print("   ❌ [Executor] Missing SOLANA_RPC_URL or EXECUTOR_PRIVATE_KEY")
        return False

    try:
        client = Client(SOLANA_RPC_URL)
        executor_keypair = get_executor_keypair()

        if not executor_keypair:
            print("   ❌ [Executor] Failed to load executor keypair")
            return False

        print(f"   [Executor] Executor: {executor_keypair.pubkey()}")

        # 2. Parse Strategy
        asset_in = strategy.get('asset_in', 'SOL').upper()
        asset_out = strategy.get('asset_out', 'USDC').upper()
        raw_amount = strategy.get('amount', 0)
        recipient_address = strategy.get('recipient_address')

        amount = amount_to_lamports(raw_amount, asset_in)

        if not recipient_address:
            print("   ❌ [Executor] Missing recipient_address")
            return False

        if amount <= 0:
            print(f"   ❌ [Executor] Invalid amount: {raw_amount}")
            return False

        print(f"   [Executor] Amount: {raw_amount} {asset_in} = {amount} smallest units")
        print(f"   [Executor] Recipient: {recipient_address}")

        # 3. Compliance Check
        allowed, reason = check_range_compliance(recipient_address)
        if not allowed:
            print(f"   ❌ [Executor] Compliance failed: {reason}")
            return False
        print(f"   ✅ [Executor] Compliance: {reason}")

        # 4. Execute based on whether swap is needed
        if asset_in == asset_out:
            # Direct transfer (no swap needed)
            return execute_direct_transfer(client, executor_keypair, recipient_address, amount, asset_in)
        else:
            # For devnet: mock swap using mainnet rates + USD Dev token
            if SOLANA_NETWORK != "mainnet-beta":
                return execute_mock_swap(client, executor_keypair, recipient_address, amount, asset_in, asset_out, current_price)
            # Jupiter swap then transfer (mainnet only)
            return execute_swap_and_transfer(client, executor_keypair, recipient_address, amount, asset_in, asset_out)

    except Exception as e:
        print(f"   ❌ [Executor] Error: {e}")
        import traceback
        traceback.print_exc()
        return None  # Return None instead of False for consistency


def execute_direct_transfer(client, executor_keypair, recipient_address, amount, asset):
    """Execute a direct SOL or SPL transfer."""
    print(f"   [Executor] Direct transfer: {amount} {asset}")

    recipient_pubkey = Pubkey.from_string(recipient_address)

    if asset == 'SOL':
        from solders.system_program import transfer, TransferParams

        transfer_ix = transfer(
            TransferParams(
                from_pubkey=executor_keypair.pubkey(),
                to_pubkey=recipient_pubkey,
                lamports=amount
            )
        )

        recent_blockhash = client.get_latest_blockhash().value.blockhash

        tx = Transaction(
            recent_blockhash=recent_blockhash,
            fee_payer=executor_keypair.pubkey()
        )
        tx.add(transfer_ix)
        tx.sign(executor_keypair)

        tx_sig = client.send_transaction(
            tx,
            executor_keypair,
            opts={"skip_preflight": False, "preflight_commitment": Confirmed}
        )

        if tx_sig.value:
            tx_hash = str(tx_sig.value)
            print(f"   ✅ Transfer successful! Sig: {tx_hash}")
            print(f"   🔗 https://explorer.solana.com/tx/{tx_hash}?cluster=devnet")
            print("="*60)
            return tx_hash  # Return tx_hash instead of boolean
        else:
            print(f"   ❌ Transfer failed: {tx_sig}")
            return None
    else:
        # SPL token transfer - TODO: implement
        print(f"   ❌ SPL transfers not yet implemented for {asset}")
        return False


def execute_mock_swap(client, executor_keypair, recipient_address, amount, asset_in, asset_out, current_price=None):
    """Mock swap for devnet: use Pyth price for conversion rate, send USD Dev tokens."""
    in_decimals = TOKEN_DECIMALS.get(asset_in.upper(), 9)
    human_amount_in = amount / (10 ** in_decimals)
    print(f"   [Executor] Mock Swap (devnet): {human_amount_in} {asset_in} -> {asset_out}")

    # Use Pyth price for conversion (passed from scheduler)
    if current_price is None or current_price <= 0:
        print("   ❌ No valid price available for conversion")
        return False
    
    # Calculate output amount based on Pyth price
    # For SOL -> USDC: multiply by current price
    out_decimals = TOKEN_DECIMALS.get(asset_out.upper(), 6)
    usdc_amount_out = human_amount_in * current_price
    out_amount = int(usdc_amount_out * (10 ** out_decimals))
    
    print(f"   [Executor] Pyth price: ${current_price:.2f}")
    print(f"   [Executor] Conversion: {human_amount_in} {asset_in} -> {usdc_amount_out:.2f} {asset_out}")

    # Send USD Dev tokens at the quoted rate
    print(f"   [Executor] Sending {usdc_amount_out:.2f} USD Dev tokens to {recipient_address}")
    return execute_spl_transfer(client, executor_keypair, recipient_address, out_amount, USD_DEV_MINT)


def execute_spl_transfer(client, executor_keypair, recipient_address, amount, mint_address):
    """Execute SPL token transfer."""
    from spl.token.instructions import transfer_checked, TransferCheckedParams
    from spl.token.constants import TOKEN_PROGRAM_ID
    from solders.pubkey import Pubkey

    mint_pubkey = Pubkey.from_string(mint_address)
    recipient_pubkey = Pubkey.from_string(recipient_address)

    # Get or create associated token accounts
    from spl.token.instructions import get_associated_token_address

    sender_ata = get_associated_token_address(executor_keypair.pubkey(), mint_pubkey)
    recipient_ata = get_associated_token_address(recipient_pubkey, mint_pubkey)

    # Check if recipient ATA exists, create if not
    recipient_ata_info = client.get_account_info(recipient_ata)

    instructions = []

    if recipient_ata_info.value is None:
        # Create recipient ATA
        from spl.token.instructions import create_associated_token_account

        create_ata_ix = create_associated_token_account(
            payer=executor_keypair.pubkey(),
            owner=recipient_pubkey,
            mint=mint_pubkey
        )
        instructions.append(create_ata_ix)
        print(f"   [Executor] Creating recipient ATA: {recipient_ata}")

    # Transfer tokens (USD Dev has 6 decimals)
    transfer_ix = transfer_checked(
        TransferCheckedParams(
            program_id=TOKEN_PROGRAM_ID,
            source=sender_ata,
            mint=mint_pubkey,
            dest=recipient_ata,
            owner=executor_keypair.pubkey(),
            amount=amount,
            decimals=6
        )
    )
    instructions.append(transfer_ix)

    # Build and send transaction
    recent_blockhash = client.get_latest_blockhash().value.blockhash

    tx = Transaction(
        recent_blockhash=recent_blockhash,
        fee_payer=executor_keypair.pubkey()
    )
    for ix in instructions:
        tx.add(ix)
    tx.sign(executor_keypair)

    from solana.rpc.types import TxOpts

    tx_sig = client.send_transaction(
        tx,
        executor_keypair,
        opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed)
    )

    if tx_sig.value:
        tx_hash = str(tx_sig.value)
        print(f"   ✅ SPL Transfer successful! Sig: {tx_hash}")
        print(f"   🔗 https://explorer.solana.com/tx/{tx_hash}?cluster=devnet")
        print("="*60)
        return tx_hash  # Return tx_hash instead of boolean
    else:
        print(f"   ❌ SPL Transfer failed: {tx_sig}")
        return None


def execute_swap_and_transfer(client, executor_keypair, recipient_address, amount, asset_in, asset_out):
    """Execute Jupiter swap and transfer result to recipient."""
    print(f"   [Executor] Swap: {amount} {asset_in} -> {asset_out}")

    input_mint = get_token_mint(asset_in)
    output_mint = get_token_mint(asset_out)

    if not input_mint or not output_mint:
        print(f"   ❌ Invalid token: {asset_in} or {asset_out}")
        return False

    # Get Jupiter quote
    quote = get_jupiter_quote(str(input_mint), str(output_mint), amount)
    if not quote:
        print("   ❌ Failed to get Jupiter quote")
        return False

    out_amount = int(quote.get('outAmount', 0))
    print(f"   [Executor] Quote: {amount} {asset_in} -> {out_amount} {asset_out}")

    # Get swap transaction
    swap_response = get_jupiter_swap_transaction(quote, str(executor_keypair.pubkey()))
    if not swap_response or 'swapTransaction' not in swap_response:
        print("   ❌ Failed to get swap transaction")
        return False

    # Execute swap
    swap_tx_data = base64.b64decode(swap_response['swapTransaction'])

    from solders.transaction import VersionedTransaction

    try:
        versioned_tx = VersionedTransaction.from_bytes(swap_tx_data)
        signed_tx = VersionedTransaction(versioned_tx.message, [executor_keypair])

        tx_sig = client.send_transaction(
            signed_tx,
            opts={"skip_preflight": False, "preflight_commitment": Confirmed}
        )

        if tx_sig.value:
            print(f"   ✅ Swap successful! Sig: {tx_sig.value}")
            print(f"   🔗 https://explorer.solana.com/tx/{tx_sig.value}?cluster=devnet")
            print("="*60)
            return True
        else:
            print(f"   ❌ Swap failed: {tx_sig}")
            return False

    except Exception as e:
        print(f"   ❌ Swap error: {e}")
        import traceback
        traceback.print_exc()
        return False


def execute_private_withdrawal(strategy, current_price):
    """Execute a private withdrawal - same as execute_trade for now.

    In production, this would first withdraw from ZK pool, then execute swap.
    For hackathon demo, we execute directly from executor wallet.
    """
    print("\n" + "="*60)
    print(f"✅ PRIVATE EXECUTION: Strategy '{strategy['id']}'")

    # For hackathon: execute directly (ZK pool integration is separate)
    # The privacy is handled by the FHE-encrypted strategy conditions
    return execute_trade(strategy, current_price)


# For testing
if __name__ == "__main__":
    test_strategy = {
        'id': 'test-001',
        'asset_in': 'SOL',
        'asset_out': 'USDC',
        'amount': 0.01,  # 0.01 SOL
        'recipient_address': 'YourTestWalletAddress',
    }

    print("Testing Solana trade executor...")
    execute_trade(test_strategy, 100.0)
