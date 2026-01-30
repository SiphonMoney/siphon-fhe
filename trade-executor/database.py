from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.types import TypeDecorator, LargeBinary
from sqlalchemy import Column, String, Float, Text, DateTime
from datetime import datetime
import uuid
import json
from encryption import db_encryption, data_compression

# ✅ Initialize db first
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
            # Compress first (better for large data)
            compressed = data_compression.compress_to_base64(value)
            # Then encrypt
            encrypted = db_encryption.encrypt(compressed)
            return encrypted.encode()
        return value
    
    def process_result_value(self, value, dialect):
        """Decrypt and decompress after retrieving"""
        if value is None:
            return None
        if isinstance(value, bytes):
            try:
                # Decrypt first
                decrypted = db_encryption.decrypt(value.decode())
                # Then decompress
                return data_compression.decompress_from_base64(decrypted)
            except Exception as e:
                # Fallback for migration compatibility
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
    user_id = db.Column(db.String, nullable=False, index=True)  # Index for faster queries
    strategy_type = db.Column(db.String, nullable=False, index=True)
    asset_in = db.Column(db.String, nullable=False)
    asset_out = db.Column(db.String, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    price_feed_id = db.Column(db.String, nullable=True)
    recipient_address = db.Column(db.String, nullable=False)
    
    # Compressed and encrypted sensitive fields (FHE keys)
    # These are the largest fields - compression + encryption reduces size significantly
    server_key = db.Column(CompressedEncryptedText, nullable=False)
    encrypted_client_key = db.Column(CompressedEncryptedText, nullable=True)  # Nullable when using MPC shares
    
    # Compressed but not encrypted (FHE ciphertexts - already encrypted by FHE)
    encrypted_upper_bound = db.Column(CompressedText, nullable=False)
    encrypted_lower_bound = db.Column(CompressedText, nullable=False)
    
    # Compressed JSON fields
    zkp_data = db.Column(CompressedText, nullable=True)
    mpc_share_indices = db.Column(CompressedText, nullable=True)
    
    # Small fields - no compression needed
    mpc_public_key_set = db.Column(db.Text, nullable=True)  # MPC public key set (smaller, hash-based)
    fhe_key_id = db.Column(db.String, nullable=True, index=True)  # Key ID when shares stored on MPC (no full key stored)
    status = db.Column(db.String, default='PENDING', nullable=False, index=True)

    # ZK Privacy Pool Integration
    utxo_commitments = db.Column(db.Text, nullable=True)  # JSON array of UTXO commitment strings
    is_private = db.Column(db.Boolean, default=True, nullable=False, index=True)  # Whether to use ZK pool

    # Timestamps for cleanup
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
            'server_key': self.server_key,
            'encrypted_client_key': self.encrypted_client_key,  # None if using MPC shares
            'encrypted_upper_bound': self.encrypted_upper_bound,
            'encrypted_lower_bound': self.encrypted_lower_bound,
            'zkp_data': json.loads(self.zkp_data) if self.zkp_data and isinstance(self.zkp_data, str) else self.zkp_data,
            'mpc_public_key_set': self.mpc_public_key_set,
            'mpc_share_indices': json.loads(self.mpc_share_indices) if self.mpc_share_indices and isinstance(self.mpc_share_indices, str) else self.mpc_share_indices,
            'fhe_key_id': self.fhe_key_id,  # Key ID when shares are on MPC servers
            'status': self.status,
            'utxo_commitments': self.utxo_commitments,  # ZK pool UTXOs
            'is_private': self.is_private,  # Use ZK pool or not
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
