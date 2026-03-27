from __future__ import annotations

import shutil
import tarfile
import zipfile
from collections import Counter
from pathlib import Path

import py7zr
import rarfile
from sqlalchemy import update
from werkzeug.datastructures import FileStorage

from app.config import settings
from app.db.session import SessionLocal
from app.models import ArchiveImport
from app.services.audit import audit
from app.services.media_probe import detect_media_kind
from app.services.storage import import_media_file, queue_media_for_processing


def _safe_target(base_dir: Path, member_name: str) -> Path:
    resolved = (base_dir / member_name).resolve()
    if not str(resolved).startswith(str(base_dir.resolve())):
        raise ValueError("Unsafe archive member path")
    return resolved


def _extract_zip(archive_path: Path, output_dir: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = _safe_target(output_dir, member.filename)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def _extract_tar(archive_path: Path, output_dir: Path) -> None:
    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            target = _safe_target(output_dir, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            with extracted as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def _extract_7z(archive_path: Path, output_dir: Path) -> None:
    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        archive.extractall(path=output_dir)


def _extract_rar(archive_path: Path, output_dir: Path) -> None:
    with rarfile.RarFile(archive_path) as archive:
        archive.extractall(path=output_dir)


def extract_archive(archive_path: Path, output_dir: Path) -> None:
    name = archive_path.name.lower()
    if name.endswith(".zip"):
        _extract_zip(archive_path, output_dir)
    elif name.endswith((".tar", ".tgz", ".tar.gz", ".tbz", ".tbz2", ".tar.bz2", ".gz", ".bz2")):
        _extract_tar(archive_path, output_dir)
    elif name.endswith(".7z"):
        _extract_7z(archive_path, output_dir)
    elif name.endswith(".rar"):
        _extract_rar(archive_path, output_dir)
    else:
        shutil.unpack_archive(str(archive_path), str(output_dir))


def _cleanup_import_artifacts(archive_path: Path, extracted_dir: Path) -> None:
    archive_path.unlink(missing_ok=True)
    shutil.rmtree(extracted_dir, ignore_errors=True)
    archive_root = archive_path.parent
    if archive_root.exists() and not any(archive_root.iterdir()):
        archive_root.rmdir()
    owner_root = archive_root.parent
    if owner_root.exists() and not any(owner_root.iterdir()):
        owner_root.rmdir()


def cleanup_archive_staging() -> None:
    if settings.archive_dir.exists():
        shutil.rmtree(settings.archive_dir, ignore_errors=True)
    settings.archive_dir.mkdir(parents=True, exist_ok=True)

    session = SessionLocal()
    try:
        session.execute(update(ArchiveImport).values(archive_path="", extracted_path=""))
        session.commit()
    finally:
        session.close()


def _ingest_saved_archive(
    session,
    owner_id: int,
    archive: ArchiveImport,
    archive_path: Path,
    *,
    auto_queue: bool = True,
) -> dict:
    extracted_dir = archive_path.parent / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)
    extract_archive(archive_path, extracted_dir)

    created_items: list[str] = []
    created_jobs: list[str] = []
    scanned_files = 0
    supported_files = 0
    unsupported_extensions: Counter[str] = Counter()
    for candidate in extracted_dir.rglob("*"):
        if not candidate.is_file():
            continue
        scanned_files += 1
        if detect_media_kind(candidate.name) is None:
            unsupported_extensions[candidate.suffix.lower() or "[no_extension]"] += 1
            continue
        supported_files += 1
        relative_path = str(candidate.relative_to(extracted_dir))
        item = import_media_file(
            session,
            owner_id,
            candidate,
            candidate.name,
            archive_id=archive.id,
            archive_relative_path=relative_path,
        )
        created_items.append(item.id)
        if auto_queue:
            job = queue_media_for_processing(session, item)
            created_jobs.append(job.id)

    imported_media_ids = list(dict.fromkeys(created_items))
    archive.file_count = len(imported_media_ids)
    archive.status = "complete" if imported_media_ids else "empty"
    archive.archive_path = ""
    archive.extracted_path = ""
    _cleanup_import_artifacts(archive_path, extracted_dir)
    session.flush()
    audit(
        "archive.ingested",
        f"Ingested archive {archive.original_filename}",
        owner_id=owner_id,
        context={
            "archive_id": archive.id,
            "media_count": len(imported_media_ids),
            "scanned_files": scanned_files,
            "supported_files": supported_files,
            "unsupported_files": max(scanned_files - supported_files, 0),
            "status": archive.status,
            "top_unsupported_extensions": unsupported_extensions.most_common(8),
        },
    )
    return {
        "archive_id": archive.id,
        "filename": archive.original_filename,
        "status": archive.status,
        "media_ids": imported_media_ids,
        "job_ids": created_jobs,
        "scanned_files": scanned_files,
        "supported_files": supported_files,
        "unsupported_files": max(scanned_files - supported_files, 0),
        "top_unsupported_extensions": [[extension, count] for extension, count in unsupported_extensions.most_common(8)],
        "artifacts_cleaned": True,
    }


def ingest_archive_path(
    session,
    owner_id: int,
    source_path: Path,
    original_filename: str,
    *,
    auto_queue: bool = True,
) -> dict:
    archive = ArchiveImport(
        owner_id=owner_id,
        original_filename=original_filename or source_path.name,
        archive_path="",
        extracted_path="",
    )
    session.add(archive)
    session.flush()

    archive_dir = settings.archive_dir / f"user_{owner_id}" / archive.id
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / (original_filename or f"{archive.id}.bin")
    source_path.replace(archive_path)
    return _ingest_saved_archive(session, owner_id, archive, archive_path, auto_queue=auto_queue)


def ingest_archive(session, owner_id: int, file: FileStorage, *, auto_queue: bool = True) -> dict:
    archive = ArchiveImport(
        owner_id=owner_id,
        original_filename=file.filename or "archive.bin",
        archive_path="",
        extracted_path="",
    )
    session.add(archive)
    session.flush()

    archive_dir = settings.archive_dir / f"user_{owner_id}" / archive.id
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / (file.filename or f"{archive.id}.bin")
    with archive_path.open("wb") as destination:
        shutil.copyfileobj(file.stream, destination)

    return _ingest_saved_archive(session, owner_id, archive, archive_path, auto_queue=auto_queue)
