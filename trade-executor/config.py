import os
from dotenv import load_dotenv

load_dotenv()

# --- Helius RPC Configuration ---
# Priority: HELIUS_API_KEY > SOLANA_RPC_URL > default devnet
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
SOLANA_NETWORK = os.getenv("SOLANA_NETWORK", "devnet")  # devnet, mainnet-beta, testnet

def get_rpc_url():
    """Get the best available RPC URL."""
    # Priority 1: Helius RPC (recommended for production)
    if HELIUS_API_KEY:
        network = "mainnet" if SOLANA_NETWORK == "mainnet-beta" else SOLANA_NETWORK
        return f"https://{network}.helius-rpc.com/?api-key={HELIUS_API_KEY}"

    # Priority 2: Custom RPC URL from environment
    custom_rpc = os.getenv("SOLANA_RPC_URL")
    if custom_rpc:
        return custom_rpc

    # Priority 3: Default public endpoints (rate limited)
    return "https://api.devnet.solana.com"

SOLANA_RPC_URL = get_rpc_url()

# Siphon Program ID (deployed on devnet)
SIPHON_PROGRAM_ID = os.getenv("SIPHON_PROGRAM_ID", "BpL3LVZdfz3LKvJXntAmFxAt7d8CHsWf65NCcsWB5em1")

# The private key for the executor account (base58 encoded)
EXECUTOR_PRIVATE_KEY = os.getenv("EXECUTOR_PRIVATE_KEY")

# --- FHE Engine URLs ---
FHE_ENGINE_URL = os.getenv("FHE_ENGINE_URL", "http://localhost:5001/evaluateStrategy")
FHE_ENGINE_CONDITION_URL = os.getenv("FHE_ENGINE_CONDITION_URL", "http://localhost:5001/evaluateCondition")
FHE_ENGINE_TREE_URL = os.getenv("FHE_ENGINE_TREE_URL", "http://localhost:5001/evaluateTree")
FHE_ENGINE_BRACKET_URL = os.getenv("FHE_ENGINE_BRACKET_URL", "http://localhost:5001/evaluate_bracket_order")
FHE_ENGINE_LIMIT_BUY_URL = os.getenv("FHE_ENGINE_LIMIT_BUY_URL", "http://localhost:5001/evaluate_limit_buy")
FHE_ENGINE_LIMIT_SELL_URL = os.getenv("FHE_ENGINE_LIMIT_SELL_URL", "http://localhost:5001/evaluate_limit_sell")

# --- Price Oracle ---
PYTH_HERMES_URL = os.getenv("PYTH_HERMES_URL", "https://hermes.pyth.network")

# --- Database ---
DATABASE_URI = os.getenv("DATABASE_URI")

# --- Note DB (Supabase) ---
NOTE_DB_URI = os.getenv("NOTE_DB_URI", "")
if not NOTE_DB_URI:
    raise RuntimeError("NOTE_DB_URI is not set — Supabase note DB connection required")

SERVER_HMAC_SECRET = os.getenv("SERVER_HMAC_SECRET", "")
if not SERVER_HMAC_SECRET:
    raise RuntimeError("SERVER_HMAC_SECRET is not set — required for nullifier registry HMAC")

# --- Server Settings ---
MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))
SKIP_ZK_VERIFY = os.getenv("SKIP_ZK_VERIFY", "false").lower() == "true"

# --- Scheduler Configuration ---
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "10"))

# --- Confidential VM decryptor (holds ClientKey; decrypts result bit only) ---
DECRYPTOR_URL = os.getenv("DECRYPTOR_URL", "").rstrip("/")

<<<<<<< HEAD
# --- Admin wallet allow-list (gated chains, e.g. ETH Sepolia testing) ---
# Separate token for the admin endpoints that manage the allow-list.
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "").strip()

def _parse_int_set(raw: str) -> set:
    out = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if part:
            try:
                out.add(int(part))
            except ValueError:
                pass
    return out

# Chains whose strategy creation/execution is restricted to allow-listed wallets.
# Default: ETH Sepolia (11155111). Override with ADMIN_GATED_CHAIN_IDS="11155111,1337".
ADMIN_GATED_CHAIN_IDS = _parse_int_set(os.getenv("ADMIN_GATED_CHAIN_IDS", "11155111"))

# Optional boot-time seed of allow-listed wallets per gated chain (comma-separated addresses).
# DB rows added via the admin API are merged on top of these.
ETH_SEPOLIA_ADMIN_WALLETS = [
    a.strip().lower() for a in os.getenv("ETH_SEPOLIA_ADMIN_WALLETS", "").split(",") if a.strip()
]
# chain_id -> list[address] seed map (extend here for other gated chains).
ADMIN_WALLET_ENV_SEED = {
    11155111: ETH_SEPOLIA_ADMIN_WALLETS,
}
=======
# --- Self-referential base URL (executor_runner calls own /nullifier-registry endpoints) ---
TRADE_EXECUTOR_BASE_URL = os.getenv("TRADE_EXECUTOR_BASE_URL", "http://localhost:5002")
>>>>>>> origin/zk-revamp

# --- Token Configuration (Solana) ---
# Token mint addresses on Solana devnet
SOLANA_TOKEN_MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",  # Native SOL (wrapped)
    "USDC": "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",  # Devnet USDC
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # Devnet USDT
}

# Mainnet token mints (for production)
MAINNET_TOKEN_MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}

# Pyth Price Feed IDs (same across chains)
# Verified from: https://hermes.pyth.network/v2/price_feeds
PYTH_PRICE_FEED_IDS = {
    "SOL": "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",  # Crypto.SOL/USD
    "ETH": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",  # Crypto.ETH/USD
    "BTC": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",  # Crypto.BTC/USD
    "USDC": "0xeaa020c61cc479712813461ce153894a96a6c00b21ed0cfc2798d1f9a9e9c94a",  # Crypto.USDC/USD
}

# --- Jupiter Aggregator (for swaps) ---
# Note: quote-api.jup.ag/v6 was deprecated Sept 2025, use api.jup.ag/swap/v1
JUPITER_API_URL = os.getenv("JUPITER_API_URL", "https://api.jup.ag/swap/v1")

# --- Range Compliance ---
RANGE_API_KEY = os.getenv("RANGE_API_KEY")
RANGE_API_URL = os.getenv("RANGE_API_URL", "https://api.range.org/v1")
RANGE_RISK_THRESHOLD = int(os.getenv("RANGE_RISK_THRESHOLD", "70"))  # Block addresses with risk > 70

# --- Logging ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Print configuration on load (hide sensitive data)
if __name__ == "__main__":
    print("=== Siphon Strategy Executor Configuration ===")
    print(f"Network: {SOLANA_NETWORK}")
    print(f"RPC URL: {SOLANA_RPC_URL[:50]}..." if len(SOLANA_RPC_URL) > 50 else f"RPC URL: {SOLANA_RPC_URL}")
    print(f"Helius: {'Configured' if HELIUS_API_KEY else 'Not configured'}")
    print(f"Program ID: {SIPHON_PROGRAM_ID}")
    print(f"Executor Key: {'Configured' if EXECUTOR_PRIVATE_KEY else 'NOT CONFIGURED'}")
    print(f"Range API: {'Configured' if RANGE_API_KEY else 'Not configured'}")
    print(f"Database: {'Configured' if DATABASE_URI else 'NOT CONFIGURED'}")
