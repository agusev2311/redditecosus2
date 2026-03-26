from __future__ import annotations

import io
import json
import tarfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import settings
from app.db.session import SessionLocal
from app.models import BackupScope, BackupSnapshot, BackupStatus, MediaItem
from app.services.audit import audit
from app.services.runtime_config import get_runtime_value


class ChunkedWriter:
    def __init__(self, output_dir: Path, chunk_size: int) -> None:
        self.output_dir = output_dir
        self.chunk_size = chunk_size
        self.parts: list[Path] = []
        self._buffer = 0
        self._index = 0
        self._handle = None
        self._open_next()

    def _open_next(self) -> None:
        if self._handle:
            self._handle.close()
        self._index += 1
        path = self.output_dir / f"backup.part{self._index:03d}.tar.gz"
        self._handle = path.open("wb")
        self.parts.append(path)
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
            self._buffer += len(piece)
            offset += len(piece)
        return len(data)

    def flush(self) -> None:
        if self._handle:
            self._handle.flush()

    def close(self) -> None:
        if self._handle:
            self._handle.close()
            self._handle = None


class BackupService:
    def create_snapshot(self, requester_id: int, scope: BackupScope, owner_id: int | None, send_to_telegram: bool) -> str:
        session = SessionLocal()
        try:
            snapshot = BackupSnapshot(
                requested_by_id=requester_id,
                owner_id=owner_id,
                scope=scope,
                status=BackupStatus.queued,
                manifest={"send_to_telegram": send_to_telegram},
            )
            session.add(snapshot)
            session.commit()
            snapshot_id = snapshot.id
        finally:
            session.close()

        thread = threading.Thread(target=self._run_snapshot, args=(snapshot_id, send_to_telegram), daemon=True)
        thread.start()
        return snapshot_id

    def _run_snapshot(self, snapshot_id: str, send_to_telegram: bool) -> None:
        session = SessionLocal()
        try:
            snapshot = session.get(BackupSnapshot, snapshot_id)
            if snapshot is None:
                return
            snapshot.status = BackupStatus.running
            session.commit()

            output_dir = settings.backups_dir / snapshot.id
            output_dir.mkdir(parents=True, exist_ok=True)
            writer = ChunkedWriter(output_dir, int(get_runtime_value("backup_chunk_mb")) * 1024 * 1024)
            manifest = self._build_manifest(session, snapshot.owner_id)
            with tarfile.open(fileobj=writer, mode="w|gz") as tar:
                self._add_bytes(tar, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
                if settings.database_path.exists():
                    tar.add(settings.database_path, arcname="database/library.db")
                if snapshot.scope == BackupScope.full:
                    self._add_full_content(tar, snapshot.owner_id)
            writer.close()

            snapshot.parts = [str(part.relative_to(settings.storage_root)) for part in writer.parts]
            snapshot.manifest = manifest
            if send_to_telegram and settings.telegram_bot_token and settings.telegram_backup_chat_id:
                self._send_parts_to_telegram(writer.parts, snapshot.id)
                if settings.delete_local_backups_after_telegram:
                    for part in writer.parts:
                        part.unlink(missing_ok=True)
            snapshot.status = BackupStatus.complete
            snapshot.completed_at = datetime.now(timezone.utc)
            session.commit()
            audit("backup.completed", f"Backup {snapshot.id} completed", owner_id=snapshot.owner_id)
        except Exception as exc:
            snapshot = session.get(BackupSnapshot, snapshot_id)
            if snapshot is not None:
                snapshot.status = BackupStatus.failed
                snapshot.error_message = str(exc)
                snapshot.completed_at = datetime.now(timezone.utc)
                session.commit()
            audit("backup.failed", f"Backup {snapshot_id} failed: {exc}", severity="error")
        finally:
            session.close()

    def _build_manifest(self, session, owner_id: int | None) -> dict:
        query = session.query(MediaItem)
        if owner_id is not None:
            query = query.filter(MediaItem.owner_id == owner_id)
        media = query.all()
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "owner_id": owner_id,
            "media_count": len(media),
            "items": [
                {
                    "id": item.id,
                    "owner_id": item.owner_id,
                    "kind": item.kind.value,
                    "filename": item.original_filename,
                    "storage_path": item.storage_path,
                    "file_size": item.file_size,
                    "timestamp": item.normalized_timestamp.isoformat() if item.normalized_timestamp else None,
                    "safety_rating": item.safety_rating.value,
                }
                for item in media
            ],
        }

    def _add_bytes(self, tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
        info = tarfile.TarInfo(name=arcname)
        info.size = len(data)
        info.mtime = int(datetime.now(timezone.utc).timestamp())
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

    def _send_parts_to_telegram(self, parts: list[Path], snapshot_id: str) -> None:
        with httpx.Client(timeout=180) as client:
            for index, part in enumerate(parts, start=1):
                with part.open("rb") as handle:
                    client.post(
                        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendDocument",
                        data={
                            "chat_id": settings.telegram_backup_chat_id,
                            "caption": f"Backup {snapshot_id} part {index}/{len(parts)}",
                        },
                        files={"document": (part.name, handle, "application/gzip")},
                    ).raise_for_status()


backup_service = BackupService()
