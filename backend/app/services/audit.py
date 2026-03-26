from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db.session import new_session
from app.models import AuditLog


logger = logging.getLogger("redditecosus2")


def configure_logging() -> None:
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        settings.logs_dir / "app.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def audit(
    event_type: str,
    message: str,
    *,
    actor_id: int | None = None,
    owner_id: int | None = None,
    severity: str = "info",
    context: dict | None = None,
) -> None:
    logger.log(getattr(logging, severity.upper(), logging.INFO), "%s | %s", event_type, message)
    session = new_session()
    try:
        session.add(
            AuditLog(
                actor_id=actor_id,
                owner_id=owner_id,
                event_type=event_type,
                severity=severity,
                message=message,
                context=context,
            )
        )
        session.commit()
    except OperationalError:
        session.rollback()
    finally:
        session.close()
