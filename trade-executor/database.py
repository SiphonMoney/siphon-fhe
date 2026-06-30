from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.types import TypeDecorator, LargeBinary
from sqlalchemy import Column, String, Float, Text, DateTime
from datetime import datetime
import uuid
import json
from encryption import db_encryption, data_compression

db = SQLAlchemy()

class CompressedEncryptedText(TypeDecorator):
    """Custom SQLAlchemy type that compresses and encrypts text data"""
    impl = LargeBinary
    cache_ok = True
    
    def process_bind_param(self, value, dialect):
        """Compress and encrypt before storing"""
        if value is None:
            return None
        if isinstance(value, str):
            compressed = data_compression.compress_to_base64(value)
            encrypted = db_encryption.encrypt(compressed)
            return encrypted.encode()
        return value
    
    def process_result_value(self, value, dialect):
        """Decrypt and decompress after retrieving"""
        if value is None:
            return None
        if isinstance(value, bytes):
            try:
                decrypted = db_encryption.decrypt(value.decode())
                return data_compression.decompress_from_base64(decrypted)
            except Exception as e:
                print(f"⚠️  Error processing value: {e}")
                return value.decode() if isinstance(value, bytes) else value
        return value

class CompressedText(TypeDecorator):
    """Custom SQLAlchemy type that compresses text data (for non-sensitive large data)"""
    impl = LargeBinary
    cache_ok = True
    
    def process_bind_param(self, value, dialect):
        """Compress before storing"""
        if value is None:
            return None
        if isinstance(value, str):
            return data_compression.compress_to_base64(value).encode()
        return value
    
    def process_result_value(self, value, dialect):
        """Decompress after retrieving"""
        if value is None:
            return None
        if isinstance(value, bytes):
            try:
                return data_compression.decompress_from_base64(value.decode())
            except:
                return value.decode() if isinstance(value, bytes) else value
        return value

class Strategy(db.Model):
    id = db.Column(db.String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String, nullable=False, index=True)
    strategy_type = db.Column(db.String, nullable=False, index=True)
    asset_in = db.Column(db.String, nullable=False)
    asset_out = db.Column(db.String, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    price_feed_id = db.Column(db.String, nullable=True)
    recipient_address = db.Column(db.String, nullable=False)
    
    # FHE keys are no longer stored per-strategy. The client key NEVER leaves the user's
    # browser; the (large) server key is stored once per user in UserFheKey and looked up
    # by user_id at evaluation time.

    # Compressed but not encrypted (FHE ciphertexts - already encrypted by FHE)
    encrypted_upper_bound = db.Column(CompressedText, nullable=True)
    encrypted_lower_bound = db.Column(CompressedText, nullable=True)

    # Latest encrypted evaluation result (hex of a RadixCiphertext, 1=triggered/0=not),
    # refreshed by the scheduler against the current price. The browser polls and decrypts
    # this locally to decide whether to authorize execution.
    encrypted_result = db.Column(CompressedText, nullable=True)
    result_updated_at = db.Column(DateTime, nullable=True)
    
    # Compressed JSON fields
    zkp_data = db.Column(CompressedText, nullable=True)
    
    # Status and Transaction
    status = db.Column(db.String, default='PENDING', nullable=False, index=True)
    tx_hash = db.Column(db.String, nullable=True)
    executed_at = db.Column(DateTime, nullable=True)  # When the trade was executed

    # ZK Privacy Pool Integration
    utxo_commitments = db.Column(db.Text, nullable=True)
    is_private = db.Column(db.Boolean, default=True, nullable=False, index=True)

    # Custom Strategies + Li.Fi Swaps Integration
    condition_tree = db.Column(db.Text, nullable=True)
    to_chain       = db.Column(db.String(20), nullable=True)
    from_chain     = db.Column(db.String(20), nullable=True)

    # Swap output routing: 'address' (default — send to recipient_address) or
    # 'vault' (re-deposit the swap output into the asset_out vault as a private note
    # owned by output_precommitment, so the user stays shielded and withdraws later).
    output_mode          = db.Column(db.String(20), nullable=False, default='address')
    output_precommitment = db.Column(db.String(80), nullable=True)

    # --- Multi-leg strategies (TWAP / RANGE_GRID) ------------------------------------------
    # A TWAP/Grid order is a parent Strategy with N child StrategyLeg rows, each an independent
    # shielded leg (its own slice note, ZK proof, nullifier, and encrypted trigger). The columns
    # below describe the schedule/ladder; per-leg data lives in StrategyLeg.
    #   leg_count        — number of legs (TWAP slices / grid rungs)
    #   interval_sec     — TWAP cadence between slices (seconds)
    #   grid_levels      — grid rung count (mirrors leg_count for RANGE_GRID)
    #   max_slippage_bps — per-leg slippage cap
    #   start_delay_sec  — delay before the first leg becomes eligible
    #   executed_count   — legs that have completed; strategy is EXECUTED when == leg_count
    #   eval_mode        — 'price' (grid rung price triggers) | 'time' (TWAP encrypted fire-time)
    leg_count        = db.Column(db.Integer, nullable=True)
    interval_sec     = db.Column(db.Integer, nullable=True)
    grid_levels      = db.Column(db.Integer, nullable=True)
    max_slippage_bps = db.Column(db.Integer, nullable=True)
    start_delay_sec  = db.Column(db.Integer, nullable=True)
    executed_count   = db.Column(db.Integer, nullable=False, default=0)
    eval_mode        = db.Column(db.String(10), nullable=True)  # 'price' | 'time'
    # Anchor time (unix seconds) the TWAP fire-times were computed against, so the executor can
    # reconstruct/verify schedule offsets. Plaintext is fine: it's just "when the order started",
    # the same instant the creation tx is already publicly timestamped on-chain.
    schedule_anchor  = db.Column(db.Integer, nullable=True)
    execution_window_sec = db.Column(db.Integer, nullable=True)  # expire + return funds after this

    # Timestamps
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        """Convert to dictionary (automatically decrypts/decompresses)"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'strategy_type': self.strategy_type,
            'asset_in': self.asset_in,
            'asset_out': self.asset_out,
            'amount': self.amount,
            'price_feed_id': self.price_feed_id,
            'recipient_address': self.recipient_address,
            'encrypted_upper_bound': self.encrypted_upper_bound,
            'encrypted_lower_bound': self.encrypted_lower_bound,
            'encrypted_result': self.encrypted_result,
            'result_updated_at': self.result_updated_at.isoformat() if self.result_updated_at else None,
            'zkp_data': json.loads(self.zkp_data) if self.zkp_data and isinstance(self.zkp_data, str) else self.zkp_data,
            'status': self.status,
            'tx_hash': self.tx_hash,
            'executed_at': self.executed_at.isoformat() if self.executed_at else None,
            'utxo_commitments': self.utxo_commitments,
            'is_private': self.is_private,
            'condition_tree': json.loads(self.condition_tree) if self.condition_tree else None,
            'to_chain': self.to_chain,
            'from_chain': self.from_chain,
            'output_mode': self.output_mode,
            'output_precommitment': self.output_precommitment,
            'leg_count': self.leg_count,
            'interval_sec': self.interval_sec,
            'grid_levels': self.grid_levels,
            'max_slippage_bps': self.max_slippage_bps,
            'start_delay_sec': self.start_delay_sec,
            'executed_count': self.executed_count,
            'eval_mode': self.eval_mode,
            'schedule_anchor': self.schedule_anchor,
            'execution_window_sec': self.execution_window_sec,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class StrategyLeg(db.Model):
    """One independent leg of a multi-leg strategy (TWAP slice / grid rung).

    Each leg is a fully shielded sub-trade: its own slice note (from Vault.split), its own
    multi-note withdrawal ZK proof + nullifier set, its own encrypted trigger, and its own
    swap. Legs are evaluated and fired independently by the scheduler/executor, so a TWAP of N
    slices produces N withdraw+swap txs with N distinct nullifiers — never reusing one note.

    Privacy: a grid leg triggers on an encrypted PRICE band (FHE GTE/LTE vs the public price);
    a TWAP leg triggers on an encrypted FIRE-TIME (FHE LTE vs the public current unix time), so
    the cadence stays hidden in ciphertext exactly like a price threshold does — never stored
    in plaintext on the server."""
    __tablename__ = 'strategy_legs'
    id          = db.Column(db.String, primary_key=True, default=lambda: str(uuid.uuid4()))
    strategy_id = db.Column(db.String, db.ForeignKey('strategy.id'), nullable=False, index=True)
    leg_index   = db.Column(db.Integer, nullable=False)
    amount      = db.Column(db.Float, nullable=False)               # per-leg amount (asset_in units)
    side        = db.Column(db.String(12), nullable=True)           # LIMIT_BUY | LIMIT_SELL (grid)
    eval_mode   = db.Column(db.String(10), nullable=False, default='price')  # 'price' | 'time'
    target_price = db.Column(db.Float, nullable=True)               # grid rung price (reference)

    # Per-leg encrypted trigger bound (FHE ciphertext, compressed at rest):
    #   grid: rung price band (lower for buy rung / upper for sell rung)
    #   twap: encrypted fire-time (seconds); fires when current_time >= fire_time
    encrypted_upper_bound = db.Column(CompressedText, nullable=True)
    encrypted_lower_bound = db.Column(CompressedText, nullable=True)
    encrypted_result      = db.Column(CompressedText, nullable=True)
    result_updated_at     = db.Column(DateTime, nullable=True)

    # Per-leg multi-note withdrawal proof (JSON). Holds pA/pB/pC, stateRoot, nullifierHashes[],
    # changeCommitment, and swap-binding signals — one note (slice) spent per leg.
    zkp_data       = db.Column(CompressedText, nullable=True)
    nullifier_hash = db.Column(db.String(80), nullable=True, index=True)  # first nullifier (claim/dedup)

    # Vault-output (output_mode='vault'): precommitment of THIS leg's asset_out note. After the
    # leg's swap, the executor re-deposits the output as Poseidon(actualOut, precommitment).
    output_precommitment = db.Column(db.String, nullable=True)

    status      = db.Column(db.String, default='PENDING', nullable=False, index=True)
    tx_hash     = db.Column(db.String, nullable=True)
    executed_at = db.Column(DateTime, nullable=True)
    created_at  = db.Column(DateTime, default=datetime.utcnow, nullable=False)

    strategy = db.relationship('Strategy', backref=db.backref('legs', lazy='dynamic', order_by='StrategyLeg.leg_index'))

    def to_dict(self):
        return {
            'id': self.id,
            'strategy_id': self.strategy_id,
            'leg_index': self.leg_index,
            'amount': self.amount,
            'side': self.side,
            'eval_mode': self.eval_mode,
            'target_price': self.target_price,
            'encrypted_upper_bound': self.encrypted_upper_bound,
            'encrypted_lower_bound': self.encrypted_lower_bound,
            'encrypted_result': self.encrypted_result,
            'result_updated_at': self.result_updated_at.isoformat() if self.result_updated_at else None,
            'zkp_data': json.loads(self.zkp_data) if self.zkp_data and isinstance(self.zkp_data, str) else self.zkp_data,
            'nullifier_hash': self.nullifier_hash,
            'output_precommitment': self.output_precommitment,
            'status': self.status,
            'tx_hash': self.tx_hash,
            'executed_at': self.executed_at.isoformat() if self.executed_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class FeeAccrual(db.Model):
    """A protocol fee accrued in the executor wallet (Part A arming / Part B execution), pending a
    sweep into the Siphon fee-vault. Accrue-then-sweep keeps tiny per-trade fees from each paying a
    full deposit's gas. Fees are taken in the INPUT asset, so they accrue per (chain, asset)."""
    __tablename__ = 'fee_accruals'
    id          = db.Column(db.String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chain_id    = db.Column(db.Integer, nullable=False, index=True)
    asset       = db.Column(db.String(12), nullable=False)
    amount_wei  = db.Column(db.String, nullable=False)   # string — wei can exceed BIGINT range
    kind        = db.Column(db.String(12), nullable=False)   # 'execution' | 'arming'
    strategy_id = db.Column(db.String, nullable=True, index=True)
    leg_id      = db.Column(db.String, nullable=True)
    tx_hash     = db.Column(db.String, nullable=True)   # the trade tx the fee was deducted from
    swept       = db.Column(db.Boolean, default=False, nullable=False, index=True)
    sweep_tx    = db.Column(db.String, nullable=True)   # the fee-vault deposit that swept it
    created_at  = db.Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id, 'chain_id': self.chain_id, 'asset': self.asset,
            'amount_wei': self.amount_wei, 'kind': self.kind, 'strategy_id': self.strategy_id,
            'leg_id': self.leg_id, 'tx_hash': self.tx_hash, 'swept': self.swept,
            'sweep_tx': self.sweep_tx,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ProtocolFeeNote(db.Model):
    """Pre-generated protocol-owned vault-note precommitments for the fee-vault sweep.

    The precommitment = Poseidon(nullifier, secret) is generated by the FRONTEND (protocol wallet)
    so it matches the on-chain Poseidon; only the precommitment is uploaded here. The protocol keeps
    the nullifier/secret in its own (protocol-wallet) note store and withdraws accrued fees later.
    Each sweep consumes one 'available' precommitment (a note's nullifier is single-use)."""
    __tablename__ = 'protocol_fee_notes'
    id            = db.Column(db.String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chain_id      = db.Column(db.Integer, nullable=False, index=True)
    asset         = db.Column(db.String(12), nullable=False)
    precommitment = db.Column(db.String, nullable=False, unique=True)
    status        = db.Column(db.String(12), nullable=False, default='available', index=True)  # available|used
    amount_wei    = db.Column(db.String, nullable=True)   # set when a sweep deposits into it
    commitment    = db.Column(db.String, nullable=True)
    sweep_tx      = db.Column(db.String, nullable=True)
    created_at    = db.Column(DateTime, default=datetime.utcnow, nullable=False)
    used_at       = db.Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id, 'chain_id': self.chain_id, 'asset': self.asset,
            'precommitment': self.precommitment, 'status': self.status,
            'amount_wei': self.amount_wei, 'commitment': self.commitment,
            'sweep_tx': self.sweep_tx,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Note(db.Model):
    __tablename__ = 'notes'
    id             = db.Column(db.String, primary_key=True, default=lambda: str(uuid.uuid4()))
    wallet         = db.Column(db.String(42), nullable=False, index=True)
    ciphertext     = db.Column(db.Text, nullable=False)
    iv             = db.Column(db.String(64), nullable=False)
    commitment     = db.Column(db.String(80), nullable=False)
    nullifier_hash = db.Column(db.String(80), nullable=False)
    chain_id       = db.Column(db.Integer, nullable=False, default=11155111)
    asset          = db.Column(db.String(10), nullable=False, default='ETH')
    spent          = db.Column(db.String(10), nullable=False, default='false')  # 'false' | 'pending' | 'true'
    created_at     = db.Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'wallet': self.wallet,
            'ciphertext': self.ciphertext,
            'iv': self.iv,
            'commitment': self.commitment,
            'nullifier_hash': self.nullifier_hash,
            'chain_id': self.chain_id,
            'asset': self.asset,
            'spent': self.spent,  # 'false' | 'pending' | 'true'
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class UserFheKey(db.Model):
    """Per-user FHE public (server) key. Generated in the user's browser and uploaded once.
    The server key is large (~100 MB) and identical across all of a user's strategies, so it
    is stored here once rather than duplicated per strategy. The client (secret) key is never
    uploaded — it stays in the browser."""
    __tablename__ = 'user_fhe_keys'
    user_id    = db.Column(db.String, primary_key=True)
    server_key = db.Column(CompressedText, nullable=False)  # hex, compressed at rest
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AdminWallet(db.Model):
    """Allow-listed wallet for gated chains (e.g. ETH Sepolia testing).

    When a chain is admin-gated, only wallets present here (or in the env seed) may create /
    execute strategies on it. Rows are added at runtime via the admin API, and/or seeded from
    an env var at boot. Addresses are stored lowercased for case-insensitive matching."""
    __tablename__ = 'admin_wallets'
    address    = db.Column(db.String(42), primary_key=True)   # lowercased EVM address
    chain_id   = db.Column(db.Integer, primary_key=True)       # which chain this allows
    label      = db.Column(db.String(120), nullable=True)
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'address': self.address,
            'chain_id': self.chain_id,
            'label': self.label,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


def get_server_key(user_id):
    """Look up a user's stored FHE server key (hex), or None if not uploaded yet."""
    row = UserFheKey.query.get(user_id)
    return row.server_key if row else None
