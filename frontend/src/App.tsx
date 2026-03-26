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
import type { BackupItem, DiskUsagePayload, JobItem, MediaItem, OverviewPayload, User } from './types'

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

function roleLabel(user: User | null) {
  if (!user) return ''
  return user.role === 'admin' ? 'Админ' : 'Участник'
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
  const [searchInput, setSearchInput] = useState('')
  const [kindFilter, setKindFilter] = useState('')
  const [ratingFilter, setRatingFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [refreshNonce, setRefreshNonce] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
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
  }

  if (needsBootstrap === null) {
    return <div className="loading-screen">Loading workspace...</div>
  }

  if (!token || !currentUser) {
    return (
      <main className="auth-shell">
        <section className="auth-card glass">
          <div className="eyebrow">Private AI Media Vault</div>
          <h1>{needsBootstrap ? 'Создайте первого администратора' : 'Войдите в библиотеку'}</h1>
          <p className="lede">
            Сайт для сортировки огромных коллекций Reddit-медиа с AI-тегами, архивами, Telegram и полной изоляцией данных между пользователями.
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
    <main className="app-shell">
      <section className="hero glass">
        <div className="hero-copy">
          <div className="eyebrow">Reddit Ecosystem 2</div>
          <h1>Найти тот самый файл должно быть проще, чем вспомнить его название.</h1>
          <p className="lede">
            AI описывает изображения, GIF и видео, выделяет технические теги, безопасность, blur и время из имени файла, а каждая библиотека живет строго отдельно от других.
          </p>
        </div>
        <div className="hero-side">
          <div className="profile-chip">
            <span>{currentUser.username}</span>
            <strong>{roleLabel(currentUser)}</strong>
          </div>
          <div className="hero-actions">
            <button className="secondary-button" type="button" onClick={() => setRefreshNonce((value) => value + 1)}>
              Обновить
            </button>
            <button className="ghost-button" type="button" onClick={logout}>
              Выйти
            </button>
          </div>
        </div>
        <div className="stats-grid">
          <article className="stat-card">
            <span>Медиа</span>
            <strong>{overview.counts.media}</strong>
          </article>
          <article className="stat-card">
            <span>Jobs</span>
            <strong>{overview.counts.jobs}</strong>
          </article>
          <article className="stat-card">
            <span>Пользователи</span>
            <strong>{overview.counts.users}</strong>
          </article>
          <article className="stat-card">
            <span>Бэкапы</span>
            <strong>{backups.length}</strong>
          </article>
        </div>
      </section>

      {error ? <div className="global-error glass">{error}</div> : null}

      <section className="top-grid">
        <article className="panel glass upload-panel" onDragOver={(event) => event.preventDefault()} onDrop={handleDrop}>
          <div className="panel-heading">
            <div>
              <div className="panel-kicker">Upload</div>
              <h2>Медиа и архивы любого размера</h2>
            </div>
            <label className="primary-button file-button">
              Выбрать файлы
              <input type="file" multiple onChange={handlePick} />
            </label>
          </div>
          <p className="muted">
            Кидайте картинки, GIF, видео, ZIP, TAR, 7Z и другие архивы с вложенными папками. Система сама выделит медиа и поставит их в очередь на индексацию.
          </p>
          <div className="drop-zone">
            <span>Drop zone</span>
            <strong>Перетащите сюда медиа или архивы</strong>
          </div>
          <div className="progress-row">
            <div className="progress-track">
              <div className="progress-bar" style={{ width: `${uploadProgress}%` }} />
            </div>
            <span>{uploading ? `${uploadProgress}%` : 'Ready'}</span>
          </div>
        </article>

        <article className="panel glass search-panel">
          <div className="panel-heading">
            <div>
              <div className="panel-kicker">Search</div>
              <h2>Поиск по памяти, описанию и тегам</h2>
            </div>
          </div>
          <div className="filters">
            <label className="filter-wide">
              Запрос
              <input
                value={searchInput}
                onChange={(event) => {
                  startTransition(() => setSearchInput(event.target.value))
                }}
                placeholder="meme с белой кошкой, purple room, vertical anime gif..."
              />
            </label>
            <label>
              Тип
              <select value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}>
                <option value="">Все</option>
                <option value="image">Image</option>
                <option value="gif">GIF</option>
                <option value="video">Video</option>
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
          <div className="log-strip">
            {overview.recent_logs.slice(0, 4).map((log) => (
              <div key={log.id} className={`log-pill severity-${log.severity}`}>
                <span>{log.event_type}</span>
                <small>{log.message}</small>
              </div>
            ))}
          </div>
        </article>
      </section>

      <section className="content-grid">
        <article className="panel glass library-panel">
          <div className="panel-heading">
            <div>
              <div className="panel-kicker">Library</div>
              <h2>Медиатека</h2>
            </div>
            <span className="muted">{media.length} результатов</span>
          </div>
          <div className="media-grid">
            {media.map((item) => (
              <button key={item.id} className="media-card" type="button" onClick={() => setSelectedMedia(item)}>
                <div className="media-preview">
                  {item.thumbnail_url ? (
                    <img src={mediaAssetUrl(item.thumbnail_url, token)} alt={item.original_filename} />
                  ) : (
                    <div className="empty-preview">{item.kind}</div>
                  )}
                </div>
                <div className="media-meta">
                  <div className="media-topline">
                    <span className={`badge badge-${item.safety_rating}`}>{item.safety_rating}</span>
                    <span className={`badge badge-status-${item.processing_status}`}>{item.processing_status}</span>
                  </div>
                  <strong title={item.original_filename}>{item.original_filename}</strong>
                  <small>
                    {item.width && item.height ? `${item.width}×${item.height}` : item.kind} · {formatBytes(item.file_size)}
                  </small>
                  <p>{item.description || 'Файл еще не описан AI или описание пока пустое.'}</p>
                  <div className="tag-row">
                    {(item.tags ?? []).slice(0, 8).map((tag) => (
                      <span key={`${item.id}-${tag.kind}-${tag.name}`} className={`tag-chip tag-${tag.kind}`}>
                        {tag.name}
                      </span>
                    ))}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </article>

        <aside className="side-stack">
          <article className="panel glass">
            <div className="panel-heading">
              <div>
                <div className="panel-kicker">Queue</div>
                <h2>Jobs</h2>
              </div>
            </div>
            <div className="compact-list">
              {jobs.slice(0, 8).map((job) => (
                <div key={job.id} className="compact-row">
                  <div>
                    <strong>{job.media_id.slice(0, 8)}</strong>
                    <small>{formatDate(job.created_at)}</small>
                  </div>
                  <span className={`badge badge-status-${job.status}`}>{job.status}</span>
                </div>
              ))}
            </div>
          </article>

          <article className="panel glass">
            <div className="panel-heading">
              <div>
                <div className="panel-kicker">Backups</div>
                <h2>Telegram backup pipeline</h2>
              </div>
              <div className="button-row">
                <button className="secondary-button" type="button" onClick={() => void handleCreateBackup('metadata')}>
                  Metadata
                </button>
                <button className="primary-button" type="button" onClick={() => void handleCreateBackup('full')}>
                  Full
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
                  <small>{backup.parts.length} parts · {formatDate(backup.created_at)}</small>
                  {backup.error_message ? <small className="error-text">{backup.error_message}</small> : null}
                </div>
              ))}
            </div>
          </article>

          <article className="panel glass">
            <div className="panel-heading">
              <div>
                <div className="panel-kicker">Prompt</div>
                <h2>AI-инструкция</h2>
              </div>
            </div>
            <pre className="prompt-preview">{overview.prompt_preview}</pre>
          </article>
        </aside>
      </section>

      {currentUser.role === 'admin' ? (
        <section className="admin-grid">
          <article className="panel glass">
            <div className="panel-heading">
              <div>
                <div className="panel-kicker">Storage</div>
                <h2>Занятое место как в Steam</h2>
              </div>
            </div>
            {storage ? (
              <div className="storage-stack">
                {Object.entries(storage.project).map(([name, value]) => (
                  <div key={name} className="storage-row">
                    <div className="row-between">
                      <span>{name}</span>
                      <strong>{formatBytes(value)}</strong>
                    </div>
                    <div className="mini-track">
                      <div className="mini-bar" style={{ width: `${Math.max((value / Math.max(storage.project.total || 1, 1)) * 100, 2)}%` }} />
                    </div>
                  </div>
                ))}
                <div className="storage-split">
                  {storage.per_user.map((entry) => (
                    <div key={`${entry.username}-${entry.kind}`} className="user-usage">
                      <span>{entry.username}</span>
                      <strong>{entry.kind}</strong>
                      <small>{formatBytes(entry.bytes)}</small>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <p className="muted">Storage analytics unavailable.</p>
            )}
          </article>

          <article className="panel glass">
            <div className="panel-heading">
              <div>
                <div className="panel-kicker">Admin</div>
                <h2>Пользователи и роли</h2>
              </div>
            </div>
            <form className="admin-form" onSubmit={handleCreateUser}>
              <input placeholder="username" value={newUserForm.username} onChange={(event) => setNewUserForm({ ...newUserForm, username: event.target.value })} required />
              <input placeholder="password" type="password" value={newUserForm.password} onChange={(event) => setNewUserForm({ ...newUserForm, password: event.target.value })} required />
              <input placeholder="@telegram" value={newUserForm.telegram} onChange={(event) => setNewUserForm({ ...newUserForm, telegram: event.target.value })} />
              <select value={newUserForm.role} onChange={(event) => setNewUserForm({ ...newUserForm, role: event.target.value as 'admin' | 'member' })}>
                <option value="member">member</option>
                <option value="admin">admin</option>
              </select>
              <button className="primary-button" type="submit">
                Добавить пользователя
              </button>
            </form>
            <div className="compact-list users-list">
              {users.map((user) => (
                <div key={user.id} className="compact-row">
                  <div>
                    <strong>{user.username}</strong>
                    <small>{user.telegram_username ? `@${user.telegram_username}` : 'No Telegram link'}</small>
                  </div>
                  <span className={`badge badge-role-${user.role}`}>{user.role}</span>
                </div>
              ))}
            </div>
          </article>
        </section>
      ) : null}

      {selectedMedia ? (
        <div className="modal-backdrop" onClick={() => setSelectedMedia(null)}>
          <div className="modal glass" onClick={(event) => event.stopPropagation()}>
            <div className="panel-heading">
              <div>
                <div className="panel-kicker">{selectedMedia.kind}</div>
                <h2>{selectedMedia.original_filename}</h2>
              </div>
              <div className="button-row">
                <button className="secondary-button" type="button" onClick={() => void handleReindex(selectedMedia.id)}>
                  Reindex
                </button>
                <button className="ghost-button" type="button" onClick={() => setSelectedMedia(null)}>
                  Close
                </button>
              </div>
            </div>
            <div className="modal-grid">
              <div className="modal-preview">
                {selectedMedia.kind === 'video' ? (
                  <video controls src={mediaAssetUrl(selectedMedia.file_url, token)} />
                ) : (
                  <img src={mediaAssetUrl(selectedMedia.file_url, token)} alt={selectedMedia.original_filename} />
                )}
              </div>
              <div className="modal-copy">
                <span className={`badge badge-${selectedMedia.safety_rating}`}>{selectedMedia.safety_rating}</span>
                <p>{selectedMedia.description || 'No AI description yet.'}</p>
                <div className="meta-table">
                  <div><span>Размер</span><strong>{formatBytes(selectedMedia.file_size)}</strong></div>
                  <div><span>Timestamp</span><strong>{formatDate(selectedMedia.normalized_timestamp)}</strong></div>
                  <div><span>Blur</span><strong>{selectedMedia.blur_score?.toFixed(1) ?? 'n/a'}</strong></div>
                  <div><span>Статус</span><strong>{selectedMedia.processing_status}</strong></div>
                </div>
                <div className="tag-row modal-tags">
                  {(selectedMedia.tags ?? []).map((tag) => (
                    <span key={`${selectedMedia.id}-${tag.kind}-${tag.name}`} className={`tag-chip tag-${tag.kind}`}>
                      {tag.name}
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

