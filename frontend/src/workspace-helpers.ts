import type {
  DiskUsagePayload,
  MediaItem,
  ProcessingStats,
  SafetyRating,
  TagCatalogItem,
  TagCatalogPayload,
  UploadResponse,
  User,
  UserRole,
} from './types'
import type { OverviewPayload } from './types'

export type WorkspaceTab = 'library' | 'feed' | 'shares' | 'tags' | 'processing' | 'backups' | 'activity' | 'admin'

export type TabDefinition = {
  id: WorkspaceTab
  short: string
  label: string
  title: string
  description: string
}

export type StorageSegment = {
  key: string
  label: string
  bytes: number
  percent: number
  color: string
}

export const TOKEN_KEY = 're2_token'

export const emptyProcessingStats: ProcessingStats = {
  workers: 0,
  queued: 0,
  processing: 0,
  failed: 0,
  complete: 0,
  completed_last_24h: 0,
  failed_last_24h: 0,
  recent_failure_events: 0,
  throughput_per_hour_24h: 0,
  avg_total_seconds: null,
  p95_total_seconds: null,
  avg_ai_seconds: null,
  p95_ai_seconds: null,
  avg_frames: null,
  avg_prompt_tokens: null,
  avg_completion_tokens: null,
  avg_reasoning_tokens: null,
  oldest_queued_seconds: null,
}

export const emptyOverview: OverviewPayload = {
  counts: {
    media: 0,
    ai_ready: 0,
    media_by_kind: { image: 0, gif: 0, video: 0 },
    media_by_status: { pending: 0, processing: 0, complete: 0, failed: 0 },
    media_by_safety: { sfw: 0, questionable: 0, nsfw: 0, unknown: 0 },
    users: 0,
    jobs: 0,
  },
  processing_stats: emptyProcessingStats,
  ai_proxy_sleep: {
    active: false,
    sleep_until: null,
    triggered_at: null,
    status_code: null,
    last_error: null,
    remaining_seconds: 0,
    monitored_status_codes: [],
    sleep_hours: 3,
  },
  memory_guard: {
    active: false,
    triggered_at: null,
    reason: null,
    snapshot: null,
    pause_available_mb: 192,
    resume_available_mb: 320,
    memory: {
      source: 'unknown',
      total_bytes: 0,
      available_bytes: 0,
      used_bytes: 0,
      limit_bytes: 0,
      raw_used_bytes: 0,
      available_mb: 0,
      used_mb: 0,
      total_mb: 0,
      usage_percent: 0,
    },
  },
  processor: {
    active: false,
    last_seen: null,
    stale_seconds: null,
    timeout_seconds: 45,
    hostname: null,
    pid: null,
    workers: 0,
    desired_workers: 0,
    active_load: 0,
    queue_size: 0,
  },
  processing_paused: false,
  recent_logs: [],
  prompt_preview: '',
}

export const emptyTagCatalog: TagCatalogPayload = {
  items: [],
  leaderboard: [],
  counts: {
    total: 0,
    described: 0,
    pending: 0,
  },
}

export const STORAGE_LABELS: Record<string, string> = {
  media: 'Медиа',
  archives: 'Архивы',
  thumbnails: 'Превью',
  backups: 'Бэкапы',
  database: 'База данных',
  logs: 'Логи',
  incoming: 'Импорт',
  other_on_drive: 'Другое на диске',
  free: 'Свободно',
}

export const STORAGE_COLORS: Record<string, string> = {
  media: '#0f766e',
  archives: '#c46b35',
  thumbnails: '#2563eb',
  backups: '#7c3aed',
  database: '#ef4444',
  logs: '#475569',
  incoming: '#f59e0b',
  other_on_drive: '#8b6f52',
  free: 'rgba(255, 255, 255, 0.45)',
}

export function workspaceTabs(role: UserRole | undefined): TabDefinition[] {
  if (role === 'guest') {
    return [
      { id: 'library', short: 'LB', label: 'Библиотека', title: 'Гостевая библиотека', description: 'Просмотр разрешенных мемов с учетом whitelist и -тегов' },
      { id: 'feed', short: 'FD', label: 'Лента', title: 'Гостевая лента', description: 'Просмотр разрешенных мемов по времени загрузки' },
    ]
  }

  return [
    { id: 'library', short: 'LB', label: 'Библиотека', title: 'Медиатека', description: 'Быстрый поиск, загрузка и просмотр файлов' },
    { id: 'feed', short: 'FD', label: 'Лента', title: 'Лента загрузок', description: 'История медиа по времени с порционной подгрузкой' },
    { id: 'shares', short: 'SH', label: 'Ссылки', title: 'Share Links', description: 'Публичные ссылки на мемы с лимитами и быстрым сжиганием' },
    { id: 'tags', short: 'TG', label: 'Теги', title: 'Каталог тегов', description: 'AI-описания тегов, свойства и лидерборд' },
    { id: 'processing', short: 'AI', label: 'Обработка', title: 'AI-очередь', description: 'Скорость, backlog и проблемные задания' },
    { id: 'backups', short: 'BK', label: 'Бэкапы', title: 'Резервные копии', description: 'Создание и отправка частей в Telegram' },
    { id: 'activity', short: 'LG', label: 'Логи', title: 'События системы', description: 'Ошибки, сигналы и журнал действий' },
    ...(role === 'admin' ? [{ id: 'admin' as const, short: 'AD', label: 'Админ', title: 'Управление', description: 'Диск, пользователи и runtime-конфиг' }] : []),
  ]
}

export function formatBytes(bytes: number) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(value >= 100 || index === 0 ? 0 : 1)} ${units[index]}`
}

export function formatDate(value?: string | null) {
  if (!value) return 'Unknown'
  return new Date(value).toLocaleString('ru-RU')
}

export function formatDuration(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a'
  const rounded = Math.max(0, Math.round(value))
  const hours = Math.floor(rounded / 3600)
  const minutes = Math.floor((rounded % 3600) / 60)
  const seconds = rounded % 60
  if (hours) return `${hours}ч ${minutes}м`
  if (minutes) return `${minutes}м ${seconds.toString().padStart(2, '0')}с`
  return `${seconds}с`
}

export function formatMetric(value?: number | null, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a'
  if (Number.isInteger(value)) return `${value}`
  return value.toFixed(digits).replace(/\.0+$/, '').replace(/(\.\d*[1-9])0+$/, '$1')
}

export function trimText(value: string | null | undefined, fallback: string, max = 180) {
  const text = (value ?? '').trim()
  if (!text) return fallback
  return text.length <= max ? text : `${text.slice(0, max).trimEnd()}...`
}

export function buildUploadNotice(result: UploadResponse) {
  const directCount = result.items.length
  const archiveCount = result.archives.length
  const importedFromArchives = result.archives.reduce((sum, archive) => sum + archive.media_ids.length, 0)
  const emptyArchives = result.archives.filter((archive) => archive.media_ids.length === 0)

  const parts: string[] = []
  if (directCount) parts.push(`напрямую загружено ${directCount}`)
  if (archiveCount) parts.push(`архивов ${archiveCount}`)
  if (importedFromArchives) parts.push(`из архивов импортировано ${importedFromArchives}`)
  if (!parts.length) parts.push('Загрузка завершена')

  if (emptyArchives.length) {
    const archive = emptyArchives[0]
    const unsupportedHint = archive.top_unsupported_extensions.length
      ? ` Чаще всего пропущены: ${archive.top_unsupported_extensions.map(([ext, count]) => `${ext} x${count}`).join(', ')}.`
      : ''
    parts.push(`в архиве ${archive.filename} не найдено поддерживаемых медиафайлов.${unsupportedHint}`)
  }

  if (directCount || importedFromArchives) {
    parts.push('превью и техметаданные догружаются в фоне')
  }

  return parts.join(' · ')
}

export function primaryDescription(item: MediaItem) {
  return item.description_ru ?? item.description ?? ''
}

export function prettifyTag(value: string) {
  return value.replaceAll('_', ' ')
}

export function extractSafetyTags(item: MediaItem | null) {
  return (item?.tags ?? []).filter((tag) => tag.kind === 'safety').map((tag) => tag.name)
}

export function parseTagInput(value: string) {
  return value
    .split(/[,\n;]+/g)
    .map((part) => part.trim().toLowerCase().replace(/\s+/g, '_'))
    .filter(Boolean)
}

export function toDateInputString(date: Date) {
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function readDetailList(payload: Record<string, unknown> | null | undefined, key: string) {
  const value = payload?.[key]
  return Array.isArray(value) ? value.map((entry) => `${entry}`) : []
}

export function readDetailText(payload: Record<string, unknown> | null | undefined, key: string) {
  const value = payload?.[key]
  return typeof value === 'string' ? value : ''
}

export function configValueToInput(value: string | number | boolean) {
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return `${value}`
}

export function roleLabel(user: User | null) {
  if (user?.role === 'admin') return 'Администратор'
  if (user?.role === 'guest') return 'Гость'
  return 'Участник'
}

export function kindLabel(kind: MediaItem['kind']) {
  if (kind === 'image') return 'Изображение'
  if (kind === 'gif') return 'GIF'
  return 'Видео'
}

export function ratingLabel(rating: SafetyRating) {
  if (rating === 'sfw') return 'SFW'
  if (rating === 'questionable') return 'Questionable'
  if (rating === 'nsfw') return 'NSFW'
  return 'Unknown'
}

export function appendUniqueMedia(current: MediaItem[], incoming: MediaItem[]) {
  if (!incoming.length) {
    return current
  }
  const seen = new Set(current.map((item) => item.id))
  return [...current, ...incoming.filter((item) => !seen.has(item.id))]
}

export function buildStorageSegments(
  items: Array<{ key: string; bytes: number }>,
  total: number,
): StorageSegment[] {
  if (!total) {
    return []
  }

  return items
    .filter((item) => item.bytes > 0)
    .map((item) => ({
      key: item.key,
      label: STORAGE_LABELS[item.key] ?? item.key,
      bytes: item.bytes,
      percent: (item.bytes / total) * 100,
      color: STORAGE_COLORS[item.key] ?? '#64748b',
    }))
}

export function topTagsFromMedia(items: MediaItem[]) {
  const tagCountMap = new Map<string, number>()
  items.forEach((item) => {
    ;(item.tags ?? []).forEach((tag) => tagCountMap.set(tag.name, (tagCountMap.get(tag.name) ?? 0) + 1))
  })
  return Array.from(tagCountMap.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 12)
}

export function orderedProjectBreakdown(storage: DiskUsagePayload | null) {
  if (!storage) {
    return [] as Array<[string, number]>
  }
  const projectUsageTotal = storage.project.total ?? 0
  const projectBreakdown = Object.entries(storage.project).filter(([name]) => name !== 'total')
  const projectSegmentOrder = ['media', 'archives', 'thumbnails', 'backups', 'database', 'logs', 'incoming']
  return [
    ...projectSegmentOrder
      .map((key) => [key, projectBreakdown.find(([name]) => name === key)?.[1] ?? 0] as [string, number])
      .filter(([, value]) => value > 0),
    ...projectBreakdown
      .filter(([name]) => !projectSegmentOrder.includes(name))
      .sort((a, b) => b[1] - a[1]),
    ...(projectUsageTotal > 0 ? [['total', projectUsageTotal] as [string, number]] : []),
  ].filter(([name]) => name !== 'total')
}

export function isCompactScreen() {
  return typeof window !== 'undefined' && window.matchMedia('(max-width: 960px)').matches
}

export function selectedTagDetails(tag: TagCatalogItem | null) {
  return tag?.details_payload ?? null
}
