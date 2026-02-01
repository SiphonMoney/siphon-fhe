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
    
    # Compressed and encrypted sensitive fields (FHE keys)
    server_key = db.Column(CompressedEncryptedText, nullable=False)
    encrypted_client_key = db.Column(CompressedEncryptedText, nullable=False)  # Required for decryption
    
    # Compressed but not encrypted (FHE ciphertexts - already encrypted by FHE)
    encrypted_upper_bound = db.Column(CompressedText, nullable=False)
    encrypted_lower_bound = db.Column(CompressedText, nullable=False)
    
    # Compressed JSON fields
    zkp_data = db.Column(CompressedText, nullable=True)
    
    # Status and Transaction
    status = db.Column(db.String, default='PENDING', nullable=False, index=True)
    tx_hash = db.Column(db.String, nullable=True)  # Solana transaction signature
    executed_at = db.Column(DateTime, nullable=True)  # When the trade was executed

    # ZK Privacy Pool Integration
    utxo_commitments = db.Column(db.Text, nullable=True)
    is_private = db.Column(db.Boolean, default=True, nullable=False, index=True)

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
            'server_key': self.server_key,
            'encrypted_client_key': self.encrypted_client_key,
            'encrypted_upper_bound': self.encrypted_upper_bound,
            'encrypted_lower_bound': self.encrypted_lower_bound,
            'zkp_data': json.loads(self.zkp_data) if self.zkp_data and isinstance(self.zkp_data, str) else self.zkp_data,
            'status': self.status,
            'tx_hash': self.tx_hash,
            'executed_at': self.executed_at.isoformat() if self.executed_at else None,
            'utxo_commitments': self.utxo_commitments,
            'is_private': self.is_private,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
