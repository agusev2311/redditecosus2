from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def uuid_str() -> str:
    return str(uuid4())


class UserRole(str, Enum):
    admin = "admin"
    member = "member"
    guest = "guest"


class MediaKind(str, Enum):
    image = "image"
    gif = "gif"
    video = "video"


class SafetyRating(str, Enum):
    sfw = "sfw"
    questionable = "questionable"
    nsfw = "nsfw"
    unknown = "unknown"


class ProcessingStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    complete = "complete"
    failed = "failed"


class JobKind(str, Enum):
    analyze_media = "analyze_media"


class JobStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    complete = "complete"
    failed = "failed"


class TagKind(str, Enum):
    semantic = "semantic"
    technical = "technical"
    safety = "safety"


class TagOrigin(str, Enum):
    ai = "ai"
    manual = "manual"
    system = "system"


class BackupScope(str, Enum):
    metadata = "metadata"
    full = "full"


class BackupStatus(str, Enum):
    queued = "queued"
    running = "running"
    complete = "complete"
    failed = "failed"


class TimestampPrecision(str, Enum):
    none = "none"
    date = "date"
    second = "second"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(SqlEnum(UserRole), default=UserRole.member)
    telegram_username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    guest_allowed_owner_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    guest_allowed_tag_names: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    guest_blocked_tag_names: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    media_items: Mapped[list["MediaItem"]] = relationship(back_populates="owner")


class AppConfigEntry(Base):
    __tablename__ = "app_config_entries"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ArchiveImport(Base):
    __tablename__ = "archive_imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    archive_path: Mapped[str] = mapped_column(Text)
    extracted_path: Mapped[str] = mapped_column(Text)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="complete")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    archive_id: Mapped[str | None] = mapped_column(ForeignKey("archive_imports.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[MediaKind] = mapped_column(SqlEnum(MediaKind), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_path: Mapped[str] = mapped_column(Text)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str] = mapped_column(String(120))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
    blur_score: Mapped[float | None] = mapped_column(nullable=True)
    safety_rating: Mapped[SafetyRating] = mapped_column(
        SqlEnum(SafetyRating),
        default=SafetyRating.unknown,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    technical_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    normalized_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timestamp_precision: Mapped[TimestampPrecision] = mapped_column(
        SqlEnum(TimestampPrecision),
        default=TimestampPrecision.none,
    )
    telegram_cached_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_cached_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        SqlEnum(ProcessingStatus),
        default=ProcessingStatus.pending,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner: Mapped[User] = relationship(back_populates="media_items")
    tags: Mapped[list["MediaTag"]] = relationship(back_populates="media", cascade="all, delete-orphan")
    share_links: Mapped[list["ShareLink"]] = relationship(back_populates="media", cascade="all, delete-orphan")


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("owner_id", "name", "kind", name="uq_tag_owner_name_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    kind: Mapped[TagKind] = mapped_column(SqlEnum(TagKind), index=True)
    description_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ai_described_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MediaTag(Base):
    __tablename__ = "media_tags"
    __table_args__ = (UniqueConstraint("media_id", "tag_id", name="uq_media_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)
    origin: Mapped[TagOrigin] = mapped_column(SqlEnum(TagOrigin), default=TagOrigin.ai)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    media: Mapped[MediaItem] = relationship(back_populates="tags")
    tag: Mapped[Tag] = relationship()


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), index=True)
    kind: Mapped[JobKind] = mapped_column(SqlEnum(JobKind), default=JobKind.analyze_media)
    status: Mapped[JobStatus] = mapped_column(SqlEnum(JobStatus), default=JobStatus.queued, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ShareLink(Base):
    __tablename__ = "share_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    media_id: Mapped[str] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), index=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    max_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    media: Mapped[MediaItem] = relationship(back_populates="share_links")
    created_by: Mapped[User] = relationship()


class BackupSnapshot(Base):
    __tablename__ = "backup_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    scope: Mapped[BackupScope] = mapped_column(SqlEnum(BackupScope), default=BackupScope.metadata)
    status: Mapped[BackupStatus] = mapped_column(SqlEnum(BackupStatus), default=BackupStatus.queued, index=True)
    parts: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(32), default="info")
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
