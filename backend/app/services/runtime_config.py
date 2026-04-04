from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.session import new_session
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
    "processing_load_budget": RuntimeConfigSpec(
        key="processing_load_budget",
        label="Processing load budget",
        description="Общий бюджет нагрузки для балансировщика. Маленькие картинки занимают 1-2 units, тяжелые GIF/видео и огромные файлы занимают больше.",
        kind="integer",
        default=settings.processing_load_budget,
        min_value=1,
        max_value=64,
    ),
    "processing_heavy_job_threshold": RuntimeConfigSpec(
        key="processing_heavy_job_threshold",
        label="Heavy job threshold",
        description="Начиная с какого load score задача считается тяжелой для балансировщика.",
        kind="integer",
        default=settings.processing_heavy_job_threshold,
        min_value=2,
        max_value=32,
    ),
    "processing_max_heavy_jobs": RuntimeConfigSpec(
        key="processing_max_heavy_jobs",
        label="Max heavy jobs",
        description="Сколько тяжелых файлов можно анализировать одновременно. Маленькие картинки при этом могут идти параллельно.",
        kind="integer",
        default=settings.processing_max_heavy_jobs,
        min_value=1,
        max_value=8,
    ),
    "processing_memory_pause_available_mb": RuntimeConfigSpec(
        key="processing_memory_pause_available_mb",
        label="Memory pause threshold MB",
        description="Если доступной памяти осталось меньше этого порога, processor перестает брать новые задачи и уходит в memory guard.",
        kind="integer",
        default=settings.processing_memory_pause_available_mb,
        min_value=64,
        max_value=8192,
    ),
    "processing_memory_resume_available_mb": RuntimeConfigSpec(
        key="processing_memory_resume_available_mb",
        label="Memory resume threshold MB",
        description="Когда доступная память снова поднимается выше этого порога, memory guard снимается и обработка продолжается автоматически. На очень маленьких VPS effective threshold может быть автоматически снижен, чтобы processor не застревал в паузе навсегда.",
        kind="integer",
        default=settings.processing_memory_resume_available_mb,
        min_value=128,
        max_value=8192,
    ),
    "processing_paused": RuntimeConfigSpec(
        key="processing_paused",
        label="Processing paused",
        description="Останавливает запуск новых задач обработки, не прерывая уже активные.",
        kind="boolean",
        default=settings.processing_paused,
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
    "ai_proxy_limit_sleep_hours": RuntimeConfigSpec(
        key="ai_proxy_limit_sleep_hours",
        label="AI limit sleep hours",
        description="На сколько часов усыплять обработку после ответа лимита от AI proxy.",
        kind="integer",
        default=settings.ai_proxy_limit_sleep_hours,
        min_value=1,
        max_value=24,
    ),
    "ai_proxy_limit_status_codes": RuntimeConfigSpec(
        key="ai_proxy_limit_status_codes",
        label="AI limit status codes",
        description="HTTP-коды, которые считаются признаком исчерпанных лимитов. Например: 419,429",
        kind="string",
        default=settings.ai_proxy_limit_status_codes,
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
    "analysis_image_max_dimension": RuntimeConfigSpec(
        key="analysis_image_max_dimension",
        label="Analysis image max dimension",
        description="Максимальный размер статичной картинки для AI-анализa. 0 = отправлять без уменьшения.",
        kind="integer",
        default=settings.analysis_image_max_dimension,
        min_value=0,
        max_value=16384,
    ),
    "backup_chunk_mb": RuntimeConfigSpec(
        key="backup_chunk_mb",
        label="Backup chunk MB",
        description="Размер части backup для Telegram.",
        kind="integer",
        default=settings.backup_chunk_mb,
        min_value=10,
        max_value=45,
    ),
    "backup_download_ttl_hours": RuntimeConfigSpec(
        key="backup_download_ttl_hours",
        label="Backup download TTL hours",
        description="Сколько часов держать на диске подготовленный downloadable backup, чтобы браузер мог возобновить скачивание после обрыва.",
        kind="integer",
        default=settings.backup_download_ttl_hours,
        min_value=1,
        max_value=168,
    ),
    "backup_telegram_pause_seconds": RuntimeConfigSpec(
        key="backup_telegram_pause_seconds",
        label="Backup Telegram pause seconds",
        description="Пауза между отправками backup-частей в Telegram, чтобы снизить риск ошибок при длинных сериях.",
        kind="integer",
        default=settings.backup_telegram_pause_seconds,
        min_value=0,
        max_value=30,
    ),
    "backup_telegram_retry_attempts": RuntimeConfigSpec(
        key="backup_telegram_retry_attempts",
        label="Backup Telegram retry attempts",
        description="Сколько раз повторять неудачную отправку части backup в Telegram перед переводом задачи в failed.",
        kind="integer",
        default=settings.backup_telegram_retry_attempts,
        min_value=1,
        max_value=10,
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


def _coerce_value(spec: RuntimeConfigSpec, raw: Any, *, strict: bool = True) -> Any:
    if spec.kind == "integer":
        try:
            value = int(raw)
        except (TypeError, ValueError):
            if strict:
                raise ValueError(f"{spec.key} must be integer")
            value = int(spec.default)
        if spec.min_value is not None and value < spec.min_value:
            if strict:
                raise ValueError(f"{spec.key} must be >= {spec.min_value}")
            return spec.min_value
        if spec.max_value is not None and value > spec.max_value:
            if strict:
                raise ValueError(f"{spec.key} must be <= {spec.max_value}")
            return spec.max_value
        return value

    if spec.kind == "boolean":
        if isinstance(raw, bool):
            return raw
        lowered = str(raw).strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        if strict:
            raise ValueError(f"{spec.key} must be boolean")
        return bool(spec.default)

    if spec.kind == "enum":
        value = str(raw).strip()
        if value not in spec.choices:
            if strict:
                raise ValueError(f"{spec.key} must be one of: {', '.join(spec.choices)}")
            fallback = str(spec.default).strip()
            return fallback if fallback in spec.choices else spec.choices[0]
        return value

    if spec.kind == "timezone":
        value = str(raw).strip()
        try:
            ZoneInfo(value)
        except Exception:
            if strict:
                raise ValueError(f"{spec.key} must be a valid timezone")
            fallback = str(spec.default).strip()
            ZoneInfo(fallback)
            return fallback
        return value

    return str(raw).strip()


def get_runtime_config_map() -> dict[str, Any]:
    session = new_session()
    try:
        rows = session.query(AppConfigEntry).all()
        row_map = {row.key: row for row in rows}
        resolved: dict[str, Any] = {}
        mutated = False
        for key, spec in CONFIG_SPECS.items():
            row = row_map.get(key)
            raw = row.value if row is not None else spec.default
            try:
                value = _coerce_value(spec, raw)
            except ValueError:
                value = _coerce_value(spec, raw, strict=False)
                if row is not None:
                    serialized = str(value)
                    if row.value != serialized:
                        row.value = serialized
                        mutated = True
            resolved[key] = value
        if mutated:
            session.commit()
        return resolved
    finally:
        session.close()


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

    session = new_session()
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
