from __future__ import annotations

from typing import Any
from typing import TypeVar

from sqlalchemy import false

from app.models import MediaItem, MediaTag, Tag, User, UserRole
from app.services.analysis_enrichment import normalize_tag_name


T = TypeVar("T")


def _dedupe_preserving_order(values: list[T]) -> list[T]:
    seen: set[T] = set()
    ordered: list[T] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def guest_allowed_owner_ids(user: User) -> list[int]:
    values = [int(value) for value in (user.guest_allowed_owner_ids or []) if str(value).strip()]
    return _dedupe_preserving_order(values)


def guest_allowed_tag_names(user: User) -> list[str]:
    values = [normalize_tag_name(str(value)) for value in (user.guest_allowed_tag_names or [])]
    return _dedupe_preserving_order([value for value in values if value])


def guest_blocked_tag_names(user: User) -> list[str]:
    values = [normalize_tag_name(str(value)) for value in (user.guest_blocked_tag_names or [])]
    return _dedupe_preserving_order([value for value in values if value])


def serialize_guest_access(user: User) -> dict[str, list[int] | list[str]] | None:
    if user.role != UserRole.guest:
        return None
    return {
        "allowed_owner_ids": guest_allowed_owner_ids(user),
        "allowed_tags": guest_allowed_tag_names(user),
        "blocked_tags": guest_blocked_tag_names(user),
    }


def normalize_guest_tag_names(raw_values: Any) -> list[str]:
    if raw_values is None or raw_values == "":
        return []
    if not isinstance(raw_values, list):
        raise ValueError("guest_access tag lists must be arrays")
    normalized = [normalize_tag_name(str(value)) for value in raw_values]
    return _dedupe_preserving_order([value for value in normalized if value])


def normalize_guest_owner_ids(session, raw_values: Any) -> list[int]:
    if raw_values is None or raw_values == "":
        return []
    if not isinstance(raw_values, list):
        raise ValueError("guest_access.allowed_owner_ids must be an array")

    parsed: list[int] = []
    for raw_value in raw_values:
        try:
            owner_id = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("guest_access.allowed_owner_ids must contain integers") from exc
        if owner_id <= 0:
            raise ValueError("guest_access.allowed_owner_ids must contain positive integers")
        parsed.append(owner_id)

    owner_ids = _dedupe_preserving_order(parsed)
    if not owner_ids:
        return []

    rows = session.query(User.id, User.role).filter(User.id.in_(owner_ids)).all()
    role_by_user_id = {int(user_id): role for user_id, role in rows}
    missing_ids = [owner_id for owner_id in owner_ids if owner_id not in role_by_user_id]
    if missing_ids:
        raise ValueError(f"Unknown allowed owner ids: {', '.join(str(value) for value in missing_ids)}")

    guest_ids = [owner_id for owner_id in owner_ids if role_by_user_id[owner_id] == UserRole.guest]
    if guest_ids:
        raise ValueError(
            f"Guest accounts can only follow admin/member uploads. Invalid ids: {', '.join(str(value) for value in guest_ids)}"
        )

    return owner_ids


def build_guest_access_config(session, *, role: UserRole, payload: Any) -> dict[str, list[int] | list[str] | None]:
    guest_access = payload or {}
    if guest_access and not isinstance(guest_access, dict):
        raise ValueError("guest_access must be an object")

    if role != UserRole.guest:
        return {
            "guest_allowed_owner_ids": None,
            "guest_allowed_tag_names": None,
            "guest_blocked_tag_names": None,
        }

    allowed_owner_ids = normalize_guest_owner_ids(session, guest_access.get("allowed_owner_ids"))
    if not allowed_owner_ids:
        raise ValueError("Guest accounts require at least one allowed owner")

    return {
        "guest_allowed_owner_ids": allowed_owner_ids,
        "guest_allowed_tag_names": normalize_guest_tag_names(guest_access.get("allowed_tags")),
        "guest_blocked_tag_names": normalize_guest_tag_names(guest_access.get("blocked_tags")),
    }


def apply_guest_access_config(user: User, config: dict[str, list[int] | list[str] | None]) -> None:
    user.guest_allowed_owner_ids = config["guest_allowed_owner_ids"]
    user.guest_allowed_tag_names = config["guest_allowed_tag_names"]
    user.guest_blocked_tag_names = config["guest_blocked_tag_names"]


def can_use_member_features(user: User) -> bool:
    return user.role != UserRole.guest


def apply_media_visibility_scope(query, user: User):
    if user.role == UserRole.admin:
        return query
    if user.role == UserRole.member:
        return query.filter(MediaItem.owner_id == user.id)

    allowed_owner_ids = guest_allowed_owner_ids(user)
    if not allowed_owner_ids:
        return query.filter(false())

    query = query.filter(MediaItem.owner_id.in_(allowed_owner_ids))

    allowed_tags = guest_allowed_tag_names(user)
    if allowed_tags:
        query = query.filter(MediaItem.tags.any(MediaTag.tag.has(Tag.name.in_(allowed_tags))))

    blocked_tags = guest_blocked_tag_names(user)
    if blocked_tags:
        query = query.filter(~MediaItem.tags.any(MediaTag.tag.has(Tag.name.in_(blocked_tags))))

    return query


def media_item_visible_to_user(item: MediaItem, user: User) -> bool:
    if user.role == UserRole.admin:
        return True
    if user.role == UserRole.member:
        return item.owner_id == user.id

    allowed_owner_ids = set(guest_allowed_owner_ids(user))
    if item.owner_id not in allowed_owner_ids:
        return False

    tag_names = {
        tag_link.tag.name
        for tag_link in item.tags
        if tag_link.tag is not None and tag_link.tag.name
    }

    allowed_tags = set(guest_allowed_tag_names(user))
    if allowed_tags and not tag_names.intersection(allowed_tags):
        return False

    blocked_tags = set(guest_blocked_tag_names(user))
    if blocked_tags and tag_names.intersection(blocked_tags):
        return False

    return True
