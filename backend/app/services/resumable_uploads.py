from __future__ import annotations

import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

from app.config import settings
from app.services.media_probe import detect_file_type

try:
    import fcntl  # type: ignore
except ImportError:
    fcntl = None

try:
    import msvcrt  # type: ignore
except ImportError:
    msvcrt = None


_DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024
_MIN_CHUNK_SIZE = 1024 * 1024
_MAX_CHUNK_SIZE = 32 * 1024 * 1024
_UPLOAD_TTL = timedelta(hours=24)
_BUFFER_SIZE = 1024 * 1024


@dataclass(frozen=True)
class UploadSessionState:
    upload_id: str
    owner_id: int
    file_name: str
    file_size: int
    chunk_size: int
    total_parts: int
    last_modified: int | None
    content_type: str | None
    file_type: str
    completed_parts: tuple[int, ...]
    uploaded_bytes: int
    created_at: datetime
    updated_at: datetime

    @property
    def is_complete(self) -> bool:
        return len(self.completed_parts) >= self.total_parts


def upload_sessions_root() -> Path:
    return settings.incoming_dir / "uploads"


def cleanup_stale_upload_sessions() -> int:
    root = upload_sessions_root()
    root.mkdir(parents=True, exist_ok=True)
    removed = 0
    now = datetime.now(timezone.utc)
    for candidate in root.iterdir():
        if not candidate.is_dir():
            continue
        state_path = candidate / "state.json"
        if state_path.exists():
            try:
                state = _read_state(candidate)
                expires_at = state.updated_at + _UPLOAD_TTL
                if expires_at >= now:
                    continue
            except Exception:
                pass
        else:
            modified_at = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
            if modified_at + _UPLOAD_TTL >= now:
                continue
        shutil.rmtree(candidate, ignore_errors=True)
        removed += 1
    return removed


def prepare_upload_session(
    *,
    owner_id: int,
    file_name: str,
    file_size: int,
    last_modified: int | None,
    content_type: str | None,
    desired_chunk_size: int | None = None,
) -> UploadSessionState:
    if file_size <= 0:
        raise ValueError("file_size must be greater than zero")

    cleanup_stale_upload_sessions()

    chunk_size = _normalize_chunk_size(desired_chunk_size)
    upload_id = _build_upload_id(owner_id, file_name, file_size, last_modified, content_type)
    session_dir = _session_dir(upload_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    with _locked_state_handle(session_dir) as handle:
        existing = _load_state_from_handle(handle)
        if existing is not None:
            _validate_existing_upload(existing, owner_id, file_name, file_size, last_modified, content_type)
            existing["updated_at"] = _now_iso()
            _write_state_to_handle(handle, existing)
            return _state_from_dict(existing)

        state = {
            "upload_id": upload_id,
            "owner_id": owner_id,
            "file_name": file_name,
            "file_size": file_size,
            "chunk_size": chunk_size,
            "total_parts": max((file_size + chunk_size - 1) // chunk_size, 1),
            "last_modified": last_modified,
            "content_type": content_type or None,
            "file_type": detect_file_type(file_name),
            "completed_parts": [],
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        _write_state_to_handle(handle, state)
        return _state_from_dict(state)


def get_upload_session(upload_id: str, owner_id: int, *, touch: bool = False) -> UploadSessionState:
    session_dir = _session_dir(upload_id)
    if not session_dir.exists():
        raise FileNotFoundError(upload_id)
    if not touch:
        state = _read_state(session_dir)
        _assert_owner(state, owner_id)
        return state

    with _locked_state_handle(session_dir) as handle:
        payload = _require_state_from_handle(handle)
        state = _state_from_dict(payload)
        _assert_owner(state, owner_id)
        payload["updated_at"] = _now_iso()
        _write_state_to_handle(handle, payload)
        return _state_from_dict(payload)


def write_upload_chunk(upload_id: str, owner_id: int, part_index: int, source_stream: BinaryIO) -> UploadSessionState:
    session_dir = _session_dir(upload_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    state = get_upload_session(upload_id, owner_id, touch=False)
    if part_index < 0 or part_index >= state.total_parts:
        raise ValueError("part_index is out of range")

    payload_path = session_dir / "payload.bin"
    expected_size = expected_part_size(state, part_index)
    start_offset = part_index * state.chunk_size

    total_written = 0
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    with payload_path.open("r+b" if payload_path.exists() else "wb+") as payload:
        payload.seek(start_offset)
        while True:
            chunk = source_stream.read(_BUFFER_SIZE)
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > expected_size:
                raise ValueError("chunk is larger than expected")
            payload.write(chunk)

    if total_written != expected_size:
        raise ValueError("chunk size does not match expected size")

    with _locked_state_handle(session_dir) as handle:
        payload = _require_state_from_handle(handle)
        refreshed = _state_from_dict(payload)
        _assert_owner(refreshed, owner_id)
        completed_parts = {int(value) for value in payload.get("completed_parts", [])}
        completed_parts.add(part_index)
        payload["completed_parts"] = sorted(completed_parts)
        payload["updated_at"] = _now_iso()
        _write_state_to_handle(handle, payload)
        return _state_from_dict(payload)


def finalize_upload_session(upload_id: str, owner_id: int) -> tuple[UploadSessionState, Path]:
    session_dir = _session_dir(upload_id)
    state = get_upload_session(upload_id, owner_id, touch=True)
    missing_parts = missing_part_indexes(state)
    if missing_parts:
        raise ValueError(f"upload is incomplete; missing {len(missing_parts)} parts")

    payload_path = session_dir / "payload.bin"
    if not payload_path.exists():
        raise FileNotFoundError(payload_path)

    file_size = payload_path.stat().st_size
    if file_size < state.file_size:
        raise ValueError("payload file is smaller than expected")
    return state, payload_path


def discard_upload_session(upload_id: str) -> None:
    shutil.rmtree(_session_dir(upload_id), ignore_errors=True)


def missing_part_indexes(state: UploadSessionState) -> list[int]:
    uploaded = set(state.completed_parts)
    return [index for index in range(state.total_parts) if index not in uploaded]


def expected_part_size(state: UploadSessionState, part_index: int) -> int:
    start_offset = part_index * state.chunk_size
    remaining = max(state.file_size - start_offset, 0)
    return min(state.chunk_size, remaining)


def serialize_upload_session(state: UploadSessionState) -> dict:
    return {
        "upload_id": state.upload_id,
        "file_name": state.file_name,
        "file_size": state.file_size,
        "chunk_size": state.chunk_size,
        "total_parts": state.total_parts,
        "uploaded_parts": list(state.completed_parts),
        "uploaded_bytes": state.uploaded_bytes,
        "is_complete": state.is_complete,
        "file_type": state.file_type,
        "created_at": state.created_at.isoformat(),
        "updated_at": state.updated_at.isoformat(),
    }


def _session_dir(upload_id: str) -> Path:
    return upload_sessions_root() / upload_id


def _build_upload_id(
    owner_id: int,
    file_name: str,
    file_size: int,
    last_modified: int | None,
    content_type: str | None,
) -> str:
    payload = f"{owner_id}:{file_name}:{file_size}:{last_modified or ''}:{content_type or ''}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _normalize_chunk_size(value: int | None) -> int:
    if value is None:
        return _DEFAULT_CHUNK_SIZE
    normalized = int(value)
    if normalized < _MIN_CHUNK_SIZE:
        return _MIN_CHUNK_SIZE
    if normalized > _MAX_CHUNK_SIZE:
        return _MAX_CHUNK_SIZE
    return normalized


def _validate_existing_upload(
    payload: dict,
    owner_id: int,
    file_name: str,
    file_size: int,
    last_modified: int | None,
    content_type: str | None,
) -> None:
    mismatches = (
        int(payload.get("owner_id", -1)) != owner_id,
        str(payload.get("file_name", "")) != file_name,
        int(payload.get("file_size", -1)) != file_size,
        (payload.get("last_modified") or None) != (last_modified or None),
        (payload.get("content_type") or None) != (content_type or None),
    )
    if any(mismatches):
        raise ValueError("upload session metadata does not match the current file")


def _assert_owner(state: UploadSessionState, owner_id: int) -> None:
    if state.owner_id != owner_id:
        raise PermissionError("upload session belongs to another user")


def _read_state(session_dir: Path) -> UploadSessionState:
    with _locked_state_handle(session_dir) as handle:
        payload = _require_state_from_handle(handle)
        return _state_from_dict(payload)


@contextmanager
def _locked_state_handle(session_dir: Path):
    session_dir.mkdir(parents=True, exist_ok=True)
    state_path = session_dir / "state.json"
    with state_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b" ")
            handle.flush()
            os.fsync(handle.fileno())
        _lock_file(handle)
        handle.seek(0)
        try:
            yield handle
        finally:
            handle.flush()
            os.fsync(handle.fileno())
            _unlock_file(handle)


def _load_state_from_handle(handle) -> dict | None:
    raw = handle.read()
    if not raw:
        return None
    text = raw.decode("utf-8").strip()
    if not text:
        return None
    return json.loads(text)


def _require_state_from_handle(handle) -> dict:
    payload = _load_state_from_handle(handle)
    if payload is None:
        raise FileNotFoundError("upload session state is missing")
    return payload


def _write_state_to_handle(handle, payload: dict) -> None:
    handle.seek(0)
    handle.truncate()
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    handle.write(encoded)
    handle.flush()
    os.fsync(handle.fileno())


def _state_from_dict(payload: dict) -> UploadSessionState:
    completed_parts = tuple(sorted({int(value) for value in payload.get("completed_parts", [])}))
    file_size = int(payload["file_size"])
    chunk_size = int(payload["chunk_size"])
    uploaded_bytes = sum(min(chunk_size, max(file_size - (index * chunk_size), 0)) for index in completed_parts)
    return UploadSessionState(
        upload_id=str(payload["upload_id"]),
        owner_id=int(payload["owner_id"]),
        file_name=str(payload["file_name"]),
        file_size=file_size,
        chunk_size=chunk_size,
        total_parts=int(payload["total_parts"]),
        last_modified=int(payload["last_modified"]) if payload.get("last_modified") not in {None, ""} else None,
        content_type=str(payload["content_type"]) if payload.get("content_type") else None,
        file_type=str(payload.get("file_type") or detect_file_type(str(payload["file_name"]))),
        completed_parts=completed_parts,
        uploaded_bytes=uploaded_bytes,
        created_at=_parse_datetime(payload["created_at"]),
        updated_at=_parse_datetime(payload["updated_at"]),
    )


def _lock_file(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    if msvcrt is not None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return


def _unlock_file(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
