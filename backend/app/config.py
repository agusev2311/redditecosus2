from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    values = [item.strip() for item in raw.split(",")]
    return tuple(item for item in values if item)


def _running_in_docker() -> bool:
    return Path("/.dockerenv").exists()


def _normalize_ai_proxy_base_url(value: str) -> str:
    if not value or not _running_in_docker():
        return value

    parsed = urlsplit(value)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return value

    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        auth += "@"

    netloc = f"{auth}host.docker.internal"
    if parsed.port:
        netloc += f":{parsed.port}"

    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("APP_ENV", "development")
    secret_key: str = os.getenv("APP_SECRET_KEY", "dev-secret-key")
    jwt_ttl_days: int = int(os.getenv("APP_JWT_TTL_DAYS", "30"))
    default_timezone: str = os.getenv("APP_DEFAULT_TIMEZONE", "Europe/Moscow")
    public_base_url: str = os.getenv("APP_PUBLIC_BASE_URL", "http://127.0.0.1:5000")
    frontend_url: str = os.getenv("APP_FRONTEND_URL", "http://127.0.0.1:5173")
    frontend_origins: tuple[str, ...] = _csv("APP_FRONTEND_URL", "http://127.0.0.1:5173")
    processing_workers: int = int(os.getenv("APP_PROCESSING_WORKERS", "3"))
    thumbnail_width: int = int(os.getenv("APP_THUMBNAIL_WIDTH", "640"))
    analysis_image_max_dimension: int = int(os.getenv("APP_ANALYSIS_IMAGE_MAX_DIMENSION", "0"))
    backup_chunk_mb: int = int(os.getenv("APP_BACKUP_CHUNK_MB", "49"))
    delete_local_backups_after_telegram: bool = _bool(
        "APP_DELETE_LOCAL_BACKUPS_AFTER_TELEGRAM",
        True,
    )
    trust_reverse_proxy: bool = _bool("APP_TRUST_REVERSE_PROXY", True)

    ai_proxy_base_url: str = _normalize_ai_proxy_base_url(os.getenv("AI_PROXY_BASE_URL", "http://127.0.0.1:8317/v1"))
    ai_proxy_api_key: str = os.getenv("AI_PROXY_API_KEY", "")
    ai_proxy_model: str = os.getenv("AI_PROXY_MODEL", "gpt-5.4")
    ai_proxy_reasoning_effort: str = os.getenv("AI_PROXY_REASONING_EFFORT", "xhigh")
    ai_proxy_max_concurrency: int = int(os.getenv("AI_PROXY_MAX_CONCURRENCY", "1"))
    ai_proxy_timeout_seconds: int = int(os.getenv("AI_PROXY_TIMEOUT_SECONDS", "300"))
    ai_proxy_verify_tls: bool = _bool("AI_PROXY_VERIFY_TLS", True)
    ai_proxy_ca_bundle: str = os.getenv("AI_PROXY_CA_BUNDLE", "")

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_backup_chat_id: str = os.getenv("TELEGRAM_BACKUP_CHAT_ID", "")
    telegram_inline_base_url: str = os.getenv("TELEGRAM_INLINE_BASE_URL", "")

    data_root: Path = Path(os.getenv("APP_DATA_ROOT") or str(BASE_DIR))
    storage_root: Path = data_root / "storage"
    incoming_dir: Path = storage_root / "incoming"
    media_dir: Path = storage_root / "media"
    archive_dir: Path = storage_root / "archives"
    thumbnails_dir: Path = storage_root / "thumbnails"
    backups_dir: Path = storage_root / "backups"
    logs_dir: Path = storage_root / "logs"
    database_path: Path = data_root / "library.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"


settings = Settings()
