from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.config import settings
from app.db.session import SessionLocal
from app.models import JobStatus, MediaItem, ProcessingJob, User, UserRole
from app.services.ai_limit_guard import clear_ai_proxy_limit_sleep
from app.services.audit import audit
from app.services.danger_zone import DANGER_RESET_CONFIRMATION, full_library_reset
from app.services.processing import get_processing_coordinator
from app.services.runtime_config import list_runtime_config_items, update_runtime_config_values
from app.services.storage import queue_media_for_processing
from app.utils.auth import admin_required, hash_password


admin_bp = Blueprint("admin", __name__)


def _serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role.value,
        "telegram_username": user.telegram_username,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@admin_bp.get("/users")
@admin_required
def list_users():
    session = SessionLocal()
    try:
        users = session.query(User).order_by(User.created_at.asc()).all()
        return jsonify({"items": [_serialize_user(user) for user in users]})
    finally:
        session.close()


@admin_bp.post("/users")
@admin_required
def create_user():
    payload = request.get_json(force=True)
    session = SessionLocal()
    try:
        user = User(
            username=payload["username"].strip(),
            password_hash=hash_password(payload["password"]),
            role=UserRole(payload.get("role", "member")),
            telegram_username=(payload.get("telegram_username") or "").strip().lstrip("@") or None,
        )
        session.add(user)
        session.commit()
        audit("admin.user_created", f"Created user {user.username}", actor_id=g.current_user.id, owner_id=user.id)
        return jsonify({"user": _serialize_user(user)}), 201
    finally:
        session.close()


@admin_bp.patch("/users/<int:user_id>")
@admin_required
def update_user(user_id: int):
    payload = request.get_json(force=True)
    session = SessionLocal()
    try:
        user = session.get(User, user_id)
        if user is None:
            return jsonify({"error": "Not found"}), 404
        if "role" in payload:
            user.role = UserRole(payload["role"])
        if "telegram_username" in payload:
            user.telegram_username = (payload.get("telegram_username") or "").strip().lstrip("@") or None
        if "password" in payload and payload["password"]:
            user.password_hash = hash_password(payload["password"])
        session.commit()
        audit("admin.user_updated", f"Updated user {user.username}", actor_id=g.current_user.id, owner_id=user.id)
        return jsonify({"user": _serialize_user(user)})
    finally:
        session.close()


@admin_bp.get("/admin/runtime-config")
@admin_required
def get_runtime_config():
    return jsonify({"items": list_runtime_config_items()})


@admin_bp.patch("/admin/runtime-config")
@admin_required
def patch_runtime_config():
    payload = request.get_json(force=True) or {}
    updates = payload.get("updates") or {}
    coordinator = get_processing_coordinator()
    values = update_runtime_config_values(updates, updated_by_id=g.current_user.id)
    if "processing_workers" in updates:
        coordinator.set_desired_workers(int(values["processing_workers"]))
    if "ai_proxy_max_concurrency" in updates or "processing_paused" in updates:
        coordinator.notify_capacity_changed()
    audit(
        "admin.runtime_config_updated",
        f"Updated runtime config keys: {', '.join(sorted(updates.keys()))}",
        actor_id=g.current_user.id,
        context={"keys": sorted(updates.keys())},
    )
    return jsonify({"items": list_runtime_config_items()})


@admin_bp.post("/admin/ai-proxy/resume")
@admin_required
def resume_ai_proxy():
    coordinator = get_processing_coordinator()
    state = clear_ai_proxy_limit_sleep(updated_by_id=g.current_user.id)
    coordinator.notify_capacity_changed()
    return jsonify({"ai_proxy_sleep": state})


@admin_bp.post("/admin/reindex-all")
@admin_required
def reindex_all_media():
    session = SessionLocal()
    queued_job_ids: list[str] = []
    skipped_active_media = 0
    total_media = 0
    try:
        media_rows = session.query(MediaItem.id, MediaItem.owner_id).order_by(MediaItem.created_at.asc()).all()
        total_media = len(media_rows)
        for media_id, owner_id in media_rows:
            active_job = (
                session.query(ProcessingJob.id)
                .filter(
                    ProcessingJob.media_id == media_id,
                    ProcessingJob.status.in_([JobStatus.queued, JobStatus.processing]),
                )
                .first()
            )
            if active_job:
                skipped_active_media += 1
                continue

            item = session.get(MediaItem, media_id)
            if item is None:
                continue
            job = queue_media_for_processing(session, item)
            queued_job_ids.append(job.id)
        session.commit()
    finally:
        session.close()

    if settings.enable_processing:
        coordinator = get_processing_coordinator()
        for job_id in queued_job_ids:
            coordinator.enqueue(job_id)

    audit(
        "admin.reindex_all_media",
        f"Queued full library reindex for {len(queued_job_ids)} items",
        actor_id=g.current_user.id,
        context={
            "total_media": total_media,
            "queued_jobs": len(queued_job_ids),
            "skipped_active_media": skipped_active_media,
        },
    )
    return jsonify(
        {
            "total_media": total_media,
            "queued_jobs": len(queued_job_ids),
            "skipped_active_media": skipped_active_media,
        }
    )


@admin_bp.post("/admin/danger/reset-library")
@admin_required
def reset_library():
    payload = request.get_json(force=True) or {}
    confirmation = str(payload.get("confirmation") or "")
    try:
        result = full_library_reset(confirmation=confirmation, updated_by_id=g.current_user.id)
    except ValueError as exc:
        return jsonify({"error": str(exc), "confirmation_phrase": DANGER_RESET_CONFIRMATION}), 400

    audit(
        "admin.danger_reset_requested",
        result["message"],
        actor_id=None if result["deleted"] else g.current_user.id,
        severity="warning",
        context={
            "deleted": result["deleted"],
            "paused": result["paused"],
            "processing_jobs": result["processing_jobs"],
            "queued_jobs": result["queued_jobs"],
            "media_count": result["media_count"],
            "user_count": result["user_count"],
        },
    )
    return jsonify({**result, "confirmation_phrase": DANGER_RESET_CONFIRMATION})
