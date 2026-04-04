from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from werkzeug.datastructures import FileStorage

from app.config import settings
from app.models import MediaItem, MediaKind, ProcessingJob, ProcessingStatus, TimestampPrecision
from app.services.audit import audit
from app.services.filename_time import parse_filename_timestamp
from app.services.media_probe import create_thumbnail, detect_media_kind, probe_media


def ensure_storage_layout() -> None:
    for path in [
        settings.storage_root,
        settings.incoming_dir,
        settings.media_dir,
        settings.archive_dir,
        settings.thumbnails_dir,
        settings.backups_dir,
        settings.logs_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def _owner_dir(owner_id: int) -> Path:
    return settings.media_dir / f"user_{owner_id}"


def _cleanup_empty_directory(path: Path | None) -> None:
    if path is None or not path.exists() or not path.is_dir():
        return
    if any(path.iterdir()):
        return
    path.rmdir()


def _write_stream(input_stream, destination: Path) -> tuple[str, int]:
    sha256 = hashlib.sha256()
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as target:
        while True:
            chunk = input_stream.read(1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
            total += len(chunk)
            target.write(chunk)
    return sha256.hexdigest(), total


def _copy_file_with_hash(source: Path, destination: Path) -> tuple[str, int]:
    sha256 = hashlib.sha256()
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_stream, destination.open("wb") as target:
        while True:
            chunk = input_stream.read(1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
            total += len(chunk)
            target.write(chunk)
    return sha256.hexdigest(), total


def _hash_existing_file(source: Path) -> tuple[str, int]:
    sha256 = hashlib.sha256()
    total = 0
    with source.open("rb") as input_stream:
        while True:
            chunk = input_stream.read(1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
            total += len(chunk)
    return sha256.hexdigest(), total


def _guess_mime_type(filename: str, kind: MediaKind) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    if kind == MediaKind.image:
        return "image/jpeg"
    if kind == MediaKind.gif:
        return "image/gif"
    if kind == MediaKind.video:
        return "video/mp4"
    return "application/octet-stream"


def _make_media_item(
    *,
    session,
    owner_id: int,
    kind: MediaKind,
    original_filename: str,
    source_path: str | None,
    final_path: Path,
    sha256: str,
    file_size: int,
    archive_id: str | None = None,
) -> MediaItem:
    normalized = parse_filename_timestamp(original_filename)
    item = MediaItem(
        owner_id=owner_id,
        archive_id=archive_id,
        kind=kind,
        original_filename=original_filename,
        source_path=source_path,
        storage_path=str(final_path.relative_to(settings.storage_root)),
        mime_type=_guess_mime_type(original_filename, kind),
        sha256=sha256,
        file_size=file_size,
        width=None,
        height=None,
        duration_seconds=None,
        blur_score=None,
        processing_status=ProcessingStatus.pending,
        normalized_timestamp=normalized.value if normalized else None,
        timestamp_precision=normalized.precision if normalized else TimestampPrecision.none,
    )
    session.add(item)
    session.flush()
    return item


def save_uploaded_media(session, owner_id: int, file: FileStorage, *, source_path: str | None = None, archive_id: str | None = None) -> MediaItem:
    kind = detect_media_kind(file.filename or "")
    if kind is None:
        raise ValueError("Unsupported media type")

    item_dir = _owner_dir(owner_id) / "items"
    temp_name = hashlib.sha1(f"{owner_id}:{file.filename}".encode("utf-8")).hexdigest()[:12]
    extension = Path(file.filename or "").suffix.lower() or ".bin"
    final_path = item_dir / f"{temp_name}{extension}"
    sha256, file_size = _write_stream(file.stream, final_path)

    existing = session.query(MediaItem).filter_by(owner_id=owner_id, sha256=sha256).first()
    if existing:
        final_path.unlink(missing_ok=True)
        return existing

    item = _make_media_item(
        session=session,
        owner_id=owner_id,
        kind=kind,
        original_filename=file.filename or final_path.name,
        source_path=source_path,
        final_path=final_path,
        sha256=sha256,
        file_size=file_size,
        archive_id=archive_id,
    )
    renamed = item_dir / f"{item.id}{extension}"
    final_path.rename(renamed)
    item.storage_path = str(renamed.relative_to(settings.storage_root))
    session.flush()
    audit(
        "media.uploaded",
        f"Uploaded media {item.original_filename}",
        owner_id=owner_id,
        context={"media_id": item.id, "kind": item.kind.value},
    )
    return item


def import_media_file(session, owner_id: int, source_path: Path, original_filename: str, *, archive_id: str, archive_relative_path: str) -> MediaItem:
    kind = detect_media_kind(original_filename)
    if kind is None:
        raise ValueError("Unsupported media type")
    item_dir = _owner_dir(owner_id) / "items"
    extension = source_path.suffix.lower() or ".bin"
    temp_path = item_dir / f"import_{hashlib.sha1(str(source_path).encode('utf-8')).hexdigest()[:12]}{extension}"
    digest, file_size = _copy_file_with_hash(source_path, temp_path)

    existing = session.query(MediaItem).filter_by(owner_id=owner_id, sha256=digest).first()
    if existing:
        temp_path.unlink(missing_ok=True)
        return existing

    item = _make_media_item(
        session=session,
        owner_id=owner_id,
        kind=kind,
        original_filename=original_filename,
        source_path=archive_relative_path,
        final_path=temp_path,
        sha256=digest,
        file_size=file_size,
        archive_id=archive_id,
    )
    final_path = item_dir / f"{item.id}{extension}"
    temp_path.rename(final_path)
    item.storage_path = str(final_path.relative_to(settings.storage_root))
    session.flush()
    return item


def save_staged_media(
    session,
    owner_id: int,
    staged_path: Path,
    original_filename: str,
    *,
    source_path: str | None = None,
    archive_id: str | None = None,
) -> MediaItem:
    kind = detect_media_kind(original_filename)
    if kind is None:
        raise ValueError("Unsupported media type")

    extension = Path(original_filename or staged_path.name).suffix.lower() or staged_path.suffix.lower() or ".bin"
    digest, file_size = _hash_existing_file(staged_path)

    existing = session.query(MediaItem).filter_by(owner_id=owner_id, sha256=digest).first()
    if existing:
        staged_path.unlink(missing_ok=True)
        return existing

    item = _make_media_item(
        session=session,
        owner_id=owner_id,
        kind=kind,
        original_filename=original_filename or staged_path.name,
        source_path=source_path,
        final_path=staged_path,
        sha256=digest,
        file_size=file_size,
        archive_id=archive_id,
    )
    item_dir = _owner_dir(owner_id) / "items"
    item_dir.mkdir(parents=True, exist_ok=True)
    final_path = item_dir / f"{item.id}{extension}"
    staged_path.replace(final_path)
    item.storage_path = str(final_path.relative_to(settings.storage_root))
    session.flush()
    audit(
        "media.uploaded",
        f"Uploaded media {item.original_filename}",
        owner_id=owner_id,
        context={"media_id": item.id, "kind": item.kind.value},
    )
    return item


def queue_media_for_processing(session, item: MediaItem) -> ProcessingJob:
    existing = (
        session.query(ProcessingJob)
        .filter_by(media_id=item.id, status="queued")
        .order_by(ProcessingJob.created_at.desc())
        .first()
    )
    if existing:
        return existing
    job = ProcessingJob(owner_id=item.owner_id, media_id=item.id)
    session.add(job)
    item.processing_status = ProcessingStatus.pending
    session.flush()
    return job


def absolute_media_path(item: MediaItem) -> Path:
    return settings.storage_root / item.storage_path


def absolute_thumbnail_path(item: MediaItem) -> Path | None:
    if not item.thumbnail_path:
        return None
    return settings.storage_root / item.thumbnail_path


def delete_media_artifacts(item: MediaItem) -> list[str]:
    removed: list[str] = []
    media_path = absolute_media_path(item)
    thumbnail_path = absolute_thumbnail_path(item)

    if media_path.exists():
        media_path.unlink(missing_ok=True)
        removed.append(str(media_path.relative_to(settings.storage_root)))
    if thumbnail_path is not None and thumbnail_path.exists():
        thumbnail_path.unlink(missing_ok=True)
        removed.append(str(thumbnail_path.relative_to(settings.storage_root)))

    _cleanup_empty_directory(media_path.parent)
    _cleanup_empty_directory(media_path.parent.parent)
    if thumbnail_path is not None and thumbnail_path.parent != settings.thumbnails_dir:
        _cleanup_empty_directory(thumbnail_path.parent)
    return removed


def ensure_media_artifacts(session, item: MediaItem, *, force: bool = False) -> bool:
    changed = False
    media_path = absolute_media_path(item)
    if not media_path.exists():
        raise FileNotFoundError(media_path)

    needs_probe = force or any(
        value is None
        for value in (
            item.width,
            item.height,
            item.duration_seconds if item.kind == MediaKind.video else 0,
            item.blur_score if item.kind in {MediaKind.image, MediaKind.video} else 0,
        )
    )
    if needs_probe:
        probe = probe_media(media_path, item.kind)
        item.mime_type = probe.mime_type or item.mime_type
        item.width = probe.width
        item.height = probe.height
        item.duration_seconds = probe.duration_seconds
        item.blur_score = probe.blur_score
        changed = True

    thumbnail_path = absolute_thumbnail_path(item)
    needs_thumbnail = force or thumbnail_path is None or not thumbnail_path.exists()
    if needs_thumbnail:
        expected_path = settings.thumbnails_dir / f"{item.id}.jpg"
        create_thumbnail(media_path, item.kind, expected_path)
        if expected_path.exists():
            relative_path = str(expected_path.relative_to(settings.storage_root))
            if item.thumbnail_path != relative_path:
                item.thumbnail_path = relative_path
                changed = True

    if changed:
        session.flush()
    return changed
