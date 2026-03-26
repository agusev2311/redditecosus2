from __future__ import annotations

from flask import Blueprint, g, jsonify, request, send_file
from sqlalchemy import or_

from app.db.session import SessionLocal
from app.models import JobStatus, MediaItem, MediaTag, ProcessingJob, Tag
from app.services.archive import ingest_archive
from app.services.audit import audit
from app.services.media_probe import detect_file_type
from app.services.processing import enqueue_media, get_processing_coordinator
from app.services.storage import absolute_media_path, absolute_thumbnail_path, queue_media_for_processing, save_uploaded_media
from app.utils.auth import login_required


media_bp = Blueprint("media", __name__)


def _media_to_dict(item: MediaItem) -> dict:
    thumbnail_path = absolute_thumbnail_path(item)
    description_ru = None
    description_en = None
    if isinstance(item.ai_payload, dict):
        description_ru = item.ai_payload.get("description_ru")
        description_en = item.ai_payload.get("description_en")
    return {
        "id": item.id,
        "kind": item.kind.value,
        "original_filename": item.original_filename,
        "source_path": item.source_path,
        "file_size": item.file_size,
        "width": item.width,
        "height": item.height,
        "duration_seconds": item.duration_seconds,
        "blur_score": item.blur_score,
        "safety_rating": item.safety_rating.value,
        "description": item.description,
        "description_ru": description_ru,
        "description_en": description_en,
        "technical_notes": item.technical_notes,
        "processing_status": item.processing_status.value,
        "normalized_timestamp": item.normalized_timestamp.isoformat() if item.normalized_timestamp else None,
        "thumbnail_url": f"/api/media/{item.id}/thumbnail" if thumbnail_path and thumbnail_path.exists() else None,
        "file_url": f"/api/media/{item.id}/file",
        "ai_payload": item.ai_payload,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _check_media_access(item: MediaItem, user) -> bool:
    return user.role.value == "admin" or item.owner_id == user.id


def _jobs_query_for_current_user(session):
    query = session.query(ProcessingJob)
    if g.current_user.role.value != "admin":
        query = query.filter(ProcessingJob.owner_id == g.current_user.id)
    return query


@media_bp.post("/media/upload")
@login_required
def upload_media():
    session = SessionLocal()
    try:
        created: list[dict] = []
        imported_archives: list[dict] = []
        for file in request.files.getlist("files"):
            file_type = detect_file_type(file.filename or "")
            if file_type == "archive":
                archive_result = ingest_archive(session, g.current_user.id, file)
                for job_id in archive_result.get("job_ids", []):
                    get_processing_coordinator().enqueue(job_id)
                imported_archives.append(archive_result)
                continue
            item = save_uploaded_media(session, g.current_user.id, file)
            job = queue_media_for_processing(session, item)
            get_processing_coordinator().enqueue(job.id)
            created.append(_media_to_dict(item))
        session.commit()
        return jsonify({"items": created, "archives": imported_archives})
    finally:
        session.close()


@media_bp.get("/media")
@login_required
def list_media():
    session = SessionLocal()
    try:
        query = session.query(MediaItem)
        if g.current_user.role.value != "admin":
            query = query.filter(MediaItem.owner_id == g.current_user.id)
        elif request.args.get("owner_id"):
            query = query.filter(MediaItem.owner_id == int(request.args["owner_id"]))

        search = request.args.get("q", "").strip()
        if search:
            query = query.outerjoin(MediaTag, MediaTag.media_id == MediaItem.id).outerjoin(Tag, Tag.id == MediaTag.tag_id)
            query = query.filter(
                or_(
                    MediaItem.original_filename.ilike(f"%{search}%"),
                    MediaItem.description.ilike(f"%{search}%"),
                    Tag.name.ilike(f"%{search.lower().replace(' ', '_')}%"),
                )
            )

        if request.args.get("kind"):
            query = query.filter(MediaItem.kind == request.args["kind"])
        if request.args.get("rating"):
            query = query.filter(MediaItem.safety_rating == request.args["rating"])
        if request.args.get("status"):
            query = query.filter(MediaItem.processing_status == request.args["status"])

        rows = query.distinct().order_by(MediaItem.created_at.desc()).limit(300).all()
        media_ids = [row.id for row in rows]
        tag_rows = (
            session.query(MediaTag.media_id, Tag.name, Tag.kind)
            .join(Tag, Tag.id == MediaTag.tag_id)
            .filter(MediaTag.media_id.in_(media_ids))
            .all()
            if media_ids
            else []
        )
        tag_map: dict[str, list[dict]] = {}
        for media_id, name, kind in tag_rows:
            tag_map.setdefault(media_id, []).append({"name": name, "kind": kind.value})
        return jsonify({"items": [{**_media_to_dict(row), "tags": tag_map.get(row.id, [])} for row in rows]})
    finally:
        session.close()


@media_bp.get("/media/<media_id>")
@login_required
def get_media(media_id: str):
    session = SessionLocal()
    try:
        item = session.get(MediaItem, media_id)
        if item is None or not _check_media_access(item, g.current_user):
            return jsonify({"error": "Not found"}), 404
        tag_rows = (
            session.query(Tag.name, Tag.kind)
            .join(MediaTag, MediaTag.tag_id == Tag.id)
            .filter(MediaTag.media_id == media_id)
            .all()
        )
        return jsonify({"item": _media_to_dict(item), "tags": [{"name": name, "kind": kind.value} for name, kind in tag_rows]})
    finally:
        session.close()


@media_bp.get("/media/<media_id>/file")
@login_required
def stream_media(media_id: str):
    session = SessionLocal()
    try:
        item = session.get(MediaItem, media_id)
        if item is None or not _check_media_access(item, g.current_user):
            return jsonify({"error": "Not found"}), 404
        return send_file(absolute_media_path(item), mimetype=item.mime_type, download_name=item.original_filename)
    finally:
        session.close()


@media_bp.get("/media/<media_id>/file/public")
def stream_media_public(media_id: str):
    session = SessionLocal()
    try:
        item = session.get(MediaItem, media_id)
        if item is None:
            return jsonify({"error": "Not found"}), 404
        return send_file(absolute_media_path(item), mimetype=item.mime_type, download_name=item.original_filename)
    finally:
        session.close()


@media_bp.get("/media/<media_id>/thumbnail")
@login_required
def stream_thumbnail(media_id: str):
    session = SessionLocal()
    try:
        item = session.get(MediaItem, media_id)
        if item is None or not _check_media_access(item, g.current_user):
            return jsonify({"error": "Not found"}), 404
        thumbnail = absolute_thumbnail_path(item)
        if thumbnail is None or not thumbnail.exists():
            return jsonify({"error": "Thumbnail missing"}), 404
        return send_file(thumbnail, mimetype="image/jpeg")
    finally:
        session.close()


@media_bp.get("/media/<media_id>/thumbnail/public")
def stream_thumbnail_public(media_id: str):
    session = SessionLocal()
    try:
        item = session.get(MediaItem, media_id)
        if item is None:
            return jsonify({"error": "Not found"}), 404
        thumbnail = absolute_thumbnail_path(item)
        if thumbnail is None or not thumbnail.exists():
            return jsonify({"error": "Thumbnail missing"}), 404
        return send_file(thumbnail, mimetype="image/jpeg")
    finally:
        session.close()


@media_bp.patch("/media/<media_id>")
@login_required
def update_media(media_id: str):
    payload = request.get_json(force=True)
    session = SessionLocal()
    try:
        item = session.get(MediaItem, media_id)
        if item is None or not _check_media_access(item, g.current_user):
            return jsonify({"error": "Not found"}), 404
        if "description" in payload:
            item.description = payload["description"]
        if "safety_rating" in payload:
            item.safety_rating = payload["safety_rating"]
        session.commit()
        return jsonify({"item": _media_to_dict(item)})
    finally:
        session.close()


@media_bp.post("/media/<media_id>/reindex")
@login_required
def reindex_media(media_id: str):
    session = SessionLocal()
    try:
        item = session.get(MediaItem, media_id)
        if item is None or not _check_media_access(item, g.current_user):
            return jsonify({"error": "Not found"}), 404
    finally:
        session.close()
    return jsonify({"job_id": enqueue_media(media_id)})


@media_bp.get("/jobs")
@login_required
def list_jobs():
    session = SessionLocal()
    try:
        query = _jobs_query_for_current_user(session)
        rows = query.order_by(ProcessingJob.created_at.desc()).limit(100).all()
        return jsonify(
            {
                "items": [
                    {
                        "id": row.id,
                        "media_id": row.media_id,
                        "status": row.status.value,
                        "attempts": row.attempts,
                        "error_message": row.error_message,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                    }
                    for row in rows
                ]
            }
        )
    finally:
        session.close()


@media_bp.post("/jobs/retry-failed")
@login_required
def retry_failed_jobs():
    session = SessionLocal()
    queued_job_ids: list[str] = []
    skipped_active_media_ids: list[str] = []
    skipped_missing_media_ids: list[str] = []
    retried_media_ids: list[str] = []
    failed_media_ids_seen: set[str] = set()

    try:
        failed_jobs = (
            _jobs_query_for_current_user(session)
            .filter(ProcessingJob.status == JobStatus.failed)
            .order_by(ProcessingJob.created_at.desc())
            .all()
        )

        for failed_job in failed_jobs:
            media_id = failed_job.media_id
            if media_id in failed_media_ids_seen:
                continue
            failed_media_ids_seen.add(media_id)

            item = session.get(MediaItem, media_id)
            if item is None or not _check_media_access(item, g.current_user):
                skipped_missing_media_ids.append(media_id)
                continue

            active_job = (
                session.query(ProcessingJob.id)
                .filter(
                    ProcessingJob.media_id == media_id,
                    ProcessingJob.status.in_([JobStatus.queued, JobStatus.processing]),
                )
                .first()
            )
            if active_job:
                skipped_active_media_ids.append(media_id)
                continue

            job = queue_media_for_processing(session, item)
            queued_job_ids.append(job.id)
            retried_media_ids.append(media_id)

        session.commit()
    finally:
        session.close()

    for job_id in queued_job_ids:
        get_processing_coordinator().enqueue(job_id)

    audit(
        "media.retry_failed_jobs",
        f"Retried failed jobs: queued {len(queued_job_ids)} media items",
        actor_id=g.current_user.id,
        owner_id=None if g.current_user.role.value == "admin" else g.current_user.id,
        context={
            "failed_jobs_total": len(failed_jobs) if 'failed_jobs' in locals() else 0,
            "failed_media_total": len(failed_media_ids_seen),
            "queued_jobs": len(queued_job_ids),
            "skipped_active_media": len(skipped_active_media_ids),
            "skipped_missing_media": len(skipped_missing_media_ids),
        },
    )

    return jsonify(
        {
            "failed_jobs_total": len(failed_jobs) if 'failed_jobs' in locals() else 0,
            "failed_media_total": len(failed_media_ids_seen),
            "queued_jobs": len(queued_job_ids),
            "queued_media_ids": retried_media_ids,
            "skipped_active_media": len(skipped_active_media_ids),
            "skipped_missing_media": len(skipped_missing_media_ids),
        }
    )
