from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from typing import Any

from app.db.session import new_session
from app.models import AppConfigEntry


PROCESSOR_HEARTBEAT_AT_KEY = "processor_heartbeat_at"
PROCESSOR_HEARTBEAT_PAYLOAD_KEY = "processor_heartbeat_payload"
PROCESSOR_HEARTBEAT_TIMEOUT_SECONDS = 45


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _upsert_value(session, key: str, value: str) -> None:
    row = session.get(AppConfigEntry, key)
    if row is None:
        session.add(AppConfigEntry(key=key, value=value))
        return
    row.value = value


def touch_processor_heartbeat(**payload: Any) -> None:
    now = _now_utc()
    heartbeat_payload = {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "timestamp": now.isoformat(),
        **payload,
    }
    session = new_session()
    try:
        _upsert_value(session, PROCESSOR_HEARTBEAT_AT_KEY, now.isoformat())
        _upsert_value(session, PROCESSOR_HEARTBEAT_PAYLOAD_KEY, json.dumps(heartbeat_payload, ensure_ascii=True))
        session.commit()
    finally:
        session.close()


def get_processor_status() -> dict[str, Any]:
    session = new_session()
    try:
        rows = (
            session.query(AppConfigEntry)
            .filter(AppConfigEntry.key.in_([PROCESSOR_HEARTBEAT_AT_KEY, PROCESSOR_HEARTBEAT_PAYLOAD_KEY]))
            .all()
        )
    finally:
        session.close()

    values = {row.key: row.value for row in rows}
    last_seen = _parse_datetime(values.get(PROCESSOR_HEARTBEAT_AT_KEY))
    payload: dict[str, Any] = {}
    try:
        if values.get(PROCESSOR_HEARTBEAT_PAYLOAD_KEY):
            payload = json.loads(values[PROCESSOR_HEARTBEAT_PAYLOAD_KEY])
    except json.JSONDecodeError:
        payload = {}

    stale_seconds = None
    active = False
    if last_seen is not None:
        stale_seconds = max((_now_utc() - last_seen).total_seconds(), 0.0)
        active = stale_seconds <= PROCESSOR_HEARTBEAT_TIMEOUT_SECONDS

    return {
        "active": active,
        "last_seen": last_seen.isoformat() if last_seen else None,
        "stale_seconds": round(stale_seconds, 1) if stale_seconds is not None else None,
        "timeout_seconds": PROCESSOR_HEARTBEAT_TIMEOUT_SECONDS,
        "hostname": payload.get("hostname"),
        "pid": payload.get("pid"),
        "workers": payload.get("workers"),
        "desired_workers": payload.get("desired_workers"),
        "active_load": payload.get("active_load"),
        "queue_size": payload.get("queue_size"),
    }
