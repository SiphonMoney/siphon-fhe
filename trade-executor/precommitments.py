from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify
from note_db import NoteSession, Precommitment
from wallet_auth import verify_tag_sig

# Precommitments stuck in `in_use` beyond this window are considered orphaned (tab closed
# before tx was sent / confirmed). Reset them to `pending` so the pool refills correctly.
_STALE_IN_USE_MINUTES = 30

precommitments_bp = Blueprint('precommitments', __name__)


@precommitments_bp.route('/precommitments', methods=['POST'])
@verify_tag_sig
def create_precommitment(tag):
    data = request.json or {}
    if not data.get('enc_blob') or not data.get('iv'):
        return jsonify({"error": "Missing enc_blob or iv"}), 400

    session = NoteSession()
    row = Precommitment(tag=tag, enc_blob=data['enc_blob'], iv=data['iv'], status='pending')
    session.add(row)
    session.commit()
    return jsonify({"id": row.id}), 201


@precommitments_bp.route('/precommitments', methods=['GET'])
@verify_tag_sig
def list_precommitments(tag):
    session = NoteSession()
    status = request.args.get('status')
    q = session.query(Precommitment).filter_by(tag=tag)
    if status:
        q = q.filter_by(status=status)
    rows = q.order_by(Precommitment.created_at.desc()).all()
    return jsonify({"precommitments": [r.to_dict() for r in rows]}), 200


@precommitments_bp.route('/precommitments/pool-count', methods=['GET'])
@verify_tag_sig
def pool_count(tag):
    session = NoteSession()
    # Reap stale in_use entries (tab-close orphans) back to pending before counting.
    stale_cutoff = datetime.utcnow() - timedelta(minutes=_STALE_IN_USE_MINUTES)
    session.query(Precommitment).filter(
        Precommitment.tag == tag,
        Precommitment.status == 'in_use',
        Precommitment.claimed_at != None,
        Precommitment.claimed_at < stale_cutoff,
    ).update({'status': 'pending'})
    session.commit()

    # Count pending + in_use (fresh in_use entries are legitimately in-flight).
    count = (
        session.query(Precommitment)
        .filter(Precommitment.tag == tag, Precommitment.status.in_(['pending', 'in_use']))
        .count()
    )
    return jsonify({"count": count}), 200


@precommitments_bp.route('/precommitments/<row_id>/claim', methods=['PATCH'])
@verify_tag_sig
def claim_precommitment(tag, row_id):
    """Atomically mark a precommitment in_use. Returns 409 if already claimed or resolved."""
    session = NoteSession()
    updated = (
        session.query(Precommitment)
        .filter_by(id=row_id, tag=tag, status='pending')
        .update({'status': 'in_use', 'claimed_at': datetime.utcnow()})
    )
    if updated == 0:
        session.rollback()
        return jsonify({"error": "Already claimed or not found"}), 409
    session.commit()
    return jsonify({"status": "ok"}), 200


@precommitments_bp.route('/precommitments/<row_id>/resolve', methods=['PATCH'])
@verify_tag_sig
def resolve_precommitment(tag, row_id):
    """Mark a precommitment resolved (consumed). Only valid when status is 'in_use'."""
    session = NoteSession()
    updated = (
        session.query(Precommitment)
        .filter_by(id=row_id, tag=tag, status='in_use')
        .update({'status': 'resolved'})
    )
    if updated == 0:
        session.rollback()
        return jsonify({"error": "Not in_use or not found"}), 409
    session.commit()
    return jsonify({"status": "ok"}), 200


@precommitments_bp.route('/precommitments/<row_id>/release', methods=['PATCH'])
@verify_tag_sig
def release_precommitment(tag, row_id):
    """Return a claimed precommitment to pending (used when tx fails/reverts). Only valid from in_use."""
    session = NoteSession()
    updated = (
        session.query(Precommitment)
        .filter_by(id=row_id, tag=tag, status='in_use')
        .update({'status': 'pending', 'claimed_at': None})
    )
    if updated == 0:
        session.rollback()
        return jsonify({"error": "Not in_use or not found"}), 409
    session.commit()
    return jsonify({"status": "ok"}), 200
