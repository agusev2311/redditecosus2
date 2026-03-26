from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import delete

from app.config import settings
from app.db.session import Base, SessionLocal, init_db
from app.models import JobStatus, MediaItem, ProcessingJob, User
from app.services.runtime_config import update_runtime_config_values
from app.services.storage import ensure_storage_layout


DANGER_RESET_CONFIRMATION = "DELETE EVERYTHING"


def _clear_directory_contents(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def arm_processing_pause(*, updated_by_id: int | None = None) -> None:
    update_runtime_config_values({"processing_paused": True}, updated_by_id=updated_by_id)


def full_library_reset(*, confirmation: str, updated_by_id: int | None = None) -> dict:
    expected = DANGER_RESET_CONFIRMATION
    if confirmation.strip() != expected:
        raise ValueError(f"Confirmation phrase must be exactly: {expected}")

    arm_processing_pause(updated_by_id=updated_by_id)

    session = SessionLocal()
    try:
        processing_count = session.query(ProcessingJob).filter(ProcessingJob.status == JobStatus.processing).count()
        queued_count = session.query(ProcessingJob).filter(ProcessingJob.status == JobStatus.queued).count()
        media_count = session.query(MediaItem).count()
        user_count = session.query(User).count()
    finally:
        session.close()

    if processing_count:
        return {
            "deleted": False,
            "paused": True,
            "processing_jobs": processing_count,
            "queued_jobs": queued_count,
            "media_count": media_count,
            "user_count": user_count,
            "message": f"Processing поставлен на паузу. Дождитесь завершения {processing_count} активных jobs и повторите удаление.",
        }

    SessionLocal.remove()
    session = SessionLocal()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(delete(table))
        session.commit()
    finally:
        session.close()

    _clear_directory_contents(settings.storage_root)
    ensure_storage_layout()
    init_db()

    return {
        "deleted": True,
        "paused": False,
        "processing_jobs": 0,
        "queued_jobs": 0,
        "media_count": media_count,
        "user_count": user_count,
        "message": f"Полный сброс завершен. Удалено {media_count} медиа и очищена база данных. Система готова к новой bootstrap-настройке.",
    }
