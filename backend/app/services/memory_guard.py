from __future__ import annotations

import ctypes
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.session import new_session
from app.models import AppConfigEntry
from app.services.audit import audit
from app.services.runtime_config import get_runtime_value
from app.services.telegram_notify import send_telegram_alert


MEMORY_GUARD_TRIGGERED_AT_KEY = "processing_memory_guard_triggered_at"
MEMORY_GUARD_REASON_KEY = "processing_memory_guard_reason"
MEMORY_GUARD_SNAPSHOT_KEY = "processing_memory_guard_snapshot"

_state_lock = threading.Lock()
_CGROUP_V2_DIR = Path("/sys/fs/cgroup")
_CGROUP_V1_DIR = Path("/sys/fs/cgroup/memory")


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


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    raw = _read_text(path)
    if not raw or raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_memory_stat(raw: str | None) -> dict[str, int]:
    stats: dict[str, int] = {}
    if not raw:
        return stats
    for line in raw.splitlines():
        key, _, value = line.partition(" ")
        if not key or not value:
            continue
        try:
            stats[key] = int(value.strip())
        except ValueError:
            continue
    return stats


def _memory_from_cgroup_v2() -> dict[str, Any] | None:
    current = _read_int(_CGROUP_V2_DIR / "memory.current")
    max_limit = _read_int(_CGROUP_V2_DIR / "memory.max")
    if current is None:
        return None
    stats = _parse_memory_stat(_read_text(_CGROUP_V2_DIR / "memory.stat"))
    inactive_file = stats.get("inactive_file", 0)
    working_set = max(current - inactive_file, 0)
    total = max_limit or current
    available = max(total - working_set, 0) if max_limit else 0
    return {
        "source": "cgroup_v2",
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": working_set,
        "limit_bytes": max_limit or total,
        "raw_used_bytes": current,
    }


def _memory_from_cgroup_v1() -> dict[str, Any] | None:
    current = _read_int(_CGROUP_V1_DIR / "memory.usage_in_bytes")
    max_limit = _read_int(_CGROUP_V1_DIR / "memory.limit_in_bytes")
    if current is None:
        return None
    stats = _parse_memory_stat(_read_text(_CGROUP_V1_DIR / "memory.stat"))
    inactive_file = stats.get("total_inactive_file", stats.get("inactive_file", 0))
    working_set = max(current - inactive_file, 0)
    total = max_limit or current
    available = max(total - working_set, 0) if max_limit else 0
    return {
        "source": "cgroup_v1",
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": working_set,
        "limit_bytes": max_limit or total,
        "raw_used_bytes": current,
    }


def _memory_from_proc_meminfo() -> dict[str, Any] | None:
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists():
        return None
    fields: dict[str, int] = {}
    for line in meminfo_path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(":")
        if not key or not value:
            continue
        number = value.strip().split(" ", 1)[0]
        try:
            fields[key] = int(number) * 1024
        except ValueError:
            continue
    total = fields.get("MemTotal")
    available = fields.get("MemAvailable")
    if not total or available is None:
        return None
    used = max(total - available, 0)
    return {
        "source": "proc_meminfo",
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": used,
        "limit_bytes": total,
        "raw_used_bytes": used,
    }


def _memory_from_windows() -> dict[str, Any] | None:
    if os.name != "nt":
        return None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_uint),
            ("dwMemoryLoad", ctypes.c_uint),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    memory_status = MEMORYSTATUSEX()
    memory_status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory_status)):
        return None
    total = int(memory_status.ullTotalPhys)
    available = int(memory_status.ullAvailPhys)
    used = max(total - available, 0)
    return {
        "source": "windows",
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": used,
        "limit_bytes": total,
        "raw_used_bytes": used,
    }


def get_memory_stats() -> dict[str, Any]:
    stats = _memory_from_cgroup_v2() or _memory_from_cgroup_v1() or _memory_from_proc_meminfo() or _memory_from_windows()
    if stats is None:
        return {
            "source": "unknown",
            "total_bytes": 0,
            "available_bytes": 0,
            "used_bytes": 0,
            "limit_bytes": 0,
            "raw_used_bytes": 0,
            "available_mb": 0,
            "used_mb": 0,
            "total_mb": 0,
            "usage_percent": 0.0,
        }

    total = max(int(stats.get("limit_bytes") or stats.get("total_bytes") or 0), 0)
    available = max(int(stats.get("available_bytes") or 0), 0)
    used = max(int(stats.get("used_bytes") or 0), 0)
    usage_percent = round((used / total) * 100, 1) if total else 0.0
    return {
        **stats,
        "available_mb": round(available / (1024 * 1024), 1),
        "used_mb": round(used / (1024 * 1024), 1),
        "total_mb": round(total / (1024 * 1024), 1),
        "usage_percent": usage_percent,
    }


def _pause_threshold_mb() -> int:
    return max(64, int(get_runtime_value("processing_memory_pause_available_mb")))


def _resume_threshold_mb() -> int:
    pause_threshold = _pause_threshold_mb()
    configured = max(64, int(get_runtime_value("processing_memory_resume_available_mb")))
    return max(configured, pause_threshold + 64)


def _read_state_rows() -> dict[str, str]:
    session = new_session()
    try:
        rows = (
            session.query(AppConfigEntry)
            .filter(
                AppConfigEntry.key.in_(
                    [
                        MEMORY_GUARD_TRIGGERED_AT_KEY,
                        MEMORY_GUARD_REASON_KEY,
                        MEMORY_GUARD_SNAPSHOT_KEY,
                    ]
                )
            )
            .all()
        )
        return {row.key: row.value for row in rows}
    finally:
        session.close()


def _upsert_state_values(values: dict[str, str]) -> None:
    session = new_session()
    try:
        for key, value in values.items():
            row = session.get(AppConfigEntry, key)
            if row is None:
                row = AppConfigEntry(key=key, value=value)
                session.add(row)
            else:
                row.value = value
        session.commit()
    finally:
        session.close()


def _delete_state_values(keys: list[str]) -> None:
    session = new_session()
    try:
        rows = session.query(AppConfigEntry).filter(AppConfigEntry.key.in_(keys)).all()
        for row in rows:
            session.delete(row)
        session.commit()
    finally:
        session.close()


def get_processing_memory_guard_state() -> dict[str, Any]:
    rows = _read_state_rows()
    stats = get_memory_stats()
    triggered_at = _parse_datetime(rows.get(MEMORY_GUARD_TRIGGERED_AT_KEY))
    active = MEMORY_GUARD_TRIGGERED_AT_KEY in rows
    return {
        "active": active,
        "triggered_at": triggered_at.isoformat() if triggered_at else None,
        "reason": rows.get(MEMORY_GUARD_REASON_KEY) or None,
        "snapshot": rows.get(MEMORY_GUARD_SNAPSHOT_KEY) or None,
        "pause_available_mb": _pause_threshold_mb(),
        "resume_available_mb": _resume_threshold_mb(),
        "memory": stats,
    }


def trigger_processing_memory_guard(*, stats: dict[str, Any]) -> dict[str, Any]:
    with _state_lock:
        current = get_processing_memory_guard_state()
        if current["active"]:
            return current

        now = _now_utc()
        reason = (
            f"Available memory dropped to {stats['available_mb']} MB "
            f"(threshold { _pause_threshold_mb() } MB, source {stats['source']})"
        )
        snapshot = (
            f"available={stats['available_mb']}MB "
            f"used={stats['used_mb']}MB "
            f"total={stats['total_mb']}MB "
            f"usage={stats['usage_percent']}%"
        )
        _upsert_state_values(
            {
                MEMORY_GUARD_TRIGGERED_AT_KEY: now.isoformat(),
                MEMORY_GUARD_REASON_KEY: reason,
                MEMORY_GUARD_SNAPSHOT_KEY: snapshot,
            }
        )

    state = get_processing_memory_guard_state()
    audit(
        "processing.memory_guard_activated",
        "Processing paused automatically because memory is too low",
        severity="warning",
        context={
            "reason": state["reason"],
            "snapshot": state["snapshot"],
            "pause_available_mb": state["pause_available_mb"],
            "memory": state["memory"],
        },
    )
    try:
        send_telegram_alert(
            "Processing memory guard activated.\n"
            f"{state['reason']}\n"
            f"{state['snapshot']}\n"
            "New media analysis jobs will wait until memory recovers."
        )
    except Exception:
        pass
    return state


def clear_processing_memory_guard() -> dict[str, Any]:
    with _state_lock:
        previous = get_processing_memory_guard_state()
        if not previous["active"]:
            return previous
        _delete_state_values(
            [
                MEMORY_GUARD_TRIGGERED_AT_KEY,
                MEMORY_GUARD_REASON_KEY,
                MEMORY_GUARD_SNAPSHOT_KEY,
            ]
        )

    current = get_processing_memory_guard_state()
    audit(
        "processing.memory_guard_cleared",
        "Processing memory guard cleared after memory recovered",
        severity="warning",
        context={
            "previous_reason": previous["reason"],
            "previous_snapshot": previous["snapshot"],
            "memory": current["memory"],
            "resume_available_mb": current["resume_available_mb"],
        },
    )
    try:
        send_telegram_alert(
            "Processing memory guard cleared.\n"
            f"Available memory recovered to {current['memory']['available_mb']} MB."
        )
    except Exception:
        pass
    return current


def evaluate_processing_memory_guard() -> dict[str, Any]:
    state = get_processing_memory_guard_state()
    stats = state["memory"]
    available_mb = float(stats.get("available_mb") or 0)

    if state["active"]:
        if available_mb >= state["resume_available_mb"]:
            return clear_processing_memory_guard()
        return state

    if stats.get("source") == "unknown":
        return state

    if available_mb <= state["pause_available_mb"]:
        return trigger_processing_memory_guard(stats=stats)
    return state


def is_processing_memory_guard_active() -> bool:
    return bool(evaluate_processing_memory_guard()["active"])
