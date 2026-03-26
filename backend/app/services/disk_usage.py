from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import func

from app.config import settings
from app.db.session import SessionLocal
from app.models import MediaItem, User


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for candidate in path.rglob("*"):
        if candidate.is_file():
            total += candidate.stat().st_size
    return total


def summarize_disk_usage() -> dict:
    session = SessionLocal()
    try:
        per_user_rows = (
            session.query(User.username, MediaItem.kind, func.coalesce(func.sum(MediaItem.file_size), 0))
            .join(MediaItem, MediaItem.owner_id == User.id)
            .group_by(User.username, MediaItem.kind)
            .all()
        )
        per_user = [{"username": username, "kind": kind.value, "bytes": total} for username, kind, total in per_user_rows]
    finally:
        session.close()

    project_used = (settings.database_path.stat().st_size if settings.database_path.exists() else 0) + _dir_size(settings.storage_root)
    disk = shutil.disk_usage(settings.storage_root.drive or settings.storage_root.anchor)
    return {
        "drive_total": disk.total,
        "drive_free": disk.free,
        "drive_used": disk.used,
        "other_on_drive": max(disk.used - project_used, 0),
        "project": {
            "database": settings.database_path.stat().st_size if settings.database_path.exists() else 0,
            "incoming": _dir_size(settings.incoming_dir),
            "media": _dir_size(settings.media_dir),
            "archives": _dir_size(settings.archive_dir),
            "thumbnails": _dir_size(settings.thumbnails_dir),
            "backups": _dir_size(settings.backups_dir),
            "logs": _dir_size(settings.logs_dir),
            "total": project_used,
        },
        "per_user": per_user,
    }

