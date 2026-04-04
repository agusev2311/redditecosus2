from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import jwt

from app.config import settings
from app.models import MediaItem, ShareLink, User, UserRole
from app.services.storage import absolute_thumbnail_path


_SHARE_ASSET_TOKEN_PURPOSE = "share_asset"
_SHARE_ASSET_TOKEN_TTL_SECONDS = 900


@dataclass
class ShareAvailability:
    is_active: bool
    status: str
    views_remaining: int | None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_positive_int(value: Any, *, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    return parsed


def parse_share_constraints(payload: dict[str, Any] | None) -> tuple[datetime | None, int | None]:
    payload = payload or {}
    expires_in_hours = _coerce_positive_int(payload.get("expires_in_hours"), field_name="expires_in_hours")
    max_views = _coerce_positive_int(payload.get("max_views"), field_name="max_views")
    expires_at = utcnow() + timedelta(hours=expires_in_hours) if expires_in_hours is not None else None
    return expires_at, max_views


def share_availability(share: ShareLink, *, now: datetime | None = None) -> ShareAvailability:
    current_time = now or utcnow()
    views_remaining = None if share.max_views is None else max(share.max_views - int(share.view_count or 0), 0)

    if share.revoked_at is not None:
        return ShareAvailability(is_active=False, status="burned", views_remaining=views_remaining)
    if share.expires_at is not None and share.expires_at <= current_time:
        return ShareAvailability(is_active=False, status="expired", views_remaining=views_remaining)
    if share.max_views is not None and int(share.view_count or 0) >= share.max_views:
        return ShareAvailability(is_active=False, status="exhausted", views_remaining=0)
    return ShareAvailability(is_active=True, status="active", views_remaining=views_remaining)


def issue_share_asset_token(share_id: str) -> str:
    payload = {
        "purpose": _SHARE_ASSET_TOKEN_PURPOSE,
        "share_id": share_id,
        "exp": utcnow() + timedelta(seconds=_SHARE_ASSET_TOKEN_TTL_SECONDS),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def share_asset_token_ttl_seconds() -> int:
    return _SHARE_ASSET_TOKEN_TTL_SECONDS


def verify_share_asset_token(token: str, share_id: str) -> None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise PermissionError("Invalid share access token") from exc
    if payload.get("purpose") != _SHARE_ASSET_TOKEN_PURPOSE or payload.get("share_id") != share_id:
        raise PermissionError("Invalid share access token")


def share_url_for(base_url: str, share_id: str) -> str:
    return f"{base_url.rstrip('/')}/?share={share_id}"


def _share_public_asset_url(path: str, asset_token: str) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}access_token={quote(asset_token, safe='')}"


def serialize_share(
    share: ShareLink,
    *,
    base_url: str,
    public_asset_token: str | None = None,
) -> dict[str, Any]:
    availability = share_availability(share)
    thumbnail_path = absolute_thumbnail_path(share.media)
    thumbnail_url = f"/api/media/{share.media_id}/thumbnail" if thumbnail_path and thumbnail_path.exists() else None
    file_url = f"/api/media/{share.media_id}/file"

    if public_asset_token is not None:
        thumbnail_url = (
            _share_public_asset_url(f"/api/shares/public/{share.id}/thumbnail", public_asset_token)
            if thumbnail_url is not None
            else None
        )
        file_url = _share_public_asset_url(f"/api/shares/public/{share.id}/file", public_asset_token)

    return {
        "id": share.id,
        "media_id": share.media_id,
        "kind": share.media.kind.value,
        "original_filename": share.media.original_filename,
        "mime_type": share.media.mime_type,
        "safety_rating": share.media.safety_rating.value,
        "processing_status": share.media.processing_status.value,
        "thumbnail_url": thumbnail_url,
        "file_url": file_url,
        "share_url": share_url_for(base_url, share.id),
        "created_by_id": share.created_by_id,
        "created_by_username": share.created_by.username if share.created_by is not None else None,
        "expires_at": share.expires_at.isoformat() if share.expires_at else None,
        "max_views": share.max_views,
        "view_count": int(share.view_count or 0),
        "views_remaining": availability.views_remaining,
        "last_viewed_at": share.last_viewed_at.isoformat() if share.last_viewed_at else None,
        "revoked_at": share.revoked_at.isoformat() if share.revoked_at else None,
        "status": availability.status,
        "is_active": availability.is_active,
        "created_at": share.created_at.isoformat() if share.created_at else None,
        "updated_at": share.updated_at.isoformat() if share.updated_at else None,
    }


def shares_query_for_user(query, user: User):
    if user.role != UserRole.admin:
        query = query.filter(ShareLink.created_by_id == user.id)
    return query


def user_can_manage_share(user: User, share: ShareLink) -> bool:
    return user.role == UserRole.admin or share.created_by_id == user.id


def create_share_link(
    session,
    *,
    media: MediaItem,
    created_by_id: int,
    expires_at: datetime | None,
    max_views: int | None,
) -> ShareLink:
    share = ShareLink(
        media_id=media.id,
        created_by_id=created_by_id,
        expires_at=expires_at,
        max_views=max_views,
    )
    session.add(share)
    session.flush()
    return share


def burn_share_link(share: ShareLink) -> None:
    if share.revoked_at is None:
        share.revoked_at = utcnow()


def open_public_share(session, share: ShareLink) -> tuple[ShareLink, str]:
    availability = share_availability(share)
    if not availability.is_active:
        raise ValueError(availability.status)

    share.view_count = int(share.view_count or 0) + 1
    share.last_viewed_at = utcnow()
    session.commit()
    session.refresh(share)
    return share, issue_share_asset_token(share.id)
