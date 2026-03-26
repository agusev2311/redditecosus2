from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import aliased

from app.db.session import SessionLocal
from app.models import MediaTag, Tag, User
from app.services.ai_limit_guard import is_ai_proxy_sleep_active
from app.services.ai_proxy import AIProxyLimitCooldownError, ai_proxy_service
from app.services.audit import audit
from app.services.runtime_config import get_runtime_value


def _missing_tag_description_filter():
    return or_(
        Tag.description_ru.is_(None),
        Tag.description_ru == "",
        Tag.description_en.is_(None),
        Tag.description_en == "",
        Tag.details_payload.is_(None),
        Tag.ai_described_at.is_(None),
    )


def tag_is_described(tag: Tag) -> bool:
    return bool(tag.description_ru and tag.description_en and tag.details_payload and tag.ai_described_at)


def _tag_scope_filter(query, user: User, owner_id: int | None = None):
    if user.role.value != "admin":
        return query.filter(Tag.owner_id == user.id)
    if owner_id is not None:
        return query.filter(Tag.owner_id == owner_id)
    return query


def serialize_tag(tag: Tag, usage_count: int) -> dict[str, Any]:
    return {
        "id": tag.id,
        "owner_id": tag.owner_id,
        "name": tag.name,
        "kind": tag.kind.value,
        "usage_count": int(usage_count or 0),
        "description_ru": tag.description_ru,
        "description_en": tag.description_en,
        "details_payload": tag.details_payload,
        "ai_described_at": tag.ai_described_at.isoformat() if tag.ai_described_at else None,
        "created_at": tag.created_at.isoformat() if tag.created_at else None,
        "updated_at": tag.updated_at.isoformat() if tag.updated_at else None,
        "is_described": tag_is_described(tag),
    }


def build_tag_catalog_payload(
    session,
    user: User,
    *,
    q: str = "",
    kind: str | None = None,
    described: str | None = None,
    limit: int = 250,
    owner_id: int | None = None,
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 250), 1000))
    scoped_usage_query = (
        session.query(Tag, func.count(MediaTag.id).label("usage_count"))
        .outerjoin(MediaTag, MediaTag.tag_id == Tag.id)
    )
    scoped_usage_query = _tag_scope_filter(scoped_usage_query, user, owner_id)
    leaderboard_rows = (
        scoped_usage_query.group_by(Tag.id)
        .order_by(func.count(MediaTag.id).desc(), Tag.name.asc())
        .limit(20)
        .all()
    )

    query = scoped_usage_query
    if q:
        search = f"%{q.strip().lower()}%"
        query = query.filter(
            or_(
                Tag.name.ilike(search),
                Tag.description_ru.ilike(search),
                Tag.description_en.ilike(search),
            )
        )
    if kind:
        query = query.filter(Tag.kind == kind)
    if described == "true":
        query = query.filter(~_missing_tag_description_filter())
    elif described == "false":
        query = query.filter(_missing_tag_description_filter())

    rows = (
        query.group_by(Tag.id)
        .order_by(func.count(MediaTag.id).desc(), Tag.name.asc())
        .limit(safe_limit)
        .all()
    )

    count_query = _tag_scope_filter(session.query(Tag), user, owner_id)
    total_tags = count_query.count()
    pending_tags = count_query.filter(_missing_tag_description_filter()).count()
    described_tags = max(total_tags - pending_tags, 0)

    items = [serialize_tag(tag, usage_count) for tag, usage_count in rows]
    leaderboard = [serialize_tag(tag, usage_count) for tag, usage_count in leaderboard_rows]
    return {
        "items": items,
        "leaderboard": leaderboard,
        "counts": {
            "total": total_tags,
            "described": described_tags,
            "pending": pending_tags,
        },
    }


def _top_cooccurring_tags(session, tag: Tag, limit: int = 18) -> list[str]:
    primary_link = aliased(MediaTag)
    co_link = aliased(MediaTag)
    co_tag = aliased(Tag)
    rows = (
        session.query(co_tag.name, func.count(co_link.id).label("usage_count"))
        .join(co_link, co_link.tag_id == co_tag.id)
        .join(primary_link, primary_link.media_id == co_link.media_id)
        .filter(primary_link.tag_id == tag.id, co_tag.id != tag.id, co_tag.owner_id == tag.owner_id)
        .group_by(co_tag.id)
        .order_by(func.count(co_link.id).desc(), co_tag.name.asc())
        .limit(limit)
        .all()
    )
    return [name for name, _usage_count in rows]


def _next_missing_tag_id(session) -> int | None:
    row = (
        session.query(Tag.id, func.count(MediaTag.id).label("usage_count"))
        .outerjoin(MediaTag, MediaTag.tag_id == Tag.id)
        .filter(_missing_tag_description_filter())
        .group_by(Tag.id)
        .order_by(func.count(MediaTag.id).desc(), Tag.updated_at.is_(None).desc(), Tag.updated_at.asc(), Tag.created_at.asc())
        .first()
    )
    return int(row[0]) if row else None


def describe_tag_by_id(tag_id: int) -> bool:
    session = SessionLocal()
    try:
        tag = session.get(Tag, tag_id)
        if tag is None or tag_is_described(tag):
            return False

        usage_count = session.query(func.count(MediaTag.id)).filter(MediaTag.tag_id == tag.id).scalar() or 0
        cooccurring_tags = _top_cooccurring_tags(session, tag)
        analysis = ai_proxy_service.describe_tag(
            tag_name=tag.name,
            tag_kind=tag.kind,
            usage_count=int(usage_count),
            cooccurring_tags=cooccurring_tags,
        )
        tag.description_ru = analysis["description_ru"]
        tag.description_en = analysis["description_en"]
        tag.details_payload = {
            "aliases": analysis.get("aliases", []),
            "parent_categories": analysis.get("parent_categories", []),
            "related_tags": analysis.get("related_tags", []),
            "distinguishing_features": analysis.get("distinguishing_features", []),
            "common_contexts": analysis.get("common_contexts", []),
            "search_hints": analysis.get("search_hints", []),
            "moderation_notes_ru": analysis.get("moderation_notes_ru", ""),
            "moderation_notes_en": analysis.get("moderation_notes_en", ""),
            "ambiguity_note_ru": analysis.get("ambiguity_note_ru", ""),
            "ambiguity_note_en": analysis.get("ambiguity_note_en", ""),
            "confidence": analysis.get("confidence"),
            "cooccurring_tags": cooccurring_tags,
            "metrics": analysis.get("x_metrics"),
        }
        tag.ai_described_at = datetime.now(timezone.utc)
        session.commit()
        audit(
            "tag.described",
            f"Described tag {tag.name}",
            owner_id=tag.owner_id,
            context={"tag_id": tag.id, "kind": tag.kind.value, "usage_count": int(usage_count)},
        )
        return True
    except AIProxyLimitCooldownError:
        session.rollback()
        return False
    except Exception as exc:
        session.rollback()
        tag = session.get(Tag, tag_id)
        if tag is not None:
            tag.updated_at = datetime.now(timezone.utc)
            session.commit()
        audit(
            "tag.describe_failed",
            f"Failed to describe tag {tag_id}: {exc}",
            severity="error",
            context={"tag_id": tag_id},
        )
        return False
    finally:
        session.close()


def count_pending_tag_descriptions(owner_id: int | None = None) -> int:
    session = SessionLocal()
    try:
        query = session.query(Tag).filter(_missing_tag_description_filter())
        if owner_id is not None:
            query = query.filter(Tag.owner_id == owner_id)
        return query.count()
    finally:
        session.close()


class TagDescriptionCoordinator:
    def __init__(self) -> None:
        self._booted = False
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None

    def boot(self) -> None:
        if self._booted:
            return
        self._booted = True
        self._thread = threading.Thread(target=self._run, name="tag-description-worker", daemon=True)
        self._thread.start()
        self.notify_backfill_needed()

    def notify_backfill_needed(self) -> None:
        self._wake_event.set()

    def _run(self) -> None:
        while True:
            if bool(get_runtime_value("processing_paused")) or is_ai_proxy_sleep_active():
                self._wake_event.wait(timeout=10)
                self._wake_event.clear()
                continue

            session = SessionLocal()
            try:
                next_tag_id = _next_missing_tag_id(session)
            finally:
                session.close()

            if next_tag_id is None:
                self._wake_event.wait(timeout=10)
                self._wake_event.clear()
                continue

            processed = describe_tag_by_id(next_tag_id)
            if not processed:
                self._wake_event.wait(timeout=8)
                self._wake_event.clear()


tag_description_coordinator = TagDescriptionCoordinator()


def get_tag_description_coordinator() -> TagDescriptionCoordinator:
    return tag_description_coordinator
