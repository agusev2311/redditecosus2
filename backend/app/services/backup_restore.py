from __future__ import annotations

import json
import re
import shutil
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.datastructures import FileStorage

from app.config import settings
from app.db.session import SessionLocal, init_db
from app.models import BackupSnapshot
from app.services.audit import audit
from app.services.backup import BACKUP_SCHEMA_VERSION, ensure_no_active_processing_jobs, utcnow
from app.services.danger_zone import arm_processing_pause
from app.services.storage import ensure_storage_layout


_IMPORT_TTL = timedelta(hours=24)
_RESTORE_CONFIRMATION = "RESTORE BACKUP"
_PART_NUMBER_RE = re.compile(r"part(\d+)", re.IGNORECASE)


def _imports_root() -> Path:
    return settings.data_root / "backup_imports"


def cleanup_stale_backup_imports() -> int:
    root = _imports_root()
    root.mkdir(parents=True, exist_ok=True)
    removed = 0
    now = utcnow()
    for candidate in root.iterdir():
        if not candidate.exists():
            continue
        modified_at = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
        if modified_at + _IMPORT_TTL >= now:
            continue
        if candidate.is_dir():
            shutil.rmtree(candidate, ignore_errors=True)
        else:
            candidate.unlink(missing_ok=True)
        removed += 1
    return removed


def _ensure_restore_confirmation(confirmation: str) -> None:
    if confirmation.strip() != _RESTORE_CONFIRMATION:
        raise ValueError(f"Confirmation phrase must be exactly: {_RESTORE_CONFIRMATION}")


def _safe_extract_tar(archive_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, mode="r:*") as archive:
        for member in archive.getmembers():
            target = (output_dir / member.name).resolve()
            if not str(target).startswith(str(output_dir.resolve())):
                raise ValueError("Unsafe backup member path")
        archive.extractall(path=output_dir)


def _read_manifest(extracted_dir: Path) -> dict:
    manifest_path = extracted_dir / "manifest.json"
    if not manifest_path.exists():
        return {"schema_version": 1}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _infer_scope(extracted_dir: Path, manifest: dict) -> str:
    scope = str(manifest.get("scope") or "").strip().lower()
    if scope in {"metadata", "full"}:
        return scope
    if any((extracted_dir / directory).exists() for directory in ("media", "archives", "thumbnails")):
        return "full"
    return "metadata"


def _clear_directory_contents(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _copy_children(source_dir: Path, destination_dir: Path) -> None:
    if not source_dir.exists():
        return
    destination_dir.mkdir(parents=True, exist_ok=True)
    for child in source_dir.iterdir():
        target = destination_dir / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)


def _sanitize_restored_database() -> None:
    session = SessionLocal()
    try:
        session.query(BackupSnapshot).delete()
        session.commit()
    finally:
        session.close()


def _assemble_backup_parts(output_path: Path, part_paths: list[Path]) -> int:
    total_size = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as destination:
        for part_path in part_paths:
            with part_path.open("rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    destination.write(chunk)
    return total_size


def _sorted_backup_parts(part_paths: list[Path]) -> list[Path]:
    def sort_key(path: Path):
        match = _PART_NUMBER_RE.search(path.name)
        if match:
            return (0, int(match.group(1)), path.name.lower())
        return (1, 0, path.name.lower())

    return sorted(part_paths, key=sort_key)


def _restore_from_archive(
    archive_path: Path,
    *,
    requested_by_id: int,
    original_file_names: list[str],
) -> dict:
    cleanup_stale_backup_imports()
    ensure_no_active_processing_jobs()
    arm_processing_pause(updated_by_id=requested_by_id)

    work_dir = _imports_root() / f"restore-{utcnow().strftime('%Y%m%d%H%M%S%f')}"
    extracted_dir = work_dir / "extracted"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        _safe_extract_tar(archive_path, extracted_dir)
        manifest = _read_manifest(extracted_dir)
        scope = _infer_scope(extracted_dir, manifest)
        database_backup = extracted_dir / "database" / "library.db"
        if not database_backup.exists():
            raise ValueError("Backup archive does not contain database/library.db")

        SessionLocal.remove()
        if scope == "full":
            _clear_directory_contents(settings.storage_root)
            ensure_storage_layout()
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(database_backup, settings.database_path)

        if scope == "full":
            _copy_children(extracted_dir / "media", settings.media_dir)
            _copy_children(extracted_dir / "archives", settings.archive_dir)
            _copy_children(extracted_dir / "thumbnails", settings.thumbnails_dir)

        ensure_storage_layout()
        init_db()
        _sanitize_restored_database()

        restored_manifest = {
            "schema_version": int(manifest.get("schema_version") or 1),
            "scope": scope,
            "owner_id": manifest.get("owner_id"),
            "media_count": manifest.get("media_count"),
            "generated_at": manifest.get("generated_at"),
            "storage": manifest.get("storage") if isinstance(manifest.get("storage"), dict) else {},
            "chunking": manifest.get("chunking") if isinstance(manifest.get("chunking"), dict) else {},
        }

        audit(
            "backup.restored",
            f"Restored backup in {scope} mode",
            actor_id=requested_by_id,
            severity="warning",
            context={
                "scope": scope,
                "source_files": original_file_names,
                "schema_version": restored_manifest["schema_version"],
            },
        )
        return {
            "restored": True,
            "reauth_required": True,
            "processing_paused": True,
            "confirmation_phrase": _RESTORE_CONFIRMATION,
            "message": "Backup восстановлен. Система оставлена на паузе, войдите заново и проверьте библиотеку.",
            "manifest": restored_manifest,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


class BackupRestoreService:
    confirmation_phrase = _RESTORE_CONFIRMATION

    def import_backup_archive(self, archive_path: Path, *, requested_by_id: int, original_filename: str, confirmation: str) -> dict:
        _ensure_restore_confirmation(confirmation)
        return _restore_from_archive(
            archive_path,
            requested_by_id=requested_by_id,
            original_file_names=[original_filename],
        )

    def import_backup_parts(self, part_paths: list[Path], *, requested_by_id: int, confirmation: str) -> dict:
        _ensure_restore_confirmation(confirmation)
        if not part_paths:
            raise ValueError("No backup parts provided")

        sorted_parts = _sorted_backup_parts(part_paths)
        work_dir = _imports_root() / f"parts-{utcnow().strftime('%Y%m%d%H%M%S%f')}"
        assembled_path = work_dir / "assembled-backup.tar.gz"
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            _assemble_backup_parts(assembled_path, sorted_parts)
            return _restore_from_archive(
                assembled_path,
                requested_by_id=requested_by_id,
                original_file_names=[path.name for path in sorted_parts],
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def stage_uploaded_part_files(self, files: list[FileStorage]) -> tuple[list[Path], Path]:
        if not files:
            raise ValueError("No backup files uploaded")
        work_dir = _imports_root() / f"upload-{utcnow().strftime('%Y%m%d%H%M%S%f')}"
        work_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for index, file in enumerate(files, start=1):
            name = Path(file.filename or f"backup-{index}.bin").name
            target = work_dir / name
            with target.open("wb") as destination:
                shutil.copyfileobj(file.stream, destination)
            paths.append(target)
        return paths, work_dir


backup_restore_service = BackupRestoreService()
