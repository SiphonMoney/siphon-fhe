"""
Supabase (PostgreSQL) session + models for the note system.
Completely separate from the main SQLite DB (strategies, FHE keys).

Uses SQLAlchemy core directly (not Flask-SQLAlchemy) so it doesn't conflict
with the main app's SQLALCHEMY_DATABASE_URI config.

Tables:
  precommitments     — pending vault-mode swap output notes (before swap executes)
  commitments        — spendable notes (direct deposits + resolved swap outputs)
  nullifier_registry — executor double-spend guard (no user data, no tags)
"""
import uuid
import os
from datetime import datetime

from sqlalchemy import create_engine, Column, String, Text, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session

_NOTE_DB_URI = os.environ.get('NOTE_DB_URI', '')
if not _NOTE_DB_URI:
    raise RuntimeError("NOTE_DB_URI env var is not set — cannot connect to Supabase note DB")

_engine = create_engine(
    _NOTE_DB_URI,
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=5,
)

_SessionFactory = sessionmaker(bind=_engine)
NoteSession = scoped_session(_SessionFactory)

Base = declarative_base()


# ── Models ────────────────────────────────────────────────────────────────────

class Precommitment(Base):
    """
    Created when a user submits a vault-mode strategy.
    enc_blob = AES-GCM({nullifier, secret, precommitment, asset, chainId}).
    Only tag (pseudonym) identifies the owner — no wallet address stored.
    """
    __tablename__ = 'precommitments'

    id         = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tag        = Column(String(42), nullable=False, index=True)
    enc_blob   = Column(Text, nullable=False)
    iv         = Column(String(64), nullable=False)
    status     = Column(String(10), nullable=False, default='pending')
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    claimed_at = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            'id':         self.id,
            'tag':        self.tag,
            'enc_blob':   self.enc_blob,
            'iv':         self.iv,
            'status':     self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'claimed_at': self.claimed_at.isoformat() if self.claimed_at else None,
        }


class Commitment(Base):
    """
    A fully realized spendable note.
    enc_blob = AES-GCM({nullifier, secret, commitment, amount, chainId}).
    asset is plaintext so executor can mark spent by (tag, asset) after execution.
    """
    __tablename__ = 'commitments'

    id         = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tag        = Column(String(42), nullable=False, index=True)
    asset      = Column(String(10), nullable=False)
    enc_blob   = Column(Text, nullable=False)
    iv         = Column(String(64), nullable=False)
    spent      = Column(String(10), nullable=False, default='false')
    source     = Column(String(20), nullable=False, default='deposit')
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id':         self.id,
            'tag':        self.tag,
            'asset':      self.asset,
            'enc_blob':   self.enc_blob,
            'iv':         self.iv,
            'spent':      self.spent,
            'source':     self.source,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class NullifierRegistry(Base):
    """
    Executor-only double-spend guard. Zero user data.
    nullifier_hmac = HMAC-SHA256(SERVER_HMAC_SECRET, nullifierHash).
    commitment_id FK allows executor to mark commitment spent after confirmation.
    """
    __tablename__ = 'nullifier_registry'

    nullifier_hmac = Column(String(64), primary_key=True)
    commitment_id  = Column(String(36), nullable=True)
    status         = Column(String(10), nullable=False, default='pending')
    claimed_at     = Column(DateTime, default=datetime.utcnow, nullable=False)


def migrate_note_db():
    """Add columns introduced after initial deployment. Safe to run on every startup (IF NOT EXISTS)."""
    if 'sqlite' in str(_engine.url):
        # SQLite doesn't support IF NOT EXISTS on ALTER TABLE; create_all() covers all columns.
        return
    with _engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE precommitments ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP"
        ))
        conn.commit()


def init_note_db():
    """Create all note tables on Supabase if they don't exist. Safe to call on every startup."""
    Base.metadata.create_all(_engine)
    migrate_note_db()
    print("[NoteDB] Supabase tables ready")
