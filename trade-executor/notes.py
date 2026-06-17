from flask import Blueprint, request, jsonify
from database import db, Note
from wallet_auth import verify_wallet_sig, require_api_token_or_wallet

notes_bp = Blueprint('notes', __name__)

@notes_bp.route('/notes', methods=['POST'])
@verify_wallet_sig
def create_note(wallet):
    data = request.json or {}
    required = ['ciphertext', 'iv', 'commitment', 'nullifier_hash']
    if not all(data.get(k) for k in required):
        return jsonify({"error": "Missing required fields"}), 400

    note = Note(
        wallet=wallet,
        ciphertext=data['ciphertext'],
        iv=data['iv'],
        commitment=data['commitment'],
        nullifier_hash=data['nullifier_hash'],
        chain_id=data.get('chain_id', 11155111),
        asset=data.get('asset', 'ETH'),
    )
    db.session.add(note)
    db.session.commit()
    return jsonify({"id": note.id}), 201

@notes_bp.route('/notes', methods=['GET'])
@verify_wallet_sig
def list_notes(wallet):
    notes = Note.query.filter_by(wallet=wallet).order_by(Note.created_at.desc()).all()
    return jsonify({"notes": [n.to_dict() for n in notes]}), 200

@notes_bp.route('/notes/<note_id>/spent', methods=['PATCH'])
@require_api_token_or_wallet
def mark_spent(wallet, note_id):
    # wallet=None means called by executor via API token — find by note_id only
    # wallet=<address> means called by user — enforce ownership
    if wallet:
        note = Note.query.filter_by(id=note_id, wallet=wallet).first()
    else:
        note = Note.query.filter_by(id=note_id).first()
    if not note:
        return jsonify({"error": "Note not found"}), 404
    status = (request.json or {}).get('status', 'true')
    if status not in ('true', 'pending', 'false'):
        return jsonify({"error": "Invalid status"}), 400
    note.spent = status
    db.session.commit()
    return jsonify({"status": "ok"}), 200

@notes_bp.route('/notes/export', methods=['GET'])
@verify_wallet_sig
def export_notes(wallet):
    notes = Note.query.filter_by(wallet=wallet).all()
    return jsonify({"notes": [n.to_dict() for n in notes]}), 200
