from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import shutil
import tarfile
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.db.session import SessionLocal
from app.models import BackupScope, BackupSnapshot, BackupStatus, JobStatus, MediaItem, ProcessingJob, User, UserRole
from app.services.audit import audit
from app.services.runtime_config import get_runtime_value


BACKUP_SCHEMA_VERSION = 2
DOWNLOAD_ARTIFACT_NAME = "backup.tar.gz"
_TELEGRAM_SAFE_CHUNK_MB_MAX = 45


@dataclass(frozen=True)
class BackupAccessResult:
    snapshot: BackupSnapshot
    allowed: bool


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _relative_storage_path(path: Path) -> str:
    return str(path.relative_to(settings.storage_root))


def _absolute_storage_path(relative_path: str | None) -> Path | None:
    if not relative_path:
        return None
    return settings.storage_root / relative_path


def _backup_output_dir(snapshot_id: str) -> Path:
    return settings.backups_dir / snapshot_id


def _safe_copy_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    return deepcopy(manifest) if isinstance(manifest, dict) else {}


def _is_snapshot_visible_to_user(snapshot: BackupSnapshot, user) -> bool:
    if user.role == UserRole.admin:
        return True
    return snapshot.requested_by_id == user.id or snapshot.owner_id == user.id


def _remove_path_if_exists(path: Path | None) -> None:
    if path is None:
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    path.unlink(missing_ok=True)


def _cleanup_empty_directory(path: Path) -> None:
    if path.exists() and path.is_dir() and not any(path.iterdir()):
        path.rmdir()


class ChunkedWriter:
    def __init__(self, output_dir: Path, chunk_size: int) -> None:
        self.output_dir = output_dir
        self.chunk_size = chunk_size
        self.parts: list[dict[str, Any]] = []
        self._buffer = 0
        self._index = 0
        self._handle = None
        self._hasher: hashlib._Hash | None = None
        self._size = 0
        self._path: Path | None = None
        self._open_next()

    def _finalize_current(self) -> None:
        if self._handle is None or self._path is None or self._hasher is None:
            return
        self._handle.flush()
        self._handle.close()
        self.parts.append(
            {
                "index": self._index,
                "file_name": self._path.name,
                "path": _relative_storage_path(self._path),
                "size_bytes": self._size,
                "sha256": self._hasher.hexdigest(),
            }
        )
        self._handle = None
        self._path = None
        self._hasher = None
        self._size = 0
        self._buffer = 0

    def _open_next(self) -> None:
        self._finalize_current()
        self._index += 1
        path = self.output_dir / f"backup.part{self._index:03d}.tar.gz"
        self._handle = path.open("wb")
        self._path = path
        self._hasher = hashlib.sha256()
        self._size = 0
        self._buffer = 0

    def write(self, data: bytes) -> int:
        offset = 0
        while offset < len(data):
            remaining = self.chunk_size - self._buffer
            if remaining == 0:
                self._open_next()
                remaining = self.chunk_size
            piece = data[offset : offset + remaining]
            self._handle.write(piece)
            self._hasher.update(piece)
            self._size += len(piece)
            self._buffer += len(piece)
            offset += len(piece)
        return len(data)

    def flush(self) -> None:
        if self._handle:
            self._handle.flush()

    def close(self) -> None:
        self._finalize_current()


class HashingFileWriter:
    def __init__(self, output_path: Path) -> None:
        self.path = output_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("wb")
        self._hasher = hashlib.sha256()
        self._size = 0

    @property
    def size_bytes(self) -> int:
        return self._size

    @property
    def sha256(self) -> str:
        return self._hasher.hexdigest()

    def write(self, data: bytes) -> int:
        self._handle.write(data)
        self._hasher.update(data)
        self._size += len(data)
        return len(data)

    def flush(self) -> None:
        self._handle.flush()

    def close(self) -> None:
        if self._handle:
            self._handle.flush()
            self._handle.close()


class BackupService:
    def create_snapshot(self, requester_id: int, scope: BackupScope, owner_id: int | None, delivery: str) -> str:
        delivery_mode = self._normalize_delivery(delivery)
        session = SessionLocal()
        try:
            snapshot = BackupSnapshot(
                requested_by_id=requester_id,
                owner_id=owner_id,
                scope=scope,
                status=BackupStatus.queued,
                manifest={
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "delivery_mode": delivery_mode,
                },
            )
            session.add(snapshot)
            session.commit()
            snapshot_id = snapshot.id
        finally:
            session.close()

        thread = threading.Thread(target=self._run_snapshot, args=(snapshot_id, delivery_mode), daemon=True)
        thread.start()
        return snapshot_id

    def serialize_snapshot(self, snapshot: BackupSnapshot) -> dict[str, Any]:
        manifest = _safe_copy_manifest(snapshot.manifest)
        manifest.setdefault("schema_version", 1)
        manifest.setdefault("delivery_mode", "telegram" if snapshot.parts else "download")

        parts_metadata = manifest.get("chunking", {}).get("part_files")
        if not isinstance(parts_metadata, list):
            parts_metadata = [
                {
                    "index": index,
                    "file_name": Path(relative_path).name,
                    "path": relative_path,
                }
                for index, relative_path in enumerate(snapshot.parts or [], start=1)
            ]

        resolved_parts: list[dict[str, Any]] = []
        for part in parts_metadata:
            path = _absolute_storage_path(str(part.get("path") or ""))
            resolved_parts.append(
                {
                    **part,
                    "exists": bool(path and path.exists()),
                }
            )

        download = manifest.get("download")
        serialized_download = None
        if isinstance(download, dict):
            artifact_path = _absolute_storage_path(str(download.get("path") or ""))
            expires_at = _parse_datetime(download.get("expires_at"))
            serialized_download = {
                **download,
                "available": bool(artifact_path and artifact_path.exists() and (expires_at is None or expires_at > utcnow())),
            }

        return {
            "id": snapshot.id,
            "scope": snapshot.scope.value,
            "status": snapshot.status.value,
            "delivery_mode": str(manifest.get("delivery_mode") or ("telegram" if snapshot.parts else "download")),
            "parts": snapshot.parts or [],
            "part_files": resolved_parts,
            "download": serialized_download,
            "manifest": manifest,
            "error_message": snapshot.error_message,
            "owner_id": snapshot.owner_id,
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
            "completed_at": snapshot.completed_at.isoformat() if snapshot.completed_at else None,
        }

    def cleanup_expired_backup_artifacts(self) -> int:
        removed = 0
        session = SessionLocal()
        try:
            snapshots = session.query(BackupSnapshot).all()
        finally:
            session.close()

        now = utcnow()
        for snapshot in snapshots:
            manifest = snapshot.manifest if isinstance(snapshot.manifest, dict) else {}
            download = manifest.get("download") if isinstance(manifest, dict) else None
            expires_at = _parse_datetime(download.get("expires_at")) if isinstance(download, dict) else None
            relative_path = str(download.get("path") or "") if isinstance(download, dict) else ""
            path = _absolute_storage_path(relative_path)
            if path and expires_at and expires_at <= now and path.exists():
                _remove_path_if_exists(path)
                removed += 1
            output_dir = _backup_output_dir(snapshot.id)
            _cleanup_empty_directory(output_dir)

        return removed

    def backup_access_for_user(self, snapshot_id: str, user) -> BackupAccessResult:
        session = SessionLocal()
        try:
            snapshot = session.get(BackupSnapshot, snapshot_id)
            if snapshot is None:
                raise FileNotFoundError(snapshot_id)
            session.expunge(snapshot)
        finally:
            session.close()
        return BackupAccessResult(snapshot=snapshot, allowed=_is_snapshot_visible_to_user(snapshot, user))

    def download_artifact_path(self, snapshot: BackupSnapshot) -> tuple[Path, dict[str, Any]] | None:
        manifest = snapshot.manifest if isinstance(snapshot.manifest, dict) else {}
        download = manifest.get("download") if isinstance(manifest, dict) else None
        if not isinstance(download, dict):
            return None
        path = _absolute_storage_path(str(download.get("path") or ""))
        expires_at = _parse_datetime(download.get("expires_at"))
        if path is None or not path.exists():
            return None
        if expires_at is not None and expires_at <= utcnow():
            _remove_path_if_exists(path)
            return None
        return path, download

    def _normalize_delivery(self, delivery: str | None) -> str:
        value = (delivery or "telegram").strip().lower()
        if value not in {"telegram", "download"}:
            raise ValueError("delivery must be telegram or download")
        return value

    def _run_snapshot(self, snapshot_id: str, delivery_mode: str) -> None:
        self.cleanup_expired_backup_artifacts()

        session = SessionLocal()
        try:
            snapshot = session.get(BackupSnapshot, snapshot_id)
            if snapshot is None:
                return
            snapshot.status = BackupStatus.running
            snapshot.error_message = None
            session.commit()

            output_dir = _backup_output_dir(snapshot.id)
            output_dir.mkdir(parents=True, exist_ok=True)
            chunk_mb = min(int(get_runtime_value("backup_chunk_mb")), _TELEGRAM_SAFE_CHUNK_MB_MAX)
            manifest = self._build_manifest(session, snapshot.owner_id, snapshot.scope, delivery_mode, snapshot.id)
            if delivery_mode == "telegram":
                writer = ChunkedWriter(output_dir, chunk_mb * 1024 * 1024)
                self._write_archive(writer, session, snapshot.owner_id, snapshot.scope, manifest)
                writer.close()
                part_paths = [_absolute_storage_path(part["path"]) for part in writer.parts]
                manifest["chunking"] = {
                    "mode": "split",
                    "chunk_size_bytes": chunk_mb * 1024 * 1024,
                    "total_parts": len(writer.parts),
                    "part_files": writer.parts,
                }
                snapshot.parts = [part["path"] for part in writer.parts]
                snapshot.manifest = manifest
                session.commit()

                if settings.telegram_bot_token and settings.telegram_backup_chat_id:
                    self._send_parts_to_telegram(
                        [path for path in part_paths if path is not None],
                        snapshot.id,
                    )
                    manifest["delivery_status"] = {"telegram_sent": True, "sent_at": utcnow().isoformat()}
                    if settings.delete_local_backups_after_telegram:
                        for part_path in part_paths:
                            _remove_path_if_exists(part_path)
                        manifest["chunking"]["local_files_deleted"] = True
                else:
                    manifest["delivery_status"] = {"telegram_sent": False, "reason": "telegram_not_configured"}
                snapshot.manifest = manifest
            else:
                download_path = output_dir / DOWNLOAD_ARTIFACT_NAME
                writer = HashingFileWriter(download_path)
                self._write_archive(writer, session, snapshot.owner_id, snapshot.scope, manifest)
                writer.close()

                expires_at = utcnow() + timedelta(hours=int(get_runtime_value("backup_download_ttl_hours")))
                manifest["chunking"] = {
                    "mode": "single",
                    "chunk_size_bytes": None,
                    "total_parts": 1,
                    "part_files": [],
                }
                manifest["download"] = {
                    "path": _relative_storage_path(download_path),
                    "file_name": f"backup-{snapshot.id}.tar.gz",
                    "content_type": "application/gzip",
                    "size_bytes": writer.size_bytes,
                    "sha256": writer.sha256,
                    "expires_at": expires_at.isoformat(),
                }
                snapshot.parts = []
                snapshot.manifest = manifest

            snapshot.status = BackupStatus.complete
            snapshot.completed_at = utcnow()
            session.commit()
            audit(
                "backup.completed",
                f"Backup {snapshot.id} completed via {delivery_mode}",
                owner_id=snapshot.owner_id,
                actor_id=snapshot.requested_by_id,
                context={"snapshot_id": snapshot.id, "scope": snapshot.scope.value, "delivery_mode": delivery_mode},
            )
        except Exception as exc:
            snapshot = session.get(BackupSnapshot, snapshot_id)
            if snapshot is not None:
                snapshot.status = BackupStatus.failed
                snapshot.error_message = str(exc)
                snapshot.completed_at = utcnow()
                session.commit()
            audit(
                "backup.failed",
                f"Backup {snapshot_id} failed: {exc}",
                severity="error",
                context={"snapshot_id": snapshot_id, "delivery_mode": delivery_mode},
            )
        finally:
            session.close()

    def _build_manifest(self, session, owner_id: int | None, scope: BackupScope, delivery_mode: str, snapshot_id: str) -> dict[str, Any]:
        query = session.query(MediaItem)
        if owner_id is not None:
            query = query.filter(MediaItem.owner_id == owner_id)
        media = query.all()
        included_directories: list[str] = []
        if scope == BackupScope.full:
            if owner_id is None:
                included_directories = ["media", "archives", "thumbnails"]
            else:
                included_directories = [f"media/user_{owner_id}", f"archives/user_{owner_id}"]

        return {
            "schema_version": BACKUP_SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "generated_at": utcnow().isoformat(),
            "owner_id": owner_id,
            "scope": scope.value,
            "delivery_mode": delivery_mode,
            "database": {
                "relative_path": "database/library.db",
                "included": settings.database_path.exists(),
                "size_bytes": settings.database_path.stat().st_size if settings.database_path.exists() else 0,
            },
            "storage": {
                "included_directories": included_directories,
            },
            "media_count": len(media),
            "items": [
                {
                    "id": item.id,
                    "owner_id": item.owner_id,
                    "kind": item.kind.value,
                    "filename": item.original_filename,
                    "storage_path": item.storage_path,
                    "thumbnail_path": item.thumbnail_path,
                    "file_size": item.file_size,
                    "timestamp": item.normalized_timestamp.isoformat() if item.normalized_timestamp else None,
                    "safety_rating": item.safety_rating.value,
                }
                for item in media
            ],
        }

    def _write_archive(
        self,
        writer,
        session,
        owner_id: int | None,
        scope: BackupScope,
        manifest: dict[str, Any],
    ) -> None:
        temp_db_path = self._prepare_database_artifact(owner_id, _backup_output_dir(manifest["snapshot_id"]))
        try:
            with tarfile.open(fileobj=writer, mode="w|gz") as tar:
                self._add_bytes(tar, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
                if temp_db_path is not None and temp_db_path.exists():
                    tar.add(temp_db_path, arcname="database/library.db")
                if scope == BackupScope.full:
                    self._add_full_content(tar, owner_id)
        finally:
            if temp_db_path is not None and temp_db_path != settings.database_path:
                temp_db_path.unlink(missing_ok=True)

    def _add_bytes(self, tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
        info = tarfile.TarInfo(name=arcname)
        info.size = len(data)
        info.mtime = int(utcnow().timestamp())
        tar.addfile(info, io.BytesIO(data))

    def _add_full_content(self, tar: tarfile.TarFile, owner_id: int | None) -> None:
        if owner_id is None:
            for directory in [settings.media_dir, settings.archive_dir, settings.thumbnails_dir]:
                if directory.exists():
                    tar.add(directory, arcname=str(directory.relative_to(settings.storage_root)))
            return

        owner_dir = settings.media_dir / f"user_{owner_id}"
        archive_dir = settings.archive_dir / f"user_{owner_id}"
        if owner_dir.exists():
            tar.add(owner_dir, arcname=str(owner_dir.relative_to(settings.storage_root)))
        if archive_dir.exists():
            tar.add(archive_dir, arcname=str(archive_dir.relative_to(settings.storage_root)))

    def _prepare_database_artifact(self, owner_id: int | None, output_dir: Path) -> Path | None:
        if not settings.database_path.exists():
            return None
        if owner_id is None:
            return settings.database_path

        sanitized_path = output_dir / "database.owner-scope.db"
        shutil.copy2(settings.database_path, sanitized_path)
        connection = sqlite3.connect(str(sanitized_path))
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("UPDATE app_config_entries SET updated_by_id = NULL WHERE updated_by_id != ?", (owner_id,))
            connection.execute("DELETE FROM share_links WHERE media_id NOT IN (SELECT id FROM media_items WHERE owner_id = ?)", (owner_id,))
            connection.execute("DELETE FROM share_links WHERE created_by_id != ?", (owner_id,))
            connection.execute("DELETE FROM processing_jobs WHERE owner_id != ?", (owner_id,))
            connection.execute("DELETE FROM media_tags WHERE media_id NOT IN (SELECT id FROM media_items WHERE owner_id = ?)", (owner_id,))
            connection.execute("DELETE FROM media_tags WHERE tag_id NOT IN (SELECT id FROM tags WHERE owner_id = ?)", (owner_id,))
            connection.execute("DELETE FROM media_items WHERE owner_id != ?", (owner_id,))
            connection.execute("DELETE FROM archive_imports WHERE owner_id != ?", (owner_id,))
            connection.execute("DELETE FROM tags WHERE owner_id != ?", (owner_id,))
            connection.execute("DELETE FROM backup_snapshots")
            connection.execute("DELETE FROM audit_logs WHERE COALESCE(owner_id, actor_id, -1) != ?", (owner_id,))
            connection.execute("DELETE FROM users WHERE id != ?", (owner_id,))
            connection.commit()
        finally:
            connection.close()
        return sanitized_path

    def _send_parts_to_telegram(self, parts: list[Path], snapshot_id: str) -> None:
        pause_seconds = int(get_runtime_value("backup_telegram_pause_seconds"))
        retry_attempts = int(get_runtime_value("backup_telegram_retry_attempts"))
        with httpx.Client(timeout=300) as client:
            for index, part in enumerate(parts, start=1):
                last_error_message = ""
                for attempt in range(1, retry_attempts + 1):
                    with part.open("rb") as handle:
                        response = client.post(
                            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendDocument",
                            data={
                                "chat_id": settings.telegram_backup_chat_id,
                                "caption": f"Backup {snapshot_id} part {index}/{len(parts)}",
                            },
                            files={"document": (part.name, handle, "application/gzip")},
                        )
                    if response.is_success:
                        break

                    last_error_message = response.text.strip() or f"HTTP {response.status_code}"
                    retriable = response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
                    if not retriable or attempt >= retry_attempts:
                        if response.status_code == 400 and "too big" in last_error_message.lower():
                            raise RuntimeError(
                                f"Telegram rejected {part.name} as too large. Lower backup_chunk_mb below the current setting."
                            )
                        raise RuntimeError(
                            f"Telegram sendDocument failed for {part.name}: HTTP {response.status_code} {last_error_message}"
                        )

                    retry_after_seconds = None
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = None
                    if isinstance(payload, dict):
                        parameters = payload.get("parameters")
                        if isinstance(parameters, dict):
                            retry_after_raw = parameters.get("retry_after")
                            if retry_after_raw is not None:
                                try:
                                    retry_after_seconds = int(retry_after_raw)
                                except (TypeError, ValueError):
                                    retry_after_seconds = None

                    time.sleep(retry_after_seconds if retry_after_seconds and retry_after_seconds > 0 else max(pause_seconds, 1) * attempt)

                if index < len(parts) and pause_seconds > 0:
                    time.sleep(pause_seconds)


def list_visible_backups(user) -> list[dict[str, Any]]:
    backup_service.cleanup_expired_backup_artifacts()
    session = SessionLocal()
    try:
        query = session.query(BackupSnapshot)
        if user.role != UserRole.admin:
            query = query.filter(
                (BackupSnapshot.requested_by_id == user.id)
                | (BackupSnapshot.owner_id == user.id)
            )
        rows = query.order_by(BackupSnapshot.created_at.desc()).limit(100).all()
        return [backup_service.serialize_snapshot(row) for row in rows]
    finally:
        session.close()


def can_restore_backup(user) -> bool:
    return user.role != UserRole.guest


def ensure_no_active_processing_jobs() -> None:
    session = SessionLocal()
    try:
        active_jobs = session.query(ProcessingJob).filter(ProcessingJob.status == JobStatus.processing).count()
    finally:
        session.close()
    if active_jobs:
        raise ValueError(f"Нельзя импортировать backup, пока выполняются {active_jobs} активных processing jobs.")


backup_service = BackupService()
