from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.models import AuditLog, JobStatus, ProcessingJob
from app.services.processing import get_processing_coordinator
from app.utils.datetimes import seconds_between


def _round_number(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = int(round((len(ordered) - 1) * ratio))
    return ordered[index]


def _completed_job_seconds(job: ProcessingJob) -> float | None:
    duration = seconds_between(job.completed_at, job.started_at)
    if duration is None:
        return None
    return max(duration, 0.0)


def build_processing_stats(
    session: Session,
    jobs_query: Query[ProcessingJob],
    logs_query: Query[AuditLog] | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    last_day = now - timedelta(hours=24)

    queued_count = jobs_query.filter(ProcessingJob.status == JobStatus.queued).count()
    processing_count = jobs_query.filter(ProcessingJob.status == JobStatus.processing).count()
    failed_count = jobs_query.filter(ProcessingJob.status == JobStatus.failed).count()
    complete_count = jobs_query.filter(ProcessingJob.status == JobStatus.complete).count()

    completed_sample = (
        jobs_query.filter(
            ProcessingJob.status == JobStatus.complete,
            ProcessingJob.started_at.is_not(None),
            ProcessingJob.completed_at.is_not(None),
        )
        .order_by(ProcessingJob.completed_at.desc())
        .limit(200)
        .all()
    )

    total_seconds = [seconds for job in completed_sample if (seconds := _completed_job_seconds(job)) is not None]
    ai_seconds = [
        float(job.payload["metrics"]["ai_seconds"])
        for job in completed_sample
        if job.payload and job.payload.get("metrics", {}).get("ai_seconds") is not None
    ]
    frame_counts = [
        float(job.payload["metrics"]["frame_count"])
        for job in completed_sample
        if job.payload and job.payload.get("metrics", {}).get("frame_count") is not None
    ]
    prompt_tokens = [
        float(job.payload["metrics"]["prompt_tokens"])
        for job in completed_sample
        if job.payload and job.payload.get("metrics", {}).get("prompt_tokens") is not None
    ]
    completion_tokens = [
        float(job.payload["metrics"]["completion_tokens"])
        for job in completed_sample
        if job.payload and job.payload.get("metrics", {}).get("completion_tokens") is not None
    ]
    reasoning_tokens = [
        float(job.payload["metrics"]["reasoning_tokens"])
        for job in completed_sample
        if job.payload and job.payload.get("metrics", {}).get("reasoning_tokens") is not None
    ]

    completed_last_24h = (
        jobs_query.filter(
            ProcessingJob.status == JobStatus.complete,
            ProcessingJob.completed_at.is_not(None),
            ProcessingJob.completed_at >= last_day,
        ).count()
    )
    oldest_queued_job = (
        jobs_query.filter(ProcessingJob.status == JobStatus.queued)
        .order_by(ProcessingJob.created_at.asc())
        .first()
    )
    failed_last_24h = (
        jobs_query.filter(
            ProcessingJob.status == JobStatus.failed,
            ProcessingJob.completed_at.is_not(None),
            ProcessingJob.completed_at >= last_day,
        ).count()
    )

    active_logs_query = logs_query if logs_query is not None else session.query(AuditLog)
    recent_failures = (
        active_logs_query.filter(
            AuditLog.event_type == "media.index_failed",
            AuditLog.created_at >= last_day,
        ).count()
    )

    return {
        "workers": get_processing_coordinator().worker_count() or get_processing_coordinator().desired_worker_count(),
        "queued": queued_count,
        "processing": processing_count,
        "failed": failed_count,
        "complete": complete_count,
        "completed_last_24h": completed_last_24h,
        "failed_last_24h": failed_last_24h,
        "recent_failure_events": recent_failures,
        "throughput_per_hour_24h": _round_number(completed_last_24h / 24 if completed_last_24h else 0.0),
        "avg_total_seconds": _round_number(mean(total_seconds)) if total_seconds else None,
        "p95_total_seconds": _round_number(_percentile(total_seconds, 0.95)) if total_seconds else None,
        "avg_ai_seconds": _round_number(mean(ai_seconds)) if ai_seconds else None,
        "p95_ai_seconds": _round_number(_percentile(ai_seconds, 0.95)) if ai_seconds else None,
        "avg_frames": _round_number(mean(frame_counts), 1) if frame_counts else None,
        "avg_prompt_tokens": _round_number(mean(prompt_tokens), 1) if prompt_tokens else None,
        "avg_completion_tokens": _round_number(mean(completion_tokens), 1) if completion_tokens else None,
        "avg_reasoning_tokens": _round_number(mean(reasoning_tokens), 1) if reasoning_tokens else None,
        "oldest_queued_seconds": _round_number(seconds_between(now, oldest_queued_job.created_at), 1) if oldest_queued_job and oldest_queued_job.created_at else None,
    }


def recent_logs_query_for_user(base_query, user):
    if user.role.value == "admin":
        return base_query
    return base_query.filter(or_(AuditLog.owner_id == user.id, AuditLog.actor_id == user.id))
