import os
import sys
from app import app, db
from config import DATABASE_URI

# This script is for one-time database initialization.
# It ensures the 'strategies.db' file and tables exist before the server starts.

if not DATABASE_URI:
    print("⚠️  DATABASE_URI not set, using default")
    DATABASE_URI = "sqlite:///instance/strategies.db?timeout=20000"

# Ensure instance directory exists
instance_dir = os.path.join(os.path.dirname(__file__), 'instance')
os.makedirs(instance_dir, exist_ok=True)
print(f"📁 Database directory: {instance_dir}")

print(f"Initializing database at: {app.config['SQLALCHEMY_DATABASE_URI']}")

# Create the database and all tables within the app context
with app.app_context():
    try:
        db.create_all()
        print("✅ Database tables created/verified successfully.")
    except Exception as e:
        print(f"⚠️  Database initialization warning: {e}")
        print("   This may be normal if tables already exist or migration is needed")
        sys.exit(0)  # Don't fail, let migration handle it