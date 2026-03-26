from __future__ import annotations

from flask import Blueprint, g, jsonify
from sqlalchemy import or_

from app.db.session import SessionLocal
from app.models import AuditLog, MediaItem, MediaKind, ProcessingJob, ProcessingStatus, SafetyRating, User
from app.services.ai_limit_guard import get_ai_proxy_sleep_state
from app.services.memory_guard import get_processing_memory_guard_state
from app.services.ai_proxy import ANALYSIS_PROMPT
from app.services.disk_usage import summarize_disk_usage
from app.services.processor_monitor import get_processor_status
from app.services.processing_stats import build_processing_stats, recent_logs_query_for_user
from app.services.runtime_config import get_runtime_value
from app.utils.auth import admin_required, login_required


dashboard_bp = Blueprint("dashboard", __name__)


def _build_media_counts(media_query):
    total_media = media_query.count()
    ai_ready_count = media_query.filter(
        or_(
            MediaItem.ai_payload.is_not(None),
            MediaItem.description.is_not(None),
        )
    ).count()
    return {
        "media": total_media,
        "ai_ready": ai_ready_count,
        "media_by_kind": {
            "image": media_query.filter(MediaItem.kind == MediaKind.image).count(),
            "gif": media_query.filter(MediaItem.kind == MediaKind.gif).count(),
            "video": media_query.filter(MediaItem.kind == MediaKind.video).count(),
        },
        "media_by_status": {
            "pending": media_query.filter(MediaItem.processing_status == ProcessingStatus.pending).count(),
            "processing": media_query.filter(MediaItem.processing_status == ProcessingStatus.processing).count(),
            "complete": media_query.filter(MediaItem.processing_status == ProcessingStatus.complete).count(),
            "failed": media_query.filter(MediaItem.processing_status == ProcessingStatus.failed).count(),
        },
        "media_by_safety": {
            "sfw": media_query.filter(MediaItem.safety_rating == SafetyRating.sfw).count(),
            "questionable": media_query.filter(MediaItem.safety_rating == SafetyRating.questionable).count(),
            "nsfw": media_query.filter(MediaItem.safety_rating == SafetyRating.nsfw).count(),
            "unknown": media_query.filter(MediaItem.safety_rating == SafetyRating.unknown).count(),
        },
    }


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

        media_counts = _build_media_counts(media_query)
        recent_logs = logs_query.order_by(AuditLog.created_at.desc()).limit(20).all()
        return jsonify(
            {
                "counts": {
                    **media_counts,
                    "users": session.query(User).count() if g.current_user.role.value == "admin" else 1,
                    "jobs": jobs_query.count(),
                },
                "processing_stats": build_processing_stats(session, media_query, jobs_query, logs_query),
                "ai_proxy_sleep": get_ai_proxy_sleep_state(),
                "memory_guard": get_processing_memory_guard_state(),
                "processor": get_processor_status(),
                "processing_paused": bool(get_runtime_value("processing_paused")),
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
