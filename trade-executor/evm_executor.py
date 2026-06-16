"""
EVM trade executor for Siphon Protocol.
Flow: ZK withdraw from vault → Uniswap v3 swap on Sepolia → funds to recipient
"""
import os
import json
import time
from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import geth_poa_middleware

load_dotenv(override=True)

# --- Config ---
ETH_RPC_URL        = os.getenv("ETH_RPC_URL", "https://rpc.sepolia.org")
EVM_EXECUTOR_KEY   = os.getenv("EVM_EXECUTOR_KEY")   # hex private key, no 0x prefix needed
ENTRYPOINT_ADDRESS = os.getenv("ENTRYPOINT_ADDRESS", "0xCd42793bda2E4ca65E47428329A839194DC3eeaD")
UNISWAP_V3_ROUTER  = os.getenv("UNISWAP_V3_ROUTER",  "0x3A9D48AB9751398BbFa63ad67599Bb04e4BdF98b")  # Sepolia UniversalRouter
WETH_ADDRESS       = os.getenv("WETH_ADDRESS",        "0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14")  # Sepolia WETH
NATIVE_ASSET       = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

# Token decimals
TOKEN_DECIMALS = {"ETH": 18, "USDC": 6, "USDT": 6, "WBTC": 8}

# Sepolia token addresses
SEPOLIA_TOKENS = {
    "USDC": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
    "USDT": "0xaa8e23fb1079ea71e0a56f48a2aa51851d8433d0",
    "WBTC": "0x92f3B59a79bFf5dc60c0d59eA13a44D082B2bdFC",
    "WETH": WETH_ADDRESS,
}

# Minimal ABIs
ENTRYPOINT_ABI = json.loads('[{"inputs":[{"internalType":"address","name":"_asset","type":"address"},{"internalType":"address","name":"_recipient","type":"address"},{"internalType":"uint256","name":"_amount","type":"uint256"},{"internalType":"uint256","name":"_stateRoot","type":"uint256"},{"internalType":"uint256","name":"_nullifier","type":"uint256"},{"internalType":"uint256","name":"_newCommitment","type":"uint256"},{"internalType":"uint256[2]","name":"_pA","type":"uint256[2]"},{"internalType":"uint256[2][2]","name":"_pB","type":"uint256[2][2]"},{"internalType":"uint256[2]","name":"_pC","type":"uint256[2]"}],"name":"withdraw","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"_asset","type":"address"},{"internalType":"uint256","name":"_amount","type":"uint256"},{"internalType":"uint256","name":"_stateRoot","type":"uint256"},{"internalType":"uint256","name":"_nullifier","type":"uint256"},{"internalType":"uint256","name":"_newCommitment","type":"uint256"},{"internalType":"address","name":"_recipient","type":"address"},{"internalType":"uint256[2]","name":"_pA","type":"uint256[2]"},{"internalType":"uint256[2][2]","name":"_pB","type":"uint256[2][2]"},{"internalType":"uint256[2]","name":"_pC","type":"uint256[2]"}],"name":"verify","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"}]')

# Custom error selectors for Vault/Entrypoint
CUSTOM_ERRORS = {
    bytes.fromhex("076490f6"): "InvalidZKProof()",
    bytes.fromhex("b6fac030"): "InvalidStateRoot()",
    bytes.fromhex("b115d857"): "NullifierAlreadySpent()",
    bytes.fromhex("dee790fb"): "VaultNotFound()",
    bytes.fromhex("9abc7491"): "InvalidWithdrawalAmount()",
    bytes.fromhex("d92e233d"): "ZeroAddress()",
    bytes.fromhex("8247bd80"): "OnlyEntrypoint()",
    bytes.fromhex("fe9ba5cd"): "InvalidDepositAmount()",
    bytes.fromhex("e346d81d"): "InvalidSwapAmount()",
}

def decode_revert(data: bytes) -> str:
    """Decode a revert reason from raw bytes (custom errors or revert string)."""
    if not data:
        return "empty revert data"
    selector = data[:4]
    if selector in CUSTOM_ERRORS:
        return f"Custom error: {CUSTOM_ERRORS[selector]}"
    # Standard Error(string) = 0x08c379a0
    if selector == bytes.fromhex("08c379a0"):
        try:
            from eth_abi import decode
            msg = decode(["string"], data[4:])[0]
            return f"Revert string: {msg}"
        except Exception:
            pass
    return f"Unknown revert selector: {selector.hex()}, raw: {data.hex()[:80]}"

# UniversalRouter execute(bytes commands, bytes[] inputs, uint256 deadline)
UNIVERSAL_ROUTER_ABI = json.loads('[{"inputs":[{"internalType":"bytes","name":"commands","type":"bytes"},{"internalType":"bytes[]","name":"inputs","type":"bytes[]"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"name":"execute","outputs":[],"stateMutability":"payable","type":"function"}]')

WETH_ABI = json.loads('[{"inputs":[{"internalType":"uint256","name":"wad","type":"uint256"}],"name":"withdraw","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"deposit","outputs":[],"stateMutability":"payable","type":"function"},{"inputs":[{"internalType":"address","name":"guy","type":"address"},{"internalType":"uint256","name":"wad","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"}]')

ERC20_ABI = json.loads('[{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]')


def get_web3() -> Web3:
    w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL))
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    return w3


def get_executor_account(w3: Web3):
    if not EVM_EXECUTOR_KEY:
        raise ValueError("EVM_EXECUTOR_KEY not set")
    key = EVM_EXECUTOR_KEY if EVM_EXECUTOR_KEY.startswith("0x") else "0x" + EVM_EXECUTOR_KEY
    return w3.eth.account.from_key(key)


def send_tx(w3: Web3, account, tx_params: dict, label: str = "") -> str:
    """Sign and send a transaction, return tx hash."""
    tx_params.setdefault("from", account.address)

    t0 = time.monotonic()
    tx_params.setdefault("nonce", w3.eth.get_transaction_count(account.address))
    tx_params.setdefault("chainId", w3.eth.chain_id)
    t_prep = (time.monotonic() - t0) * 1000

    # Gas estimation with 20% buffer (only if not pre-set)
    t_gas = 0.0
    if "gas" not in tx_params:
        tg = time.monotonic()
        try:
            gas_est = w3.eth.estimate_gas(tx_params)
            tx_params["gas"] = int(gas_est * 1.2)
        except Exception as e:
            print(f"   [EVM] Gas estimation failed: {e}, using fallback 2000000")
            tx_params["gas"] = 2_000_000
        t_gas = (time.monotonic() - tg) * 1000

    base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
    priority  = w3.to_wei(2, "gwei")
    tx_params["maxPriorityFeePerGas"] = priority
    tx_params["maxFeePerGas"] = base_fee * 2 + priority

    t_sign = time.monotonic()
    signed = account.sign_transaction(tx_params)
    t_sign = (time.monotonic() - t_sign) * 1000

    t_send = time.monotonic()
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    t_send = (time.monotonic() - t_send) * 1000
    print(f"   [EVM] Tx sent: {tx_hash.hex()}")

    t_mine = time.monotonic()
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    t_mine = (time.monotonic() - t_mine) * 1000

    if receipt.status != 1:
        raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")

    tag = f" [{label}]" if label else ""
    print(f"   [EVM] Tx mined in block {receipt.blockNumber}: {tx_hash.hex()}")
    print(f"   [Benchmark]{tag} nonce/chain={t_prep:.0f}ms | gas_est={t_gas:.0f}ms | sign={t_sign:.0f}ms | broadcast={t_send:.0f}ms | mine={t_mine:.0f}ms | gas_used={receipt.gasUsed}")
    return tx_hash.hex()


def zk_withdraw_from_vault(
    w3: Web3,
    account,
    asset_address: str,
    recipient: str,
    amount_wei: int,
    zk_proof: dict,
) -> str:
    """
    Call Entrypoint.withdraw() with the ZK proof to pull funds out of the vault.
    zk_proof must contain: stateRoot, nullifierHash, newCommitment, pA, pB, pC
    """
    print(f"   [EVM] ZK withdraw: {amount_wei} wei of {asset_address} → {recipient}")

    entrypoint = w3.eth.contract(
        address=Web3.to_checksum_address(ENTRYPOINT_ADDRESS),
        abi=ENTRYPOINT_ABI,
    )

    # Parse proof components — stored as string arrays in the DB
    pA = [int(x) for x in zk_proof["pA"]]
    pB = [[int(x) for x in row] for row in zk_proof["pB"]]
    pC = [int(x) for x in zk_proof["pC"]]

    print(f"   [EVM] Proof components:")
    print(f"         stateRoot:     {zk_proof['stateRoot']}")
    print(f"         nullifierHash: {zk_proof['nullifierHash']}")
    print(f"         newCommitment: {zk_proof['newCommitment']}")
    print(f"         pA: {pA}")
    print(f"         pB: {pB}")
    print(f"         pC: {pC}")

    # Dry-run via raw JSON-RPC eth_call (web3.py misreads void-returning functions)
    import requests as _req
    _withdraw_sel = Web3.keccak(
        text="withdraw(address,address,uint256,uint256,uint256,uint256,uint256[2],uint256[2][2],uint256[2])"
    )[:4]
    from eth_abi import encode as _enc
    _calldata = _withdraw_sel + _enc(
        ["address","address","uint256","uint256","uint256","uint256","uint256[2]","uint256[2][2]","uint256[2]"],
        [Web3.to_checksum_address(asset_address), Web3.to_checksum_address(recipient),
         amount_wei, int(zk_proof["stateRoot"]), int(zk_proof["nullifierHash"]),
         int(zk_proof["newCommitment"]), pA, pB, pC]
    )
    t_dryrun = time.monotonic()
    _resp = _req.post(ETH_RPC_URL, json={
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": ENTRYPOINT_ADDRESS, "from": account.address, "data": "0x" + _calldata.hex(), "gas": "0x4C4B40"}, "latest"]
    }, timeout=15).json()
    t_dryrun = (time.monotonic() - t_dryrun) * 1000
    if "error" in _resp:
        _err_data = _resp["error"].get("data", "")
        _raw = bytes.fromhex(_err_data[2:]) if isinstance(_err_data, str) and _err_data.startswith("0x") else b""
        print(f"   [EVM] ❌ eth_call revert: {decode_revert(_raw)} | raw: {_resp['error']}")
        raise RuntimeError(f"withdraw() would revert: {decode_revert(_raw)}")
    else:
        print(f"   [EVM] ✅ eth_call dry-run OK ({t_dryrun:.0f}ms)")

    # Pre-set gas so build_transaction doesn't call estimate_gas inline
    tx = entrypoint.functions.withdraw(
        Web3.to_checksum_address(asset_address),
        Web3.to_checksum_address(recipient),
        amount_wei,
        int(zk_proof["stateRoot"]),
        int(zk_proof["nullifierHash"]),
        int(zk_proof["newCommitment"]),
        pA,
        pB,
        pC,
    ).build_transaction({"from": account.address, "gas": 2_000_000})

    print(f"   [Benchmark] [zk_withdraw] eth_call_dryrun={t_dryrun:.0f}ms")
    return send_tx(w3, account, tx, label="zk_withdraw")


def swap_eth_to_token(
    w3: Web3,
    account,
    token_out_address: str,
    amount_wei: int,
    recipient: str,
    fee_tier: int = 3000,
) -> str:
    """
    ETH → token swap via Uniswap UniversalRouter (V3_SWAP_EXACT_IN, command=0x00).
    Sends ETH as msg.value; router wraps internally via WRAP_ETH (command=0x0b) then swaps.
    Two-command sequence: WRAP_ETH + V3_SWAP_EXACT_IN, all in one execute() call.
    """
    from eth_abi import encode as _abi_encode
    print(f"   [EVM] Swapping {amount_wei} wei ETH → {token_out_address} via UniversalRouter")

    router = w3.eth.contract(
        address=Web3.to_checksum_address(UNISWAP_V3_ROUTER),
        abi=UNIVERSAL_ROUTER_ABI,
    )

    # UniversalRouter commands (each byte = one command):
    # 0x0b = WRAP_ETH  (wraps msg.value into WETH, sends to router address = 0x01 recipient)
    # 0x00 = V3_SWAP_EXACT_IN
    commands = bytes([0x0b, 0x00])

    # WRAP_ETH input: (address recipient, uint256 amountMin)
    # recipient = 0x0000000000000000000000000000000000000002 (MSG_SENDER constant in UR = router itself)
    ROUTER_ADDR = int(Web3.to_checksum_address(UNISWAP_V3_ROUTER), 16)
    wrap_input = _abi_encode(["address", "uint256"], [Web3.to_checksum_address(UNISWAP_V3_ROUTER), amount_wei])

    # V3_SWAP_EXACT_IN input: (address recipient, uint256 amountIn, uint256 amountOutMin, bytes path, bool payerIsUser)
    # path = tokenIn (3 bytes fee) tokenOut, packed
    path = (
        bytes.fromhex(Web3.to_checksum_address(WETH_ADDRESS)[2:])
        + fee_tier.to_bytes(3, "big")
        + bytes.fromhex(Web3.to_checksum_address(token_out_address)[2:])
    )
    swap_input = _abi_encode(
        ["address", "uint256", "uint256", "bytes", "bool"],
        [Web3.to_checksum_address(recipient), amount_wei, 0, path, False]
    )

    deadline = int(time.time()) + 300

    tx = router.functions.execute(
        commands,
        [wrap_input, swap_input],
        deadline,
    ).build_transaction({
        "from":  account.address,
        "value": amount_wei,
        "gas":   300_000,
    })
    return send_tx(w3, account, tx, label="ur_swap")


def transfer_eth(w3: Web3, account, recipient: str, amount_wei: int) -> str:
    """Direct ETH transfer (no swap needed for ETH→ETH strategies)."""
    print(f"   [EVM] Transferring {amount_wei} wei ETH → {recipient}")
    tx = {
        "to":    Web3.to_checksum_address(recipient),
        "value": amount_wei,
        "data":  b"",
    }
    return send_tx(w3, account, tx)


def execute_evm_trade(strategy: dict, current_price: float) -> str | None:
    """
    Full EVM execution flow:
      1. ZK withdraw from Siphon vault (if zkp_data present)
      2. Swap asset_in → asset_out via Uniswap v3 (if different tokens)
      3. Transfer directly if same token

    Returns tx_hash of final transaction, or None on failure.
    """
    print(f"\n{'='*60}")
    print(f"[EVM Executor] Strategy {strategy.get('id')} | price={current_price:.2f}")

    if not EVM_EXECUTOR_KEY:
        print("   [EVM] ❌ EVM_EXECUTOR_KEY not configured")
        return None

    try:
        w3 = get_web3()
        if not w3.is_connected():
            print(f"   [EVM] ❌ Cannot connect to RPC: {ETH_RPC_URL}")
            return None

        account = get_executor_account(w3)
        print(f"   [EVM] Executor: {account.address}")

        asset_in  = strategy.get("asset_in", "ETH").upper()
        asset_out = strategy.get("asset_out", "USDC").upper()
        amount    = float(strategy.get("amount", 0))
        recipient = strategy.get("recipient_address")
        zkp_data  = strategy.get("zkp_data")

        if not recipient:
            print("   [EVM] ❌ No recipient_address")
            return None

        decimals_in = TOKEN_DECIMALS.get(asset_in, 18)
        amount_wei  = int(amount * (10 ** decimals_in))

        if amount_wei <= 0:
            print(f"   [EVM] ❌ Invalid amount: {amount}")
            return None

        # Step 1: ZK withdraw from vault (if proof provided)
        if zkp_data:
            zk = zkp_data if isinstance(zkp_data, dict) else json.loads(zkp_data)
            if zk.get("pA") and zk.get("stateRoot"):
                proof = zk
            elif isinstance(zk.get("proof"), dict) and zk["proof"].get("pA"):
                proof = zk["proof"]
            else:
                proof = None

            if proof:
                asset_address = NATIVE_ASSET if asset_in == "ETH" else SEPOLIA_TOKENS.get(asset_in, "")
                needs_swap = asset_in != asset_out
                withdraw_to = account.address if needs_swap else recipient
                t_zk = time.monotonic()
                zk_tx = zk_withdraw_from_vault(
                    w3, account,
                    asset_address,
                    withdraw_to,
                    amount_wei,
                    proof,
                )
                t_zk_ms = (time.monotonic() - t_zk) * 1000
                print(f"   [Benchmark] [zk_withdraw_total]   = {t_zk_ms:.0f}ms")
            else:
                print("   [EVM] ⚠️  zkp_data present but no pA/stateRoot — skipping ZK withdraw")
        else:
            print("   [EVM] ℹ️  No zkp_data — executing direct from executor wallet")

        # Step 2: swap or direct transfer
        if asset_in == asset_out:
            tx_hash = zk_tx
        else:
            token_out_address = SEPOLIA_TOKENS.get(asset_out)
            if not token_out_address:
                print(f"   [EVM] ❌ Unknown output token: {asset_out}")
                return None
            t_swap = time.monotonic()
            tx_hash = swap_eth_to_token(w3, account, token_out_address, amount_wei, recipient)
            print(f"   [Benchmark] [swap_total]             = {(time.monotonic()-t_swap)*1000:.0f}ms")

        print(f"   [EVM] ✅ Done: {tx_hash}")
        print("="*60)
        return tx_hash

    except Exception as e:
        print(f"   [EVM] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None
