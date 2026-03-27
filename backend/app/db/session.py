from __future__ import annotations

import threading
import sqlite3

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, scoped_session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    connect_args={
        "check_same_thread": False,
        "timeout": 60,
    },
    future=True,
)
SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
SessionLocal = scoped_session(SessionFactory)
_schema_lock = threading.Lock()


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=60000")
    cursor.close()


def init_db() -> None:
    from app.models.entities import AppConfigEntry, ArchiveImport, AuditLog, BackupSnapshot, MediaItem, MediaTag, ProcessingJob, Tag, User  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _run_schema_migrations()


def _ensure_sqlite_column(table_name: str, column_name: str, ddl: str) -> None:
    inspector = inspect(engine)
    column_names = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in column_names:
        return
    with engine.begin() as connection:
        connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")


def _run_schema_migrations() -> None:
    _ensure_sqlite_column("tags", "description_ru", "TEXT")
    _ensure_sqlite_column("tags", "description_en", "TEXT")
    _ensure_sqlite_column("tags", "details_payload", "JSON")
    _ensure_sqlite_column("tags", "ai_described_at", "DATETIME")
    _ensure_sqlite_column("tags", "updated_at", "DATETIME")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_media_items_created_at_id ON media_items (created_at DESC, id DESC)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_media_items_owner_created_at_id ON media_items (owner_id, created_at DESC, id DESC)"
        )


def is_missing_table_error(exc: Exception) -> bool:
    return isinstance(exc, OperationalError) and "no such table" in str(exc).lower()


def ensure_database_schema() -> None:
    with _schema_lock:
        init_db()


def new_session() -> Session:
    return SessionFactory()
