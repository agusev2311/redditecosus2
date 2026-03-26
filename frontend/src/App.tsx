import { type ChangeEvent, type DragEvent, type FormEvent, startTransition, useDeferredValue, useEffect, useState } from 'react'

import {
  bootstrap,
  createBackup,
  createUser,
  getBootstrapStatus,
  getMedia,
  getOverview,
  getRuntimeConfig,
  getStorage,
  getUsers,
  listBackups,
  listJobs,
  listMedia,
  listTags,
  login,
  mediaAssetUrl,
  me,
  reindexMedia,
  reindexAllMedia,
  resumeAIProxy,
  resetLibrary,
  retryFailedJobs,
  triggerTagBackfill,
  updateMedia,
  updateRuntimeConfig,
  uploadFiles,
} from './api'
import type { BackupItem, DiskUsagePayload, JobItem, MediaItem, OverviewPayload, ProcessingStats, RuntimeConfigItem, SafetyRating, TagCatalogItem, TagCatalogPayload, UploadResponse, User } from './types'

type WorkspaceTab = 'library' | 'feed' | 'tags' | 'processing' | 'backups' | 'activity' | 'admin'

type TabDefinition = {
  id: WorkspaceTab
  short: string
  label: string
  title: string
  description: string
}

const TOKEN_KEY = 're2_token'
const emptyProcessingStats: ProcessingStats = {
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
const emptyOverview: OverviewPayload = {
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
const emptyTagCatalog: TagCatalogPayload = {
  items: [],
  leaderboard: [],
  counts: {
    total: 0,
    described: 0,
    pending: 0,
  },
}

function formatBytes(bytes: number) {
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

function formatDate(value?: string | null) {
  if (!value) return 'Unknown'
  return new Date(value).toLocaleString('ru-RU')
}

function formatDuration(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a'
  const rounded = Math.max(0, Math.round(value))
  const hours = Math.floor(rounded / 3600)
  const minutes = Math.floor((rounded % 3600) / 60)
  const seconds = rounded % 60
  if (hours) return `${hours}ч ${minutes}м`
  if (minutes) return `${minutes}м ${seconds.toString().padStart(2, '0')}с`
  return `${seconds}с`
}

function formatMetric(value?: number | null, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a'
  if (Number.isInteger(value)) return `${value}`
  return value.toFixed(digits).replace(/\.0+$/, '').replace(/(\.\d*[1-9])0+$/, '$1')
}

function trimText(value: string | null | undefined, fallback: string, max = 180) {
  const text = (value ?? '').trim()
  if (!text) return fallback
  return text.length <= max ? text : `${text.slice(0, max).trimEnd()}...`
}

function buildUploadNotice(result: UploadResponse) {
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

  return parts.join(' · ')
}

function primaryDescription(item: MediaItem) {
  return item.description_ru ?? item.description ?? ''
}

function prettifyTag(value: string) {
  return value.replaceAll('_', ' ')
}

function extractSafetyTags(item: MediaItem | null) {
  return (item?.tags ?? []).filter((tag) => tag.kind === 'safety').map((tag) => tag.name)
}

function parseTagInput(value: string) {
  return value
    .split(/[,\n;]+/g)
    .map((part) => part.trim().toLowerCase().replace(/\s+/g, '_'))
    .filter(Boolean)
}

function toDateInputString(date: Date) {
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

function readDetailList(payload: Record<string, unknown> | null | undefined, key: string) {
  const value = payload?.[key]
  return Array.isArray(value) ? value.map((entry) => `${entry}`) : []
}

function readDetailText(payload: Record<string, unknown> | null | undefined, key: string) {
  const value = payload?.[key]
  return typeof value === 'string' ? value : ''
}

function configValueToInput(value: string | number | boolean) {
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return `${value}`
}

const STORAGE_LABELS: Record<string, string> = {
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

const STORAGE_COLORS: Record<string, string> = {
  media: '#b989ff',
  archives: '#7d8bff',
  thumbnails: '#5ec8f8',
  backups: '#f0b36a',
  database: '#ef7aa4',
  logs: '#73c38f',
  incoming: '#d2c16d',
  other_on_drive: '#5b5470',
  free: 'rgba(255, 255, 255, 0.12)',
}

function roleLabel(user: User | null) {
  return user?.role === 'admin' ? 'Администратор' : 'Участник'
}

function kindLabel(kind: MediaItem['kind']) {
  if (kind === 'image') return 'Изображение'
  if (kind === 'gif') return 'GIF'
  return 'Видео'
}

function ratingLabel(rating: SafetyRating) {
  if (rating === 'sfw') return 'SFW'
  if (rating === 'questionable') return 'Questionable'
  if (rating === 'nsfw') return 'NSFW'
  return 'Unknown'
}

function StatCard({ label, value, hint, tone = 'default' }: { label: string; value: string | number; hint?: string; tone?: 'default' | 'accent' | 'success' | 'danger' }) {
  return (
    <article className={`stat-card tone-${tone}`}>
      <span className="stat-label">{label}</span>
      <strong className="stat-value">{value}</strong>
      {hint ? <small className="stat-hint">{hint}</small> : null}
    </article>
  )
}

function SidebarTab({
  active,
  short,
  label,
  description,
  collapsed,
  onClick,
}: {
  active: boolean
  short: string
  label: string
  description: string
  collapsed: boolean
  onClick: () => void
}) {
  return (
    <button className={`sidebar-tab ${active ? 'active' : ''}`} type="button" onClick={onClick}>
      <span className="sidebar-tab-mark">{short}</span>
      {!collapsed ? (
        <span className="sidebar-tab-copy">
          <strong>{label}</strong>
          <small>{description}</small>
        </span>
      ) : null}
    </button>
  )
}

function MediaCard({ item, token, active, onOpen }: { item: MediaItem; token: string; active: boolean; onOpen: () => void }) {
  const visibleTags = (item.tags ?? []).slice(0, 6)
  return (
    <article className={`gallery-card ${active ? 'active' : ''}`}>
      <button className="gallery-hitbox" type="button" onClick={onOpen}>
        <span className="sr-only">Open media</span>
      </button>
      <div className="gallery-preview">
        {item.thumbnail_url ? <img src={mediaAssetUrl(item.thumbnail_url, token)} alt={item.original_filename} loading="lazy" /> : <div className="gallery-empty">{kindLabel(item.kind)}</div>}
        <div className="gallery-overlay">
          <span>{kindLabel(item.kind)}</span>
          <span className={`badge badge-${item.safety_rating}`}>{ratingLabel(item.safety_rating)}</span>
        </div>
      </div>
      <div className="gallery-body">
        <div className="row-meta">
          <span className={`badge badge-status-${item.processing_status}`}>{item.processing_status}</span>
          <small>{formatBytes(item.file_size)}</small>
        </div>
        <h3 title={item.original_filename}>{item.original_filename}</h3>
        <p>{trimText(primaryDescription(item), 'AI-описание пока не готово. После индексации здесь появится краткий разбор сцены.', 120)}</p>
        <div className="row-meta compact">
          <span>{item.width && item.height ? `${item.width}×${item.height}` : kindLabel(item.kind)}</span>
          <span>{item.duration_seconds ? formatDuration(item.duration_seconds) : formatDate(item.normalized_timestamp)}</span>
        </div>
        <div className="chip-row">
          {visibleTags.map((tag) => (
            <span key={`${item.id}-${tag.kind}-${tag.name}`} className={`tag-chip tag-${tag.kind}`}>
              {tag.name.replaceAll('_', ' ')}
            </span>
          ))}
        </div>
      </div>
    </article>
  )
}

function FeedCard({ item, token, onOpen }: { item: MediaItem; token: string; onOpen: () => void }) {
  const visibleTags = (item.tags ?? []).slice(0, 10)
  return (
    <article className="feed-card glass-panel">
      <button className="gallery-hitbox" type="button" onClick={onOpen}>
        <span className="sr-only">Open media</span>
      </button>
      <div className="feed-preview">
        {item.thumbnail_url ? <img src={mediaAssetUrl(item.thumbnail_url, token)} alt={item.original_filename} loading="lazy" /> : <div className="gallery-empty">{kindLabel(item.kind)}</div>}
      </div>
      <div className="feed-body">
        <div className="row-meta">
          <div className="chip-row">
            <span className={`badge badge-${item.safety_rating}`}>{ratingLabel(item.safety_rating)}</span>
            <span className={`badge badge-status-${item.processing_status}`}>{item.processing_status}</span>
            <span className="badge">{kindLabel(item.kind)}</span>
          </div>
          <small>{formatDate(item.created_at ?? item.normalized_timestamp)}</small>
        </div>
        <h2 title={item.original_filename}>{item.original_filename}</h2>
        <p className="feed-description">{trimText(primaryDescription(item), 'AI-описание пока не готово.', 540)}</p>
        <div className="row-meta compact">
          <span>{item.width && item.height ? `${item.width}×${item.height}` : kindLabel(item.kind)}</span>
          <span>{formatBytes(item.file_size)}</span>
          <span>{item.duration_seconds ? formatDuration(item.duration_seconds) : 'static'}</span>
        </div>
        <div className="chip-row spacious">
          {visibleTags.map((tag) => (
            <span key={`${item.id}-${tag.kind}-${tag.name}`} className={`tag-chip tag-${tag.kind}`}>
              {prettifyTag(tag.name)}
            </span>
          ))}
        </div>
      </div>
    </article>
  )
}

function TagRow({ item, active, onClick }: { item: TagCatalogItem; active: boolean; onClick: () => void }) {
  return (
    <button className={`tag-row ${active ? 'active' : ''}`} type="button" onClick={onClick}>
      <div className="tag-row-copy">
        <strong>{prettifyTag(item.name)}</strong>
        <small>{item.kind} · использований {item.usage_count}</small>
      </div>
      <span className={`badge ${item.is_described ? 'badge-status-complete' : 'badge-status-pending'}`}>
        {item.is_described ? 'described' : 'pending'}
      </span>
    </button>
  )
}

function TagDetailsContent({
  selectedTag,
  selectedTagDetails,
  onClose,
}: {
  selectedTag: TagCatalogItem | null
  selectedTagDetails: Record<string, unknown> | null
  onClose?: () => void
}) {
  if (!selectedTag) {
    return (
      <article className="empty-state">
        <h2>Выберите тег слева.</h2>
        <p className="muted">Здесь появится подробное описание, свойства, связи и подсказки для поиска.</p>
      </article>
    )
  }

  return (
    <>
      <div className="panel-head">
        <div>
          <span>{selectedTag.kind}</span>
          <h2>{prettifyTag(selectedTag.name)}</h2>
        </div>
        <div className="tag-detail-head-actions">
          <div className="chip-row">
            <span className="badge">{selectedTag.usage_count} uses</span>
            <span className={`badge ${selectedTag.is_described ? 'badge-status-complete' : 'badge-status-pending'}`}>{selectedTag.is_described ? 'ready' : 'pending'}</span>
          </div>
          {onClose ? <button className="ghost-button mobile-only" type="button" onClick={onClose}>Закрыть</button> : null}
        </div>
      </div>
      <div className="tag-details-scroll">
        <div className="note-block">
          <span>Описание RU</span>
          <p>{trimText(selectedTag.description_ru, 'AI-описание для этого тега еще не готово.', 2000)}</p>
        </div>
        <div className="note-block">
          <span>Description EN</span>
          <p>{trimText(selectedTag.description_en, 'English tag description is not available yet.', 2000)}</p>
        </div>
        <div className="detail-grid tag-detail-grid">
          <div><span>Aliases</span><strong>{readDetailList(selectedTagDetails, 'aliases').map(prettifyTag).join(', ') || 'n/a'}</strong></div>
          <div><span>Parents</span><strong>{readDetailList(selectedTagDetails, 'parent_categories').map(prettifyTag).join(', ') || 'n/a'}</strong></div>
          <div><span>Related</span><strong>{readDetailList(selectedTagDetails, 'related_tags').map(prettifyTag).join(', ') || 'n/a'}</strong></div>
          <div><span>Described</span><strong>{formatDate(selectedTag.ai_described_at)}</strong></div>
        </div>
        <div className="note-block">
          <span>Свойства</span>
          <ul className="detail-list">
            {readDetailList(selectedTagDetails, 'distinguishing_features').map((value) => <li key={value}>{value}</li>)}
          </ul>
        </div>
        <div className="note-block">
          <span>Типичный контекст</span>
          <ul className="detail-list">
            {readDetailList(selectedTagDetails, 'common_contexts').map((value) => <li key={value}>{value}</li>)}
          </ul>
        </div>
        <div className="note-block">
          <span>Search hints</span>
          <div className="chip-row spacious">
            {readDetailList(selectedTagDetails, 'search_hints').map((value) => <span key={value} className="tag-chip">{value}</span>)}
          </div>
        </div>
        {(readDetailText(selectedTagDetails, 'moderation_notes_ru') || readDetailText(selectedTagDetails, 'ambiguity_note_ru')) ? (
          <div className="note-block">
            <span>Примечания</span>
            <p>{trimText(readDetailText(selectedTagDetails, 'moderation_notes_ru'), '', 1200)}</p>
            {readDetailText(selectedTagDetails, 'ambiguity_note_ru') ? <p className="muted">{trimText(readDetailText(selectedTagDetails, 'ambiguity_note_ru'), '', 600)}</p> : null}
          </div>
        ) : null}
      </div>
    </>
  )
}

function App() {
  const [token, setToken] = useState<string>(() => localStorage.getItem(TOKEN_KEY) ?? '')
  const [currentUser, setCurrentUser] = useState<User | null>(null)
  const [needsBootstrap, setNeedsBootstrap] = useState<boolean | null>(null)
  const [overview, setOverview] = useState<OverviewPayload>(emptyOverview)
  const [media, setMedia] = useState<MediaItem[]>([])
  const [feedItems, setFeedItems] = useState<MediaItem[]>([])
  const [jobs, setJobs] = useState<JobItem[]>([])
  const [backups, setBackups] = useState<BackupItem[]>([])
  const [storage, setStorage] = useState<DiskUsagePayload | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfigItem[]>([])
  const [runtimeConfigForm, setRuntimeConfigForm] = useState<Record<string, string>>({})
  const [tagCatalog, setTagCatalog] = useState<TagCatalogPayload>(emptyTagCatalog)
  const [selectedTag, setSelectedTag] = useState<TagCatalogItem | null>(null)
  const [tagDetailOpen, setTagDetailOpen] = useState(false)
  const [selectedMedia, setSelectedMedia] = useState<MediaItem | null>(null)
  const [viewerOpen, setViewerOpen] = useState(false)
  const [searchInput, setSearchInput] = useState('')
  const [kindFilter, setKindFilter] = useState('')
  const [ratingFilter, setRatingFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [feedFrom, setFeedFrom] = useState('')
  const [feedTo, setFeedTo] = useState('')
  const [tagSearch, setTagSearch] = useState('')
  const [tagKindFilter, setTagKindFilter] = useState('')
  const [tagDescribedFilter, setTagDescribedFilter] = useState('')
  const [savingSafety, setSavingSafety] = useState(false)
  const [backfillingTags, setBackfillingTags] = useState(false)
  const [safetyForm, setSafetyForm] = useState<{ rating: SafetyRating; tags: string }>({ rating: 'unknown', tags: '' })
  const [refreshNonce, setRefreshNonce] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [dragActive, setDragActive] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [authForm, setAuthForm] = useState({ username: '', password: '', telegram: '' })
  const [newUserForm, setNewUserForm] = useState({ username: '', password: '', telegram: '', role: 'member' as 'admin' | 'member' })
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('library')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [retryingFailed, setRetryingFailed] = useState(false)
  const [savingRuntimeConfig, setSavingRuntimeConfig] = useState(false)
  const [reindexingAll, setReindexingAll] = useState(false)
  const [resumingAIProxy, setResumingAIProxy] = useState(false)
  const [dangerConfirmation, setDangerConfirmation] = useState('')
  const [resettingLibrary, setResettingLibrary] = useState(false)
  const deferredSearch = useDeferredValue(searchInput)
  const deferredTagSearch = useDeferredValue(tagSearch)
  const isAdmin = currentUser?.role === 'admin'

  useEffect(() => {
    void getBootstrapStatus().then((data) => setNeedsBootstrap(data.needs_bootstrap)).catch((reason) => setError(reason instanceof Error ? reason.message : 'Failed to load bootstrap status'))
  }, [])
  useEffect(() => {
    if (!token) return setCurrentUser(null)
    void me(token).then((data) => setCurrentUser(data.user)).catch(() => {
      localStorage.removeItem(TOKEN_KEY)
      setToken('')
    })
  }, [token])
  useEffect(() => {
    if (!token || !currentUser) return
    let cancelled = false
    const load = async () => {
      try {
        const [
          overviewPayload,
          mediaPayload,
          jobsPayload,
          backupsPayload,
          feedPayload,
          tagPayload,
          adminPayload,
        ] = await Promise.all([
          getOverview(token),
          activeTab === 'library'
            ? listMedia(token, { q: deferredSearch || undefined, kind: kindFilter || undefined, rating: ratingFilter || undefined, status: statusFilter || undefined })
            : Promise.resolve(null),
          activeTab === 'processing' ? listJobs(token) : Promise.resolve(null),
          activeTab === 'backups' ? listBackups(token) : Promise.resolve(null),
          activeTab === 'feed'
            ? listMedia(token, {
                created_from: feedFrom || undefined,
                created_to: feedTo || undefined,
                limit: '120',
              })
            : Promise.resolve(null),
          activeTab === 'tags'
            ? listTags(token, {
                q: deferredTagSearch || undefined,
                kind: tagKindFilter || undefined,
                described: tagDescribedFilter || undefined,
                limit: '240',
              })
            : Promise.resolve(null),
          isAdmin && activeTab === 'admin'
            ? Promise.all([getStorage(token), getUsers(token), getRuntimeConfig(token)])
            : Promise.resolve(null),
        ])
        if (cancelled) return
        setOverview(overviewPayload)
        if (mediaPayload) setMedia(mediaPayload.items)
        if (jobsPayload) setJobs(jobsPayload.items)
        if (backupsPayload) setBackups(backupsPayload.items)
        if (feedPayload) setFeedItems(feedPayload.items)
        if (tagPayload) setTagCatalog(tagPayload)
        if (adminPayload) {
          const [storagePayload, usersPayload, runtimeConfigPayload] = adminPayload
          setStorage(storagePayload)
          setUsers(usersPayload.items)
          setRuntimeConfig(runtimeConfigPayload.items)
          setRuntimeConfigForm((current) => (
            Object.keys(current).length
              ? current
              : Object.fromEntries(runtimeConfigPayload.items.map((item) => [item.key, configValueToInput(item.value)]))
          ))
        } else if (!isAdmin) {
          setStorage(null)
          setUsers([])
          setRuntimeConfig([])
          setRuntimeConfigForm({})
        }
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Failed to refresh dashboard')
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), 12000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [token, currentUser, deferredSearch, kindFilter, ratingFilter, statusFilter, activeTab, deferredTagSearch, tagKindFilter, tagDescribedFilter, feedFrom, feedTo, refreshNonce, isAdmin])
  useEffect(() => {
    if (!selectedMedia) return
    const refreshed = [...media, ...feedItems].find((item) => item.id === selectedMedia.id)
    if (!refreshed) return
    setSelectedMedia((current) => {
      if (!current || current.id !== refreshed.id) return current
      return {
        ...current,
        ...refreshed,
        description: current.description ?? refreshed.description,
        description_ru: current.description_ru ?? refreshed.description_ru,
        description_en: current.description_en ?? refreshed.description_en,
        technical_notes: current.technical_notes ?? refreshed.technical_notes,
        ai_payload: current.ai_payload ?? refreshed.ai_payload,
        tags: refreshed.tags?.length ? refreshed.tags : current.tags,
      }
    })
  }, [media, feedItems, selectedMedia])
  useEffect(() => {
    if (!selectedMedia) return
    setSafetyForm({
      rating: selectedMedia.safety_rating,
      tags: extractSafetyTags(selectedMedia).join(', '),
    })
  }, [selectedMedia])
  useEffect(() => {
    if (!tagCatalog.items.length) {
      setSelectedTag(null)
      return
    }
    const refreshed = selectedTag ? tagCatalog.items.find((item) => item.id === selectedTag.id) : null
    if (refreshed) {
      if (refreshed !== selectedTag) setSelectedTag(refreshed)
      return
    }
    setSelectedTag(tagCatalog.items[0])
  }, [tagCatalog, selectedTag])
  useEffect(() => {
    if (currentUser?.role !== 'admin' && activeTab === 'admin') setActiveTab('library')
  }, [activeTab, currentUser])
  useEffect(() => {
    if (activeTab !== 'tags') setTagDetailOpen(false)
  }, [activeTab])

  const handleAuthSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    try {
      const auth = needsBootstrap ? await bootstrap(authForm.username, authForm.password, authForm.telegram) : await login(authForm.username, authForm.password)
      localStorage.setItem(TOKEN_KEY, auth.token)
      setToken(auth.token)
      setCurrentUser(auth.user)
      setNeedsBootstrap(false)
      setAuthForm({ username: '', password: '', telegram: '' })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Authentication failed')
    }
  }

  const handleFiles = async (files: File[]) => {
    if (!token || files.length === 0) return
    setError('')
    setNotice('')
    setUploading(true)
    setUploadProgress(0)
    try {
      const result = await uploadFiles(token, files, setUploadProgress)
      setNotice(buildUploadNotice(result))
      setRefreshNonce((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Upload failed')
    } finally {
      setUploading(false)
      window.setTimeout(() => setUploadProgress(0), 800)
    }
  }

  const handleCreateBackup = async (scope: 'metadata' | 'full') => {
    if (!token) return
    setError('')
    setNotice('')
    try {
      await createBackup(token, scope, true)
      setRefreshNonce((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Backup request failed')
    }
  }

  const handleCreateUser = async (event: FormEvent) => {
    event.preventDefault()
    if (!token) return
    setError('')
    setNotice('')
    try {
      await createUser(token, { username: newUserForm.username, password: newUserForm.password, role: newUserForm.role, telegram_username: newUserForm.telegram })
      setNewUserForm({ username: '', password: '', telegram: '', role: 'member' })
      setRefreshNonce((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'User creation failed')
    }
  }

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragActive(false)
    void handleFiles(Array.from(event.dataTransfer.files))
  }

  const handlePick = (event: ChangeEvent<HTMLInputElement>) => {
    void handleFiles(Array.from(event.target.files ?? []))
    event.target.value = ''
  }

  const handleReindex = async (mediaId: string) => {
    if (!token) return
    setError('')
    setNotice('')
    try {
      await reindexMedia(token, mediaId)
      setRefreshNonce((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Reindex failed')
    }
  }

  const handleRetryFailedJobs = async () => {
    if (!token || retryingFailed) return
    setError('')
    setNotice('')
    setRetryingFailed(true)
    try {
      const result = await retryFailedJobs(token)
      const parts = [
        `В очередь возвращено ${result.queued_jobs}`,
        `уникальных failed-медиа найдено ${result.failed_media_total}`,
      ]
      if (result.skipped_active_media) parts.push(`уже активных пропущено ${result.skipped_active_media}`)
      if (result.skipped_missing_media) parts.push(`недоступных пропущено ${result.skipped_missing_media}`)
      setNotice(parts.join(' · '))
      setRefreshNonce((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Retry failed jobs request failed')
    } finally {
      setRetryingFailed(false)
    }
  }

  const handleSaveRuntimeConfig = async (event: FormEvent) => {
    event.preventDefault()
    if (!token || currentUser?.role !== 'admin' || savingRuntimeConfig) return
    setError('')
    setNotice('')
    setSavingRuntimeConfig(true)
    try {
      const updates = Object.fromEntries(runtimeConfig.map((item) => [item.key, runtimeConfigForm[item.key] ?? configValueToInput(item.value)]))
      const payload = await updateRuntimeConfig(token, updates)
      setRuntimeConfig(payload.items)
      setRuntimeConfigForm(Object.fromEntries(payload.items.map((item) => [item.key, configValueToInput(item.value)])))
      setNotice('Runtime config сохранен')
      setRefreshNonce((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Runtime config update failed')
    } finally {
      setSavingRuntimeConfig(false)
    }
  }

  const handleReindexAllMedia = async () => {
    if (!token || currentUser?.role !== 'admin' || reindexingAll) return
    setError('')
    setNotice('')
    setReindexingAll(true)
    try {
      const result = await reindexAllMedia(token)
      setNotice(`Полный reindex: queued ${result.queued_jobs} · skipped active ${result.skipped_active_media} · total ${result.total_media}`)
      setRefreshNonce((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Full reindex request failed')
    } finally {
      setReindexingAll(false)
    }
  }

  const handleResumeAIProxy = async () => {
    if (!token || currentUser?.role !== 'admin' || resumingAIProxy) return
    setError('')
    setNotice('')
    setResumingAIProxy(true)
    try {
      const result = await resumeAIProxy(token)
      setOverview((current) => ({ ...current, ai_proxy_sleep: result.ai_proxy_sleep }))
      setNotice('AI proxy cooldown снят вручную')
      setRefreshNonce((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'AI proxy resume failed')
    } finally {
      setResumingAIProxy(false)
    }
  }

  const handleResetLibrary = async (event: FormEvent) => {
    event.preventDefault()
    if (!token || currentUser?.role !== 'admin' || resettingLibrary) return
    setError('')
    setNotice('')
    setResettingLibrary(true)
    try {
      const result = await resetLibrary(token, dangerConfirmation)
      setNotice(result.message)
      if (result.deleted) {
        localStorage.removeItem(TOKEN_KEY)
        setToken('')
        setCurrentUser(null)
        setNeedsBootstrap(true)
        setOverview(emptyOverview)
        setStorage(null)
        setUsers([])
        setRuntimeConfig([])
        setRuntimeConfigForm({})
        setMedia([])
        setFeedItems([])
        setTagCatalog(emptyTagCatalog)
        setSelectedTag(null)
        setJobs([])
        setBackups([])
        setSelectedMedia(null)
        setViewerOpen(false)
        setActiveTab('library')
      } else {
        setRefreshNonce((value) => value + 1)
      }
      setDangerConfirmation('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Full reset request failed')
    } finally {
      setResettingLibrary(false)
    }
  }

  const handleSaveSafety = async (event: FormEvent) => {
    event.preventDefault()
    if (!token || !selectedMedia || savingSafety) return
    setError('')
    setNotice('')
    setSavingSafety(true)
    try {
      const response = await updateMedia(token, selectedMedia.id, {
        safety_rating: safetyForm.rating,
        safety_tags: parseTagInput(safetyForm.tags),
      })
      setSelectedMedia(response.item)
      setMedia((current) => current.map((item) => (item.id === response.item.id ? response.item : item)))
      setFeedItems((current) => current.map((item) => (item.id === response.item.id ? response.item : item)))
      setNotice('Safety-теги обновлены вручную')
      setRefreshNonce((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Safety update failed')
    } finally {
      setSavingSafety(false)
    }
  }

  const handleBackfillTags = async () => {
    if (!token || backfillingTags) return
    setError('')
    setNotice('')
    setBackfillingTags(true)
    try {
      const result = await triggerTagBackfill(token)
      setNotice(result.message)
      setRefreshNonce((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Tag backfill request failed')
    } finally {
      setBackfillingTags(false)
    }
  }

  const applyFeedPreset = (days: number | null) => {
    if (days === null) {
      setFeedFrom('')
      setFeedTo('')
      return
    }
    const now = new Date()
    const from = new Date(now)
    from.setDate(now.getDate() - days)
    setFeedFrom(toDateInputString(from))
    setFeedTo(toDateInputString(now))
  }

  const handleSelectTag = (item: TagCatalogItem) => {
    startTransition(() => {
      setSelectedTag(item)
      if (typeof window !== 'undefined' && window.matchMedia('(max-width: 960px)').matches) {
        setTagDetailOpen(true)
      }
    })
  }

  const openMedia = (item: MediaItem) => startTransition(() => {
    setSelectedMedia(item)
    setViewerOpen(true)
    void (async () => {
      if (!token) return
      try {
        const response = await getMedia(token, item.id)
        setMedia((current) => current.map((entry) => (entry.id === response.item.id ? { ...entry, ...response.item } : entry)))
        setFeedItems((current) => current.map((entry) => (entry.id === response.item.id ? { ...entry, ...response.item } : entry)))
        setSelectedMedia((current) => (current && current.id === item.id ? response.item : current))
      } catch (reason) {
        setSelectedMedia((current) => current)
        setError((current) => (current || (reason instanceof Error ? reason.message : 'Failed to load media details')))
      }
    })()
  })
  const logout = () => {
    localStorage.removeItem(TOKEN_KEY)
    setToken('')
    setCurrentUser(null)
    setOverview(emptyOverview)
    setStorage(null)
    setUsers([])
    setRuntimeConfig([])
    setRuntimeConfigForm({})
    setMedia([])
    setFeedItems([])
    setTagCatalog(emptyTagCatalog)
    setSelectedTag(null)
    setJobs([])
    setBackups([])
    setSelectedMedia(null)
    setViewerOpen(false)
    setNotice('')
    setError('')
  }

  const tagCountMap = new Map<string, number>()
  media.forEach((item) => {
    ;(item.tags ?? []).forEach((tag) => tagCountMap.set(tag.name, (tagCountMap.get(tag.name) ?? 0) + 1))
  })

  const processingStats = overview.processing_stats ?? emptyProcessingStats
  const aiProxySleep = overview.ai_proxy_sleep ?? emptyOverview.ai_proxy_sleep
  const memoryGuard = overview.memory_guard ?? emptyOverview.memory_guard
  const processor = overview.processor ?? emptyOverview.processor
  const queueCounts = {
    queued: processingStats.queued,
    processing: processingStats.processing,
    complete: processingStats.complete,
    failed: processingStats.failed,
  }
  const kindCounts = overview.counts.media_by_kind
  const completedMedia = overview.counts.ai_ready
  const nsfwMedia = overview.counts.media_by_safety.nsfw
  const backlogCount = processingStats.queued + processingStats.processing
  const failedJobsTotal = processingStats.failed
  const backlogEtaSeconds = processingStats.avg_total_seconds && processingStats.workers ? Math.round((backlogCount * processingStats.avg_total_seconds) / Math.max(processingStats.workers, 1)) : null
  const aiCoverage = overview.counts.media ? Math.round((completedMedia / overview.counts.media) * 100) : 0
  const processingStatusBanner = overview.processing_paused
    ? 'Обработка поставлена на паузу вручную. Новые задачи не стартуют, пока вы не снимете Processing paused в админке.'
    : memoryGuard.active
      ? `Обработка приостановлена memory guard: сейчас доступно ${formatMetric(memoryGuard.memory.available_mb)} MB. Автовозврат после подъема выше ${memoryGuard.resume_available_mb} MB.`
      : aiProxySleep.active
        ? `AI proxy cooldown активен до ${formatDate(aiProxySleep.sleep_until)}. Осталось ${formatDuration(aiProxySleep.remaining_seconds ?? 0)}.`
        : backlogCount > 0 && !processor.active
          ? 'Очередь стоит, потому что processor не подает heartbeat. Проверьте сервис processor в Docker и его логи.'
          : ''
  const driveUsagePercent = storage?.drive_total ? Math.round((storage.drive_used / storage.drive_total) * 100) : 0
  const projectUsageTotal = storage?.project.total ?? 0
  const projectBreakdown = Object.entries(storage?.project ?? {}).filter(([name]) => name !== 'total')
  const projectSegmentOrder = ['media', 'archives', 'thumbnails', 'backups', 'database', 'logs', 'incoming']
  const orderedProjectBreakdown = [
    ...projectSegmentOrder
      .map((key) => [key, storage?.project?.[key] ?? 0] as const)
      .filter(([, value]) => value > 0),
    ...projectBreakdown.filter(([name, value]) => !projectSegmentOrder.includes(name) && value > 0),
  ]
  const driveBarSegments = storage?.drive_total
    ? [
        ...orderedProjectBreakdown.map(([name, value]) => ({
          key: name,
          label: STORAGE_LABELS[name] ?? name,
          bytes: value,
          color: STORAGE_COLORS[name] ?? '#8a82a6',
          percent: (value / storage.drive_total) * 100,
        })),
        ...(storage.other_on_drive > 0
          ? [{
              key: 'other_on_drive',
              label: STORAGE_LABELS.other_on_drive,
              bytes: storage.other_on_drive,
              color: STORAGE_COLORS.other_on_drive,
              percent: (storage.other_on_drive / storage.drive_total) * 100,
            }]
          : []),
        ...(storage.drive_free > 0
          ? [{
              key: 'free',
              label: STORAGE_LABELS.free,
              bytes: storage.drive_free,
              color: STORAGE_COLORS.free,
              percent: (storage.drive_free / storage.drive_total) * 100,
            }]
          : []),
      ]
    : []
  const projectBarSegments = projectUsageTotal
    ? orderedProjectBreakdown.map(([name, value]) => ({
        key: name,
        label: STORAGE_LABELS[name] ?? name,
        bytes: value,
        color: STORAGE_COLORS[name] ?? '#8a82a6',
        percent: (value / projectUsageTotal) * 100,
      }))
    : []
  const topTags = Array.from(tagCountMap.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 12)
  const leaderboardTags = tagCatalog.leaderboard.length ? tagCatalog.leaderboard : []
  const queueFocus = jobs.filter((job) => job.status === 'failed' || job.status === 'processing').slice(0, 8)
  const tabs: TabDefinition[] = [
    { id: 'library', short: 'LB', label: 'Библиотека', title: 'Медиатека', description: 'Поиск, загрузка и просмотр файлов' },
    { id: 'feed', short: 'FD', label: 'Лента', title: 'Лента загрузок', description: 'Просмотр медиа по времени, как в непрерывной ленте' },
    { id: 'tags', short: 'TG', label: 'Теги', title: 'Каталог тегов', description: 'AI-описания тегов, свойства и лидерборд' },
    { id: 'processing', short: 'AI', label: 'Обработка', title: 'AI-очередь', description: 'Скорость, backlog и проблемные задания' },
    { id: 'backups', short: 'BK', label: 'Бэкапы', title: 'Резервные копии', description: 'Создание и отправка частей в Telegram' },
    { id: 'activity', short: 'LG', label: 'Логи', title: 'События системы', description: 'Сигналы, ошибки и журнал действий' },
    ...(currentUser?.role === 'admin' ? [{ id: 'admin' as const, short: 'AD', label: 'Админ', title: 'Управление', description: 'Диск, пользователи и права доступа' }] : []),
  ]
  const currentTab = tabs.find((tab) => tab.id === activeTab) ?? tabs[0]
  const selectedTagDetails = selectedTag?.details_payload ?? null

  if (needsBootstrap === null) return <div className="loading-screen">Loading workspace...</div>
  if (!token || !currentUser) {
    return (
      <main className="auth-shell">
        <section className="auth-card glass-panel">
          <div className="eyebrow">Private AI Media Vault</div>
          <h1>{needsBootstrap ? 'Создайте первого администратора' : 'Вход в библиотеку'}</h1>
          <p className="lede">Большая медиатека для изображений, GIF, видео и архивов с AI-индексацией, изолированными библиотеками и быстрым поиском.</p>
          <form className="auth-form" onSubmit={handleAuthSubmit}>
            <label>Логин<input value={authForm.username} onChange={(event) => setAuthForm({ ...authForm, username: event.target.value })} required /></label>
            <label>Пароль<input type="password" value={authForm.password} onChange={(event) => setAuthForm({ ...authForm, password: event.target.value })} required /></label>
            {needsBootstrap ? <label>Telegram username<input value={authForm.telegram} onChange={(event) => setAuthForm({ ...authForm, telegram: event.target.value })} placeholder="@username" /></label> : null}
            <button className="primary-button" type="submit">{needsBootstrap ? 'Инициализировать систему' : 'Войти'}</button>
          </form>
          {notice ? <div className="global-notice">{notice}</div> : null}
          {error ? <div className="inline-error">{error}</div> : null}
        </section>
      </main>
    )
  }

  return (
    <main className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''} ${mobileSidebarOpen ? 'sidebar-open' : ''}`}>
      <button className={`sidebar-backdrop ${mobileSidebarOpen ? 'visible' : ''}`} type="button" onClick={() => setMobileSidebarOpen(false)} aria-label="Close navigation" />
      <aside className="app-sidebar glass-panel">
        <div className="sidebar-top">
          <div className="brand-lockup">
            <div className="brand-mark">RE2</div>
            {!sidebarCollapsed ? (
              <div className="brand-copy">
                <strong>Reddit Ecosystem 2</strong>
                <small>{currentUser.username}</small>
                <small>{roleLabel(currentUser)}</small>
              </div>
            ) : null}
          </div>
          <div className="sidebar-controls">
            <button className="icon-button desktop-only" type="button" onClick={() => setSidebarCollapsed((value) => !value)}>{sidebarCollapsed ? '>' : '<'}</button>
            <button className="icon-button mobile-only" type="button" onClick={() => setMobileSidebarOpen(false)}>x</button>
          </div>
        </div>
        <div className="sidebar-summary">
          <StatCard label="Всего медиа" value={overview.counts.media} hint={`${kindCounts.image} img · ${kindCounts.gif} gif · ${kindCounts.video} video`} tone="accent" />
          {!sidebarCollapsed ? <StatCard label="AI готово" value={`${aiCoverage}%`} hint={`queue ${backlogCount} · nsfw ${nsfwMedia}`} /> : null}
        </div>
        <nav className="sidebar-nav">
          {tabs.map((tab) => <SidebarTab key={tab.id} active={tab.id === activeTab} short={tab.short} label={tab.label} description={tab.description} collapsed={sidebarCollapsed} onClick={() => { setActiveTab(tab.id); setMobileSidebarOpen(false) }} />)}
        </nav>
        <div className="sidebar-foot">
          <button className="secondary-button" type="button" onClick={() => setRefreshNonce((value) => value + 1)}>{sidebarCollapsed ? 'R' : 'Обновить'}</button>
          <button className="ghost-button" type="button" onClick={logout}>{sidebarCollapsed ? 'X' : 'Выйти'}</button>
        </div>
      </aside>
      <section className="app-main">
        <header className="workspace-header glass-panel">
          <div className="workspace-title">
            <button className="icon-button mobile-only" type="button" onClick={() => setMobileSidebarOpen(true)}>menu</button>
            <div className="workspace-title-copy">
              <span>{currentTab.label}</span>
              <h1>{currentTab.title}</h1>
              <p className="workspace-subtitle">{currentTab.description}</p>
            </div>
          </div>
          <div className="workspace-pills"><span className="status-pill">{overview.counts.media} media</span><span className="status-pill">AI {aiCoverage}%</span><span className="status-pill">queue {backlogCount}</span></div>
        </header>
        {error ? <div className="global-error glass-panel">{error}</div> : null}
        {notice ? <div className="global-notice glass-panel">{notice}</div> : null}
        {processingStatusBanner ? <div className="global-warning glass-panel">{processingStatusBanner}</div> : null}
        {activeTab === 'library' ? (
          <div className="tab-stack">
            <section className="hero-grid">
              <article className="glass-panel hero-panel">
                <div className="panel-head">
                  <div><span>Обзор</span><h2>Большая библиотека под быстрый поиск</h2></div>
                </div>
                <div className="stat-grid">
                  <StatCard label="Всего" value={overview.counts.media} hint="в текущей библиотеке" tone="accent" />
                  <StatCard label="AI готово" value={`${aiCoverage}%`} hint={`${completedMedia} файлов готовы`} tone="success" />
                  <StatCard label="Очередь" value={backlogCount} hint={`queued ${queueCounts.queued} · processing ${queueCounts.processing}`} />
                  <StatCard label="NSFW" value={nsfwMedia} hint="помечено модерацией" tone="danger" />
                </div>
                <div className="chip-row spacious">
                  {topTags.map(([tag, count]) => <span key={tag} className="tag-chip">{prettifyTag(tag)} · {count}</span>)}
                </div>
              </article>
              <section
                className={`glass-panel upload-dropzone ${dragActive ? 'is-dragging' : ''}`}
                onDragOver={(event) => {
                  event.preventDefault()
                  setDragActive(true)
                }}
                onDragLeave={() => setDragActive(false)}
                onDrop={handleDrop}
              >
                <div className="panel-head">
                  <div><span>Загрузка</span><h2>Загрузка медиа и архивов</h2></div>
                  <label className="primary-button file-button">Выбрать<input type="file" multiple onChange={handlePick} /></label>
                </div>
                <p className="lede">Поддерживаются изображения, GIF, видео и архивы с вложенными папками. После загрузки файлы сразу становятся в AI-очередь.</p>
                <div className="dropzone-core">
                  <strong>{dragActive ? 'Отпускайте файлы сюда' : 'Перетащите сюда архивы, видео, GIF или изображения'}</strong>
                  <small>Ограничения по размеру не задаются интерфейсом.</small>
                </div>
                <div className="progress-block">
                  <div className="progress-track"><div className="progress-bar" style={{ width: `${uploadProgress}%` }} /></div>
                  <div className="row-meta"><span>{uploading ? 'Идет загрузка' : 'Ожидание'}</span><strong>{uploading ? `${uploadProgress}%` : 'Ready'}</strong></div>
                </div>
              </section>
            </section>
            <section className="glass-panel filter-panel">
              <div className="panel-head"><div><span>Фильтры</span><h2>Поиск по памяти, тегам и AI-описанию</h2></div></div>
              <div className="filter-grid">
                <label className="filter-span-2">Запрос<input value={searchInput} onChange={(event) => startTransition(() => setSearchInput(event.target.value))} placeholder="protogen, meme, red room, vertical video..." /></label>
                <label>Тип<select value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}><option value="">Все</option><option value="image">Изображения</option><option value="gif">GIF</option><option value="video">Видео</option></select></label>
                <label>Safety<select value={ratingFilter} onChange={(event) => setRatingFilter(event.target.value)}><option value="">Все</option><option value="sfw">SFW</option><option value="questionable">Questionable</option><option value="nsfw">NSFW</option></select></label>
                <label>AI статус<select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">Все</option><option value="pending">pending</option><option value="processing">processing</option><option value="complete">complete</option><option value="failed">failed</option></select></label>
              </div>
            </section>
            <section className="glass-panel gallery-panel">
              <div className="panel-head">
                <div><span>Медиатека</span><h2>{media.length} результатов</h2></div>
                <div className="chip-row"><span className="tag-chip">queued {queueCounts.queued}</span><span className="tag-chip">processing {queueCounts.processing}</span><span className="tag-chip">complete {queueCounts.complete}</span><span className="tag-chip">failed {queueCounts.failed}</span></div>
              </div>
              <div className="gallery-grid">
                {media.length ? media.map((item) => <MediaCard key={item.id} item={item} token={token} active={selectedMedia?.id === item.id} onOpen={() => openMedia(item)} />) : <article className="glass-panel empty-state"><h2>Под текущие фильтры ничего не нашлось.</h2><p className="muted">Снимите часть фильтров или дождитесь, пока очередь доиндексирует свежие файлы.</p></article>}
              </div>
            </section>
          </div>
        ) : null}
        {activeTab === 'feed' ? (
          <div className="tab-stack">
            <section className="glass-panel filter-panel">
              <div className="panel-head">
                <div><span>Период</span><h2>Лента по времени загрузки</h2></div>
                <div className="button-row">
                  <button className="secondary-button" type="button" onClick={() => applyFeedPreset(7)}>7 дней</button>
                  <button className="secondary-button" type="button" onClick={() => applyFeedPreset(30)}>30 дней</button>
                  <button className="secondary-button" type="button" onClick={() => applyFeedPreset(365)}>1 год</button>
                  <button className="ghost-button" type="button" onClick={() => applyFeedPreset(null)}>Весь архив</button>
                </div>
              </div>
              <div className="filter-grid">
                <label>От<input type="date" value={feedFrom} onChange={(event) => setFeedFrom(event.target.value)} /></label>
                <label>До<input type="date" value={feedTo} onChange={(event) => setFeedTo(event.target.value)} /></label>
                <div className="note-block filter-span-2">
                  <span>Как это работает</span>
                  <p>Лента фильтрует по моменту загрузки в систему, чтобы можно было быстро открыть старые мемы, свежие подборки или конкретный временной пласт архива.</p>
                </div>
              </div>
            </section>
            <section className="glass-panel list-panel">
              <div className="panel-head">
                <div><span>Лента</span><h2>{feedItems.length} элементов в выбранном диапазоне</h2></div>
              </div>
              <div className="feed-stack">
                {feedItems.length ? (
                  feedItems.map((item) => <FeedCard key={item.id} item={item} token={token} onOpen={() => openMedia(item)} />)
                ) : (
                  <article className="empty-state">
                    <h2>В выбранном отрезке пока ничего нет.</h2>
                    <p className="muted">Расширьте диапазон дат или загрузите старые архивы еще раз, если хотите восстановить раннюю историю.</p>
                  </article>
                )}
              </div>
            </section>
          </div>
        ) : null}
        {activeTab === 'tags' ? (
          <div className="tab-stack">
            <section className="glass-panel hero-panel">
              <div className="panel-head">
                <div><span>Каталог</span><h2>AI-описания, свойства и лидерборд тегов</h2></div>
                <div className="button-row">
                  <button className="secondary-button" type="button" onClick={() => void handleBackfillTags()} disabled={backfillingTags}>
                    {backfillingTags ? 'Запускаем...' : 'Прогнать недоописанные теги'}
                  </button>
                </div>
              </div>
              <div className="stat-grid">
                <StatCard label="Всего тегов" value={tagCatalog.counts.total} />
                <StatCard label="Описаны" value={tagCatalog.counts.described} tone="success" />
                <StatCard label="Ожидают" value={tagCatalog.counts.pending} tone="danger" />
                <StatCard label="Лидерборд" value={leaderboardTags.length} hint="топ по использованию" tone="accent" />
              </div>
                <div className="chip-row spacious">
                  {leaderboardTags.map((tag) => (
                    <button
                      key={tag.id}
                      className={`tag-chip tag-${tag.kind}`}
                      type="button"
                      onClick={() => {
                        handleSelectTag(tag)
                        setTagSearch(tag.name)
                      }}
                    >
                      {prettifyTag(tag.name)} · {tag.usage_count}
                    </button>
                  ))}
              </div>
            </section>
            <section className="glass-panel filter-panel">
              <div className="panel-head"><div><span>Фильтры</span><h2>Поиск по тегам и статусу описания</h2></div></div>
              <div className="filter-grid">
                <label className="filter-span-2">Поиск<input value={tagSearch} onChange={(event) => startTransition(() => setTagSearch(event.target.value))} placeholder="boykisser, hollow_knight, meme..." /></label>
                <label>Kind<select value={tagKindFilter} onChange={(event) => setTagKindFilter(event.target.value)}><option value="">Все</option><option value="semantic">semantic</option><option value="technical">technical</option><option value="safety">safety</option></select></label>
                <label>Описание<select value={tagDescribedFilter} onChange={(event) => setTagDescribedFilter(event.target.value)}><option value="">Все</option><option value="true">Только описанные</option><option value="false">Только pending</option></select></label>
              </div>
            </section>
            <section className="tag-catalog-grid">
              <section className="glass-panel list-panel">
                <div className="panel-head"><div><span>Список</span><h2>{tagCatalog.items.length} тегов в выдаче</h2></div></div>
                <div className="list-stack">
                  {tagCatalog.items.length ? tagCatalog.items.map((item) => <TagRow key={item.id} item={item} active={selectedTag?.id === item.id} onClick={() => handleSelectTag(item)} />) : <article className="empty-state"><h2>Теги по этому фильтру не найдены.</h2><p className="muted">Измените поиск или дождитесь, пока processor доопишет pending-теги.</p></article>}
                </div>
              </section>
              <section className="glass-panel tags-panel tag-detail-panel">
                <TagDetailsContent selectedTag={selectedTag} selectedTagDetails={selectedTagDetails} />
              </section>
            </section>
          </div>
        ) : null}
        {activeTab === 'processing' ? (
          <div className="tab-stack">
            <section className="glass-panel metrics-panel">
              <div className="panel-head"><div><span>Метрики</span><h2>Скорость и качество обработки</h2></div></div>
              <div className="stat-grid wide">
                <StatCard label="Workers" value={processingStats.workers} />
                <StatCard label="Avg total" value={`${formatMetric(processingStats.avg_total_seconds)}с`} />
                <StatCard label="P95 total" value={`${formatMetric(processingStats.p95_total_seconds)}с`} />
                <StatCard label="Avg AI" value={`${formatMetric(processingStats.avg_ai_seconds)}с`} />
                <StatCard label="Throughput" value={`${formatMetric(processingStats.throughput_per_hour_24h)}/ч`} tone="success" />
                <StatCard label="ETA backlog" value={formatDuration(backlogEtaSeconds)} />
                <StatCard label="Frames" value={formatMetric(processingStats.avg_frames)} />
                <StatCard label="Reasoning" value={formatMetric(processingStats.avg_reasoning_tokens)} />
              </div>
            </section>
            <section className="glass-panel jobs-panel">
              <div className="panel-head">
                <div><span>Очередь</span><h2>Активные и проблемные jobs</h2></div>
                <div className="button-row">
                  <button className="secondary-button" type="button" onClick={() => void handleRetryFailedJobs()} disabled={retryingFailed || failedJobsTotal === 0}>
                    {retryingFailed ? 'Повторяем...' : `Повторить failed (${failedJobsTotal})`}
                  </button>
                </div>
              </div>
              <div className="queue-stat-grid">
                <StatCard label="Queued" value={queueCounts.queued} />
                <StatCard label="Processing" value={queueCounts.processing} />
                <StatCard label="Complete" value={queueCounts.complete} tone="success" />
                <StatCard label="Failed" value={failedJobsTotal} tone="danger" />
              </div>
              <div className="list-stack">
                {(queueFocus.length ? queueFocus : jobs.slice(0, 8)).map((job) => (
                  <article key={job.id} className="list-row">
                    <div><strong>{job.media_id.slice(0, 8)}</strong><small>{formatDate(job.created_at)}</small>{job.error_message ? <small className="error-text">{trimText(job.error_message, '', 120)}</small> : null}</div>
                    <span className={`badge badge-status-${job.status}`}>{job.status}</span>
                  </article>
                ))}
              </div>
            </section>
          </div>
        ) : null}
        {activeTab === 'backups' ? (
          <div className="tab-stack split-stack">
            <section className="glass-panel action-panel">
              <div className="panel-head"><div><span>Бэкапы</span><h2>Telegram backup pipeline</h2></div></div>
              <p className="lede">Metadata-бэкап быстрый и легкий, full backup архивирует больше данных и режется на части для Telegram.</p>
              <div className="button-row"><button className="secondary-button" type="button" onClick={() => void handleCreateBackup('metadata')}>Metadata</button><button className="primary-button" type="button" onClick={() => void handleCreateBackup('full')}>Full backup</button></div>
            </section>
            <section className="glass-panel list-panel">
              <div className="panel-head"><div><span>История</span><h2>Последние backup-задачи</h2></div></div>
              <div className="list-stack">
                {backups.slice(0, 10).map((backup) => <article key={backup.id} className="list-row"><div><strong>{backup.scope}</strong><small>{backup.parts.length} частей · {formatDate(backup.created_at)}</small>{backup.error_message ? <small className="error-text">{backup.error_message}</small> : null}</div><span className={`badge badge-status-${backup.status}`}>{backup.status}</span></article>)}
              </div>
            </section>
          </div>
        ) : null}
        {activeTab === 'activity' ? (
          <div className="tab-stack split-stack">
            <section className="glass-panel list-panel">
              <div className="panel-head"><div><span>Логи</span><h2>Последние события системы</h2></div></div>
              <div className="list-stack">
                {overview.recent_logs.slice(0, 12).map((log) => <article key={log.id} className="list-row"><div><strong>{log.event_type}</strong><small>{trimText(log.message, '', 140)}</small><small>{formatDate(log.created_at)}</small></div><span className={`badge badge-severity-${log.severity}`}>{log.severity}</span></article>)}
              </div>
            </section>
            <section className="glass-panel tags-panel">
              <div className="panel-head"><div><span>Теги</span><h2>Частые теги в текущей выборке</h2></div></div>
              <div className="chip-row spacious">
                {topTags.map(([tag, count]) => <button key={tag} className="tag-chip" type="button" onClick={() => { startTransition(() => setSearchInput(prettifyTag(tag))); setActiveTab('library') }}>{prettifyTag(tag)} · {count}</button>)}
              </div>
            </section>
          </div>
        ) : null}
        {activeTab === 'admin' && currentUser.role === 'admin' ? (
          <div className="tab-stack">
            <section className="glass-panel metrics-panel">
              <div className="panel-head"><div><span>Диск</span><h2>Диск и распределение по проекту</h2></div></div>
              {storage ? (
                <>
                  <div className="stat-grid wide">
                    <StatCard label="Использовано" value={`${driveUsagePercent}%`} tone="accent" />
                    <StatCard label="Всего" value={formatBytes(storage.drive_total)} />
                    <StatCard label="Свободно" value={formatBytes(storage.drive_free)} tone="success" />
                    <StatCard label="Проект" value={formatBytes(projectUsageTotal)} />
                  </div>
                  <div className="storage-bars">
                    <div className="storage-bar-card">
                      <div className="row-meta">
                        <strong>Диск целиком</strong>
                        <small>{formatBytes(storage.drive_used)} занято из {formatBytes(storage.drive_total)}</small>
                      </div>
                      <div className="storage-bar" aria-label="Disk usage breakdown">
                        {driveBarSegments.map((segment) => (
                          <span
                            key={segment.key}
                            className="storage-segment"
                            title={`${segment.label}: ${formatBytes(segment.bytes)} (${formatMetric(segment.percent)}%)`}
                            style={{ width: `${segment.percent}%`, background: segment.color }}
                          />
                        ))}
                      </div>
                    </div>
                    <div className="storage-bar-card">
                      <div className="row-meta">
                        <strong>Состав проекта</strong>
                        <small>{formatBytes(projectUsageTotal)} внутри RE2</small>
                      </div>
                      <div className="storage-bar" aria-label="Project storage breakdown">
                        {projectBarSegments.map((segment) => (
                          <span
                            key={segment.key}
                            className="storage-segment"
                            title={`${segment.label}: ${formatBytes(segment.bytes)} (${formatMetric(segment.percent)}%)`}
                            style={{ width: `${segment.percent}%`, background: segment.color }}
                          />
                        ))}
                      </div>
                    </div>
                    <div className="storage-legend">
                      {driveBarSegments.map((segment) => (
                        <article key={segment.key} className="storage-legend-item">
                          <span className="storage-swatch" style={{ background: segment.color }} />
                          <div>
                            <strong>{segment.label}</strong>
                            <small>{formatBytes(segment.bytes)} · {formatMetric(segment.percent)}%</small>
                          </div>
                        </article>
                      ))}
                    </div>
                  </div>
                  <div className="list-stack">
                    {orderedProjectBreakdown.map(([name, value]) => <article key={name} className="list-row"><div><strong>{STORAGE_LABELS[name] ?? name}</strong><small>{formatBytes(value)}</small></div><span className="badge">{Math.max(Math.round((value / Math.max(projectUsageTotal || 1, 1)) * 100), 1)}%</span></article>)}
                  </div>
                </>
              ) : <p className="muted">Storage analytics unavailable.</p>}
            </section>
            <section className="glass-panel admin-panel">
              <div className="panel-head"><div><span>Операции</span><h2>Переиндексация и runtime config</h2></div></div>
              <div className="note-block">
                <span>AI proxy cooldown</span>
                <p>
                  {aiProxySleep.active
                    ? `Обработка спит до ${formatDate(aiProxySleep.sleep_until)} после HTTP ${aiProxySleep.status_code ?? 'unknown'}. Осталось ${formatDuration(aiProxySleep.remaining_seconds ?? 0)}.`
                    : `Сейчас лимитного cooldown нет. Отслеживаемые коды: ${aiProxySleep.monitored_status_codes.join(', ') || 'n/a'}. Длительность сна: ${aiProxySleep.sleep_hours}ч.`}
                </p>
                {aiProxySleep.last_error ? <small className="muted">{trimText(aiProxySleep.last_error, '', 220)}</small> : null}
                <div className="button-row">
                  <button className="secondary-button" type="button" onClick={() => void handleResumeAIProxy()} disabled={resumingAIProxy || !aiProxySleep.active}>
                    {resumingAIProxy ? 'Возобновляем...' : 'Возобновить сейчас'}
                  </button>
                </div>
              </div>
              <div className="note-block">
                <span>Memory guard</span>
                <p>
                  {memoryGuard.active
                    ? `Processor поставлен на паузу из-за памяти. Доступно ${formatMetric(memoryGuard.memory.available_mb)} MB из ${formatMetric(memoryGuard.memory.total_mb)} MB. Автовозврат после подъема выше ${memoryGuard.resume_available_mb} MB.`
                    : `Сейчас memory guard не активен. Пауза включится ниже ${memoryGuard.pause_available_mb} MB доступной памяти, автопродолжение выше ${memoryGuard.resume_available_mb} MB.`}
                </p>
                {memoryGuard.reason ? <small className="muted">{trimText(memoryGuard.reason, '', 220)}</small> : null}
                {memoryGuard.snapshot ? <small className="muted">{trimText(memoryGuard.snapshot, '', 220)}</small> : null}
              </div>
              <div className="button-row">
                <button className="secondary-button" type="button" onClick={() => void handleReindexAllMedia()} disabled={reindexingAll}>
                  {reindexingAll ? 'Ставим в очередь...' : 'Переиндексировать всю библиотеку'}
                </button>
              </div>
              <form className="runtime-config-form" onSubmit={handleSaveRuntimeConfig}>
                {runtimeConfig.map((item) => (
                  <label key={item.key}>
                    {item.label}
                    {item.kind === 'boolean' ? (
                      <select value={runtimeConfigForm[item.key] ?? configValueToInput(item.value)} onChange={(event) => setRuntimeConfigForm((current) => ({ ...current, [item.key]: event.target.value }))}>
                        <option value="true">true</option>
                        <option value="false">false</option>
                      </select>
                    ) : item.kind === 'enum' ? (
                      <select value={runtimeConfigForm[item.key] ?? configValueToInput(item.value)} onChange={(event) => setRuntimeConfigForm((current) => ({ ...current, [item.key]: event.target.value }))}>
                        {item.choices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}
                      </select>
                    ) : (
                      <input
                        type={item.kind === 'integer' ? 'number' : 'text'}
                        min={item.min ?? undefined}
                        max={item.max ?? undefined}
                        value={runtimeConfigForm[item.key] ?? configValueToInput(item.value)}
                        onChange={(event) => setRuntimeConfigForm((current) => ({ ...current, [item.key]: event.target.value }))}
                      />
                    )}
                    <small>{item.description}</small>
                  </label>
                ))}
                <button className="primary-button" type="submit" disabled={savingRuntimeConfig}>
                  {savingRuntimeConfig ? 'Сохраняем...' : 'Сохранить конфиг'}
                </button>
              </form>
            </section>
            <section className="glass-panel admin-panel">
              <div className="panel-head"><div><span>Доступ</span><h2>Пользователи и роли</h2></div></div>
              <form className="admin-form" onSubmit={handleCreateUser}>
                <label>username<input value={newUserForm.username} onChange={(event) => setNewUserForm({ ...newUserForm, username: event.target.value })} required /></label>
                <label>password<input type="password" value={newUserForm.password} onChange={(event) => setNewUserForm({ ...newUserForm, password: event.target.value })} required /></label>
                <label>telegram<input value={newUserForm.telegram} onChange={(event) => setNewUserForm({ ...newUserForm, telegram: event.target.value })} placeholder="@username" /></label>
                <label>role<select value={newUserForm.role} onChange={(event) => setNewUserForm({ ...newUserForm, role: event.target.value as 'admin' | 'member' })}><option value="member">member</option><option value="admin">admin</option></select></label>
                <button className="primary-button" type="submit">Добавить пользователя</button>
              </form>
              <div className="list-stack">{users.map((user) => <article key={user.id} className="list-row"><div><strong>{user.username}</strong><small>{user.telegram_username ? `@${user.telegram_username}` : 'Telegram не подключен'}</small></div><span className={`badge badge-role-${user.role}`}>{user.role}</span></article>)}</div>
            </section>
            <section className="glass-panel admin-panel danger-panel">
              <div className="panel-head"><div><span>Danger Zone</span><h2>Полное удаление базы и всех медиа</h2></div></div>
              <div className="danger-copy">
                <p className="lede">Это удалит всю базу данных, все медиафайлы, архивы, превью, бэкапы, логи и пользователей. Действие необратимо.</p>
                <small>Если сейчас есть активные jobs, система сначала поставит processing на паузу и попросит повторить удаление после завершения текущих задач.</small>
              </div>
              <form className="danger-form" onSubmit={handleResetLibrary}>
                <label>
                  Введите <strong>DELETE EVERYTHING</strong> для подтверждения
                  <input
                    value={dangerConfirmation}
                    onChange={(event) => setDangerConfirmation(event.target.value)}
                    placeholder="DELETE EVERYTHING"
                    autoComplete="off"
                  />
                </label>
                <div className="button-row">
                  <button className="danger-button" type="submit" disabled={resettingLibrary || dangerConfirmation !== 'DELETE EVERYTHING'}>
                    {resettingLibrary ? 'Удаляем...' : 'Удалить всю базу и медиа'}
                  </button>
                </div>
              </form>
            </section>
          </div>
        ) : null}
      </section>
      {viewerOpen && selectedMedia ? (
        <div className="modal-backdrop" onClick={() => setViewerOpen(false)}>
          <div className="modal glass-panel" onClick={(event) => event.stopPropagation()}>
            <div className="panel-head">
              <div><span>{kindLabel(selectedMedia.kind)}</span><h2>{selectedMedia.original_filename}</h2></div>
              <div className="button-row"><button className="secondary-button" type="button" onClick={() => void handleReindex(selectedMedia.id)}>Reindex</button><button className="ghost-button" type="button" onClick={() => setViewerOpen(false)}>Close</button></div>
            </div>
            <div className="modal-grid">
              <div className="modal-preview">{selectedMedia.kind === 'video' ? <video controls src={mediaAssetUrl(selectedMedia.file_url, token)} /> : <img src={mediaAssetUrl(selectedMedia.file_url, token)} alt={selectedMedia.original_filename} />}</div>
              <div className="modal-copy">
                <div className="chip-row"><span className={`badge badge-${selectedMedia.safety_rating}`}>{ratingLabel(selectedMedia.safety_rating)}</span><span className={`badge badge-status-${selectedMedia.processing_status}`}>{selectedMedia.processing_status}</span></div>
                <form className="safety-form note-block" onSubmit={handleSaveSafety}>
                  <span>Safety moderation</span>
                  <label>
                    Rating
                    <select value={safetyForm.rating} onChange={(event) => setSafetyForm((current) => ({ ...current, rating: event.target.value as SafetyRating }))}>
                      <option value="unknown">Unknown</option>
                      <option value="sfw">SFW</option>
                      <option value="questionable">Questionable</option>
                      <option value="nsfw">NSFW</option>
                    </select>
                  </label>
                  <label>
                    Safety tags
                    <textarea
                      value={safetyForm.tags}
                      onChange={(event) => setSafetyForm((current) => ({ ...current, tags: event.target.value }))}
                      placeholder="sfw, suggestive, nudity, censored..."
                      rows={4}
                    />
                  </label>
                  <button className="secondary-button" type="submit" disabled={savingSafety}>
                    {savingSafety ? 'Сохраняем...' : 'Сохранить safety-теги'}
                  </button>
                </form>
                <div className="note-block">
                  <span>Описание RU</span>
                  <p>{trimText(selectedMedia.description_ru ?? selectedMedia.description, 'AI-описание пока отсутствует.', 1200)}</p>
                </div>
                <div className="note-block">
                  <span>Description EN</span>
                  <p>{trimText(selectedMedia.description_en, 'English description is not available yet.', 1200)}</p>
                </div>
                <div className="detail-grid">
                  <div><span>Размер</span><strong>{formatBytes(selectedMedia.file_size)}</strong></div>
                  <div><span>Разрешение</span><strong>{selectedMedia.width && selectedMedia.height ? `${selectedMedia.width}×${selectedMedia.height}` : 'n/a'}</strong></div>
                  <div><span>Длительность</span><strong>{formatDuration(selectedMedia.duration_seconds)}</strong></div>
                  <div><span>Timestamp</span><strong>{formatDate(selectedMedia.normalized_timestamp)}</strong></div>
                </div>
                {selectedMedia.technical_notes ? <div className="note-block">{selectedMedia.technical_notes}</div> : null}
                <div className="chip-row spacious">{(selectedMedia.tags ?? []).map((tag) => <span key={`${selectedMedia.id}-${tag.kind}-${tag.name}`} className={`tag-chip tag-${tag.kind}`}>{tag.name.replaceAll('_', ' ')}</span>)}</div>
              </div>
            </div>
          </div>
        </div>
      ) : null}
      {tagDetailOpen && selectedTag ? (
        <div className="tag-sheet-backdrop" onClick={() => setTagDetailOpen(false)}>
          <div className="tag-sheet glass-panel" onClick={(event) => event.stopPropagation()}>
            <TagDetailsContent selectedTag={selectedTag} selectedTagDetails={selectedTagDetails} onClose={() => setTagDetailOpen(false)} />
          </div>
        </div>
      ) : null}
    </main>
  )
}

export default App
