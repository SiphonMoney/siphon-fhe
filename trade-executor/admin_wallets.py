"""Admin wallet allow-list for gated chains (e.g. ETH Sepolia testing).

When a chain is in ADMIN_GATED_CHAIN_IDS, only allow-listed wallets may create/execute
strategies on it. The allow-list is the union of:
  - an env seed (config.ADMIN_WALLET_ENV_SEED), applied at boot, and
  - DB rows (AdminWallet), added/removed at runtime via the admin API.
Addresses are matched case-insensitively (stored lowercased).
"""
from __future__ import annotations

from database import db, AdminWallet
from config import ADMIN_GATED_CHAIN_IDS, ADMIN_WALLET_ENV_SEED


def _norm(address: str) -> str:
    return (address or "").strip().lower()


def chain_is_gated(chain_id) -> bool:
    try:
        return int(chain_id) in ADMIN_GATED_CHAIN_IDS
    except (TypeError, ValueError):
        return False


def is_wallet_allowed(address: str, chain_id) -> bool:
    """True if `address` may transact on `chain_id`. Non-gated chains are always allowed."""
    if not chain_is_gated(chain_id):
        return True
    addr = _norm(address)
    if not addr:
        return False
    cid = int(chain_id)
    # env seed
    if addr in (ADMIN_WALLET_ENV_SEED.get(cid) or []):
        return True
    # DB
    return AdminWallet.query.filter_by(address=addr, chain_id=cid).first() is not None


def add_wallet(address: str, chain_id, label: str | None = None) -> dict:
    addr = _norm(address)
    cid = int(chain_id)
    if not addr.startswith("0x") or len(addr) != 42:
        raise ValueError("address must be a 0x-prefixed 42-char EVM address")
    existing = AdminWallet.query.filter_by(address=addr, chain_id=cid).first()
    if existing:
        if label is not None:
            existing.label = label
            db.session.commit()
        return existing.to_dict()
    row = AdminWallet(address=addr, chain_id=cid, label=label)
    db.session.add(row)
    db.session.commit()
    return row.to_dict()


def remove_wallet(address: str, chain_id) -> bool:
    addr = _norm(address)
    cid = int(chain_id)
    row = AdminWallet.query.filter_by(address=addr, chain_id=cid).first()
    if not row:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


def list_wallets(chain_id=None) -> list[dict]:
    """All allow-listed wallets (DB + env seed), optionally filtered by chain."""
    q = AdminWallet.query
    if chain_id is not None:
        q = q.filter_by(chain_id=int(chain_id))
    rows = [r.to_dict() for r in q.all()]
    db_keys = {(r["address"], r["chain_id"]) for r in rows}
    # Surface env-seed entries too so the listing reflects what's actually enforced.
    for cid, addrs in ADMIN_WALLET_ENV_SEED.items():
        if chain_id is not None and int(chain_id) != cid:
            continue
        for a in addrs:
            if (a, cid) not in db_keys:
                rows.append({"address": a, "chain_id": cid, "label": "env-seed", "created_at": None})
    return rows
