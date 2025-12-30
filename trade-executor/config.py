import os
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

# --- Service & On-Chain URLs ---
FHE_ENGINE_URL = os.getenv("FHE_ENGINE_URL")
# Specialized endpoints for the FHE engine
FHE_ENGINE_BRACKET_URL = os.getenv("FHE_ENGINE_BRACKET_URL", "http://localhost:5001/evaluate_bracket_order")
FHE_ENGINE_LIMIT_BUY_URL = os.getenv("FHE_ENGINE_LIMIT_BUY_URL", "http://localhost:5001/evaluate_limit_buy")
FHE_ENGINE_LIMIT_SELL_URL = os.getenv("FHE_ENGINE_LIMIT_SELL_URL", "http://localhost:5001/evaluate_limit_sell")

PYTH_HERMES_URL = os.getenv("PYTH_HERMES_URL")
# ARKIV_RPC_URL = os.getenv("ARKIV_RPC_URL") # Uncomment if using Arkiv

DATABASE_URI = os.getenv("DATABASE_URI")
raw_rpc = os.getenv("SEPOLIA_RPC_URL", "")
SEPOLIA_RPC_URL = raw_rpc.strip('"').strip("'")

# --- On-Chain Addresses & Keys ---
# Using Web3.to_checksum_address ensures the address is in the correct format
# Make sure your .env file does not have trailing spaces in the address string!
contract_address_raw = os.getenv("ENTRYPOINT_CONTRACT_ADDRESS")
if contract_address_raw:
    ENTRYPOINT_CONTRACT_ADDRESS = Web3.to_checksum_address(contract_address_raw.strip())
else:
    ENTRYPOINT_CONTRACT_ADDRESS = None

# The private key for the account that will execute trades
EXECUTOR_PRIVATE_KEY = os.getenv("EXECUTOR_PRIVATE_KEY")

# --- Server Settings ---
# Default to 16MB max payload size if not set
MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))

# Flag to bypass on-chain ZK verification for demos (default: False)
SKIP_ZK_VERIFY = os.getenv("SKIP_ZK_VERIFY", "false").lower() == "true"

# --- Scheduler Configuration ---
CHECK_INTERVAL_SECONDS = 10

# --- Master Token Mapping ---
# Maps token symbols to their Pyth Price Feed IDs
PYTH_PRICE_FEED_IDS = {
    "ETH": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "BTC": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "SOL": "0xef0d8b612d455ac6463494a99859f5b220de1b000b2b8d5423867332c525164d",
}