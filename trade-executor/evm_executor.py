"""
EVM trade executor for Siphon Protocol.
Flow: ZK withdraw from vault → Uniswap v3 swap → funds to recipient.
Chain-specific RPC + contracts come from evm_chain_config (per strategy from_chain).
"""
import os
import json
import time
from typing import Optional
from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import geth_poa_middleware

from evm_chain_config import (
    EvmChainConfig,
    NATIVE_ASSET,
    get_evm_chain_config,
    resolve_execution_chain_id,
)

load_dotenv(override=True)

# Per-chain hot wallets (hex, with or without 0x). EVM_EXECUTOR_KEY is the legacy fallback.
_CHAIN_EXECUTOR_ENV = {
    8453: "BASE_EXECUTOR_KEY",
    11155111: "ETH_SEPOLIA_EXECUTOR_KEY",
}


def get_evm_executor_private_key(chain_id: int) -> str:
    """Resolve signing key for a chain (BASE_EXECUTOR_KEY on Base, etc.)."""
    env_name = _CHAIN_EXECUTOR_ENV.get(chain_id, "EVM_EXECUTOR_KEY")
    key = (os.getenv(env_name) or os.getenv("EVM_EXECUTOR_KEY") or "").strip()
    if not key:
        raise ValueError(
            f"No executor key for chain {chain_id}: set {env_name} or EVM_EXECUTOR_KEY"
        )
    return key if key.startswith("0x") else "0x" + key

# Token decimals
TOKEN_DECIMALS = {"ETH": 18, "USDC": 6, "USDT": 6, "WBTC": 8}

# Minimal ABIs
ENTRYPOINT_ABI = json.loads('[{"inputs":[{"internalType":"address","name":"_asset","type":"address"},{"internalType":"address","name":"_recipient","type":"address"},{"internalType":"uint256","name":"_withdrawnValue","type":"uint256"},{"internalType":"uint256","name":"_stateRoot","type":"uint256"},{"internalType":"uint256[]","name":"_nullifierHashes","type":"uint256[]"},{"internalType":"uint256","name":"_changeCommitment","type":"uint256"},{"internalType":"uint256[2]","name":"_pA","type":"uint256[2]"},{"internalType":"uint256[2][2]","name":"_pB","type":"uint256[2][2]"},{"internalType":"uint256[2]","name":"_pC","type":"uint256[2]"}],"name":"withdraw","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"_asset","type":"address"},{"internalType":"uint256","name":"_amount","type":"uint256"},{"internalType":"uint256","name":"_precommitment","type":"uint256"}],"name":"deposit","outputs":[{"internalType":"uint256","name":"_commitment","type":"uint256"}],"stateMutability":"payable","type":"function"}]')

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

class FatalExecutionError(Exception):
    """Raised when execution should not be retried (e.g. nullifier already spent)."""
    pass

class NullifierSpentSwapFailed(FatalExecutionError):
    """ZK withdraw confirmed (nullifier spent) but swap/bridge failed. Funds are in executor wallet."""
    pass

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

ERC20_ABI = json.loads('[{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]')

UNIVERSAL_ROUTER_ABI = json.loads('[{"inputs":[{"internalType":"bytes","name":"commands","type":"bytes"},{"internalType":"bytes[]","name":"inputs","type":"bytes[]"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"name":"execute","outputs":[],"stateMutability":"payable","type":"function"}]')
# Uniswap SwapRouter02: exactInputSingle with the 7-field struct (no deadline) — payable,
# auto-wraps msg.value when tokenIn == WETH9.
SWAP_ROUTER_02_ABI = json.loads('[{"inputs":[{"components":[{"internalType":"address","name":"tokenIn","type":"address"},{"internalType":"address","name":"tokenOut","type":"address"},{"internalType":"uint24","name":"fee","type":"uint24"},{"internalType":"address","name":"recipient","type":"address"},{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"amountOutMinimum","type":"uint256"},{"internalType":"uint160","name":"sqrtPriceLimitX96","type":"uint160"}],"internalType":"struct IV3SwapRouter.ExactInputSingleParams","name":"params","type":"tuple"}],"name":"exactInputSingle","outputs":[{"internalType":"uint256","name":"amountOut","type":"uint256"}],"stateMutability":"payable","type":"function"}]')
SIMPLE_SWAP_ROUTER_ABI = json.loads('[{"inputs":[{"internalType":"address","name":"pool","type":"address"},{"internalType":"address","name":"weth","type":"address"},{"components":[{"internalType":"address","name":"tokenIn","type":"address"},{"internalType":"address","name":"tokenOut","type":"address"},{"internalType":"uint24","name":"fee","type":"uint24"},{"internalType":"address","name":"recipient","type":"address"},{"internalType":"uint256","name":"deadline","type":"uint256"},{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"amountOutMinimum","type":"uint256"},{"internalType":"uint160","name":"sqrtPriceLimitX96","type":"uint160"}],"internalType":"struct ISwapRouter.ExactInputSingleParams","name":"params","type":"tuple"}],"name":"exactInputSingleWithETH","outputs":[{"internalType":"uint256","name":"amountOut","type":"uint256"}],"stateMutability":"payable","type":"function"}]')
UNISWAP_V3_FACTORY_ABI = json.loads('[{"inputs":[{"internalType":"address","name":"tokenA","type":"address"},{"internalType":"address","name":"tokenB","type":"address"},{"internalType":"uint24","name":"fee","type":"uint24"}],"name":"getPool","outputs":[{"internalType":"address","name":"pool","type":"address"}],"stateMutability":"view","type":"function"}]')
WETH_ABI = json.loads('[{"inputs":[{"internalType":"uint256","name":"wad","type":"uint256"}],"name":"withdraw","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[],"name":"deposit","outputs":[],"stateMutability":"payable","type":"function"},{"inputs":[{"internalType":"address","name":"guy","type":"address"},{"internalType":"uint256","name":"wad","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"}]')

_SIMPLE_SWAP_SELECTOR = Web3.keccak(
    text="exactInputSingleWithETH(address,address,(address,address,uint24,address,uint256,uint256,uint256,uint160))"
)[:4].hex().removeprefix("0x")
# Uniswap SwapRouter02 exactInputSingle((address,address,uint24,address,uint256,uint256,uint160)) = 0x04e45aaf
_SWAP_ROUTER_02_SELECTOR = Web3.keccak(
    text="exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))"
)[:4].hex().removeprefix("0x")


def get_web3(chain: EvmChainConfig) -> Web3:
    w3 = Web3(Web3.HTTPProvider(chain.rpc_url))
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    return w3


def get_executor_account(w3: Web3, chain_id: int):
    return w3.eth.account.from_key(get_evm_executor_private_key(chain_id))


# Base L2: sub-cent priority; 5 gwei exhausts a small executor wallet on heavy txs.
_CHAIN_PRIORITY_GWEI = {
    8453: 0.001,
    11155111: 2.0,
}

# Modest fallbacks when estimate_gas fails (not used when estimate succeeds).
_GAS_FALLBACK = {
    "zk_withdraw": 1_000_000,
    "ur_swap": 400_000,
    "simple_swap": 400_000,
}


def _fee_params(w3: Web3, chain_id: int) -> dict:
    base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
    priority_gwei = _CHAIN_PRIORITY_GWEI.get(chain_id, 2.0)
    priority = w3.to_wei(priority_gwei, "gwei")
    max_fee = max(base_fee * 2 + priority, priority * 2)
    return {"maxPriorityFeePerGas": priority, "maxFeePerGas": max_fee}


def send_tx(w3: Web3, account, tx_params: dict, label: str = "", gas_buffer: float = 1.3) -> str:
    """Sign and send a transaction, return tx hash."""
    tx_params.setdefault("from", account.address)

    t0 = time.monotonic()
    # Use the PENDING nonce so back-to-back txs in one flow (e.g. ZK withdraw →
    # swap) queue sequentially. Using the confirmed nonce here would collide with
    # an in-flight prior tx and the second tx gets dropped.
    tx_params.setdefault("nonce", w3.eth.get_transaction_count(account.address, "pending"))
    tx_params.setdefault("chainId", w3.eth.chain_id)
    chain_id = int(tx_params["chainId"])
    t_prep = (time.monotonic() - t0) * 1000

    # Estimate gas without a preset cap, then add a small buffer (~30%).
    t_gas = 0.0
    tg = time.monotonic()
    gas_floor = int(tx_params.pop("gas", 0) or 0)
    estimate_params = dict(tx_params)
    try:
        est = w3.eth.estimate_gas(estimate_params)
        gas_limit = max(gas_floor, int(est * gas_buffer))
        print(f"   [EVM] Gas estimate: {est} → limit {gas_limit} ({label or 'tx'})")
    except Exception as e:
        gas_limit = gas_floor or _GAS_FALLBACK.get(label, 600_000)
        print(f"   [EVM] Gas estimation failed: {e}, using fallback {gas_limit}")
    tx_params["gas"] = gas_limit
    t_gas = (time.monotonic() - tg) * 1000

    fees = _fee_params(w3, chain_id)
    max_cost = gas_limit * fees["maxFeePerGas"]
    balance = w3.eth.get_balance(account.address)
    if balance < max_cost:
        raise ValueError(
            f"Executor wallet {account.address} needs ~{max_cost / 1e18:.6f} ETH for gas "
            f"(have {balance / 1e18:.6f} ETH on chain {chain_id}). Top up the executor wallet."
        )

    tx_params["maxPriorityFeePerGas"] = fees["maxPriorityFeePerGas"]
    tx_params["maxFeePerGas"] = fees["maxFeePerGas"]

    # The executor wallet is an EIP-7702 delegated account (code starts 0xef0100…),
    # which the Base sequencer caps at 1 in-flight tx. After a prior tx in the same
    # flow (e.g. the ZK withdraw) mines, it can briefly linger in the txpool, so a
    # back-to-back send (the swap) is rejected with
    #   "in-flight transaction limit reached for delegated accounts" (code -32000).
    # Retry with backoff, refreshing the pending nonce each attempt (it advances once
    # the prior tx fully clears).
    t_sign = 0.0
    t_send = time.monotonic()
    tx_hash = None
    for attempt in range(6):
        try:
            ts = time.monotonic()
            signed = account.sign_transaction(tx_params)
            t_sign = (time.monotonic() - ts) * 1000
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            break
        except Exception as e:
            if "in-flight transaction limit" in str(e) and attempt < 5:
                wait = 2 + attempt * 2
                print(f"   [EVM] ⏳ delegated-account in-flight limit; retry {attempt+1}/5 in {wait}s")
                time.sleep(wait)
                tx_params["nonce"] = w3.eth.get_transaction_count(account.address, "pending")
                continue
            raise
    if tx_hash is None:
        raise RuntimeError("Failed to broadcast tx: in-flight transaction limit not cleared after retries")
    t_send = (time.monotonic() - t_send) * 1000
    print(f"   [EVM] Tx sent: {tx_hash.hex()}")

    t_mine = time.monotonic()
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=600)
    t_mine = (time.monotonic() - t_mine) * 1000

    if receipt.status != 1:
        revert_reason = _replay_revert_reason(w3, tx_hash, receipt.blockNumber)
        detail = f" ({revert_reason})" if revert_reason else ""
        raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}{detail}")

    tag = f" [{label}]" if label else ""
    print(f"   [EVM] Tx mined in block {receipt.blockNumber}: {tx_hash.hex()}")
    print(f"   [Benchmark]{tag} nonce/chain={t_prep:.0f}ms | gas_est={t_gas:.0f}ms | sign={t_sign:.0f}ms | broadcast={t_send:.0f}ms | mine={t_mine:.0f}ms | gas_used={receipt.gasUsed}")
    return tx_hash.hex()


def _replay_revert_reason(w3: Web3, tx_hash, block_number: int) -> str:
    """Best-effort decode of an on-chain revert."""
    try:
        tx = w3.eth.get_transaction(tx_hash)
        w3.eth.call(
            {
                "from": tx["from"],
                "to": tx["to"],
                "data": tx["input"],
                "value": tx["value"],
                "gas": tx["gas"],
            },
            block_number - 1,
        )
        return ""
    except Exception as exc:
        data = getattr(exc, "data", None)
        if isinstance(data, str) and data.startswith("0x") and len(data) > 10:
            return decode_revert(bytes.fromhex(data[2:]))
        if isinstance(data, dict):
            for val in data.values():
                if isinstance(val, str) and val.startswith("0x") and len(val) > 10:
                    return decode_revert(bytes.fromhex(val[2:]))
        msg = str(exc)
        if "revert" in msg.lower() or "execution" in msg.lower():
            return msg
        return ""


def _router_uses_simple_swap(w3: Web3, router_address: str) -> bool:
    code = w3.eth.get_code(Web3.to_checksum_address(router_address)).hex().lower()
    return _SIMPLE_SWAP_SELECTOR in code


def _router_uses_swap_router_02(w3: Web3, router_address: str) -> bool:
    """Uniswap SwapRouter02 exposes exactInputSingle but not the UniversalRouter execute()."""
    code = w3.eth.get_code(Web3.to_checksum_address(router_address)).hex().lower()
    return _SWAP_ROUTER_02_SELECTOR in code and "3593564c" not in code


def _resolve_v3_pool(
    w3: Web3,
    chain: EvmChainConfig,
    token_in: str,
    token_out: str,
    fee_tier: int,
) -> tuple[str, int]:
    factory = w3.eth.contract(
        address=Web3.to_checksum_address(chain.uniswap_v3_factory),
        abi=UNISWAP_V3_FACTORY_ABI,
    )
    token_a = Web3.to_checksum_address(token_in)
    token_b = Web3.to_checksum_address(token_out)
    for fee in dict.fromkeys([fee_tier, 3000, 500, 10000, 100]):
        pool = factory.functions.getPool(token_a, token_b, fee).call()
        if pool and str(pool).lower() != "0x0000000000000000000000000000000000000000":
            return Web3.to_checksum_address(pool), fee
    raise RuntimeError(
        f"No Uniswap V3 pool for {token_in} → {token_out} on {chain.name} (tried common fee tiers)"
    )


def _extract_nullifier_hash(zk_proof: dict) -> str:
    """Extract the single nullifier hash the contract expects.

    Frontend sends nullifierHashes[] (array) from the multi-note circuit.
    The deployed Entrypoint takes a single _nullifier — use index [0].
    Fallback to the singular nullifierHash field for backward compatibility.
    """
    hashes = zk_proof.get("nullifierHashes")
    if hashes and isinstance(hashes, list) and len(hashes) > 0:
        return str(hashes[0])
    return str(zk_proof.get("nullifierHash", ""))


def _extract_change_commitment(zk_proof: dict) -> str:
    """Extract the change commitment.

    Frontend sends changeCommitment (circuit output). newCommitment is an alias
    kept for backward compatibility with older proof shapes.
    """
    return str(zk_proof.get("changeCommitment") or zk_proof.get("newCommitment", ""))


def zk_withdraw_from_vault(
    w3: Web3,
    account,
    chain: EvmChainConfig,
    asset_address: str,
    recipient: str,
    amount_wei: int,
    zk_proof: dict,
) -> str:
    """
    Call Entrypoint.withdraw() with the ZK proof to pull funds out of the vault.
    zk_proof must contain: stateRoot, nullifierHashes (array), changeCommitment, pA, pB, pC
    """
    print(f"   [EVM] ZK withdraw on {chain.name} ({chain.chain_id}): {amount_wei} wei of {asset_address} → {recipient}")

    entrypoint = w3.eth.contract(
        address=Web3.to_checksum_address(chain.entrypoint),
        abi=ENTRYPOINT_ABI,
    )

    nullifier_hash   = _extract_nullifier_hash(zk_proof)
    change_commitment = _extract_change_commitment(zk_proof)

    if not nullifier_hash:
        raise FatalExecutionError("zk_proof missing nullifierHashes / nullifierHash")
    if not change_commitment:
        raise FatalExecutionError("zk_proof missing changeCommitment / newCommitment")

    # Parse proof components — stored as string arrays in the DB
    pA = [int(x) for x in zk_proof["pA"]]
    pB = [[int(x) for x in row] for row in zk_proof["pB"]]
    pC = [int(x) for x in zk_proof["pC"]]

    print(f"   [EVM] Proof components:")
    print(f"         stateRoot:        {zk_proof['stateRoot']}")
    print(f"         nullifierHash[0]: {nullifier_hash}")
    print(f"         changeCommitment: {change_commitment}")
    print(f"         pA: {pA}")
    print(f"         pB: {pB}")
    print(f"         pC: {pC}")

    # All nullifier hashes as a list (contract takes uint256[])
    nullifier_hashes_list = [int(nullifier_hash)]

    # Dry-run via raw JSON-RPC eth_call (web3.py misreads void-returning functions)
    import requests as _req
    _withdraw_sel = Web3.keccak(
        text="withdraw(address,address,uint256,uint256,uint256[],uint256,uint256[2],uint256[2][2],uint256[2])"
    )[:4]
    from eth_abi import encode as _enc
    _calldata = _withdraw_sel + _enc(
        ["address","address","uint256","uint256","uint256[]","uint256","uint256[2]","uint256[2][2]","uint256[2]"],
        [Web3.to_checksum_address(asset_address), Web3.to_checksum_address(recipient),
         amount_wei, int(zk_proof["stateRoot"]), nullifier_hashes_list,
         int(change_commitment), pA, pB, pC]
    )
    t_dryrun = time.monotonic()
    _resp = _req.post(chain.rpc_url, json={
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": chain.entrypoint, "from": account.address, "data": "0x" + _calldata.hex(), "gas": "0x4C4B40"}, "latest"]
    }, timeout=15).json()
    t_dryrun = (time.monotonic() - t_dryrun) * 1000
    if "error" in _resp:
        _err_data = _resp["error"].get("data", "")
        _raw = bytes.fromhex(_err_data[2:]) if isinstance(_err_data, str) and _err_data.startswith("0x") else b""
        _reason = decode_revert(_raw)
        print(f"   [EVM] ❌ eth_call revert: {_reason} | raw: {_resp['error']}")
        # Fatal errors — retrying will never succeed
        _fatal_errors = {"NullifierAlreadySpent()", "InvalidZKProof()", "ZeroAddress()", "InvalidWithdrawalAmount()"}
        if any(e in _reason for e in _fatal_errors):
            raise FatalExecutionError(f"withdraw() would revert: {_reason}")
        raise RuntimeError(f"withdraw() would revert: {_reason}")
    else:
        print(f"   [EVM] ✅ eth_call dry-run OK ({t_dryrun:.0f}ms)")

    # Pre-set gas so build_transaction doesn't call estimate_gas inline
    tx = entrypoint.functions.withdraw(
        Web3.to_checksum_address(asset_address),
        Web3.to_checksum_address(recipient),
        amount_wei,
        int(zk_proof["stateRoot"]),
        nullifier_hashes_list,
        int(change_commitment),
        pA,
        pB,
        pC,
    ).build_transaction({"from": account.address})

    print(f"   [Benchmark] [zk_withdraw] eth_call_dryrun={t_dryrun:.0f}ms")
    return send_tx(w3, account, tx, label="zk_withdraw", gas_buffer=1.35)





def _swap_eth_to_token_universal(
    w3: Web3,
    account,
    chain: EvmChainConfig,
    token_out_address: str,
    amount_wei: int,
    recipient: str,
    fee_tier: int,
) -> str:
    """ETH → token via Uniswap Universal Router: WRAP_ETH + V3_SWAP_EXACT_IN."""
    from eth_abi import encode as _abi_encode

    router = w3.eth.contract(
        address=Web3.to_checksum_address(chain.uniswap_v3_router),
        abi=UNIVERSAL_ROUTER_ABI,
    )

    commands = bytes([0x0b, 0x00])
    wrap_input = _abi_encode(
        ["address", "uint256"],
        [Web3.to_checksum_address(chain.uniswap_v3_router), amount_wei],
    )
    path = (
        bytes.fromhex(Web3.to_checksum_address(chain.weth)[2:])
        + fee_tier.to_bytes(3, "big")
        + bytes.fromhex(Web3.to_checksum_address(token_out_address)[2:])
    )
    swap_input = _abi_encode(
        ["address", "uint256", "uint256", "bytes", "bool"],
        [Web3.to_checksum_address(recipient), amount_wei, 0, path, False],
    )

    tx = router.functions.execute(
        commands,
        [wrap_input, swap_input],
        int(time.time()) + 300,
    ).build_transaction({
        "from": account.address,
        "value": amount_wei,
        "gas": 300_000,
    })
    return send_tx(w3, account, tx, label="ur_swap")


def _swap_eth_to_token_simple(
    w3: Web3,
    account,
    chain: EvmChainConfig,
    token_out_address: str,
    amount_wei: int,
    recipient: str,
    fee_tier: int,
) -> str:
    """ETH → token via deployed SimpleSwapRouter (Sepolia entrypoint router)."""
    pool, fee = _resolve_v3_pool(
        w3, chain, chain.weth, token_out_address, fee_tier
    )
    router = w3.eth.contract(
        address=Web3.to_checksum_address(chain.uniswap_v3_router),
        abi=SIMPLE_SWAP_ROUTER_ABI,
    )
    params = (
        Web3.to_checksum_address(chain.weth),
        Web3.to_checksum_address(token_out_address),
        fee,
        Web3.to_checksum_address(recipient),
        int(time.time()) + 300,
        amount_wei,
        0,
        0,
    )
    tx = router.functions.exactInputSingleWithETH(
        pool,
        Web3.to_checksum_address(chain.weth),
        params,
    ).build_transaction({
        "from": account.address,
        "value": amount_wei,
        "gas": 350_000,
    })
    return send_tx(w3, account, tx, label="simple_swap")


def _swap_eth_to_token_swap_router_02(
    w3: Web3,
    account,
    chain: EvmChainConfig,
    token_out_address: str,
    amount_wei: int,
    recipient: str,
    fee_tier: int,
) -> str:
    """ETH → token via Uniswap SwapRouter02.exactInputSingle (payable, auto-wraps ETH)."""
    # Pick a fee tier that actually has a pool (preferring the requested one).
    _, fee = _resolve_v3_pool(w3, chain, chain.weth, token_out_address, fee_tier)
    router = w3.eth.contract(
        address=Web3.to_checksum_address(chain.uniswap_v3_router),
        abi=SWAP_ROUTER_02_ABI,
    )
    params = (
        Web3.to_checksum_address(chain.weth),       # tokenIn (msg.value gets wrapped)
        Web3.to_checksum_address(token_out_address),  # tokenOut
        fee,
        Web3.to_checksum_address(recipient),
        amount_wei,
        0,                                            # amountOutMinimum
        0,                                            # sqrtPriceLimitX96
    )
    tx = router.functions.exactInputSingle(params).build_transaction({
        "from": account.address,
        "value": amount_wei,
        "gas": 300_000,
    })
    return send_tx(w3, account, tx, label="ur_swap")


def swap_eth_to_token(
    w3: Web3,
    account,
    chain: EvmChainConfig,
    token_out_address: str,
    amount_wei: int,
    recipient: str,
    fee_tier: Optional[int] = None,
) -> str:
    """ETH → ERC20 using the swap router configured for this chain."""
    fee = fee_tier if fee_tier is not None else chain.swap_fee_tier
    router_addr = chain.uniswap_v3_router
    if _router_uses_simple_swap(w3, router_addr):
        print(
            f"   [EVM] Swapping on {chain.name} ({chain.chain_id}): "
            f"{amount_wei} wei ETH → {token_out_address} via SimpleSwapRouter"
        )
        return _swap_eth_to_token_simple(
            w3, account, chain, token_out_address, amount_wei, recipient, fee
        )

    if _router_uses_swap_router_02(w3, router_addr):
        print(
            f"   [EVM] Swapping on {chain.name} ({chain.chain_id}): "
            f"{amount_wei} wei ETH → {token_out_address} via SwapRouter02"
        )
        return _swap_eth_to_token_swap_router_02(
            w3, account, chain, token_out_address, amount_wei, recipient, fee
        )

    print(
        f"   [EVM] Swapping on {chain.name} ({chain.chain_id}): "
        f"{amount_wei} wei ETH → {token_out_address} via UniversalRouter"
    )
    return _swap_eth_to_token_universal(
        w3, account, chain, token_out_address, amount_wei, recipient, fee
    )


def transfer_eth(w3: Web3, account, recipient: str, amount_wei: int) -> str:
    """Direct ETH transfer (no swap needed for ETH→ETH strategies)."""
    print(f"   [EVM] Transferring {amount_wei} wei ETH → {recipient}")
    tx = {
        "to":    Web3.to_checksum_address(recipient),
        "value": amount_wei,
        "data":  b"",
    }
    return send_tx(w3, account, tx)


def erc20_balance_of(w3: Web3, token_address: str, owner: str) -> int:
    token = w3.eth.contract(
        address=Web3.to_checksum_address(token_address),
        abi=ERC20_ABI,
    )
    return int(token.functions.balanceOf(Web3.to_checksum_address(owner)).call())


# Transfer(address indexed from, address indexed to, uint256 value)
_TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)")


def token_received_in_tx(w3: Web3, tx_hash: str, token_address: str, recipient: str) -> int:
    """Sum the token amount delivered to `recipient` in `tx_hash` by reading the swap receipt's
    ERC20 Transfer events. Deterministic — avoids the read-after-write race where a balanceOf
    right after the swap mines can hit an un-synced RPC replica and report a 0 delta."""
    receipt = w3.eth.get_transaction_receipt(tx_hash)
    token = Web3.to_checksum_address(token_address)
    recip = Web3.to_checksum_address(recipient)
    total = 0
    for log in receipt["logs"]:
        if Web3.to_checksum_address(log["address"]) != token:
            continue
        topics = log["topics"]
        if len(topics) < 3 or bytes(topics[0]) != bytes(_TRANSFER_TOPIC):
            continue
        to_addr = Web3.to_checksum_address("0x" + bytes(topics[2])[-20:].hex())
        if to_addr == recip:
            total += int(bytes(log["data"]).hex() or "0", 16)
    return total


def deposit_to_vault(
    w3: Web3,
    account,
    chain: EvmChainConfig,
    token_address: str,
    amount_wei: int,
    precommitment: int,
) -> str:
    """Re-deposit ERC20 funds the executor holds into the Siphon vault as a private note.

    The Entrypoint pulls `amount_wei` from the executor (msg.sender) into the asset's vault and
    mints commitment = Poseidon(actualReceived, precommitment) on-chain. The user — who alone
    knows the nullifier/secret behind `precommitment` — can later withdraw this note themselves.
    """
    token = w3.eth.contract(
        address=Web3.to_checksum_address(token_address),
        abi=ERC20_ABI,
    )
    entrypoint = w3.eth.contract(
        address=Web3.to_checksum_address(chain.entrypoint),
        abi=ENTRYPOINT_ABI,
    )

    entrypoint_addr = Web3.to_checksum_address(chain.entrypoint)

    # Approve the Entrypoint to pull the tokens (Entrypoint routes them straight to the vault).
    # Set explicit gas so build_transaction does NOT run an inline estimate_gas (which would hit
    # the same read-after-write RPC lag and could simulate against stale state).
    approve_tx = token.functions.approve(
        entrypoint_addr,
        amount_wei,
    ).build_transaction({"from": account.address, "gas": 120_000})
    send_tx(w3, account, approve_tx, label="approve_deposit")

    # Wait until the executor's RPC actually reflects the new allowance before building the
    # deposit. Without this, the deposit's gas pre-flight can simulate against allowance=0 on an
    # un-synced replica and revert with "transfer amount exceeds allowance".
    for attempt in range(12):
        try:
            allowance = token.functions.allowance(account.address, entrypoint_addr).call()
        except Exception:
            allowance = 0
        if allowance >= amount_wei:
            break
        time.sleep(1)
    else:
        print(f"   [EVM] ⚠️ allowance still {allowance} < {amount_wei} after wait; attempting deposit anyway")

    print(
        f"   [EVM] Re-depositing {amount_wei} of {token_address} into Siphon vault "
        f"as note precommitment={precommitment}"
    )
    # Explicit gas again: skip the inline estimate and give send_tx a gas floor.
    deposit_tx = entrypoint.functions.deposit(
        Web3.to_checksum_address(token_address),
        amount_wei,
        int(precommitment),
    ).build_transaction({"from": account.address, "value": 0, "gas": 500_000})
    return send_tx(w3, account, deposit_tx, label="vault_deposit")


def deposit_native_to_vault(
    w3: Web3,
    account,
    chain: EvmChainConfig,
    amount_wei: int,
    precommitment: int,
) -> str:
    """Deposit native ETH the executor holds into the native Siphon vault as a private note.
    Payable deposit — no approve; mints Poseidon(amount, precommitment) on-chain. Used by the
    fee-vault sweep to shield accrued protocol fees."""
    entrypoint = w3.eth.contract(
        address=Web3.to_checksum_address(chain.entrypoint),
        abi=ENTRYPOINT_ABI,
    )
    deposit_tx = entrypoint.functions.deposit(
        Web3.to_checksum_address(NATIVE_ASSET),
        amount_wei,
        int(precommitment),
    ).build_transaction({"from": account.address, "value": amount_wei, "gas": 500_000})
    return send_tx(w3, account, deposit_tx, label="fee_vault_deposit")


def execute_evm_trade(strategy: dict, current_price: float, on_withdraw_confirmed=None) -> Optional[str]:
    """
    Full EVM execution flow:
      1. ZK withdraw from Siphon vault (if zkp_data present)
      2. Swap asset_in → asset_out via Uniswap v3 (if different tokens)
      3. Transfer directly if same token

    on_withdraw_confirmed: optional callable(zk_tx_hash) fired the moment the ZK
    withdraw receipt confirms on-chain (status==1), before the swap. This is the
    point at which the nullifier is genuinely spent, so the caller marks the note
    spent here — a later swap failure must NOT revert it.

    Returns tx_hash of final transaction, or None on failure.
    """
    print(f"\n{'='*60}")
    print(f"[EVM Executor] Strategy {strategy.get('id')} | price={current_price:.2f}")

    try:
        exec_chain_id = resolve_execution_chain_id(strategy)
        get_evm_executor_private_key(exec_chain_id)
    except ValueError as e:
        print(f"   [EVM] ❌ {e}")
        return None

    try:
        chain = get_evm_chain_config(exec_chain_id)
        w3 = get_web3(chain)
        if not w3.is_connected():
            print(f"   [EVM] ❌ Cannot connect to RPC for chain {exec_chain_id}: {chain.rpc_url}")
            return None

        on_chain_id = w3.eth.chain_id
        if on_chain_id != chain.chain_id:
            print(f"   [EVM] ❌ RPC chain mismatch: expected {chain.chain_id}, got {on_chain_id}")
            return None

        account = get_executor_account(w3, exec_chain_id)
        print(f"   [EVM] Chain: {chain.name} ({chain.chain_id}) | Entrypoint: {chain.entrypoint}")
        print(f"   [EVM] Executor: {account.address}")

        asset_in  = strategy.get("asset_in", "ETH").upper()
        asset_out = strategy.get("asset_out", "USDC").upper()
        amount    = float(strategy.get("amount", 0))
        recipient = strategy.get("recipient_address")
        zkp_data  = strategy.get("zkp_data")
        output_mode = (strategy.get("output_mode") or "address").lower()
        output_precommitment = strategy.get("output_precommitment")

        # In 'vault' mode the swap output is re-deposited into the Siphon vault as a private
        # note (no external recipient). The user later withdraws it themselves.
        is_vault_output = output_mode == "vault"

        if is_vault_output and not output_precommitment:
            print("   [EVM] ❌ output_mode=vault but no output_precommitment")
            return None
        if not is_vault_output and not recipient:
            print("   [EVM] ❌ No recipient_address")
            return None

        decimals_in = TOKEN_DECIMALS.get(asset_in, 18)
        from decimal import Decimal, ROUND_DOWN
        amount_wei  = int(Decimal(str(amount)) * Decimal(10 ** decimals_in))

        if amount_wei <= 0:
            print(f"   [EVM] ❌ Invalid amount: {amount}")
            return None

        to_chain   = strategy.get('to_chain', str(exec_chain_id))
        from_chain = strategy.get('from_chain', str(exec_chain_id))
        is_cross_chain = str(to_chain) != str(from_chain)

        # Step 1: ZK withdraw from vault to executor wallet
        zk_tx = None
        if zkp_data:
            zk = zkp_data if isinstance(zkp_data, dict) else json.loads(zkp_data)
            if zk.get("pA") and zk.get("stateRoot"):
                proof = zk
            elif isinstance(zk.get("proof"), dict) and zk["proof"].get("pA"):
                proof = zk["proof"]
            else:
                proof = None

            if proof:
                asset_address = chain.token_address(asset_in)
                t_zk = time.monotonic()
                zk_tx = zk_withdraw_from_vault(
                    w3, account,
                    chain,
                    asset_address,
                    account.address,  # withdraw to executor, swap/bridge handles final delivery
                    amount_wei,
                    proof,
                )
                t_zk_ms = (time.monotonic() - t_zk) * 1000
                print(f"   [Benchmark] [zk_withdraw_total]   = {t_zk_ms:.0f}ms")
                # send_tx() inside zk_withdraw_from_vault only returns after the
                # receipt confirmed with status==1, so reaching here means the
                # withdraw is on-chain and the nullifier is spent. Mark it NOW,
                # before the swap, so a swap failure can't make it spendable again.
                if zk_tx and on_withdraw_confirmed:
                    try:
                        on_withdraw_confirmed(zk_tx)
                    except Exception as cb_err:
                        # Never let bookkeeping break the on-chain flow; the
                        # NullifierSpentSwapFailed path is the safety net.
                        print(f"   [EVM] ⚠️ on_withdraw_confirmed callback error: {cb_err}")
            else:
                print("   [EVM] ⚠️  zkp_data present but no pA/stateRoot — skipping ZK withdraw")
        else:
            print("   [EVM] ℹ️  No zkp_data — executing direct from executor wallet")

        # ── Protocol fee (Part B: execution fee) ──────────────────────────────────────────────
        # Deducted in the INPUT asset from the amount the executor now custodies. The fee simply
        # STAYS in the executor wallet (zero extra gas) and is recorded as a FeeAccrual for a later
        # sweep into the Siphon fee-vault (accrue-then-sweep). The user receives net = amount - fee.
        net_wei = amount_wei
        try:
            from fee_calc import compute_execution_fee_wei
            fee_wei = compute_execution_fee_wei(amount_wei, decimals_in, float(current_price or 0))
            if fee_wei > 0:
                net_wei = amount_wei - fee_wei
                print(f"   [Fee] Execution fee = {fee_wei} wei {asset_in} "
                      f"(net {net_wei}); retained in executor wallet for fee-vault sweep")
                from fee_ledger import record_fee
                record_fee(exec_chain_id, asset_in, fee_wei, kind='execution',
                           strategy_id=strategy.get('id'))
        except Exception as fee_err:
            print(f"   [Fee] ⚠️ fee skipped (executing full amount): {fee_err}")
            net_wei = amount_wei

        # Step 2: cross-chain → Li.Fi; same-chain swap → Uniswap; same asset → direct transfer
        t_swap = time.monotonic()
        try:
            if is_cross_chain:
                from_token = chain.token_address(asset_in)
                try:
                    to_token = get_evm_chain_config(to_chain).token_address(asset_out)
                except ValueError:
                    to_token = NATIVE_ASSET if asset_out == "ETH" else asset_out
                from lifi_executor import execute_lifi_swap
                tx_hash = execute_lifi_swap(
                    from_chain=str(from_chain),
                    to_chain=str(to_chain),
                    from_token=from_token,
                    to_token=to_token,
                    from_amount_wei=net_wei,
                    recipient=recipient,
                    rpc_url=chain.rpc_url,
                )
                print(f"   [Benchmark] [lifi_swap_total]             = {(time.monotonic()-t_swap)*1000:.0f}ms")
            elif asset_in == "ETH" and asset_out != "ETH":
                token_out_address = chain.token_address(asset_out)
                if is_vault_output:
                    # Swap to the executor wallet, then re-deposit the actual output into the
                    # asset_out vault as a private note (Poseidon(amountOut, precommitment)).
                    swap_tx = swap_eth_to_token(
                        w3, account, chain, token_out_address, net_wei, account.address
                    )
                    print(f"   [EVM] Swap tx (to executor): {swap_tx}")
                    # Read the exact amount delivered from the swap receipt (Transfer event) — robust
                    # against RPC read-after-write lag that a balanceOf snapshot can hit.
                    received = token_received_in_tx(w3, swap_tx, token_out_address, account.address)
                    if received <= 0:
                        # Fallback: the executor's full token balance (it should hold only this swap's output).
                        received = erc20_balance_of(w3, token_out_address, account.address)
                    if received <= 0:
                        raise RuntimeError(f"Swap produced no {asset_out} (received {received})")
                    print(f"   [EVM] {asset_out} received from swap: {received}")
                    print(f"   [Benchmark] [uniswap_swap_total]          = {(time.monotonic()-t_swap)*1000:.0f}ms")
                    tx_hash = deposit_to_vault(
                        w3, account, chain, token_out_address, received, int(output_precommitment)
                    )
                else:
                    tx_hash = swap_eth_to_token(w3, account, chain, token_out_address, net_wei, recipient)
                    print(f"   [Benchmark] [uniswap_swap_total]          = {(time.monotonic()-t_swap)*1000:.0f}ms")
            else:
                tx_hash = transfer_eth(w3, account, recipient, net_wei)
                print(f"   [Benchmark] [direct_transfer_total]       = {(time.monotonic()-t_swap)*1000:.0f}ms")
        except Exception as swap_err:
            if zk_tx:
                # Nullifier is spent on-chain — mark fatal so scheduler doesn't retry with same nullifier
                raise NullifierSpentSwapFailed(
                    f"ZK withdraw confirmed ({zk_tx}) but swap failed: {swap_err}. "
                    f"Funds ({amount_wei} wei) are in executor wallet {account.address}."
                ) from swap_err
            raise

        print(f"   [EVM] ✅ Done: {tx_hash}")
        print("="*60)
        return tx_hash

    except FatalExecutionError:
        raise
    except ValueError as e:
        print(f"   [EVM] ❌ Config error: {e}")
        return None
    except Exception as e:
        print(f"   [EVM] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None


# Entrypoint.swap(...) ABI — atomic on-chain Vault.swap used for TWAP/grid LEGS. Each leg spends
# ONE slice note (single nullifier, 9-signal swap circuit) and the vault swaps from its own funds.
ENTRYPOINT_SWAP_ABI = json.loads('[{"inputs":[{"internalType":"address","name":"_pool","type":"address"},{"internalType":"address","name":"_srcToken","type":"address"},{"internalType":"address","name":"_dstToken","type":"address"},{"internalType":"address","name":"_recipient","type":"address"},{"internalType":"uint256","name":"_amountIn","type":"uint256"},{"internalType":"uint256","name":"_minAmountOut","type":"uint256"},{"internalType":"uint24","name":"_fee","type":"uint24"},{"internalType":"uint256","name":"_deadline","type":"uint256"},{"components":[{"internalType":"uint256","name":"stateRoot","type":"uint256"},{"internalType":"uint256","name":"nullifier","type":"uint256"},{"internalType":"uint256","name":"newCommitment","type":"uint256"},{"internalType":"address","name":"recipient","type":"address"},{"internalType":"address","name":"pool","type":"address"},{"internalType":"address","name":"dstToken","type":"address"},{"internalType":"uint256","name":"fee","type":"uint256"},{"internalType":"uint256","name":"minAmountOut","type":"uint256"},{"internalType":"uint256[2]","name":"pA","type":"uint256[2]"},{"internalType":"uint256[2][2]","name":"pB","type":"uint256[2][2]"},{"internalType":"uint256[2]","name":"pC","type":"uint256[2]"}],"internalType":"struct IVault.ZKProof","name":"_zkProof","type":"tuple"}],"name":"swap","outputs":[],"stateMutability":"payable","type":"function"}]')


def execute_evm_leg_swap(strategy: dict, current_price: float, on_withdraw_confirmed=None) -> Optional[str]:
    """Execute ONE TWAP/grid leg as an atomic on-chain Vault.swap (Entrypoint.swap).

    The leg's zkp_data is a 9-signal swap proof (one slice note → one swap), produced by the
    browser's generateSwapProof. We rebuild the IVault.ZKProof struct from it and submit
    Entrypoint.swap; the vault verifies the proof, spends the slice nullifier, inserts the change
    note, and swaps from its own funds to the proof-bound recipient. No funds touch the executor.
    """
    exec_chain_id = resolve_execution_chain_id(strategy)
    chain = get_evm_chain_config(exec_chain_id)
    w3 = get_web3(chain)
    if not w3.is_connected():
        print(f"   [EVM] ❌ Cannot connect to RPC for chain {exec_chain_id}")
        return None
    account = get_executor_account(w3, exec_chain_id)

    zkp = strategy.get("zkp_data")
    zk = zkp if isinstance(zkp, dict) else json.loads(zkp)
    if isinstance(zk.get("proof"), dict):
        zk = zk["proof"]

    nullifiers = zk.get("nullifierHashes") or ([zk["nullifierHash"]] if zk.get("nullifierHash") else [])
    if not nullifiers:
        raise FatalExecutionError("leg swap proof missing nullifierHashes")
    nullifier = int(nullifiers[0])

    asset_in = strategy.get("asset_in", "ETH").upper()
    src_token = chain.token_address(asset_in)
    # Native sentinel → WETH for the on-chain swap path (vault wraps internally).
    src_for_call = chain.weth if src_token == NATIVE_ASSET else src_token

    pool      = Web3.to_checksum_address(zk["pool"])
    dst_token = Web3.to_checksum_address(zk["dstToken"])
    recipient = Web3.to_checksum_address(zk["recipient"])
    fee       = int(zk["fee"])
    min_out   = int(zk["minAmountOut"])
    amount_in = int(zk["amountIn"])
    deadline  = int(time.time()) + 300

    pA = [int(x) for x in zk["pA"]]
    pB = [[int(x) for x in row] for row in zk["pB"]]
    pC = [int(x) for x in zk["pC"]]

    zkproof_struct = (
        int(zk["stateRoot"]), nullifier,
        int(zk.get("changeCommitment") or zk.get("newCommitment")),
        recipient, pool, dst_token, fee, min_out, pA, pB, pC,
    )

    entrypoint = w3.eth.contract(address=Web3.to_checksum_address(chain.entrypoint), abi=ENTRYPOINT_SWAP_ABI)
    print(f"   [EVM] Leg swap on {chain.name}: {amount_in} {asset_in} → {dst_token} (pool {pool}, fee {fee})")
    tx = entrypoint.functions.swap(
        pool, Web3.to_checksum_address(src_for_call), dst_token, recipient,
        amount_in, min_out, fee, deadline, zkproof_struct,
    ).build_transaction({"from": account.address, "gas": 1_500_000})
    tx_hash = send_tx(w3, account, tx, label="leg_swap")
    # Vault.swap is atomic — once it confirms, the slice nullifier is spent on-chain.
    if tx_hash and on_withdraw_confirmed:
        try:
            on_withdraw_confirmed(tx_hash)
        except Exception as cb_err:
            print(f"   [EVM] ⚠️ leg on_withdraw_confirmed callback error: {cb_err}")
    return tx_hash
