import { type ChangeEvent, type DragEvent, type FormEvent, startTransition, useDeferredValue, useEffect, useState } from 'react'

import {
  bootstrap,
  createBackup,
  createUser,
  getBootstrapStatus,
  getOverview,
  getRuntimeConfig,
  getStorage,
  getUsers,
  listBackups,
  listJobs,
  listMedia,
  login,
  mediaAssetUrl,
  me,
  reindexMedia,
  reindexAllMedia,
  retryFailedJobs,
  updateRuntimeConfig,
  uploadFiles,
} from './api'
import type { BackupItem, DiskUsagePayload, JobItem, MediaItem, OverviewPayload, ProcessingStats, RuntimeConfigItem, SafetyRating, User } from './types'

type WorkspaceTab = 'library' | 'processing' | 'backups' | 'activity' | 'admin'

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
  recent_logs: [],
  prompt_preview: '',
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

function primaryDescription(item: MediaItem) {
  return item.description_ru ?? item.description ?? ''
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

function App() {
  const [token, setToken] = useState<string>(() => localStorage.getItem(TOKEN_KEY) ?? '')
  const [currentUser, setCurrentUser] = useState<User | null>(null)
  const [needsBootstrap, setNeedsBootstrap] = useState<boolean | null>(null)
  const [overview, setOverview] = useState<OverviewPayload>(emptyOverview)
  const [media, setMedia] = useState<MediaItem[]>([])
  const [jobs, setJobs] = useState<JobItem[]>([])
  const [backups, setBackups] = useState<BackupItem[]>([])
  const [storage, setStorage] = useState<DiskUsagePayload | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfigItem[]>([])
  const [runtimeConfigForm, setRuntimeConfigForm] = useState<Record<string, string>>({})
  const [selectedMedia, setSelectedMedia] = useState<MediaItem | null>(null)
  const [viewerOpen, setViewerOpen] = useState(false)
  const [searchInput, setSearchInput] = useState('')
  const [kindFilter, setKindFilter] = useState('')
  const [ratingFilter, setRatingFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
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
  const deferredSearch = useDeferredValue(searchInput)

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
        const [overviewPayload, mediaPayload, jobsPayload, backupsPayload] = await Promise.all([
          getOverview(token),
          listMedia(token, { q: deferredSearch || undefined, kind: kindFilter || undefined, rating: ratingFilter || undefined, status: statusFilter || undefined }),
          listJobs(token),
          listBackups(token),
        ])
        if (cancelled) return
        setOverview(overviewPayload)
        setMedia(mediaPayload.items)
        setJobs(jobsPayload.items)
        setBackups(backupsPayload.items)
        if (currentUser.role === 'admin') {
          const [storagePayload, usersPayload, runtimeConfigPayload] = await Promise.all([getStorage(token), getUsers(token), getRuntimeConfig(token)])
          if (cancelled) return
          setStorage(storagePayload)
          setUsers(usersPayload.items)
          setRuntimeConfig(runtimeConfigPayload.items)
          setRuntimeConfigForm((current) => (
            Object.keys(current).length
              ? current
              : Object.fromEntries(runtimeConfigPayload.items.map((item) => [item.key, configValueToInput(item.value)]))
          ))
        } else {
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
  }, [token, currentUser, deferredSearch, kindFilter, ratingFilter, statusFilter, refreshNonce])
  useEffect(() => {
    if (!selectedMedia) return
    const refreshed = media.find((item) => item.id === selectedMedia.id)
    if (!refreshed) {
      setSelectedMedia(null)
      setViewerOpen(false)
      return
    }
    if (refreshed !== selectedMedia) setSelectedMedia(refreshed)
  }, [media, selectedMedia])
  useEffect(() => {
    if (currentUser?.role !== 'admin' && activeTab === 'admin') setActiveTab('library')
  }, [activeTab, currentUser])

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
    setUploading(true)
    setUploadProgress(0)
    try {
      await uploadFiles(token, files, setUploadProgress)
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

  const openMedia = (item: MediaItem) => startTransition(() => {
    setSelectedMedia(item)
    setViewerOpen(true)
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
  const queueFocus = jobs.filter((job) => job.status === 'failed' || job.status === 'processing').slice(0, 8)
  const tabs: TabDefinition[] = [
    { id: 'library', short: 'LB', label: 'Библиотека', title: 'Медиатека', description: 'Поиск, загрузка и просмотр файлов' },
    { id: 'processing', short: 'AI', label: 'Обработка', title: 'AI-очередь', description: 'Скорость, backlog и проблемные задания' },
    { id: 'backups', short: 'BK', label: 'Бэкапы', title: 'Резервные копии', description: 'Создание и отправка частей в Telegram' },
    { id: 'activity', short: 'LG', label: 'Логи', title: 'События системы', description: 'Сигналы, ошибки и быстрый переход по тегам' },
    ...(currentUser?.role === 'admin' ? [{ id: 'admin' as const, short: 'AD', label: 'Админ', title: 'Управление', description: 'Диск, пользователи и права доступа' }] : []),
  ]
  const currentTab = tabs.find((tab) => tab.id === activeTab) ?? tabs[0]

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
                  {topTags.map(([tag, count]) => <span key={tag} className="tag-chip">{tag.replaceAll('_', ' ')} · {count}</span>)}
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
                {topTags.map(([tag, count]) => <button key={tag} className="tag-chip" type="button" onClick={() => { startTransition(() => setSearchInput(tag.replaceAll('_', ' '))); setActiveTab('library') }}>{tag.replaceAll('_', ' ')} · {count}</button>)}
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
    </main>
  )
}

export default App
