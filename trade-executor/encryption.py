"""
Database Encryption and Compression Module
Provides encryption at rest and compression for large data fields
"""
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import os
import zlib
import gzip
from dotenv import load_dotenv

# Load environment variables (local/dev convenience)
load_dotenv()

class DatabaseEncryption:
    """Handles encryption/decryption of sensitive database fields"""
    
    def __init__(self):
        # Get encryption key from environment (generate once and store securely)
        key_material = os.getenv('DB_ENCRYPTION_KEY', '').encode()
        if not key_material:
            # Generate a default key for development (WARNING: Change in production!)
            print("⚠️  WARNING: Using default encryption key. Set DB_ENCRYPTION_KEY in production!")
            key_material = b'default-key-change-in-production-32-bytes!!'
        
        # Derive key using PBKDF2
        salt = os.getenv('DB_ENCRYPTION_SALT', 'siphon_salt_2024').encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(key_material))
        self.cipher = Fernet(key)
    
    def encrypt(self, plaintext: str) -> str:
        """Encrypt sensitive data before storing in database"""
        if not plaintext:
            return ""
        return self.cipher.encrypt(plaintext.encode()).decode()
    
    def decrypt(self, ciphertext: str) -> str:
        """Decrypt data retrieved from database"""
        if not ciphertext:
            return ""
        try:
            return self.cipher.decrypt(ciphertext.encode()).decode()
        except Exception as e:
            # If decryption fails, might be plaintext (for migration)
            print(f"⚠️  Decryption failed, assuming plaintext: {e}")
            return ciphertext

class DataCompression:
    """Handles compression/decompression of large data fields"""
    
    @staticmethod
    def compress(data: str) -> bytes:
        """Compress data using gzip (better compression than zlib for text)"""
        if not data:
            return b""
        return gzip.compress(data.encode(), compresslevel=9)
    
    @staticmethod
    def decompress(data: bytes) -> str:
        """Decompress data"""
        if not data:
            return ""
        try:
            return gzip.decompress(data).decode()
        except:
            # If decompression fails, might be uncompressed (for migration)
            return data.decode() if isinstance(data, bytes) else data
    
    @staticmethod
    def compress_to_base64(data: str) -> str:
        """Compress and encode to base64 for database storage"""
        compressed = DataCompression.compress(data)
        return base64.b64encode(compressed).decode()
    
    @staticmethod
    def decompress_from_base64(data: str) -> str:
        """Decode from base64 and decompress"""
        if not data:
            return ""
        try:
            compressed = base64.b64decode(data)
            return DataCompression.decompress(compressed)
        except:
            # If it's not base64, might be uncompressed
            return data

# Singleton instances
db_encryption = DatabaseEncryption()
data_compression = DataCompression()
