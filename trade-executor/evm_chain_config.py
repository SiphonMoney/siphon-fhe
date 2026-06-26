"""
Per-chain EVM config for the trade-executor.
Strategies carry from_chain / to_chain; execution picks RPC + contracts by from_chain.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict

NATIVE_ASSET = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

# Chains the executor can submit transactions on (must match siphon-app networks).
SUPPORTED_EXECUTOR_CHAIN_IDS = {8453, 11155111}


@dataclass(frozen=True)
class EvmChainConfig:
    chain_id: int
    name: str
    rpc_url: str
    entrypoint: str
    uniswap_v3_router: str
    weth: str
    usdc: str

    def token_address(self, symbol: str) -> str:
        sym = symbol.upper()
        if sym == "ETH":
            return NATIVE_ASSET
        if sym == "USDC":
            return self.usdc
        if sym == "WETH":
            return self.weth
        extra = _EXTRA_TOKENS.get(self.chain_id, {})
        if sym in extra:
            return extra[sym]
        return sym


# Extra tokens only used on some testnets (executor swap path).
_EXTRA_TOKENS: Dict[int, Dict[str, str]] = {
    11155111: {
        "USDT": "0xaa8e23fb1079ea71e0a56f48a2aa51851d8433d0",
        "WBTC": "0x92f3B59a79bFf5dc60c0d59eA13a44D082B2bdFC",
    },
}

# Defaults aligned with siphon-app/src/lib/networks.ts
_CHAIN_DEFAULTS: Dict[int, EvmChainConfig] = {
    8453: EvmChainConfig(
        chain_id=8453,
        name="Base",
        rpc_url="https://mainnet.base.org",
        entrypoint="0x2f7d237977A86830708D9C872f5F4D3D7A980138",
        uniswap_v3_router="0x2626664c2603336E57B271c5C0b26F421741e481",
        weth="0x4200000000000000000000000000000000000006",
        usdc="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    ),
    11155111: EvmChainConfig(
        chain_id=11155111,
        name="Ethereum Sepolia",
        rpc_url="https://ethereum-sepolia-rpc.publicnode.com",
        entrypoint="0x867e9C195eB85960c390D4a7A64F4e16905D6638",
        uniswap_v3_router="0x5D49f98ea31bfa7B41473Bc034BCA56B659C11A3",
        weth="0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14",
        usdc="0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
    ),
}


def _env(key: str, fallback: str = "") -> str:
    return (os.getenv(key) or fallback).strip()


def get_evm_chain_config(chain_id: str | int) -> EvmChainConfig:
    """Resolve RPC + contract addresses for a chain. Env overrides per chain."""
    cid = int(chain_id)
    base = _CHAIN_DEFAULTS.get(cid)
    if not base:
        raise ValueError(
            f"Unsupported EVM chain {cid}. Executor supports: {sorted(SUPPORTED_EXECUTOR_CHAIN_IDS)}"
        )

    if cid == 8453:
        return EvmChainConfig(
            chain_id=cid,
            name=base.name,
            rpc_url=_env("BASE_MAINNET_RPC", _env("ETH_RPC_URL", base.rpc_url)),
            entrypoint=_env("BASE_MAINNET_ENTRYPOINT", _env("ENTRYPOINT_ADDRESS", base.entrypoint)),
            uniswap_v3_router=_env("BASE_MAINNET_UNISWAP_V3_ROUTER", _env("UNISWAP_V3_ROUTER", base.uniswap_v3_router)),
            weth=_env("BASE_MAINNET_WETH", _env("WETH_ADDRESS", base.weth)),
            usdc=_env("BASE_MAINNET_USDC", _env("USDC_ADDRESS", base.usdc)),
        )

    if cid == 11155111:
        return EvmChainConfig(
            chain_id=cid,
            name=base.name,
            rpc_url=_env("ETH_SEPOLIA_RPC", base.rpc_url),
            entrypoint=_env("ETH_SEPOLIA_ENTRYPOINT", base.entrypoint),
            uniswap_v3_router=_env("ETH_SEPOLIA_UNISWAP_V3_ROUTER", base.uniswap_v3_router),
            weth=_env("ETH_SEPOLIA_WETH", base.weth),
            usdc=_env("ETH_SEPOLIA_USDC", base.usdc),
        )

    raise ValueError(f"Unsupported EVM chain {cid}")


def resolve_execution_chain_id(strategy: dict) -> int:
    """Chain used for ZK withdraw + same-chain swap (strategy source chain)."""
    raw = strategy.get("from_chain") or strategy.get("chain_id") or "8453"
    cid = int(raw)
    if cid not in SUPPORTED_EXECUTOR_CHAIN_IDS:
        raise ValueError(
            f"Strategy from_chain={cid} is not supported by the executor. "
            f"Supported: {sorted(SUPPORTED_EXECUTOR_CHAIN_IDS)}"
        )
    return cid
