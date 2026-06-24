import re
import base58

EVM_CHAIN_IDS = {"1", "11155111", "42161", "10", "8453", "84532", "137"}
EVM_PATTERN   = re.compile(r'^0x[0-9a-fA-F]{40}$')

def is_valid_evm_address(addr: str) -> bool:
    return bool(EVM_PATTERN.match(addr))

def is_valid_solana_address(addr: str) -> bool:
    try:
        decoded = base58.b58decode(addr)
        return 32 <= len(decoded) <= 44
    except Exception:
        return False

def validate_recipient(recipient_address: str, to_chain: str) -> tuple[bool, str]:
    """
    Returns (is_valid, error_message).
    to_chain: EVM chain ID string ("1", "11155111", etc.) or "solana"
    """
    if not recipient_address:
        return False, "recipient_address is required"
    if not to_chain:
        return False, "to_chain is required"

    if to_chain.lower() == "solana":
        if not is_valid_solana_address(recipient_address):
            return False, f"Invalid Solana address '{recipient_address}' for destination chain solana"
        return True, ""

    if to_chain in EVM_CHAIN_IDS:
        if not is_valid_evm_address(recipient_address):
            return False, f"Invalid EVM address '{recipient_address}' for destination chain {to_chain}"
        return True, ""

    return False, f"Unknown destination chain '{to_chain}'"
