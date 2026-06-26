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
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
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


def get_server_key(user_id):
    """Look up a user's stored FHE server key (hex), or None if not uploaded yet."""
    row = UserFheKey.query.get(user_id)
    return row.server_key if row else None
