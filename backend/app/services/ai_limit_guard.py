from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.session import new_session
from app.models import AppConfigEntry
from app.services.audit import audit
from app.services.runtime_config import get_runtime_value
from app.services.telegram_notify import send_telegram_alert


AI_PROXY_SLEEP_UNTIL_KEY = "ai_proxy_sleep_until"
AI_PROXY_SLEEP_TRIGGERED_AT_KEY = "ai_proxy_sleep_triggered_at"
AI_PROXY_SLEEP_STATUS_CODE_KEY = "ai_proxy_sleep_status_code"
AI_PROXY_SLEEP_LAST_ERROR_KEY = "ai_proxy_sleep_last_error"

_state_lock = threading.Lock()


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


def _read_state_rows() -> dict[str, str]:
    session = new_session()
    try:
        rows = (
            session.query(AppConfigEntry)
            .filter(
                AppConfigEntry.key.in_(
                    [
                        AI_PROXY_SLEEP_UNTIL_KEY,
                        AI_PROXY_SLEEP_TRIGGERED_AT_KEY,
                        AI_PROXY_SLEEP_STATUS_CODE_KEY,
                        AI_PROXY_SLEEP_LAST_ERROR_KEY,
                    ]
                )
            )
            .all()
        )
        return {row.key: row.value for row in rows}
    finally:
        session.close()


def _upsert_state_values(values: dict[str, str], *, updated_by_id: int | None = None) -> None:
    session = new_session()
    try:
        for key, value in values.items():
            row = session.get(AppConfigEntry, key)
            if row is None:
                row = AppConfigEntry(key=key, value=value, updated_by_id=updated_by_id)
                session.add(row)
            else:
                row.value = value
                row.updated_by_id = updated_by_id
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


def get_ai_proxy_limit_status_codes() -> list[int]:
    raw = str(get_runtime_value("ai_proxy_limit_status_codes") or "").strip()
    values: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            code = int(chunk)
        except ValueError:
            continue
        if code > 0 and code not in values:
            values.append(code)
    return values or [419, 429]


def is_ai_proxy_limit_status(status_code: int) -> bool:
    return int(status_code) in get_ai_proxy_limit_status_codes()


def get_ai_proxy_sleep_state(now: datetime | None = None) -> dict[str, Any]:
    current = now or _now_utc()
    rows = _read_state_rows()
    sleep_until = _parse_datetime(rows.get(AI_PROXY_SLEEP_UNTIL_KEY))
    triggered_at = _parse_datetime(rows.get(AI_PROXY_SLEEP_TRIGGERED_AT_KEY))
    status_code_raw = rows.get(AI_PROXY_SLEEP_STATUS_CODE_KEY)
    try:
        status_code = int(status_code_raw) if status_code_raw else None
    except ValueError:
        status_code = None

    active = bool(sleep_until and sleep_until > current)
    remaining_seconds = int((sleep_until - current).total_seconds()) if active and sleep_until else 0
    return {
        "active": active,
        "sleep_until": sleep_until.isoformat() if sleep_until else None,
        "triggered_at": triggered_at.isoformat() if triggered_at else None,
        "status_code": status_code,
        "last_error": rows.get(AI_PROXY_SLEEP_LAST_ERROR_KEY) or None,
        "remaining_seconds": remaining_seconds if active else 0,
        "monitored_status_codes": get_ai_proxy_limit_status_codes(),
        "sleep_hours": int(get_runtime_value("ai_proxy_limit_sleep_hours")),
    }


def is_ai_proxy_sleep_active(now: datetime | None = None) -> bool:
    return bool(get_ai_proxy_sleep_state(now).get("active"))


def trigger_ai_proxy_limit_sleep(status_code: int, detail: str, *, updated_by_id: int | None = None) -> dict[str, Any]:
    with _state_lock:
        current_state = get_ai_proxy_sleep_state()
        if current_state["active"]:
            return current_state

        now = _now_utc()
        sleep_hours = max(1, int(get_runtime_value("ai_proxy_limit_sleep_hours")))
        sleep_until = now + timedelta(hours=sleep_hours)
        compact_detail = " ".join((detail or "").split())[:800]
        _upsert_state_values(
            {
                AI_PROXY_SLEEP_UNTIL_KEY: sleep_until.isoformat(),
                AI_PROXY_SLEEP_TRIGGERED_AT_KEY: now.isoformat(),
                AI_PROXY_SLEEP_STATUS_CODE_KEY: str(int(status_code)),
                AI_PROXY_SLEEP_LAST_ERROR_KEY: compact_detail,
            },
            updated_by_id=updated_by_id,
        )

    state = get_ai_proxy_sleep_state()
    message = (
        f"AI proxy limit cooldown activated.\n"
        f"HTTP status: {status_code}\n"
        f"Sleep until: {state['sleep_until']}\n"
        f"Duration: {state['sleep_hours']}h\n"
        f"Details: {compact_detail or 'n/a'}"
    )
    audit(
        "ai_proxy.limit_sleep_started",
        f"AI proxy cooldown activated after HTTP {status_code}",
        severity="warning",
        context={
            "status_code": status_code,
            "sleep_until": state["sleep_until"],
            "sleep_hours": state["sleep_hours"],
            "detail": compact_detail,
        },
    )
    try:
        send_telegram_alert(message)
    except Exception:
        pass
    return state


def clear_ai_proxy_limit_sleep(*, updated_by_id: int | None = None) -> dict[str, Any]:
    with _state_lock:
        previous_state = get_ai_proxy_sleep_state()
        _delete_state_values(
            [
                AI_PROXY_SLEEP_UNTIL_KEY,
                AI_PROXY_SLEEP_TRIGGERED_AT_KEY,
                AI_PROXY_SLEEP_STATUS_CODE_KEY,
                AI_PROXY_SLEEP_LAST_ERROR_KEY,
            ]
        )

    audit(
        "ai_proxy.limit_sleep_cleared",
        "AI proxy cooldown cleared manually",
        severity="warning",
        actor_id=updated_by_id,
        context={
            "previous_sleep_until": previous_state.get("sleep_until"),
            "previous_status_code": previous_state.get("status_code"),
        },
    )
    return get_ai_proxy_sleep_state()
