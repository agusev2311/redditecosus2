from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.session import SessionLocal
from app.models import AppConfigEntry


ConfigKind = Literal["string", "integer", "boolean", "enum", "timezone"]


@dataclass(frozen=True)
class RuntimeConfigSpec:
    key: str
    label: str
    description: str
    kind: ConfigKind
    default: Any
    min_value: int | None = None
    max_value: int | None = None
    choices: tuple[str, ...] = ()


CONFIG_SPECS: dict[str, RuntimeConfigSpec] = {
    "processing_workers": RuntimeConfigSpec(
        key="processing_workers",
        label="Workers",
        description="Количество параллельных AI workers.",
        kind="integer",
        default=settings.processing_workers,
        min_value=1,
        max_value=24,
    ),
    "ai_proxy_base_url": RuntimeConfigSpec(
        key="ai_proxy_base_url",
        label="AI proxy URL",
        description="Базовый URL OpenAI-compatible proxy.",
        kind="string",
        default=settings.ai_proxy_base_url,
    ),
    "ai_proxy_model": RuntimeConfigSpec(
        key="ai_proxy_model",
        label="AI model",
        description="Модель для индексации медиа.",
        kind="string",
        default=settings.ai_proxy_model,
    ),
    "ai_proxy_reasoning_effort": RuntimeConfigSpec(
        key="ai_proxy_reasoning_effort",
        label="Reasoning effort",
        description="Уровень reasoning_effort для proxy.",
        kind="enum",
        default=settings.ai_proxy_reasoning_effort,
        choices=("low", "medium", "high", "xhigh"),
    ),
    "ai_proxy_max_concurrency": RuntimeConfigSpec(
        key="ai_proxy_max_concurrency",
        label="AI max concurrency",
        description="Максимум одновременно активных анализов медиа, включая чтение файлов, кадры и запросы к AI proxy.",
        kind="integer",
        default=settings.ai_proxy_max_concurrency,
        min_value=1,
        max_value=8,
    ),
    "ai_proxy_timeout_seconds": RuntimeConfigSpec(
        key="ai_proxy_timeout_seconds",
        label="AI timeout",
        description="Таймаут запроса к proxy в секундах.",
        kind="integer",
        default=settings.ai_proxy_timeout_seconds,
        min_value=30,
        max_value=3600,
    ),
    "ai_proxy_verify_tls": RuntimeConfigSpec(
        key="ai_proxy_verify_tls",
        label="Verify TLS",
        description="Проверять TLS сертификат AI proxy.",
        kind="boolean",
        default=settings.ai_proxy_verify_tls,
    ),
    "ai_proxy_ca_bundle": RuntimeConfigSpec(
        key="ai_proxy_ca_bundle",
        label="CA bundle path",
        description="Путь к CA bundle внутри контейнера.",
        kind="string",
        default=settings.ai_proxy_ca_bundle,
    ),
    "default_timezone": RuntimeConfigSpec(
        key="default_timezone",
        label="Default timezone",
        description="Таймзона для нормализации времени из имени файла.",
        kind="timezone",
        default=settings.default_timezone,
    ),
    "thumbnail_width": RuntimeConfigSpec(
        key="thumbnail_width",
        label="Thumbnail width",
        description="Максимальная ширина превью.",
        kind="integer",
        default=settings.thumbnail_width,
        min_value=160,
        max_value=4096,
    ),
    "backup_chunk_mb": RuntimeConfigSpec(
        key="backup_chunk_mb",
        label="Backup chunk MB",
        description="Размер части backup для Telegram.",
        kind="integer",
        default=settings.backup_chunk_mb,
        min_value=10,
        max_value=49,
    ),
    "analysis_existing_tag_limit": RuntimeConfigSpec(
        key="analysis_existing_tag_limit",
        label="Existing tag limit",
        description="Сколько существующих тегов передавать модели как preferred vocabulary.",
        kind="integer",
        default=320,
        min_value=32,
        max_value=2000,
    ),
}


def _coerce_value(spec: RuntimeConfigSpec, raw: Any) -> Any:
    if spec.kind == "integer":
        value = int(raw)
        if spec.min_value is not None and value < spec.min_value:
            raise ValueError(f"{spec.key} must be >= {spec.min_value}")
        if spec.max_value is not None and value > spec.max_value:
            raise ValueError(f"{spec.key} must be <= {spec.max_value}")
        return value

    if spec.kind == "boolean":
        if isinstance(raw, bool):
            return raw
        lowered = str(raw).strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{spec.key} must be boolean")

    if spec.kind == "enum":
        value = str(raw).strip()
        if value not in spec.choices:
            raise ValueError(f"{spec.key} must be one of: {', '.join(spec.choices)}")
        return value

    if spec.kind == "timezone":
        value = str(raw).strip()
        ZoneInfo(value)
        return value

    return str(raw).strip()


def get_runtime_config_map() -> dict[str, Any]:
    session = SessionLocal()
    try:
        rows = session.query(AppConfigEntry).all()
        values = {row.key: row.value for row in rows}
    finally:
        session.close()

    resolved: dict[str, Any] = {}
    for key, spec in CONFIG_SPECS.items():
        raw = values.get(key, spec.default)
        resolved[key] = _coerce_value(spec, raw)
    return resolved


def get_runtime_value(key: str) -> Any:
    if key not in CONFIG_SPECS:
        raise KeyError(key)
    return get_runtime_config_map()[key]


def list_runtime_config_items() -> list[dict[str, Any]]:
    current = get_runtime_config_map()
    items: list[dict[str, Any]] = []
    for key, spec in CONFIG_SPECS.items():
        items.append(
            {
                "key": key,
                "label": spec.label,
                "description": spec.description,
                "kind": spec.kind,
                "value": current[key],
                "default": spec.default,
                "min": spec.min_value,
                "max": spec.max_value,
                "choices": list(spec.choices),
            }
        )
    return items


def update_runtime_config_values(updates: dict[str, Any], *, updated_by_id: int | None = None) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, raw in updates.items():
        spec = CONFIG_SPECS.get(key)
        if spec is None:
            raise ValueError(f"Unknown config key: {key}")
        normalized[key] = _coerce_value(spec, raw)

    session = SessionLocal()
    try:
        for key, value in normalized.items():
            row = session.get(AppConfigEntry, key)
            if row is None:
                row = AppConfigEntry(key=key, value=str(value), updated_by_id=updated_by_id)
                session.add(row)
            else:
                row.value = str(value)
                row.updated_by_id = updated_by_id
        session.commit()
    finally:
        session.close()

    return get_runtime_config_map()
