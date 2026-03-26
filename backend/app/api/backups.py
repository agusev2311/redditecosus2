from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.db.session import SessionLocal
from app.models import BackupScope, BackupSnapshot, UserRole
from app.services.backup import backup_service
from app.utils.auth import login_required


backups_bp = Blueprint("backups", __name__)


@backups_bp.get("/backups")
@login_required
def list_backups():
    from flask import g

    session = SessionLocal()
    try:
        query = session.query(BackupSnapshot)
        if g.current_user.role != UserRole.admin:
            query = query.filter(
                (BackupSnapshot.requested_by_id == g.current_user.id)
                | (BackupSnapshot.owner_id == g.current_user.id)
            )
        rows = query.order_by(BackupSnapshot.created_at.desc()).limit(100).all()
        return jsonify(
            {
                "items": [
                    {
                        "id": row.id,
                        "scope": row.scope.value,
                        "status": row.status.value,
                        "parts": row.parts or [],
                        "manifest": row.manifest or {},
                        "error_message": row.error_message,
                        "owner_id": row.owner_id,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                    }
                    for row in rows
                ]
            }
        )
    finally:
        session.close()


@backups_bp.post("/backups")
@login_required
def create_backup():
    from flask import g

    payload = request.get_json(force=True)
    owner_id = payload.get("owner_id")
    if g.current_user.role != UserRole.admin:
        owner_id = g.current_user.id
    snapshot_id = backup_service.create_snapshot(
        requester_id=g.current_user.id,
        scope=BackupScope(payload.get("scope", "metadata")),
        owner_id=owner_id,
        send_to_telegram=bool(payload.get("send_to_telegram", True)),
    )
    return jsonify({"backup_id": snapshot_id}), 202

