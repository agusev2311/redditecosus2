from __future__ import annotations

from datetime import date, datetime, time, timezone

from flask import Blueprint, g, jsonify, request, send_file
from sqlalchemy import or_

from app.config import settings
from app.db.session import SessionLocal
from app.models import JobStatus, MediaItem, MediaTag, ProcessingJob, SafetyRating, Tag, TagKind, TagOrigin
from app.services.analysis_enrichment import normalize_tag_name
from app.services.archive import ingest_archive
from app.services.audit import audit
from app.services.media_probe import detect_file_type
from app.services.processing import enqueue_media, get_processing_coordinator
from app.services.storage import absolute_media_path, absolute_thumbnail_path, queue_media_for_processing, save_uploaded_media
from app.services.tag_catalog import get_tag_description_coordinator
from app.utils.auth import login_required


media_bp = Blueprint("media", __name__)

_RATING_TAGS = {rating.value for rating in SafetyRating}
_MEDIA_LIST_DESCRIPTION_MAX_CHARS = 240


def _trim_text(value: str | None, max_chars: int | None) -> str | None:
    if value is None or max_chars is None:
        return value
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _media_to_dict(item: MediaItem, *, include_full_payload: bool, description_max_chars: int | None = None) -> dict:
    thumbnail_path = absolute_thumbnail_path(item)
    description_ru = None
    description_en = None
    if isinstance(item.ai_payload, dict):
        description_ru = _trim_text(item.ai_payload.get("description_ru"), description_max_chars)
        description_en = _trim_text(item.ai_payload.get("description_en"), description_max_chars)
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
        "description": _trim_text(item.description, description_max_chars),
        "description_ru": description_ru,
        "description_en": description_en,
        "technical_notes": item.technical_notes if include_full_payload else None,
        "processing_status": item.processing_status.value,
        "normalized_timestamp": item.normalized_timestamp.isoformat() if item.normalized_timestamp else None,
        "thumbnail_url": f"/api/media/{item.id}/thumbnail" if thumbnail_path and thumbnail_path.exists() else None,
        "file_url": f"/api/media/{item.id}/file",
        "ai_payload": item.ai_payload if include_full_payload else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _tags_for_media_ids(session, media_ids: list[str]) -> dict[str, list[dict]]:
    tag_rows = (
        session.query(MediaTag.media_id, Tag.name, Tag.kind)
        .join(Tag, Tag.id == MediaTag.tag_id)
        .filter(MediaTag.media_id.in_(media_ids))
        .order_by(MediaTag.created_at.asc())
        .all()
        if media_ids
        else []
    )
    tag_map: dict[str, list[dict]] = {}
    for media_id, name, kind in tag_rows:
        tag_map.setdefault(media_id, []).append({"name": name, "kind": kind.value})
    return tag_map


def _serialize_media_item(
    session,
    item: MediaItem,
    *,
    include_full_payload: bool,
    description_max_chars: int | None = None,
) -> dict:
    tag_map = _tags_for_media_ids(session, [item.id])
    return {
        **_media_to_dict(
            item,
            include_full_payload=include_full_payload,
            description_max_chars=description_max_chars,
        ),
        "tags": tag_map.get(item.id, []),
    }


def _serialize_media_list(
    session,
    rows: list[MediaItem],
    *,
    include_full_payload: bool,
    description_max_chars: int | None = None,
) -> list[dict]:
    media_ids = [row.id for row in rows]
    tag_map = _tags_for_media_ids(session, media_ids)
    return [
        {
            **_media_to_dict(
                row,
                include_full_payload=include_full_payload,
                description_max_chars=description_max_chars,
            ),
            "tags": tag_map.get(row.id, []),
        }
        for row in rows
    ]


def _parse_datetime_filter(raw_value: str | None, *, end_of_day: bool) -> datetime | None:
    if not raw_value:
        return None
    value = raw_value.strip()
    if not value:
        return None
    if len(value) == 10:
        parsed_date = date.fromisoformat(value)
        parsed_time = time.max if end_of_day else time.min
        return datetime.combine(parsed_date, parsed_time, tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize_manual_safety_tags(raw_tags: list[str], rating: SafetyRating | None) -> list[str]:
    desired: list[str] = []
    seen: set[str] = set()
    for raw_tag in raw_tags:
        name = normalize_tag_name(raw_tag)
        if not name:
            continue
        if name in _RATING_TAGS and rating is not None and name != rating.value:
            continue
        if name in seen:
            continue
        seen.add(name)
        desired.append(name)
    if rating is not None and rating.value != SafetyRating.unknown.value and rating.value not in seen:
        desired.insert(0, rating.value)
    return desired


def _sync_safety_tags(session, item: MediaItem, rating: SafetyRating | None, safety_tags: list[str] | None) -> list[str]:
    current_safety_names = [
        tag.name
        for tag in session.query(Tag)
        .join(MediaTag, MediaTag.tag_id == Tag.id)
        .filter(MediaTag.media_id == item.id, Tag.kind == TagKind.safety)
        .order_by(Tag.name.asc())
        .all()
    ]
    effective_rating = rating
    requested_tags = safety_tags if safety_tags is not None else current_safety_names

    if effective_rating is None:
        for candidate in requested_tags:
            normalized = normalize_tag_name(candidate)
            if normalized in _RATING_TAGS:
                effective_rating = SafetyRating(normalized)
                break
    if effective_rating is None:
        effective_rating = item.safety_rating

    desired_names = _normalize_manual_safety_tags(requested_tags, effective_rating)

    current_links = (
        session.query(MediaTag)
        .join(Tag, Tag.id == MediaTag.tag_id)
        .filter(MediaTag.media_id == item.id, Tag.kind == TagKind.safety)
        .all()
    )
    for link in current_links:
        session.delete(link)

    for name in desired_names:
        tag = session.query(Tag).filter_by(owner_id=item.owner_id, name=name, kind=TagKind.safety).first()
        if tag is None:
            tag = Tag(owner_id=item.owner_id, name=name, kind=TagKind.safety)
            session.add(tag)
            session.flush()
        session.add(MediaTag(media_id=item.id, tag_id=tag.id, origin=TagOrigin.manual))

    item.safety_rating = effective_rating
    if isinstance(item.ai_payload, dict):
        payload = dict(item.ai_payload)
        payload["safety_rating"] = item.safety_rating.value
        payload["safety_tags"] = desired_names
        item.ai_payload = payload
    return desired_names


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
                if settings.enable_processing:
                    for job_id in archive_result.get("job_ids", []):
                        get_processing_coordinator().enqueue(job_id)
                imported_archives.append(archive_result)
                continue
            item = save_uploaded_media(session, g.current_user.id, file)
            job = queue_media_for_processing(session, item)
            if settings.enable_processing:
                get_processing_coordinator().enqueue(job.id)
            created.append(
                _media_to_dict(
                    item,
                    include_full_payload=False,
                    description_max_chars=_MEDIA_LIST_DESCRIPTION_MAX_CHARS,
                )
            )
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
        created_from = _parse_datetime_filter(request.args.get("created_from"), end_of_day=False)
        if created_from is not None:
            query = query.filter(MediaItem.created_at >= created_from)
        created_to = _parse_datetime_filter(request.args.get("created_to"), end_of_day=True)
        if created_to is not None:
            query = query.filter(MediaItem.created_at <= created_to)

        limit_raw = (request.args.get("limit") or "").strip()
        ordered_query = query.distinct().order_by(MediaItem.created_at.desc())
        if limit_raw:
            limit = max(1, min(int(limit_raw), 2000))
            rows = ordered_query.limit(limit).all()
        else:
            rows = ordered_query.all()
        return jsonify(
            {
                "items": _serialize_media_list(
                    session,
                    rows,
                    include_full_payload=False,
                    description_max_chars=_MEDIA_LIST_DESCRIPTION_MAX_CHARS,
                )
            }
        )
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
        return jsonify({"item": _serialize_media_item(session, item, include_full_payload=True)})
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
        parsed_rating = SafetyRating(payload["safety_rating"]) if "safety_rating" in payload and payload["safety_rating"] else None
        parsed_safety_tags = None
        if "safety_tags" in payload:
            raw_value = payload.get("safety_tags") or []
            if not isinstance(raw_value, list):
                return jsonify({"error": "safety_tags must be a list"}), 400
            parsed_safety_tags = [str(value) for value in raw_value]
        if parsed_rating is not None or parsed_safety_tags is not None:
            normalized_tags = _sync_safety_tags(session, item, parsed_rating, parsed_safety_tags)
            audit(
                "media.safety_updated",
                f"Updated safety metadata for {item.original_filename}",
                actor_id=g.current_user.id,
                owner_id=item.owner_id,
                context={"media_id": item.id, "rating": item.safety_rating.value, "safety_tags": normalized_tags},
            )
        session.commit()
        get_tag_description_coordinator().notify_backfill_needed()
        refreshed = session.get(MediaItem, media_id)
        return jsonify({"item": _serialize_media_item(session, refreshed, include_full_payload=True)})
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

    if settings.enable_processing:
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
