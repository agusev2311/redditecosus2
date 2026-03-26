from __future__ import annotations

import queue
import threading
from datetime import datetime, timezone

from app.config import settings
from app.db.session import SessionLocal
from app.models import JobStatus, MediaItem, MediaTag, ProcessingJob, ProcessingStatus, SafetyRating, Tag, TagKind, TagOrigin
from app.services.ai_proxy import ai_proxy_service
from app.services.audit import audit
from app.services.storage import queue_media_for_processing


class ProcessingCoordinator:
    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._booted = False

    def boot(self) -> None:
        if self._booted:
            return
        self._booted = True
        self._enqueue_existing_jobs()
        for index in range(settings.processing_workers):
            worker = threading.Thread(target=self._run, name=f"media-worker-{index}", daemon=True)
            worker.start()
            self._workers.append(worker)

    def enqueue(self, job_id: str) -> None:
        self._queue.put(job_id)

    def _enqueue_existing_jobs(self) -> None:
        session = SessionLocal()
        try:
            jobs = session.query(ProcessingJob).filter(ProcessingJob.status == JobStatus.queued).all()
            for job in jobs:
                self.enqueue(job.id)
        finally:
            session.close()

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._process(job_id)
            finally:
                self._queue.task_done()

    def _process(self, job_id: str) -> None:
        session = SessionLocal()
        try:
            job = session.get(ProcessingJob, job_id)
            if job is None or job.status == JobStatus.complete:
                return
            media = session.get(MediaItem, job.media_id)
            if media is None:
                return

            job.status = JobStatus.processing
            job.started_at = datetime.now(timezone.utc)
            job.attempts += 1
            media.processing_status = ProcessingStatus.processing
            session.commit()

            analysis = ai_proxy_service.analyze_media(media)
            self._apply_analysis(session, media, analysis)
            job.status = JobStatus.complete
            job.error_message = None
            job.completed_at = datetime.now(timezone.utc)
            media.processing_status = ProcessingStatus.complete
            session.commit()
            audit(
                "media.indexed",
                f"Indexed media {media.original_filename}",
                owner_id=media.owner_id,
                context={"media_id": media.id, "job_id": job.id},
            )
        except Exception as exc:
            session.rollback()
            job = session.get(ProcessingJob, job_id)
            if job is not None:
                job.status = JobStatus.failed
                job.error_message = str(exc)
                job.completed_at = datetime.now(timezone.utc)
                media = session.get(MediaItem, job.media_id)
                if media is not None:
                    media.processing_status = ProcessingStatus.failed
                session.commit()
            audit("media.index_failed", f"Index failed: {exc}", severity="error", context={"job_id": job_id})
        finally:
            session.close()

    def _apply_analysis(self, session, media: MediaItem, analysis: dict) -> None:
        session.query(MediaTag).filter(MediaTag.media_id == media.id).delete()
        media.description = analysis["description"]
        media.technical_notes = analysis["blur_assessment"]
        media.ai_payload = analysis
        media.safety_rating = SafetyRating(analysis["safety_rating"])

        grouped = [
            (analysis["semantic_tags"], TagKind.semantic),
            (analysis["technical_tags"] + analysis.get("local_technical_tags", []), TagKind.technical),
            (analysis["safety_tags"], TagKind.safety),
        ]
        for names, kind in grouped:
            seen_names: set[str] = set()
            for raw_name in names:
                name = raw_name.strip().lower().replace(" ", "_")
                if not name:
                    continue
                if name in seen_names:
                    continue
                seen_names.add(name)
                tag = session.query(Tag).filter_by(owner_id=media.owner_id, name=name, kind=kind).first()
                if tag is None:
                    tag = Tag(owner_id=media.owner_id, name=name, kind=kind)
                    session.add(tag)
                    session.flush()
                session.add(MediaTag(media_id=media.id, tag_id=tag.id, origin=TagOrigin.ai))


coordinator = ProcessingCoordinator()


def enqueue_media(media_id: str) -> str:
    session = SessionLocal()
    try:
        media = session.get(MediaItem, media_id)
        if media is None:
            raise ValueError("Media not found")
        job = queue_media_for_processing(session, media)
        session.commit()
        coordinator.enqueue(job.id)
        return job.id
    finally:
        session.close()


def get_processing_coordinator() -> ProcessingCoordinator:
    return coordinator
