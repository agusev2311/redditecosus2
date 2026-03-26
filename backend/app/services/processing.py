from __future__ import annotations

import itertools
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.db.session import SessionLocal
from app.models import JobStatus, MediaItem, MediaTag, ProcessingJob, ProcessingStatus, SafetyRating, Tag, TagKind, TagOrigin
from app.services.ai_limit_guard import is_ai_proxy_sleep_active
from app.services.ai_proxy import AIProxyLimitCooldownError, ai_proxy_service
from app.services.audit import audit
from app.services.memory_guard import evaluate_processing_memory_guard
from app.services.runtime_config import get_runtime_value
from app.services.storage import queue_media_for_processing
from app.services.tag_catalog import get_tag_description_coordinator
from app.utils.datetimes import seconds_between


_MB = 1024 * 1024


class QueuedJob:
    __slots__ = ("priority", "sequence", "job_id")

    def __init__(self, *, priority: tuple[int, int, int, int], sequence: int, job_id: str) -> None:
        self.priority = priority
        self.sequence = sequence
        self.job_id = job_id

    def __lt__(self, other: "QueuedJob") -> bool:
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.sequence < other.sequence


def estimate_media_load_units(media: MediaItem) -> int:
    file_size = max(int(media.file_size or 0), 0)
    width = max(int(media.width or 0), 0)
    height = max(int(media.height or 0), 0)
    pixels = width * height
    duration = float(media.duration_seconds or 0.0)
    extension = Path(media.original_filename or "").suffix.lower()

    if media.kind.value == "image":
        if pixels >= 48_000_000 or file_size >= 80 * _MB:
            return 8
        if pixels >= 24_000_000 or file_size >= 32 * _MB:
            return 5
        if pixels >= 10_000_000 or file_size >= 10 * _MB:
            return 3
        return 1

    if media.kind.value == "gif":
        if file_size >= 96 * _MB or pixels >= 12_000_000:
            return 10
        if file_size >= 32 * _MB or pixels >= 4_000_000:
            return 6
        return 3

    # videos are typically heavier because frame extraction + decode pressure RAM and CPU
    if duration >= 180 or file_size >= 256 * _MB:
        return 12
    if duration >= 60 or file_size >= 96 * _MB:
        return 8
    if duration >= 20 or file_size >= 32 * _MB or extension in {".mkv", ".mov"}:
        return 5
    return 3


class ProcessingCoordinator:
    def __init__(self) -> None:
        self._queue: queue.PriorityQueue[QueuedJob] = queue.PriorityQueue()
        self._queued_job_ids: set[str] = set()
        self._workers: dict[int, threading.Thread] = {}
        self._worker_stops: dict[int, threading.Event] = {}
        self._processing_slots = threading.Condition()
        self._active_load = 0
        self._active_heavy_jobs = 0
        self._sync_lock = threading.Lock()
        self._worker_counter = 0
        self._enqueue_counter = itertools.count()
        self._desired_workers = 0
        self._lock = threading.Lock()
        self._booted = False

    def boot(self) -> None:
        if self._booted:
            return
        self._booted = True
        recovered_jobs = self._recover_inflight_jobs()
        self._enqueue_existing_jobs()
        self.set_desired_workers(int(get_runtime_value("processing_workers")))
        if recovered_jobs:
            audit(
                "processing.recovered_inflight",
                f"Recovered {recovered_jobs} in-flight jobs after startup",
                context={"recovered_jobs": recovered_jobs},
            )

    def enqueue(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._queued_job_ids:
                return
            self._queued_job_ids.add(job_id)
        self._queue.put(self._build_queued_job(job_id))

    def _build_queued_job(self, job_id: str) -> QueuedJob:
        session = SessionLocal()
        try:
            row = (
                session.query(
                    ProcessingJob.created_at,
                    ProcessingJob.attempts,
                    MediaItem.kind,
                    MediaItem.file_size,
                    MediaItem.width,
                    MediaItem.height,
                    MediaItem.duration_seconds,
                    MediaItem.original_filename,
                )
                .join(MediaItem, MediaItem.id == ProcessingJob.media_id)
                .filter(ProcessingJob.id == job_id)
                .first()
            )
        finally:
            session.close()

        if row is None:
            priority = (999, 999_999_999, 999, 999)
        else:
            created_at, attempts, kind, file_size, width, height, duration_seconds, original_filename = row
            pseudo_media = MediaItem(
                kind=kind,
                file_size=file_size or 0,
                width=width,
                height=height,
                duration_seconds=duration_seconds,
                original_filename=original_filename or "",
                owner_id=0,
                storage_path="",
                mime_type="application/octet-stream",
                sha256="",
            )
            load_units = estimate_media_load_units(pseudo_media)
            kind_bias = {"image": 0, "gif": 1, "video": 2}.get(kind.value, 3)
            timestamp_bias = int(created_at.timestamp()) if created_at else 0
            priority = (load_units, kind_bias, attempts or 0, timestamp_bias)
        return QueuedJob(priority=priority, sequence=next(self._enqueue_counter), job_id=job_id)

    def desired_worker_count(self) -> int:
        with self._lock:
            return self._desired_workers

    def worker_count(self) -> int:
        with self._lock:
            self._workers = {worker_id: thread for worker_id, thread in self._workers.items() if thread.is_alive()}
            self._worker_stops = {worker_id: stop for worker_id, stop in self._worker_stops.items() if worker_id in self._workers}
            return len(self._workers)

    def notify_capacity_changed(self) -> None:
        with self._processing_slots:
            self._processing_slots.notify_all()

    def _processing_paused(self) -> bool:
        return bool(get_runtime_value("processing_paused")) or is_ai_proxy_sleep_active() or bool(evaluate_processing_memory_guard()["active"])

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
        self._sync_queued_jobs(limit=500)

    def _sync_queued_jobs(self, limit: int = 64) -> int:
        if self._processing_paused():
            return 0
        if not self._sync_lock.acquire(blocking=False):
            return 0
        try:
            session = SessionLocal()
            try:
                jobs = (
                    session.query(ProcessingJob.id)
                    .filter(ProcessingJob.status == JobStatus.queued)
                    .order_by(ProcessingJob.created_at.asc())
                    .limit(limit)
                    .all()
                )
            finally:
                session.close()

            added = 0
            for (job_id,) in jobs:
                with self._lock:
                    if job_id in self._queued_job_ids:
                        continue
                    self._queued_job_ids.add(job_id)
                self._queue.put(self._build_queued_job(job_id))
                added += 1
            return added
        finally:
            self._sync_lock.release()

    def _recover_inflight_jobs(self) -> int:
        session = SessionLocal()
        try:
            jobs = session.query(ProcessingJob).filter(ProcessingJob.status == JobStatus.processing).all()
            if not jobs:
                return 0

            media_ids = [job.media_id for job in jobs]
            for job in jobs:
                job.status = JobStatus.queued
                job.started_at = None
                job.completed_at = None
                job.error_message = None

            (
                session.query(MediaItem)
                .filter(
                    MediaItem.id.in_(media_ids),
                    MediaItem.processing_status == ProcessingStatus.processing,
                )
                .update({MediaItem.processing_status: ProcessingStatus.pending}, synchronize_session=False)
            )
            session.commit()
            return len(jobs)
        finally:
            session.close()

    def _run(self, worker_id: int, stop_event: threading.Event) -> None:
        while True:
            if stop_event.is_set():
                with self._lock:
                    self._workers.pop(worker_id, None)
                    self._worker_stops.pop(worker_id, None)
                return
            if self._processing_paused():
                stop_event.wait(0.5)
                continue
            try:
                queued_job = self._queue.get(timeout=1.0)
            except queue.Empty:
                self._sync_queued_jobs()
                continue
            job_id = queued_job.job_id
            try:
                self._process(job_id, stop_event)
            finally:
                self._queue.task_done()
                with self._lock:
                    self._queued_job_ids.discard(job_id)

    def _acquire_processing_slot(self, media: MediaItem, stop_event: threading.Event) -> tuple[bool, int]:
        load_units = estimate_media_load_units(media)
        heavy_threshold = max(1, int(get_runtime_value("processing_heavy_job_threshold")))
        is_heavy = load_units >= heavy_threshold
        while True:
            if stop_event.is_set():
                return False, load_units
            if self._processing_paused():
                stop_event.wait(0.5)
                continue
            load_budget = max(1, int(get_runtime_value("processing_load_budget")))
            max_heavy_jobs = max(1, int(get_runtime_value("processing_max_heavy_jobs")))
            with self._processing_slots:
                can_fit_budget = self._active_load + load_units <= load_budget
                can_fit_heavy = not is_heavy or self._active_heavy_jobs < max_heavy_jobs
                if can_fit_budget and can_fit_heavy:
                    self._active_load += load_units
                    if is_heavy:
                        self._active_heavy_jobs += 1
                    return True, load_units
                self._processing_slots.wait(timeout=0.5)

    def _release_processing_slot(self, load_units: int) -> None:
        with self._processing_slots:
            self._active_load = max(0, self._active_load - load_units)
            heavy_threshold = max(1, int(get_runtime_value("processing_heavy_job_threshold")))
            if load_units >= heavy_threshold:
                self._active_heavy_jobs = max(0, self._active_heavy_jobs - 1)
            self._processing_slots.notify_all()

    def _process(self, job_id: str, stop_event: threading.Event) -> None:
        session = SessionLocal()
        load_units = 1
        acquired_slot = False
        try:
            job = session.get(ProcessingJob, job_id)
            if job is None or job.status == JobStatus.complete:
                return
            media = session.get(MediaItem, job.media_id)
            if media is None:
                return

            acquired, load_units = self._acquire_processing_slot(media, stop_event)
            if not acquired:
                self.enqueue(job_id)
                return
            acquired_slot = True

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
            get_tag_description_coordinator().notify_backfill_needed()
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
        except AIProxyLimitCooldownError as exc:
            session.rollback()
            job = session.get(ProcessingJob, job_id)
            if job is not None:
                job.status = JobStatus.queued
                job.error_message = f"AI proxy cooldown until {exc.sleep_until or 'unknown'} (HTTP {exc.status_code})"
                job.started_at = None
                job.completed_at = None
                existing_payload = job.payload or {}
                existing_payload["cooldown"] = {
                    "status_code": exc.status_code,
                    "sleep_until": exc.sleep_until,
                    "detail": exc.detail,
                    "worker": threading.current_thread().name,
                }
                job.payload = existing_payload
                media = session.get(MediaItem, job.media_id)
                if media is not None:
                    media.processing_status = ProcessingStatus.pending
                session.commit()
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
            if acquired_slot:
                self._release_processing_slot(load_units)

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
        if settings.enable_processing:
            coordinator.enqueue(job.id)
        return job.id
    finally:
        session.close()


def get_processing_coordinator() -> ProcessingCoordinator:
    return coordinator
