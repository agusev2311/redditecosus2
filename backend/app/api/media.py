from __future__ import annotations

from datetime import date, datetime, time, timezone

from flask import Blueprint, g, jsonify, request, send_file
from sqlalchemy import and_, or_
from sqlalchemy.orm import load_only

from app.config import settings
from app.db.session import SessionLocal
from app.models import JobStatus, MediaItem, MediaTag, ProcessingJob, SafetyRating, Tag, TagKind, TagOrigin
from app.services.analysis_enrichment import normalize_tag_name
from app.services.archive import ingest_archive, ingest_archive_path
from app.services.audit import audit
from app.services.media_probe import detect_file_type
from app.services.processing import enqueue_media, get_processing_coordinator
from app.services.resumable_uploads import (
    discard_upload_session,
    finalize_upload_session,
    get_upload_session,
    prepare_upload_session,
    serialize_upload_session,
    write_upload_chunk,
)
from app.services.storage import (
    absolute_media_path,
    absolute_thumbnail_path,
    queue_media_for_processing,
    save_staged_media,
    save_uploaded_media,
)
from app.services.tag_catalog import get_tag_description_coordinator
from app.utils.auth import login_required


media_bp = Blueprint("media", __name__)

_RATING_TAGS = {rating.value for rating in SafetyRating}
_MEDIA_LIST_DESCRIPTION_MAX_CHARS = 240
_MEDIA_PAGE_LIMIT_DEFAULT = 48
_MEDIA_PAGE_LIMIT_MAX = 200
_MEDIA_LIST_COLUMNS = (
    MediaItem.id,
    MediaItem.kind,
    MediaItem.original_filename,
    MediaItem.source_path,
    MediaItem.file_size,
    MediaItem.width,
    MediaItem.height,
    MediaItem.duration_seconds,
    MediaItem.blur_score,
    MediaItem.safety_rating,
    MediaItem.description,
    MediaItem.processing_status,
    MediaItem.normalized_timestamp,
    MediaItem.thumbnail_path,
    MediaItem.created_at,
)


def _trim_text(value: str | None, max_chars: int | None) -> str | None:
    if value is None or max_chars is None:
        return value
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _media_to_dict(
    item: MediaItem,
    *,
    include_full_payload: bool,
    include_localized_descriptions: bool = True,
    description_max_chars: int | None = None,
) -> dict:
    thumbnail_path = absolute_thumbnail_path(item)
    description_ru = None
    description_en = None
    if include_localized_descriptions and isinstance(item.ai_payload, dict):
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
    include_localized_descriptions: bool = True,
    description_max_chars: int | None = None,
) -> dict:
    tag_map = _tags_for_media_ids(session, [item.id])
    return {
        **_media_to_dict(
            item,
            include_full_payload=include_full_payload,
            include_localized_descriptions=include_localized_descriptions,
            description_max_chars=description_max_chars,
        ),
        "tags": tag_map.get(item.id, []),
    }


def _serialize_media_list(
    session,
    rows: list[MediaItem],
    *,
    include_full_payload: bool,
    include_localized_descriptions: bool = True,
    description_max_chars: int | None = None,
) -> list[dict]:
    media_ids = [row.id for row in rows]
    tag_map = _tags_for_media_ids(session, media_ids)
    return [
        {
            **_media_to_dict(
                row,
                include_full_payload=include_full_payload,
                include_localized_descriptions=include_localized_descriptions,
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


def _parse_positive_int(raw_value: str | None, *, default: int, minimum: int = 1, maximum: int = _MEDIA_PAGE_LIMIT_MAX) -> int:
    value = default
    if raw_value:
        value = int(raw_value)
    return max(minimum, min(value, maximum))


def _make_media_cursor(item: MediaItem) -> str | None:
    if item.created_at is None:
        return None
    return f"{item.created_at.isoformat()}|{item.id}"


def _parse_media_cursor(raw_value: str | None) -> tuple[datetime, str] | None:
    if not raw_value:
        return None
    created_at_raw, separator, media_id = raw_value.partition("|")
    if not separator or not media_id:
        raise ValueError("Invalid cursor")
    created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at, media_id


def _send_uncached_file(path, *, mimetype: str | None, download_name: str | None = None):
    response = send_file(
        path,
        mimetype=mimetype,
        download_name=download_name,
        conditional=True,
        max_age=0,
        etag=False,
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


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


@media_bp.post("/uploads/init")
@login_required
def init_upload():
    payload = request.get_json(force=True)
    file_name = str(payload.get("file_name") or "").strip()
    if not file_name:
        return jsonify({"error": "file_name is required"}), 400

    try:
        file_size = int(payload.get("file_size") or 0)
        last_modified_raw = payload.get("last_modified")
        last_modified = int(last_modified_raw) if last_modified_raw not in {None, ""} else None
        chunk_size_raw = payload.get("chunk_size")
        chunk_size = int(chunk_size_raw) if chunk_size_raw not in {None, ""} else None
        state = prepare_upload_session(
            owner_id=g.current_user.id,
            file_name=file_name,
            file_size=file_size,
            last_modified=last_modified,
            content_type=str(payload.get("content_type") or "").strip() or None,
            desired_chunk_size=chunk_size,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"upload": serialize_upload_session(state)})


@media_bp.put("/uploads/<upload_id>/parts/<int:part_index>")
@login_required
def upload_chunk(upload_id: str, part_index: int):
    try:
        state = write_upload_chunk(upload_id, g.current_user.id, part_index, request.stream)
    except FileNotFoundError:
        return jsonify({"error": "Upload session not found"}), 404
    except PermissionError:
        return jsonify({"error": "Upload session not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"upload": serialize_upload_session(state)})


@media_bp.post("/uploads/<upload_id>/complete")
@login_required
def complete_upload(upload_id: str):
    try:
        state, staged_path = finalize_upload_session(upload_id, g.current_user.id)
    except FileNotFoundError:
        return jsonify({"error": "Upload session not found"}), 404
    except PermissionError:
        return jsonify({"error": "Upload session not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    session = SessionLocal()
    direct_jobs: list[str] = []
    created_items: list[dict] = []
    imported_archives: list[dict] = []
    try:
        if state.file_type == "archive":
            archive_result = ingest_archive_path(
                session,
                g.current_user.id,
                staged_path,
                state.file_name,
                auto_queue=True,
            )
            imported_archives.append(archive_result)
            direct_jobs.extend(archive_result.get("job_ids", []))
        else:
            item = save_staged_media(session, g.current_user.id, staged_path, state.file_name)
            job = queue_media_for_processing(session, item)
            direct_jobs.append(job.id)
            created_items.append(
                _media_to_dict(
                    item,
                    include_full_payload=False,
                    include_localized_descriptions=False,
                    description_max_chars=_MEDIA_LIST_DESCRIPTION_MAX_CHARS,
                )
            )
        session.commit()
    except ValueError as exc:
        session.rollback()
        return jsonify({"error": str(exc)}), 400
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    discard_upload_session(upload_id)
    if settings.enable_processing:
        coordinator = get_processing_coordinator()
        for job_id in direct_jobs:
            coordinator.enqueue(job_id)

    return jsonify({"items": created_items, "archives": imported_archives})


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
        query = session.query(MediaItem).options(load_only(*_MEDIA_LIST_COLUMNS))
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

        cursor = _parse_media_cursor((request.args.get("cursor") or "").strip() or None)
        if cursor is not None:
            cursor_created_at, cursor_id = cursor
            query = query.filter(
                or_(
                    MediaItem.created_at < cursor_created_at,
                    and_(MediaItem.created_at == cursor_created_at, MediaItem.id < cursor_id),
                )
            )

        limit = _parse_positive_int(
            (request.args.get("limit") or "").strip() or None,
            default=_MEDIA_PAGE_LIMIT_DEFAULT,
        )
        ordered_query = query.distinct().order_by(MediaItem.created_at.desc(), MediaItem.id.desc())
        page = ordered_query.limit(limit + 1).all()
        has_more = len(page) > limit
        rows = page[:limit]
        next_cursor = _make_media_cursor(rows[-1]) if has_more and rows else None
        return jsonify(
            {
                "items": _serialize_media_list(
                    session,
                    rows,
                    include_full_payload=False,
                    include_localized_descriptions=False,
                    description_max_chars=_MEDIA_LIST_DESCRIPTION_MAX_CHARS,
                ),
                "has_more": has_more,
                "next_cursor": next_cursor,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
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
        return _send_uncached_file(
            absolute_media_path(item),
            mimetype=item.mime_type,
            download_name=item.original_filename,
        )
    finally:
        session.close()


@media_bp.get("/media/<media_id>/file/public")
def stream_media_public(media_id: str):
    session = SessionLocal()
    try:
        item = session.get(MediaItem, media_id)
        if item is None:
            return jsonify({"error": "Not found"}), 404
        return _send_uncached_file(
            absolute_media_path(item),
            mimetype=item.mime_type,
            download_name=item.original_filename,
        )
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
        return _send_uncached_file(thumbnail, mimetype="image/jpeg")
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
        return _send_uncached_file(thumbnail, mimetype="image/jpeg")
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
