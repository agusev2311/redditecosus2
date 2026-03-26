from __future__ import annotations

import shutil
import tarfile
import zipfile
from pathlib import Path

import py7zr
import rarfile
from werkzeug.datastructures import FileStorage

from app.config import settings
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

    extracted_dir = archive_dir / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)
    extract_archive(archive_path, extracted_dir)

    created_items: list[str] = []
    created_jobs: list[str] = []
    for candidate in extracted_dir.rglob("*"):
        if not candidate.is_file():
            continue
        if detect_media_kind(candidate.name) is None:
            continue
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

    archive.archive_path = str(archive_path.relative_to(settings.storage_root))
    archive.extracted_path = str(extracted_dir.relative_to(settings.storage_root))
    archive.file_count = len(created_items)
    archive.status = "complete"
    session.flush()
    audit(
        "archive.ingested",
        f"Ingested archive {archive.original_filename}",
        owner_id=owner_id,
        context={"archive_id": archive.id, "media_count": len(created_items)},
    )
    return {"archive_id": archive.id, "media_ids": created_items, "job_ids": created_jobs}
