from __future__ import annotations

from flask import Blueprint, g, jsonify

from app.db.session import SessionLocal
from app.models import AuditLog, MediaItem, ProcessingJob, User
from app.services.ai_proxy import ANALYSIS_PROMPT
from app.services.disk_usage import summarize_disk_usage
from app.services.processing_stats import build_processing_stats, recent_logs_query_for_user
from app.utils.auth import admin_required, login_required


dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.get("/dashboard/overview")
@login_required
def overview():
    session = SessionLocal()
    try:
        media_query = session.query(MediaItem)
        jobs_query = session.query(ProcessingJob)
        logs_query = session.query(AuditLog)
        if g.current_user.role.value != "admin":
            media_query = media_query.filter(MediaItem.owner_id == g.current_user.id)
            jobs_query = jobs_query.filter(ProcessingJob.owner_id == g.current_user.id)
            logs_query = recent_logs_query_for_user(logs_query, g.current_user)

        recent_logs = logs_query.order_by(AuditLog.created_at.desc()).limit(20).all()
        return jsonify(
            {
                "counts": {
                    "media": media_query.count(),
                    "users": session.query(User).count() if g.current_user.role.value == "admin" else 1,
                    "jobs": jobs_query.count(),
                },
                "processing_stats": build_processing_stats(session, jobs_query, logs_query),
                "recent_logs": [
                    {
                        "id": row.id,
                        "event_type": row.event_type,
                        "message": row.message,
                        "severity": row.severity,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in recent_logs
                ],
                "prompt_preview": ANALYSIS_PROMPT,
            }
        )
    finally:
        session.close()


@dashboard_bp.get("/dashboard/storage")
@admin_required
def storage():
    return jsonify(summarize_disk_usage())


@dashboard_bp.get("/dashboard/logs")
@admin_required
def logs():
    session = SessionLocal()
    try:
        rows = session.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).all()
        return jsonify(
            {
                "items": [
                    {
                        "id": row.id,
                        "event_type": row.event_type,
                        "severity": row.severity,
                        "message": row.message,
                        "context": row.context,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in rows
                ]
            }
        )
    finally:
        session.close()
