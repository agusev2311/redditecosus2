from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("APP_ENV", "development")
    secret_key: str = os.getenv("APP_SECRET_KEY", "dev-secret-key")
    jwt_ttl_days: int = int(os.getenv("APP_JWT_TTL_DAYS", "30"))
    default_timezone: str = os.getenv("APP_DEFAULT_TIMEZONE", "Europe/Moscow")
    public_base_url: str = os.getenv("APP_PUBLIC_BASE_URL", "http://127.0.0.1:5000")
    frontend_url: str = os.getenv("APP_FRONTEND_URL", "http://127.0.0.1:5173")
    processing_workers: int = int(os.getenv("APP_PROCESSING_WORKERS", "3"))
    thumbnail_width: int = int(os.getenv("APP_THUMBNAIL_WIDTH", "640"))
    backup_chunk_mb: int = int(os.getenv("APP_BACKUP_CHUNK_MB", "49"))
    delete_local_backups_after_telegram: bool = _bool(
        "APP_DELETE_LOCAL_BACKUPS_AFTER_TELEGRAM",
        True,
    )

    ai_proxy_base_url: str = os.getenv("AI_PROXY_BASE_URL", "http://127.0.0.1:8317/v1")
    ai_proxy_api_key: str = os.getenv("AI_PROXY_API_KEY", "")
    ai_proxy_model: str = os.getenv("AI_PROXY_MODEL", "gpt-5.4")
    ai_proxy_reasoning_effort: str = os.getenv("AI_PROXY_REASONING_EFFORT", "xhigh")
    ai_proxy_timeout_seconds: int = int(os.getenv("AI_PROXY_TIMEOUT_SECONDS", "300"))

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_backup_chat_id: str = os.getenv("TELEGRAM_BACKUP_CHAT_ID", "")
    telegram_inline_base_url: str = os.getenv("TELEGRAM_INLINE_BASE_URL", "")

    storage_root: Path = BASE_DIR / "storage"
    incoming_dir: Path = storage_root / "incoming"
    media_dir: Path = storage_root / "media"
    archive_dir: Path = storage_root / "archives"
    thumbnails_dir: Path = storage_root / "thumbnails"
    backups_dir: Path = storage_root / "backups"
    logs_dir: Path = storage_root / "logs"
    database_path: Path = BASE_DIR / "library.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"


settings = Settings()

