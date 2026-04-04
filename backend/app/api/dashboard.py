from __future__ import annotations

from flask import Blueprint, g, jsonify
from sqlalchemy import case, false, func, or_

from app.db.session import SessionLocal
from app.models import AuditLog, MediaItem, MediaKind, ProcessingJob, ProcessingStatus, SafetyRating, User
from app.services.ai_limit_guard import get_ai_proxy_sleep_state
from app.services.memory_guard import get_processing_memory_guard_state
from app.services.ai_proxy import ANALYSIS_PROMPT
from app.services.disk_usage import summarize_disk_usage
from app.services.guest_access import apply_media_visibility_scope, guest_allowed_owner_ids
from app.services.processor_monitor import get_processor_status
from app.services.processing_stats import build_processing_stats, recent_logs_query_for_user
from app.services.runtime_config import get_runtime_value
from app.utils.auth import admin_required, login_required


dashboard_bp = Blueprint("dashboard", __name__)


def _build_media_counts(media_query):
    counts = media_query.with_entities(
        func.count(MediaItem.id).label("total_media"),
        func.sum(
            case(
                (
                    or_(
                        MediaItem.ai_payload.is_not(None),
                        MediaItem.description.is_not(None),
                    ),
                    1,
                ),
                else_=0,
            )
        ).label("ai_ready"),
        func.sum(case((MediaItem.kind == MediaKind.image, 1), else_=0)).label("image_count"),
        func.sum(case((MediaItem.kind == MediaKind.gif, 1), else_=0)).label("gif_count"),
        func.sum(case((MediaItem.kind == MediaKind.video, 1), else_=0)).label("video_count"),
        func.sum(case((MediaItem.processing_status == ProcessingStatus.pending, 1), else_=0)).label("pending_count"),
        func.sum(case((MediaItem.processing_status == ProcessingStatus.processing, 1), else_=0)).label("processing_count"),
        func.sum(case((MediaItem.processing_status == ProcessingStatus.complete, 1), else_=0)).label("complete_count"),
        func.sum(case((MediaItem.processing_status == ProcessingStatus.failed, 1), else_=0)).label("failed_count"),
        func.sum(case((MediaItem.safety_rating == SafetyRating.sfw, 1), else_=0)).label("sfw_count"),
        func.sum(case((MediaItem.safety_rating == SafetyRating.questionable, 1), else_=0)).label("questionable_count"),
        func.sum(case((MediaItem.safety_rating == SafetyRating.nsfw, 1), else_=0)).label("nsfw_count"),
        func.sum(case((MediaItem.safety_rating == SafetyRating.unknown, 1), else_=0)).label("unknown_count"),
    ).one()

    total_media = int(counts.total_media or 0)
    ai_ready_count = int(counts.ai_ready or 0)
    return {
        "media": total_media,
        "ai_ready": ai_ready_count,
        "media_by_kind": {
            "image": int(counts.image_count or 0),
            "gif": int(counts.gif_count or 0),
            "video": int(counts.video_count or 0),
        },
        "media_by_status": {
            "pending": int(counts.pending_count or 0),
            "processing": int(counts.processing_count or 0),
            "complete": int(counts.complete_count or 0),
            "failed": int(counts.failed_count or 0),
        },
        "media_by_safety": {
            "sfw": int(counts.sfw_count or 0),
            "questionable": int(counts.questionable_count or 0),
            "nsfw": int(counts.nsfw_count or 0),
            "unknown": int(counts.unknown_count or 0),
        },
    }


@dashboard_bp.get("/dashboard/overview")
@login_required
def overview():
    session = SessionLocal()
    try:
        media_query = apply_media_visibility_scope(session.query(MediaItem), g.current_user)
        jobs_query = session.query(ProcessingJob)
        logs_query = session.query(AuditLog)

        if g.current_user.role.value == "member":
            jobs_query = jobs_query.filter(ProcessingJob.owner_id == g.current_user.id)
            logs_query = recent_logs_query_for_user(logs_query, g.current_user)
        elif g.current_user.role.value == "guest":
            jobs_query = jobs_query.filter(false())
            logs_query = logs_query.filter(false())

        media_counts = _build_media_counts(media_query)
        recent_logs = logs_query.order_by(AuditLog.created_at.desc()).limit(20).all()
        visible_users = (
            len(guest_allowed_owner_ids(g.current_user))
            if g.current_user.role.value == "guest"
            else 1
        )
        return jsonify(
            {
                "counts": {
                    **media_counts,
                    "users": session.query(User).count() if g.current_user.role.value == "admin" else visible_users,
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
