#!/usr/bin/env python3
"""
Security Setup Script
Generates encryption keys and helps configure the environment
"""
import os
import secrets
from cryptography.fernet import Fernet

def generate_encryption_key():
    """Generate a secure encryption key"""
    return Fernet.generate_key().decode()

def generate_api_token():
    """Generate a secure API token"""
    return secrets.token_urlsafe(32)

def main():
    print("=" * 60)
    print("Security Setup for Siphon Money")
    print("=" * 60)
    print()
    
    # Generate keys
    encryption_key = generate_encryption_key()
    api_token = generate_api_token()
    
    print("✅ Generated security keys:")
    print()
    print("Add these to your .env file:")
    print("-" * 60)
    print(f"DB_ENCRYPTION_KEY={encryption_key}")
    print(f"DB_ENCRYPTION_SALT=siphon-salt-{secrets.token_hex(8)}")
    print(f"API_TOKEN={api_token}")
    print("-" * 60)
    print()
    
    # Check if .env exists
    env_path = ".env"
    if os.path.exists(env_path):
        print(f"⚠️  {env_path} already exists. Please add the above values manually.")
    else:
        print(f"💡 Create a .env file and add the above values.")
    
    print()
    print("Next steps:")
    print("1. Add the keys to your .env file")
    print("2. Run: python migrate_database.py")
    print("3. Restart your services")
    print()

if __name__ == "__main__":
    main()
