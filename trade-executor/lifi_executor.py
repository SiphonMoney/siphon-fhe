"""
Li.Fi cross-chain swap executor.
Docs: https://docs.li.fi/li.fi-api/li.fi-api
Quote API: GET https://li.quest/v1/quote
"""
import os
import time
from typing import Optional
import requests
from web3 import Web3
from web3.middleware import geth_poa_middleware
from dotenv import load_dotenv

load_dotenv()

LIFI_API_BASE = "https://li.quest/v1"
LIFI_API_KEY = os.getenv("LIFI_API_KEY", "")


def _account_for_chain(w3: Web3, chain_id: int):
    from evm_executor import get_evm_executor_private_key
    return w3.eth.account.from_key(get_evm_executor_private_key(chain_id))


def get_lifi_quote(
    from_chain: str,
    to_chain: str,
    from_token: str,
    to_token: str,
    from_amount: int,
    from_address: str,
    to_address: str,
) -> dict:
    """Get best route quote from Li.Fi."""
    params = {
        "fromChain": from_chain,
        "toChain": to_chain,
        "fromToken": from_token,
        "toToken": to_token,
        "fromAmount": str(from_amount),
        "fromAddress": from_address,
        "toAddress": to_address,
    }
    headers = {"x-lifi-api-key": LIFI_API_KEY} if LIFI_API_KEY else {}
    resp = requests.get(f"{LIFI_API_BASE}/quote", params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def execute_lifi_swap(
    from_chain: str,
    to_chain: str,
    from_token: str,
    to_token: str,
    from_amount_wei: int,
    recipient: str,
    rpc_url: Optional[str] = None,
) -> str:
    """Execute a Li.Fi swap/bridge on the source chain. Returns tx hash."""
    if not rpc_url:
        from evm_chain_config import get_evm_chain_config
        rpc_url = get_evm_chain_config(from_chain).rpc_url

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)

    account = _account_for_chain(w3, int(from_chain))

    print(f"[LiFi] Quoting {from_token}→{to_token} from chain {from_chain} to {to_chain}")
    t0 = time.monotonic()
    quote = get_lifi_quote(
        from_chain=from_chain,
        to_chain=to_chain,
        from_token=from_token,
        to_token=to_token,
        from_amount=from_amount_wei,
        from_address=account.address,
        to_address=recipient,
    )
    print(f"[Benchmark] lifi_quote                          = {(time.monotonic()-t0)*1000:>8.1f} ms")

    tx_req = quote.get("transactionRequest")
    if not tx_req:
        raise RuntimeError(f"Li.Fi returned no transactionRequest: {quote}")

    approval = quote.get("estimate", {}).get("approvalAddress")
    if approval and from_token.lower() not in (
        "0x0000000000000000000000000000000000000000",
        "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    ):
        _approve_token(w3, account, from_token, approval, from_amount_wei)

    nonce = w3.eth.get_transaction_count(account.address)
    base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
    priority = w3.to_wei(2, "gwei")

    tx = {
        "from": account.address,
        "to": tx_req["to"],
        "data": tx_req.get("data", "0x"),
        "value": int(tx_req.get("value", 0), 16) if isinstance(tx_req.get("value"), str) else int(tx_req.get("value", 0)),
        "gas": int(tx_req.get("gasLimit", 500_000), 16) if isinstance(tx_req.get("gasLimit"), str) else int(tx_req.get("gasLimit", 500_000)),
        "maxFeePerGas": base_fee * 2 + priority,
        "maxPriorityFeePerGas": priority,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
        "type": 2,
    }

    signed = account.sign_transaction(tx)
    t1 = time.monotonic()
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction).hex()
    print(f"[LiFi] Tx broadcast: {tx_hash}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    print(f"[Benchmark] lifi_swap_total                     = {(time.monotonic()-t1)*1000:>8.1f} ms")
    if receipt.status != 1:
        raise RuntimeError(f"Li.Fi tx failed: {tx_hash}")
    return tx_hash


def _approve_token(w3, account, token_address, spender, amount):
    """Approve ERC20 spend for Li.Fi router."""
    erc20_abi = '[{"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"}]'
    token = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=erc20_abi)
    nonce = w3.eth.get_transaction_count(account.address)
    base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
    tx = token.functions.approve(spender, amount).build_transaction({
        "from": account.address,
        "nonce": nonce,
        "maxFeePerGas": base_fee * 2 + w3.to_wei(2, "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(2, "gwei"),
        "type": 2,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction).hex()
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    print(f"[LiFi] Token approved: {tx_hash}")
