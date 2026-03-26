import { type ChangeEvent, type DragEvent, type FormEvent, startTransition, useDeferredValue, useEffect, useState } from 'react'

import {
  bootstrap,
  createBackup,
  createUser,
  getBootstrapStatus,
  getOverview,
  getStorage,
  getUsers,
  listBackups,
  listJobs,
  listMedia,
  login,
  mediaAssetUrl,
  me,
  reindexMedia,
  uploadFiles,
} from './api'
import type { BackupItem, DiskUsagePayload, JobItem, MediaItem, OverviewPayload, SafetyRating, User } from './types'

const TOKEN_KEY = 're2_token'

const emptyOverview: OverviewPayload = {
  counts: { media: 0, users: 0, jobs: 0 },
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
  if (!value) return 'n/a'
  const rounded = Math.max(1, Math.round(value))
  const hours = Math.floor(rounded / 3600)
  const minutes = Math.floor((rounded % 3600) / 60)
  const seconds = rounded % 60
  if (hours) return `${hours}ч ${minutes}м`
  if (minutes) return `${minutes}м ${seconds.toString().padStart(2, '0')}с`
  return `${seconds}с`
}

function trimText(value: string | null | undefined, fallback: string, max = 176) {
  const source = (value ?? '').trim()
  if (!source) return fallback
  if (source.length <= max) return source
  return `${source.slice(0, max).trimEnd()}...`
}

function roleLabel(user: User | null) {
  if (!user) return ''
  return user.role === 'admin' ? 'Администратор' : 'Участник'
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
  const [authForm, setAuthForm] = useState({ username: '', password: '', telegram: '' })
  const [newUserForm, setNewUserForm] = useState({ username: '', password: '', telegram: '', role: 'member' as 'admin' | 'member' })
  const deferredSearch = useDeferredValue(searchInput)

  useEffect(() => {
    void getBootstrapStatus()
      .then((data) => setNeedsBootstrap(data.needs_bootstrap))
      .catch((reason) => setError(reason instanceof Error ? reason.message : 'Failed to load bootstrap status'))
  }, [])

  useEffect(() => {
    if (!token) {
      setCurrentUser(null)
      return
    }
    void me(token)
      .then((data) => setCurrentUser(data.user))
      .catch(() => {
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
          listMedia(token, {
            q: deferredSearch || undefined,
            kind: kindFilter || undefined,
            rating: ratingFilter || undefined,
            status: statusFilter || undefined,
          }),
          listJobs(token),
          listBackups(token),
        ])

        if (cancelled) return
        setOverview(overviewPayload)
        setMedia(mediaPayload.items)
        setJobs(jobsPayload.items)
        setBackups(backupsPayload.items)

        if (currentUser.role === 'admin') {
          const [storagePayload, usersPayload] = await Promise.all([getStorage(token), getUsers(token)])
          if (cancelled) return
          setStorage(storagePayload)
          setUsers(usersPayload.items)
        }
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'Failed to refresh dashboard')
        }
      }
    }

    void load()
    const timer = window.setInterval(() => {
      void load()
    }, 12000)
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
    if (refreshed !== selectedMedia) {
      setSelectedMedia(refreshed)
    }
  }, [media, selectedMedia])

  const handleAuthSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    try {
      const auth = needsBootstrap
        ? await bootstrap(authForm.username, authForm.password, authForm.telegram)
        : await login(authForm.username, authForm.password)
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

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragActive(false)
    void handleFiles(Array.from(event.dataTransfer.files))
  }

  const handlePick = (event: ChangeEvent<HTMLInputElement>) => {
    void handleFiles(Array.from(event.target.files ?? []))
    event.target.value = ''
  }

  const handleCreateBackup = async (scope: 'metadata' | 'full') => {
    if (!token) return
    setError('')
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
    try {
      await createUser(token, {
        username: newUserForm.username,
        password: newUserForm.password,
        role: newUserForm.role,
        telegram_username: newUserForm.telegram,
      })
      setNewUserForm({ username: '', password: '', telegram: '', role: 'member' })
      setRefreshNonce((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'User creation failed')
    }
  }

  const handleReindex = async (mediaId: string) => {
    if (!token) return
    setError('')
    try {
      await reindexMedia(token, mediaId)
      setRefreshNonce((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Reindex failed')
    }
  }

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY)
    setToken('')
    setCurrentUser(null)
    setStorage(null)
    setUsers([])
    setMedia([])
    setJobs([])
    setBackups([])
    setSelectedMedia(null)
    setViewerOpen(false)
  }

  const jumpToSection = (sectionId: string) => {
    document.getElementById(sectionId)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const queueCounts = { queued: 0, processing: 0, complete: 0, failed: 0 }
  jobs.forEach((job) => {
    queueCounts[job.status] += 1
  })

  const kindCounts = { image: 0, gif: 0, video: 0 }
  let completedMedia = 0
  let nsfwMedia = 0
  const tagCountMap = new Map<string, { name: string; kind: string; count: number }>()

  media.forEach((item) => {
    kindCounts[item.kind] += 1
    if (item.processing_status === 'complete') completedMedia += 1
    if (item.safety_rating === 'nsfw') nsfwMedia += 1
    ;(item.tags ?? []).forEach((tag) => {
      const key = `${tag.kind}:${tag.name}`
      const current = tagCountMap.get(key)
      if (current) {
        current.count += 1
      } else {
        tagCountMap.set(key, { name: tag.name, kind: tag.kind, count: 1 })
      }
    })
  })

  const backlogCount = queueCounts.queued + queueCounts.processing
  const spotlight = selectedMedia ?? media[0] ?? null
  const trendingTags = Array.from(tagCountMap.values())
    .sort((left, right) => right.count - left.count || left.name.localeCompare(right.name))
    .slice(0, 8)
  const aiCoverage = media.length ? Math.round((completedMedia / media.length) * 100) : 0
  const driveUsagePercent = storage?.drive_total ? Math.round((storage.drive_used / storage.drive_total) * 100) : 0
  const projectUsageTotal = storage?.project.total ?? 0
  const projectBreakdown = Object.entries(storage?.project ?? {}).filter(([name]) => name !== 'total')
  const highPriorityJobs = jobs.filter((job) => job.status === 'failed' || job.status === 'processing').slice(0, 6)
  const resultDescription =
    searchInput || kindFilter || ratingFilter || statusFilter
      ? 'Результаты уже отфильтрованы по вашему текущему запросу.'
      : 'Последние материалы в персональной библиотеке. Клик по карточке переносит фокус на детальный просмотр справа.'

  if (needsBootstrap === null) {
    return <div className="loading-screen">Loading workspace...</div>
  }

  if (!token || !currentUser) {
    return (
      <main className="auth-shell">
        <section className="auth-card glass-panel">
          <div className="eyebrow">Private AI Media Vault</div>
          <h1>{needsBootstrap ? 'Создайте первого администратора' : 'Вход в библиотеку'}</h1>
          <p className="lede">
            Минималистичная медиатека для изображений, GIF, видео и архивов с AI-индексацией и раздельными библиотеками пользователей.
          </p>

          <form className="auth-form" onSubmit={handleAuthSubmit}>
            <label>
              Логин
              <input value={authForm.username} onChange={(event) => setAuthForm({ ...authForm, username: event.target.value })} required />
            </label>
            <label>
              Пароль
              <input type="password" value={authForm.password} onChange={(event) => setAuthForm({ ...authForm, password: event.target.value })} required />
            </label>
            {needsBootstrap ? (
              <label>
                Telegram username
                <input value={authForm.telegram} onChange={(event) => setAuthForm({ ...authForm, telegram: event.target.value })} placeholder="@username" />
              </label>
            ) : null}
            <button className="primary-button" type="submit">
              {needsBootstrap ? 'Инициализировать систему' : 'Войти'}
            </button>
          </form>

          {error ? <div className="inline-error">{error}</div> : null}
        </section>
      </main>
    )
  }

  return (
    <main className="workspace-shell">
      <header className="topbar glass-panel">
        <div className="brand-lockup">
          <div className="brand-mark">RE2</div>
          <div className="brand-copy">
            <strong>Reddit Ecosystem 2</strong>
            <small>
              {currentUser.username} · {roleLabel(currentUser)}
            </small>
          </div>
        </div>

        <div className="topbar-actions">
          <button className="secondary-button" type="button" onClick={() => setRefreshNonce((value) => value + 1)}>
            Обновить
          </button>
          <button className="ghost-button" type="button" onClick={logout}>
            Выйти
          </button>
        </div>
      </header>

      {error ? <div className="global-error glass-panel">{error}</div> : null}

      <div className="workspace-grid">
        <aside className="left-rail">
          <section className="glass-panel identity-panel">
            <div className="panel-kicker">Workspace</div>
            <h1>Файлы теперь живут в индексируемой библиотеке, а не в папочном хаосе.</h1>
            <p className="lede">
              Система уже знает, кто вы, сколько медиа в зоне ответственности, что происходит в очереди и насколько библиотека готова к поиску.
            </p>

            <div className="identity-grid">
              <article className="metric-card">
                <span>Всего медиа</span>
                <strong>{overview.counts.media}</strong>
              </article>
              <article className="metric-card">
                <span>AI coverage</span>
                <strong>{aiCoverage}%</strong>
              </article>
              <article className="metric-card">
                <span>Backlog</span>
                <strong>{backlogCount}</strong>
              </article>
              <article className="metric-card">
                <span>NSFW</span>
                <strong>{nsfwMedia}</strong>
              </article>
            </div>

            <div className="jump-grid">
              <button className="jump-button" type="button" onClick={() => jumpToSection('library')}>
                Библиотека
              </button>
              <button className="jump-button" type="button" onClick={() => jumpToSection('queue')}>
                Очередь
              </button>
              <button className="jump-button" type="button" onClick={() => jumpToSection('backups')}>
                Бэкапы
              </button>
              {currentUser.role === 'admin' ? (
                <button className="jump-button" type="button" onClick={() => jumpToSection('admin')}>
                  Админ
                </button>
              ) : null}
            </div>
          </section>

          <section
            id="upload"
            className={`glass-panel upload-panel ${dragActive ? 'is-dragging' : ''}`}
            onDragOver={(event) => {
              event.preventDefault()
              setDragActive(true)
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={handleDrop}
          >
            <div className="section-head">
              <div>
                <div className="panel-kicker">Ingest</div>
                <h2>Загрузка медиа и архивов</h2>
              </div>
              <label className="primary-button file-button">
                Выбрать файлы
                <input type="file" multiple onChange={handlePick} />
              </label>
            </div>

            <p className="muted">
              Поддерживаются изображения, GIF, видео и архивы с вложенными папками. Все новые файлы автоматически ставятся в очередь на AI-разбор.
            </p>

            <div className="dropwell">
              <span>{dragActive ? 'Отпускайте файлы' : 'Drop zone / Upload deck'}</span>
              <strong>Перетащите сюда архивы, видео, GIF или изображения любого размера</strong>
              <small>Для тяжелых загрузок progress bar останется на экране до завершения передачи.</small>
            </div>

            <div className="progress-cluster">
              <div className="progress-track">
                <div className="progress-bar" style={{ width: `${uploadProgress}%` }} />
              </div>
              <div className="progress-meta">
                <span>{uploading ? 'Идет загрузка' : 'Ожидание'}</span>
                <strong>{uploading ? `${uploadProgress}%` : 'Ready'}</strong>
              </div>
            </div>
          </section>

          <section className="glass-panel pulse-panel">
            <div className="section-head">
              <div>
                <div className="panel-kicker">Pulse</div>
                <h2>Что сейчас в приоритете</h2>
              </div>
            </div>

            <div className="pulse-stack">
              <article className="pulse-card">
                <span>Очередь обработки</span>
                <strong>{backlogCount} файлов ждут или обрабатываются</strong>
                <small>
                  queued: {queueCounts.queued} · processing: {queueCounts.processing} · failed: {queueCounts.failed}
                </small>
              </article>
              <article className="pulse-card">
                <span>Частые теги</span>
                <strong>{trendingTags.length ? trendingTags[0].name.replaceAll('_', ' ') : 'Пока пусто'}</strong>
                <small>{trendingTags.length ? `${trendingTags[0].count} совпадений в текущей выборке` : 'Появятся после AI-индексации'}</small>
              </article>
              <article className="pulse-card">
                <span>Локальная устойчивость</span>
                <strong>{backups.length} backup snapshots</strong>
                <small>{backups[0] ? `Последний запуск: ${formatDate(backups[0].created_at)}` : 'Бэкапы еще не запускались'}</small>
              </article>
            </div>
          </section>
        </aside>

        <section className="center-stage">
          <section className="glass-panel command-hero">
            <div className="command-copy">
              <div className="eyebrow">Command Deck</div>
              <h2>Ищите “ту самую пикчу” по памяти, тегам, safety и очереди, а не по удаче.</h2>
              <p className="lede">
                Центр экрана сфокусирован на библиотеке: поиск, фильтрация, карточки медиа и быстрый контекст по тому, насколько коллекция уже разобрана AI.
              </p>
              <div className="hero-ribbon">
                <div className="hero-chip">
                  <span>Изображения</span>
                  <strong>{kindCounts.image}</strong>
                </div>
                <div className="hero-chip">
                  <span>GIF</span>
                  <strong>{kindCounts.gif}</strong>
                </div>
                <div className="hero-chip">
                  <span>Видео</span>
                  <strong>{kindCounts.video}</strong>
                </div>
                <div className="hero-chip">
                  <span>Пользователи</span>
                  <strong>{overview.counts.users}</strong>
                </div>
              </div>
            </div>

            <div className="mission-grid">
              <article className="mission-card">
                <span>AI coverage</span>
                <strong>{aiCoverage}%</strong>
                <p>Доля текущих результатов, которые уже полностью описаны и протегированы.</p>
              </article>
              <article className="mission-card">
                <span>Need attention</span>
                <strong>{queueCounts.failed}</strong>
                <p>Файлы с failed-статусом можно быстро отправить на повторную индексацию из блока справа.</p>
              </article>
              <article className="mission-card">
                <span>Drive usage</span>
                <strong>{storage ? `${driveUsagePercent}%` : 'n/a'}</strong>
                <p>{storage ? `${formatBytes(storage.drive_used)} занято на диске` : 'Подробности по диску доступны администраторам.'}</p>
              </article>
            </div>
          </section>

          <section id="library" className="glass-panel filter-deck">
            <div className="section-head">
              <div>
                <div className="panel-kicker">Search Atlas</div>
                <h2>Поиск по памяти, тегам и AI-описанию</h2>
              </div>
            </div>

            <div className="search-grid">
              <label className="search-field">
                Запрос
                <input
                  value={searchInput}
                  onChange={(event) => {
                    startTransition(() => setSearchInput(event.target.value))
                  }}
                  placeholder="кошкодевочка в фиолетовой комнате, meme gif, vertical edit, white hair..."
                />
              </label>

              <label>
                Тип
                <select value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}>
                  <option value="">Все</option>
                  <option value="image">Изображение</option>
                  <option value="gif">GIF</option>
                  <option value="video">Видео</option>
                </select>
              </label>

              <label>
                Safety
                <select value={ratingFilter} onChange={(event) => setRatingFilter(event.target.value)}>
                  <option value="">Все</option>
                  <option value="sfw">SFW</option>
                  <option value="questionable">Questionable</option>
                  <option value="nsfw">NSFW</option>
                </select>
              </label>

              <label>
                Статус
                <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                  <option value="">Все</option>
                  <option value="pending">Pending</option>
                  <option value="processing">Processing</option>
                  <option value="complete">Complete</option>
                  <option value="failed">Failed</option>
                </select>
              </label>
            </div>

            <div className="tag-stream">
              {trendingTags.length ? (
                trendingTags.map((tag) => (
                  <button
                    key={`${tag.kind}-${tag.name}`}
                    className={`tag-chip tag-${tag.kind}`}
                    type="button"
                    onClick={() => {
                      startTransition(() => setSearchInput(tag.name.replaceAll('_', ' ')))
                    }}
                  >
                    <span>{tag.name.replaceAll('_', ' ')}</span>
                    <strong>{tag.count}</strong>
                  </button>
                ))
              ) : (
                <div className="muted">Частые теги появятся здесь после первых индексаций.</div>
              )}
            </div>
          </section>

          <section className="library-toolbar">
            <div>
              <div className="eyebrow">Library Feed</div>
              <h2>{media.length} результатов</h2>
              <p className="muted">{resultDescription}</p>
            </div>
            <div className="toolbar-chips">
              <span className="toolbar-chip">queued {queueCounts.queued}</span>
              <span className="toolbar-chip">processing {queueCounts.processing}</span>
              <span className="toolbar-chip">complete {queueCounts.complete}</span>
              <span className="toolbar-chip">failed {queueCounts.failed}</span>
            </div>
          </section>

          <section className="atlas-grid">
            {media.length ? (
              media.map((item) => {
                const active = spotlight?.id === item.id
                return (
                  <article key={item.id} className={`atlas-card ${active ? 'active' : ''}`}>
                    <button
                      className="atlas-hitbox"
                      type="button"
                      onClick={() => {
                        setSelectedMedia(item)
                        setViewerOpen(true)
                      }}
                    >
                      <span className="sr-only">Select media</span>
                    </button>

                    <div className="atlas-preview">
                      {item.thumbnail_url ? (
                        <img src={mediaAssetUrl(item.thumbnail_url, token)} alt={item.original_filename} />
                      ) : (
                        <div className="empty-preview">{kindLabel(item.kind)}</div>
                      )}
                      <div className="preview-overlay">
                        <span className="preview-kind">{kindLabel(item.kind)}</span>
                        <span className={`badge badge-${item.safety_rating}`}>{ratingLabel(item.safety_rating)}</span>
                      </div>
                    </div>

                    <div className="atlas-body">
                      <div className="atlas-topline">
                        <span className={`badge badge-status-${item.processing_status}`}>{item.processing_status}</span>
                        <span className="micro-meta">{formatBytes(item.file_size)}</span>
                      </div>

                      <h3 title={item.original_filename}>{item.original_filename}</h3>
                      <p>{trimText(item.description, 'AI-описание пока не готово. После индексации здесь появится подробный разбор сцены.')}</p>

                      <div className="info-strip">
                        <span>{item.width && item.height ? `${item.width}×${item.height}` : kindLabel(item.kind)}</span>
                        <span>{item.duration_seconds ? formatDuration(item.duration_seconds) : formatDate(item.normalized_timestamp)}</span>
                      </div>

                      <div className="tag-cloud">
                        {(item.tags ?? []).slice(0, 7).map((tag) => (
                          <span key={`${item.id}-${tag.kind}-${tag.name}`} className={`tag-chip static-chip tag-${tag.kind}`}>
                            {tag.name.replaceAll('_', ' ')}
                          </span>
                        ))}
                      </div>
                    </div>
                  </article>
                )
              })
            ) : (
              <article className="empty-state glass-panel">
                <div className="panel-kicker">No Results</div>
                <h2>Под текущие фильтры ничего не нашлось.</h2>
                <p className="muted">
                  Снимите часть фильтров или загрузите новые файлы. Если вы только что загрузили архив, дайте очереди немного времени на разбор и индексацию.
                </p>
              </article>
            )}
          </section>
        </section>

        <aside className="right-rail">
          <section className="glass-panel spotlight-panel">
            <div className="section-head">
              <div>
                <div className="panel-kicker">Spotlight</div>
                <h2>{spotlight ? 'Текущий фокус' : 'Выберите карточку'}</h2>
              </div>
              {spotlight ? (
                <div className="button-row">
                  <button className="secondary-button" type="button" onClick={() => setViewerOpen(true)}>
                    Открыть
                  </button>
                  <button className="ghost-button" type="button" onClick={() => void handleReindex(spotlight.id)}>
                    Reindex
                  </button>
                </div>
              ) : null}
            </div>

            {spotlight ? (
              <div className="spotlight-body">
                <div className="spotlight-preview">
                  {spotlight.kind === 'video' ? (
                    <video controls src={mediaAssetUrl(spotlight.file_url, token)} />
                  ) : (
                    <img src={mediaAssetUrl(spotlight.file_url, token)} alt={spotlight.original_filename} />
                  )}
                </div>

                <div className="spotlight-copy">
                  <div className="spotlight-badges">
                    <span className={`badge badge-${spotlight.safety_rating}`}>{ratingLabel(spotlight.safety_rating)}</span>
                    <span className={`badge badge-status-${spotlight.processing_status}`}>{spotlight.processing_status}</span>
                  </div>
                  <h3>{spotlight.original_filename}</h3>
                  <p>{trimText(spotlight.description, 'AI-описание пока отсутствует.', 240)}</p>

                  <div className="detail-grid">
                    <div>
                      <span>Размер</span>
                      <strong>{formatBytes(spotlight.file_size)}</strong>
                    </div>
                    <div>
                      <span>Тип</span>
                      <strong>{kindLabel(spotlight.kind)}</strong>
                    </div>
                    <div>
                      <span>Blur</span>
                      <strong>{spotlight.blur_score?.toFixed(1) ?? 'n/a'}</strong>
                    </div>
                    <div>
                      <span>Время</span>
                      <strong>{formatDate(spotlight.normalized_timestamp)}</strong>
                    </div>
                  </div>

                  {spotlight.technical_notes ? <div className="note-block">{spotlight.technical_notes}</div> : null}

                  <div className="tag-cloud">
                    {(spotlight.tags ?? []).map((tag) => (
                      <span key={`${spotlight.id}-${tag.kind}-${tag.name}`} className={`tag-chip static-chip tag-${tag.kind}`}>
                        {tag.name.replaceAll('_', ' ')}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <div className="muted">Откройте библиотеку в центре и выберите файл, чтобы увидеть расширенный просмотр.</div>
            )}
          </section>

          <section id="queue" className="glass-panel queue-panel">
            <div className="section-head">
              <div>
                <div className="panel-kicker">Queue</div>
                <h2>Состояние jobs</h2>
              </div>
            </div>

            <div className="queue-grid">
              <article className="queue-stat">
                <span>Queued</span>
                <strong>{queueCounts.queued}</strong>
              </article>
              <article className="queue-stat">
                <span>Processing</span>
                <strong>{queueCounts.processing}</strong>
              </article>
              <article className="queue-stat">
                <span>Complete</span>
                <strong>{queueCounts.complete}</strong>
              </article>
              <article className="queue-stat danger">
                <span>Failed</span>
                <strong>{queueCounts.failed}</strong>
              </article>
            </div>

            <div className="compact-list">
              {(highPriorityJobs.length ? highPriorityJobs : jobs.slice(0, 6)).map((job) => (
                <div key={job.id} className="compact-row queue-row">
                  <div>
                    <strong>{job.media_id.slice(0, 8)}</strong>
                    <small>{formatDate(job.created_at)}</small>
                    {job.error_message ? <small className="error-text">{trimText(job.error_message, '', 120)}</small> : null}
                  </div>
                  <span className={`badge badge-status-${job.status}`}>{job.status}</span>
                </div>
              ))}
            </div>
          </section>

          <section id="backups" className="glass-panel backup-panel">
            <div className="section-head">
              <div>
                <div className="panel-kicker">Backups</div>
                <h2>Telegram backup pipeline</h2>
              </div>
              <div className="button-row">
                <button className="secondary-button" type="button" onClick={() => void handleCreateBackup('metadata')}>
                  Metadata
                </button>
                <button className="primary-button" type="button" onClick={() => void handleCreateBackup('full')}>
                  Full backup
                </button>
              </div>
            </div>

            <div className="compact-list">
              {backups.slice(0, 6).map((backup) => (
                <div key={backup.id} className="compact-row compact-row-column">
                  <div className="row-between">
                    <strong>{backup.scope}</strong>
                    <span className={`badge badge-status-${backup.status}`}>{backup.status}</span>
                  </div>
                  <small>{backup.parts.length} частей · {formatDate(backup.created_at)}</small>
                  {backup.error_message ? <small className="error-text">{backup.error_message}</small> : null}
                </div>
              ))}
            </div>
          </section>

          <section className="glass-panel signal-panel">
            <div className="section-head">
              <div>
                <div className="panel-kicker">Signals</div>
                <h2>Логи и AI-подсказка</h2>
              </div>
            </div>

            <div className="signal-log-list">
              {overview.recent_logs.slice(0, 5).map((log) => (
                <article key={log.id} className={`log-card severity-${log.severity}`}>
                  <span>{log.event_type}</span>
                  <strong>{trimText(log.message, '', 110)}</strong>
                  <small>{formatDate(log.created_at)}</small>
                </article>
              ))}
            </div>

            <pre className="prompt-preview">{overview.prompt_preview}</pre>
          </section>
        </aside>
      </div>

      {currentUser.role === 'admin' ? (
        <section id="admin" className="admin-stage">
          <article className="glass-panel storage-panel">
            <div className="section-head">
              <div>
                <div className="panel-kicker">Storage</div>
                <h2>Диск как у Steam, но для медиатеки</h2>
              </div>
            </div>

            {storage ? (
              <>
                <div className="storage-hero">
                  <div className="storage-ring">
                    <strong>{driveUsagePercent}%</strong>
                    <span>использовано</span>
                  </div>
                  <div className="storage-facts">
                    <div>
                      <span>Всего на диске</span>
                      <strong>{formatBytes(storage.drive_total)}</strong>
                    </div>
                    <div>
                      <span>Свободно</span>
                      <strong>{formatBytes(storage.drive_free)}</strong>
                    </div>
                    <div>
                      <span>Проект</span>
                      <strong>{formatBytes(projectUsageTotal)}</strong>
                    </div>
                    <div>
                      <span>Прочее на диске</span>
                      <strong>{formatBytes(storage.other_on_drive)}</strong>
                    </div>
                  </div>
                </div>

                <div className="storage-breakdown">
                  {projectBreakdown.map(([name, value]) => (
                    <div key={name} className="storage-row">
                      <div className="row-between">
                        <span>{name}</span>
                        <strong>{formatBytes(value)}</strong>
                      </div>
                      <div className="mini-track">
                        <div className="mini-bar" style={{ width: `${Math.max((value / Math.max(projectUsageTotal || 1, 1)) * 100, 2)}%` }} />
                      </div>
                    </div>
                  ))}
                </div>

                <div className="user-storage-grid">
                  {storage.per_user.map((entry) => (
                    <article key={`${entry.username}-${entry.kind}`} className="user-usage">
                      <span>{entry.username}</span>
                      <strong>{entry.kind}</strong>
                      <small>{formatBytes(entry.bytes)}</small>
                    </article>
                  ))}
                </div>
              </>
            ) : (
              <p className="muted">Storage analytics unavailable.</p>
            )}
          </article>

          <article className="glass-panel users-panel">
            <div className="section-head">
              <div>
                <div className="panel-kicker">Access</div>
                <h2>Пользователи и роли</h2>
              </div>
            </div>

            <form className="admin-form" onSubmit={handleCreateUser}>
              <label>
                username
                <input value={newUserForm.username} onChange={(event) => setNewUserForm({ ...newUserForm, username: event.target.value })} required />
              </label>
              <label>
                password
                <input type="password" value={newUserForm.password} onChange={(event) => setNewUserForm({ ...newUserForm, password: event.target.value })} required />
              </label>
              <label>
                telegram
                <input value={newUserForm.telegram} onChange={(event) => setNewUserForm({ ...newUserForm, telegram: event.target.value })} placeholder="@username" />
              </label>
              <label>
                role
                <select value={newUserForm.role} onChange={(event) => setNewUserForm({ ...newUserForm, role: event.target.value as 'admin' | 'member' })}>
                  <option value="member">member</option>
                  <option value="admin">admin</option>
                </select>
              </label>
              <button className="primary-button" type="submit">
                Добавить пользователя
              </button>
            </form>

            <div className="user-list">
              {users.map((user) => (
                <article key={user.id} className="user-card">
                  <div>
                    <strong>{user.username}</strong>
                    <small>{user.telegram_username ? `@${user.telegram_username}` : 'Telegram не подключен'}</small>
                  </div>
                  <div className="user-card-meta">
                    <span className={`badge badge-role-${user.role}`}>{user.role}</span>
                    <small>{formatDate(user.created_at)}</small>
                  </div>
                </article>
              ))}
            </div>
          </article>
        </section>
      ) : null}

      {viewerOpen && spotlight ? (
        <div className="modal-backdrop" onClick={() => setViewerOpen(false)}>
          <div className="modal glass-panel" onClick={(event) => event.stopPropagation()}>
            <div className="section-head">
              <div>
                <div className="panel-kicker">{kindLabel(spotlight.kind)}</div>
                <h2>{spotlight.original_filename}</h2>
              </div>
              <div className="button-row">
                <button className="secondary-button" type="button" onClick={() => void handleReindex(spotlight.id)}>
                  Reindex
                </button>
                <button className="ghost-button" type="button" onClick={() => setViewerOpen(false)}>
                  Close
                </button>
              </div>
            </div>

            <div className="modal-grid">
              <div className="modal-preview">
                {spotlight.kind === 'video' ? (
                  <video controls src={mediaAssetUrl(spotlight.file_url, token)} />
                ) : (
                  <img src={mediaAssetUrl(spotlight.file_url, token)} alt={spotlight.original_filename} />
                )}
              </div>

              <div className="modal-copy">
                <div className="spotlight-badges">
                  <span className={`badge badge-${spotlight.safety_rating}`}>{ratingLabel(spotlight.safety_rating)}</span>
                  <span className={`badge badge-status-${spotlight.processing_status}`}>{spotlight.processing_status}</span>
                </div>
                <p>{trimText(spotlight.description, 'AI-описание пока отсутствует.', 500)}</p>

                <div className="detail-grid">
                  <div>
                    <span>Размер</span>
                    <strong>{formatBytes(spotlight.file_size)}</strong>
                  </div>
                  <div>
                    <span>Разрешение</span>
                    <strong>{spotlight.width && spotlight.height ? `${spotlight.width}×${spotlight.height}` : 'n/a'}</strong>
                  </div>
                  <div>
                    <span>Длительность</span>
                    <strong>{formatDuration(spotlight.duration_seconds)}</strong>
                  </div>
                  <div>
                    <span>Timestamp</span>
                    <strong>{formatDate(spotlight.normalized_timestamp)}</strong>
                  </div>
                </div>

                {spotlight.technical_notes ? <div className="note-block">{spotlight.technical_notes}</div> : null}

                <div className="tag-cloud">
                  {(spotlight.tags ?? []).map((tag) => (
                    <span key={`${spotlight.id}-modal-${tag.kind}-${tag.name}`} className={`tag-chip static-chip tag-${tag.kind}`}>
                      {tag.name.replaceAll('_', ' ')}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  )
}

export default App
