from __future__ import annotations

import queue
import threading
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.models import JobStatus, MediaItem, MediaTag, ProcessingJob, ProcessingStatus, SafetyRating, Tag, TagKind, TagOrigin
from app.services.ai_proxy import ai_proxy_service
from app.services.audit import audit
from app.services.runtime_config import get_runtime_value
from app.services.storage import queue_media_for_processing
from app.utils.datetimes import seconds_between


class ProcessingCoordinator:
    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._workers: dict[int, threading.Thread] = {}
        self._worker_stops: dict[int, threading.Event] = {}
        self._worker_counter = 0
        self._desired_workers = 0
        self._lock = threading.Lock()
        self._booted = False

    def boot(self) -> None:
        if self._booted:
            return
        self._booted = True
        self._enqueue_existing_jobs()
        self.set_desired_workers(int(get_runtime_value("processing_workers")))

    def enqueue(self, job_id: str) -> None:
        self._queue.put(job_id)

    def desired_worker_count(self) -> int:
        with self._lock:
            return self._desired_workers

    def worker_count(self) -> int:
        with self._lock:
            self._workers = {worker_id: thread for worker_id, thread in self._workers.items() if thread.is_alive()}
            self._worker_stops = {worker_id: stop for worker_id, stop in self._worker_stops.items() if worker_id in self._workers}
            return len(self._workers)

    def set_desired_workers(self, count: int) -> int:
        target = max(1, int(count))
        with self._lock:
            self._workers = {worker_id: thread for worker_id, thread in self._workers.items() if thread.is_alive()}
            self._worker_stops = {worker_id: stop for worker_id, stop in self._worker_stops.items() if worker_id in self._workers}
            self._desired_workers = target

            current_ids = sorted(self._workers.keys())
            if len(current_ids) < target:
                for _ in range(target - len(current_ids)):
                    self._worker_counter += 1
                    worker_id = self._worker_counter
                    stop_event = threading.Event()
                    worker = threading.Thread(target=self._run, args=(worker_id, stop_event), name=f"media-worker-{worker_id}", daemon=True)
                    self._workers[worker_id] = worker
                    self._worker_stops[worker_id] = stop_event
                    worker.start()
            elif len(current_ids) > target:
                for worker_id in current_ids[target:]:
                    stop_event = self._worker_stops.get(worker_id)
                    if stop_event is not None:
                        stop_event.set()

        return self.worker_count()

    def _enqueue_existing_jobs(self) -> None:
        session = SessionLocal()
        try:
            jobs = session.query(ProcessingJob).filter(ProcessingJob.status == JobStatus.queued).all()
            for job in jobs:
                self.enqueue(job.id)
        finally:
            session.close()

    def _run(self, worker_id: int, stop_event: threading.Event) -> None:
        while True:
            if stop_event.is_set():
                with self._lock:
                    self._workers.pop(worker_id, None)
                    self._worker_stops.pop(worker_id, None)
                return
            try:
                job_id = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
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
            total_seconds_raw = seconds_between(job.completed_at, job.started_at)
            total_seconds = max(total_seconds_raw, 0.0) if total_seconds_raw is not None else None
            job.payload = {
                "metrics": {
                    **(analysis.get("x_metrics") or {}),
                    "total_seconds": round(total_seconds, 3) if total_seconds is not None else None,
                    "worker": threading.current_thread().name,
                }
            }
            media.processing_status = ProcessingStatus.complete
            session.commit()
            audit(
                "media.indexed",
                f"Indexed media {media.original_filename}",
                owner_id=media.owner_id,
                context={
                    "media_id": media.id,
                    "job_id": job.id,
                    "total_seconds": total_seconds,
                    "ai_seconds": (analysis.get("x_metrics") or {}).get("ai_seconds"),
                },
            )
        except Exception as exc:
            session.rollback()
            job = session.get(ProcessingJob, job_id)
            if job is not None:
                job.status = JobStatus.failed
                job.error_message = str(exc)
                job.completed_at = datetime.now(timezone.utc)
                total_seconds_raw = seconds_between(job.completed_at, job.started_at)
                total_seconds = max(total_seconds_raw, 0.0) if total_seconds_raw is not None else None
                existing_payload = job.payload or {}
                existing_payload["metrics"] = {
                    **(existing_payload.get("metrics") or {}),
                    "total_seconds": round(total_seconds, 3) if total_seconds is not None else None,
                    "worker": threading.current_thread().name,
                }
                job.payload = existing_payload
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
