import hmac as hmac_lib
import hashlib
import os
from flask import Blueprint, request, jsonify
from sqlalchemy.exc import IntegrityError
from note_db import NoteSession, Commitment, NullifierRegistry
from wallet_auth import verify_tag_sig, require_api_token

commitments_bp = Blueprint('commitments', __name__)

_hmac_secret_raw = os.environ.get('SERVER_HMAC_SECRET', '')
if not _hmac_secret_raw:
    raise RuntimeError("SERVER_HMAC_SECRET env var is not set — cannot start commitments module")
_HMAC_SECRET = _hmac_secret_raw.encode()


def _nullifier_hmac(nullifier_hash: str) -> str:
    return hmac_lib.new(_HMAC_SECRET, nullifier_hash.encode(), hashlib.sha256).hexdigest()


@commitments_bp.route('/commitments', methods=['POST'])
@verify_tag_sig
def create_commitment(tag):
    data = request.json or {}
    if not data.get('enc_blob') or not data.get('iv') or not data.get('asset'):
        return jsonify({"error": "Missing enc_blob, iv, or asset"}), 400

    session = NoteSession()
    row = Commitment(
        tag=tag,
        asset=data['asset'].upper(),
        enc_blob=data['enc_blob'],
        iv=data['iv'],
        spent='false',
        source=data.get('source', 'deposit'),
    )
    session.add(row)
    session.commit()
    return jsonify({"id": row.id}), 201


@commitments_bp.route('/commitments', methods=['GET'])
@verify_tag_sig
def list_commitments(tag):
    session = NoteSession()
    rows = (session.query(Commitment)
            .filter_by(tag=tag)
            .order_by(Commitment.created_at.desc())
            .all())
    return jsonify({"commitments": [r.to_dict() for r in rows]}), 200


@commitments_bp.route('/commitments/<row_id>/spent', methods=['PATCH'])
@verify_tag_sig
def mark_commitment_spent(tag, row_id):
    data = request.json or {}
    status = data.get('status', 'true')
    if status not in ('true', 'pending', 'false'):
        return jsonify({"error": "Invalid status"}), 400
    session = NoteSession()
    row = session.query(Commitment).filter_by(id=row_id, tag=tag).first()
    if not row:
        return jsonify({"error": "Not found"}), 404
    row.spent = status
    session.commit()
    return jsonify({"status": "ok"}), 200


@commitments_bp.route('/commitments/export', methods=['GET'])
@verify_tag_sig
def export_commitments(tag):
    session = NoteSession()
    rows = session.query(Commitment).filter_by(tag=tag).all()
    return jsonify({"commitments": [r.to_dict() for r in rows]}), 200


# ── Executor-only endpoints (API token auth) ──────────────────────────────────

@commitments_bp.route('/nullifier-registry', methods=['POST'])
@require_api_token
def claim_nullifier():
    """Atomically claim a nullifier before executing. Returns 409 if already claimed."""
    data = request.json or {}
    nullifier_hash = data.get('nullifier_hash', '')
    commitment_id  = data.get('commitment_id')
    if not nullifier_hash:
        return jsonify({"error": "Missing nullifier_hash"}), 400

    hmac_val = _nullifier_hmac(nullifier_hash)
    session  = NoteSession()
    row = NullifierRegistry(nullifier_hmac=hmac_val, commitment_id=commitment_id, status='pending')
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        # PK collision — another executor claimed this nullifier concurrently.
        session.rollback()
        existing = session.query(NullifierRegistry).filter_by(nullifier_hmac=hmac_val).first()
        status = existing.status if existing else 'unknown'
        return jsonify({"error": "Already claimed", "status": status}), 409
    return jsonify({"status": "claimed", "nullifier_hmac": hmac_val}), 201


@commitments_bp.route('/nullifier-registry/<nullifier_hash>/spent', methods=['PATCH'])
@require_api_token
def mark_nullifier_spent(nullifier_hash):
    """Mark nullifier spent after on-chain withdraw confirms. Also flips commitment.spent."""
    hmac_val = _nullifier_hmac(nullifier_hash)
    session  = NoteSession()
    row = session.query(NullifierRegistry).filter_by(nullifier_hmac=hmac_val).first()
    if not row:
        return jsonify({"error": "Not found"}), 404

    row.status = 'spent'
    if row.commitment_id:
        comm = session.query(Commitment).filter_by(id=row.commitment_id).first()
        if comm:
            comm.spent = 'true'

    session.commit()
    return jsonify({"status": "ok"}), 200


@commitments_bp.route('/nullifier-registry/<nullifier_hash>/release', methods=['PATCH'])
@require_api_token
def release_nullifier(nullifier_hash):
    """Release a pending claim back to false when withdraw fails/reverts."""
    hmac_val = _nullifier_hmac(nullifier_hash)
    session  = NoteSession()
    row = session.query(NullifierRegistry).filter_by(
        nullifier_hmac=hmac_val, status='pending'
    ).first()
    if not row:
        return jsonify({"error": "Not found or already spent"}), 404

    row.status = 'false'
    if row.commitment_id:
        comm = session.query(Commitment).filter_by(id=row.commitment_id).first()
        if comm and comm.spent == 'pending':
            comm.spent = 'false'

    session.commit()
    return jsonify({"status": "ok"}), 200


@commitments_bp.route('/commitments/<row_id>/executor-update', methods=['PATCH'])
@require_api_token
def executor_update_commitment(row_id):
    """Executor replaces enc_blob with change note blob after successful withdrawal."""
    data     = request.json or {}
    enc_blob = data.get('enc_blob')
    iv       = data.get('iv')
    spent    = data.get('spent', 'false')
    if not enc_blob or not iv:
        return jsonify({"error": "Missing enc_blob or iv"}), 400

    session = NoteSession()
    row = session.query(Commitment).filter_by(id=row_id).first()
    if not row:
        return jsonify({"error": "Not found"}), 404

    row.enc_blob = enc_blob
    row.iv       = iv
    row.spent    = spent
    session.commit()
    return jsonify({"status": "ok"}), 200
