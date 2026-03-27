import { useEffect, useState, type ChangeEvent, type DragEvent, type FormEvent, type ReactNode } from 'react'

import { mediaAssetUrl } from './api'
import type {
  BackupItem,
  DiskUsagePayload,
  JobItem,
  MediaItem,
  OverviewPayload,
  RuntimeConfigItem,
  SafetyRating,
  TagCatalogItem,
  TagCatalogPayload,
  User,
} from './types'
import type { StorageSegment, TabDefinition, WorkspaceTab } from './workspace-helpers'
import {
  STORAGE_LABELS,
  configValueToInput,
  formatBytes,
  formatDate,
  formatDuration,
  formatMetric,
  kindLabel,
  prettifyTag,
  primaryDescription,
  ratingLabel,
  readDetailList,
  readDetailText,
  roleLabel,
  trimText,
} from './workspace-helpers'

type Tone = 'default' | 'accent' | 'success' | 'danger'

type QueueCounts = {
  queued: number
  processing: number
  complete: number
  failed: number
}

function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <article className="glass-panel empty-state">
      <h2>{title}</h2>
      <p className="muted">{description}</p>
    </article>
  )
}

function InfoBanner({ tone, text }: { tone: 'error' | 'notice' | 'warning'; text: string }) {
  return <div className={`glass-panel global-banner banner-${tone}`}>{text}</div>
}

function LoadMoreRow({
  visible,
  loading,
  buttonLabel,
  hint,
  onClick,
}: {
  visible: boolean
  loading: boolean
  buttonLabel: string
  hint: string
  onClick: () => void
}) {
  if (!visible) {
    return null
  }
  return (
    <div className="button-row load-more-row">
      <button className="secondary-button" type="button" onClick={onClick} disabled={loading}>
        {loading ? 'Загружаем еще...' : buttonLabel}
      </button>
      <small className="muted">{hint}</small>
    </div>
  )
}

export function StatCard({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string
  value: string | number
  hint?: string
  tone?: Tone
}) {
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

function MediaPreview({
  item,
  token,
  className,
  children,
}: {
  item: MediaItem
  token: string
  className: string
  children?: ReactNode
}) {
  const thumbnailUrl = mediaAssetUrl(item.thumbnail_url, token)
  const sourceUrl = item.kind === 'video' ? '' : mediaAssetUrl(item.file_url, token)
  const [currentUrl, setCurrentUrl] = useState(thumbnailUrl || sourceUrl)

  useEffect(() => {
    setCurrentUrl(thumbnailUrl || sourceUrl)
  }, [thumbnailUrl, sourceUrl])

  const handleError = () => {
    if (sourceUrl && currentUrl !== sourceUrl) {
      setCurrentUrl(sourceUrl)
      return
    }
    setCurrentUrl('')
  }

  return (
    <div className={className}>
      {currentUrl ? (
        <img
          src={currentUrl}
          alt={item.original_filename}
          loading="lazy"
          decoding="async"
          onError={handleError}
        />
      ) : (
        <div className="gallery-empty">{kindLabel(item.kind)}</div>
      )}
      {children}
    </div>
  )
}

function MediaCard({
  item,
  token,
  active,
  onOpen,
}: {
  item: MediaItem
  token: string
  active: boolean
  onOpen: () => void
}) {
  const visibleTags = (item.tags ?? []).slice(0, 6)
  return (
    <article className={`gallery-card ${active ? 'active' : ''}`}>
      <button className="gallery-hitbox" type="button" onClick={onOpen}>
        <span className="sr-only">Open media</span>
      </button>
      <MediaPreview item={item} token={token} className="gallery-preview">
        <div className="gallery-overlay">
          <span>{kindLabel(item.kind)}</span>
          <span className={`badge badge-${item.safety_rating}`}>{ratingLabel(item.safety_rating)}</span>
        </div>
      </MediaPreview>
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
              {prettifyTag(tag.name)}
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
      <MediaPreview item={item} token={token} className="feed-preview" />
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
        {item.is_described ? 'ready' : 'pending'}
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

export function AuthScreen({
  needsBootstrap,
  authForm,
  setAuthForm,
  onSubmit,
  notice,
  error,
}: {
  needsBootstrap: boolean
  authForm: { username: string; password: string; telegram: string }
  setAuthForm: (value: { username: string; password: string; telegram: string }) => void
  onSubmit: (event: FormEvent) => void
  notice: string
  error: string
}) {
  return (
    <main className="auth-shell">
      <section className="auth-card glass-panel">
        <div className="eyebrow">Private AI Media Vault</div>
        <h1>{needsBootstrap ? 'Создайте первого администратора' : 'Вход в библиотеку'}</h1>
        <p className="lede">Переписанный рабочий фронтенд для большой медиатеки: быстрые вкладки, resumable upload и предсказуемое состояние без огромного монолита.</p>
        <form className="auth-form" onSubmit={onSubmit}>
          <label>Логин<input value={authForm.username} onChange={(event) => setAuthForm({ ...authForm, username: event.target.value })} required /></label>
          <label>Пароль<input type="password" value={authForm.password} onChange={(event) => setAuthForm({ ...authForm, password: event.target.value })} required /></label>
          {needsBootstrap ? <label>Telegram username<input value={authForm.telegram} onChange={(event) => setAuthForm({ ...authForm, telegram: event.target.value })} placeholder="@username" /></label> : null}
          <button className="primary-button" type="submit">{needsBootstrap ? 'Инициализировать систему' : 'Войти'}</button>
        </form>
        {notice ? <InfoBanner tone="notice" text={notice} /> : null}
        {error ? <div className="inline-error">{error}</div> : null}
      </section>
    </main>
  )
}

export function AppSidebar({
  currentUser,
  sidebarCollapsed,
  tabs,
  activeTab,
  counts,
  aiCoverage,
  backlogCount,
  nsfwMedia,
  onToggleCollapse,
  onCloseMobile,
  onSelectTab,
  onRefresh,
  onLogout,
}: {
  currentUser: User
  sidebarCollapsed: boolean
  tabs: TabDefinition[]
  activeTab: WorkspaceTab
  counts: OverviewPayload['counts']
  aiCoverage: number
  backlogCount: number
  nsfwMedia: number
  onToggleCollapse: () => void
  onCloseMobile: () => void
  onSelectTab: (tab: WorkspaceTab) => void
  onRefresh: () => void
  onLogout: () => void
}) {
  return (
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
          <button className="icon-button desktop-only" type="button" onClick={onToggleCollapse}>{sidebarCollapsed ? '>' : '<'}</button>
          <button className="icon-button mobile-only" type="button" onClick={onCloseMobile}>x</button>
        </div>
      </div>
      <div className="sidebar-summary">
        <StatCard label="Всего медиа" value={counts.media} hint={`${counts.media_by_kind.image} img · ${counts.media_by_kind.gif} gif · ${counts.media_by_kind.video} video`} tone="accent" />
        {!sidebarCollapsed ? <StatCard label="AI готово" value={`${aiCoverage}%`} hint={`queue ${backlogCount} · nsfw ${nsfwMedia}`} /> : null}
      </div>
      <nav className="sidebar-nav">
        {tabs.map((tab) => (
          <SidebarTab
            key={tab.id}
            active={tab.id === activeTab}
            short={tab.short}
            label={tab.label}
            description={tab.description}
            collapsed={sidebarCollapsed}
            onClick={() => onSelectTab(tab.id)}
          />
        ))}
      </nav>
      <div className="sidebar-foot">
        <button className="secondary-button" type="button" onClick={onRefresh}>{sidebarCollapsed ? 'R' : 'Обновить'}</button>
        <button className="ghost-button" type="button" onClick={onLogout}>{sidebarCollapsed ? 'X' : 'Выйти'}</button>
      </div>
    </aside>
  )
}

export function WorkspaceHeader({
  currentTab,
  mediaCount,
  aiCoverage,
  backlogCount,
  onOpenSidebar,
}: {
  currentTab: TabDefinition
  mediaCount: number
  aiCoverage: number
  backlogCount: number
  onOpenSidebar: () => void
}) {
  return (
    <header className="workspace-header glass-panel">
      <div className="workspace-title">
        <button className="icon-button mobile-only" type="button" onClick={onOpenSidebar}>menu</button>
        <div className="workspace-title-copy">
          <span>{currentTab.label}</span>
          <h1>{currentTab.title}</h1>
          <p className="workspace-subtitle">{currentTab.description}</p>
        </div>
      </div>
      <div className="workspace-pills">
        <span className="status-pill">{mediaCount} media</span>
        <span className="status-pill">AI {aiCoverage}%</span>
        <span className="status-pill">queue {backlogCount}</span>
      </div>
    </header>
  )
}

export function WorkspaceAlerts({
  error,
  notice,
  warning,
}: {
  error: string
  notice: string
  warning: string
}) {
  return (
    <>
      {error ? <InfoBanner tone="error" text={error} /> : null}
      {notice ? <InfoBanner tone="notice" text={notice} /> : null}
      {warning ? <InfoBanner tone="warning" text={warning} /> : null}
    </>
  )
}

export function LibraryTab({
  overview,
  aiCoverage,
  completedMedia,
  nsfwMedia,
  queueCounts,
  topTags,
  dragActive,
  onDragOver,
  onDragLeave,
  onDrop,
  onPick,
  uploadProgress,
  uploadPhaseLabel,
  uploadPhaseValue,
  searchInput,
  onSearchChange,
  kindFilter,
  onKindFilterChange,
  ratingFilter,
  onRatingFilterChange,
  statusFilter,
  onStatusFilterChange,
  loadingMedia,
  media,
  selectedMediaId,
  token,
  onOpenMedia,
  mediaHasMore,
  onLoadMoreMedia,
  loadingMoreMedia,
}: {
  overview: OverviewPayload
  aiCoverage: number
  completedMedia: number
  nsfwMedia: number
  queueCounts: QueueCounts
  topTags: Array<[string, number]>
  dragActive: boolean
  onDragOver: (event: DragEvent<HTMLDivElement>) => void
  onDragLeave: () => void
  onDrop: (event: DragEvent<HTMLDivElement>) => void
  onPick: (event: ChangeEvent<HTMLInputElement>) => void
  uploadProgress: number
  uploadPhaseLabel: string
  uploadPhaseValue: string
  searchInput: string
  onSearchChange: (value: string) => void
  kindFilter: string
  onKindFilterChange: (value: string) => void
  ratingFilter: string
  onRatingFilterChange: (value: string) => void
  statusFilter: string
  onStatusFilterChange: (value: string) => void
  loadingMedia: boolean
  media: MediaItem[]
  selectedMediaId: string | null
  token: string
  onOpenMedia: (item: MediaItem) => void
  mediaHasMore: boolean
  onLoadMoreMedia: () => void
  loadingMoreMedia: boolean
}) {
  const backlogCount = queueCounts.queued + queueCounts.processing
  return (
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
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
        >
          <div className="panel-head">
            <div><span>Загрузка</span><h2>Новый upload-движок</h2></div>
            <label className="primary-button file-button">Выбрать<input type="file" multiple onChange={onPick} /></label>
          </div>
          <p className="lede">Поддерживаются изображения, GIF, видео и архивы с вложенными папками. Загрузка идет chunk-ами в несколько потоков, а при обрыве можно продолжить с уже принятых частей.</p>
          <div className="dropzone-core">
            <strong>{dragActive ? 'Отпускайте файлы сюда' : 'Перетащите сюда архивы, видео, GIF или изображения'}</strong>
            <small>Временные части лежат в staging и автоматически очищаются после импорта или по TTL.</small>
          </div>
          <div className="progress-block">
            <div className="progress-track"><div className="progress-bar" style={{ width: `${uploadProgress}%` }} /></div>
            <div className="row-meta"><span>{uploadPhaseLabel}</span><strong>{uploadPhaseValue}</strong></div>
          </div>
        </section>
      </section>
      <section className="glass-panel filter-panel">
        <div className="panel-head"><div><span>Фильтры</span><h2>Поиск по памяти, тегам и AI-описанию</h2></div></div>
        <div className="filter-grid">
          <label className="filter-span-2">Запрос<input value={searchInput} onChange={(event) => onSearchChange(event.target.value)} placeholder="protogen, meme, red room, vertical video..." /></label>
          <label>Тип<select value={kindFilter} onChange={(event) => onKindFilterChange(event.target.value)}><option value="">Все</option><option value="image">Изображения</option><option value="gif">GIF</option><option value="video">Видео</option></select></label>
          <label>Safety<select value={ratingFilter} onChange={(event) => onRatingFilterChange(event.target.value)}><option value="">Все</option><option value="sfw">SFW</option><option value="questionable">Questionable</option><option value="nsfw">NSFW</option></select></label>
          <label>AI статус<select value={statusFilter} onChange={(event) => onStatusFilterChange(event.target.value)}><option value="">Все</option><option value="pending">pending</option><option value="processing">processing</option><option value="complete">complete</option><option value="failed">failed</option></select></label>
        </div>
      </section>
      <section className="glass-panel gallery-panel">
        <div className="panel-head">
          <div><span>Медиатека</span><h2>{loadingMedia && media.length === 0 ? 'Загружаем первую порцию' : `${media.length}${mediaHasMore ? '+' : ''} результатов`}</h2></div>
          <div className="chip-row"><span className="tag-chip">queued {queueCounts.queued}</span><span className="tag-chip">processing {queueCounts.processing}</span><span className="tag-chip">complete {queueCounts.complete}</span><span className="tag-chip">failed {queueCounts.failed}</span></div>
        </div>
        <div className="gallery-grid">
          {loadingMedia && media.length === 0 ? (
            <EmptyState title="Загружаем первую порцию медиа..." description="Список теперь приходит частями, поэтому интерфейс открывается быстрее даже на большой библиотеке." />
          ) : media.length ? (
            media.map((item) => <MediaCard key={item.id} item={item} token={token} active={selectedMediaId === item.id} onOpen={() => onOpenMedia(item)} />)
          ) : (
            <EmptyState title="Под текущие фильтры ничего не нашлось." description="Снимите часть фильтров или дождитесь, пока очередь доиндексирует свежие файлы." />
          )}
        </div>
        <LoadMoreRow
          visible={mediaHasMore}
          loading={loadingMoreMedia}
          buttonLabel="Загрузить еще"
          hint="Следующие карточки подгружаются отдельно, без ожидания всей библиотеки."
          onClick={onLoadMoreMedia}
        />
      </section>
    </div>
  )
}

export function FeedTab({
  feedFrom,
  onFeedFromChange,
  feedTo,
  onFeedToChange,
  onApplyPreset,
  loadingFeed,
  feedItems,
  feedHasMore,
  onLoadMoreFeed,
  loadingMoreFeed,
  token,
  onOpenMedia,
}: {
  feedFrom: string
  onFeedFromChange: (value: string) => void
  feedTo: string
  onFeedToChange: (value: string) => void
  onApplyPreset: (days: number | null) => void
  loadingFeed: boolean
  feedItems: MediaItem[]
  feedHasMore: boolean
  onLoadMoreFeed: () => void
  loadingMoreFeed: boolean
  token: string
  onOpenMedia: (item: MediaItem) => void
}) {
  return (
    <div className="tab-stack">
      <section className="glass-panel filter-panel">
        <div className="panel-head">
          <div><span>Период</span><h2>Лента по времени загрузки</h2></div>
          <div className="button-row">
            <button className="secondary-button" type="button" onClick={() => onApplyPreset(7)}>7 дней</button>
            <button className="secondary-button" type="button" onClick={() => onApplyPreset(30)}>30 дней</button>
            <button className="secondary-button" type="button" onClick={() => onApplyPreset(365)}>1 год</button>
            <button className="ghost-button" type="button" onClick={() => onApplyPreset(null)}>Весь архив</button>
          </div>
        </div>
        <div className="filter-grid">
          <label>От<input type="date" value={feedFrom} onChange={(event) => onFeedFromChange(event.target.value)} /></label>
          <label>До<input type="date" value={feedTo} onChange={(event) => onFeedToChange(event.target.value)} /></label>
          <div className="note-block filter-span-2">
            <span>Как это работает</span>
            <p>Лента теперь подгружается порциями, поэтому длинная история не блокирует интерфейс и не тянет сразу все медиа.</p>
          </div>
        </div>
      </section>
      <section className="glass-panel list-panel">
        <div className="panel-head">
          <div><span>Лента</span><h2>{loadingFeed && feedItems.length === 0 ? 'Загружаем первую порцию' : `${feedItems.length}${feedHasMore ? '+' : ''} элементов в выбранном диапазоне`}</h2></div>
        </div>
        <div className="feed-stack">
          {loadingFeed && feedItems.length === 0 ? (
            <EmptyState title="Собираем ленту..." description="Свежие записи подаются порциями, чтобы исторический фид не блокировал интерфейс." />
          ) : feedItems.length ? (
            feedItems.map((item) => <FeedCard key={item.id} item={item} token={token} onOpen={() => onOpenMedia(item)} />)
          ) : (
            <EmptyState title="В выбранном отрезке пока ничего нет." description="Расширьте диапазон дат или загрузите старые архивы еще раз, если хотите восстановить раннюю историю." />
          )}
        </div>
        <LoadMoreRow
          visible={feedHasMore}
          loading={loadingMoreFeed}
          buttonLabel="Показать еще"
          hint="Остальная история подтягивается по требованию, а не целиком."
          onClick={onLoadMoreFeed}
        />
      </section>
    </div>
  )
}

export function TagsTab({
  tagCatalog,
  leaderboardTags,
  backfillingTags,
  onBackfillTags,
  tagSearch,
  onTagSearchChange,
  tagKindFilter,
  onTagKindFilterChange,
  tagDescribedFilter,
  onTagDescribedFilterChange,
  selectedTag,
  selectedTagDetails,
  onSelectTag,
  onSelectLeaderboard,
}: {
  tagCatalog: TagCatalogPayload
  leaderboardTags: TagCatalogItem[]
  backfillingTags: boolean
  onBackfillTags: () => void
  tagSearch: string
  onTagSearchChange: (value: string) => void
  tagKindFilter: string
  onTagKindFilterChange: (value: string) => void
  tagDescribedFilter: string
  onTagDescribedFilterChange: (value: string) => void
  selectedTag: TagCatalogItem | null
  selectedTagDetails: Record<string, unknown> | null
  onSelectTag: (item: TagCatalogItem) => void
  onSelectLeaderboard: (item: TagCatalogItem) => void
}) {
  return (
    <div className="tab-stack">
      <section className="glass-panel hero-panel">
        <div className="panel-head">
          <div><span>Каталог</span><h2>AI-описания, свойства и лидерборд тегов</h2></div>
          <div className="button-row">
            <button className="secondary-button" type="button" onClick={onBackfillTags} disabled={backfillingTags}>
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
              onClick={() => onSelectLeaderboard(tag)}
            >
              {prettifyTag(tag.name)} · {tag.usage_count}
            </button>
          ))}
        </div>
      </section>
      <section className="glass-panel filter-panel">
        <div className="panel-head"><div><span>Фильтры</span><h2>Поиск по тегам и статусу описания</h2></div></div>
        <div className="filter-grid">
          <label className="filter-span-2">Поиск<input value={tagSearch} onChange={(event) => onTagSearchChange(event.target.value)} placeholder="boykisser, hollow_knight, meme..." /></label>
          <label>Kind<select value={tagKindFilter} onChange={(event) => onTagKindFilterChange(event.target.value)}><option value="">Все</option><option value="semantic">semantic</option><option value="technical">technical</option><option value="safety">safety</option></select></label>
          <label>Описание<select value={tagDescribedFilter} onChange={(event) => onTagDescribedFilterChange(event.target.value)}><option value="">Все</option><option value="true">Только описанные</option><option value="false">Только pending</option></select></label>
        </div>
      </section>
      <section className="tag-catalog-grid">
        <section className="glass-panel list-panel">
          <div className="panel-head"><div><span>Список</span><h2>{tagCatalog.items.length} тегов в выдаче</h2></div></div>
          <div className="list-stack">
            {tagCatalog.items.length ? tagCatalog.items.map((item) => <TagRow key={item.id} item={item} active={selectedTag?.id === item.id} onClick={() => onSelectTag(item)} />) : <EmptyState title="Теги по этому фильтру не найдены." description="Измените поиск или дождитесь, пока processor доопишет pending-теги." />}
          </div>
        </section>
        <section className="glass-panel tags-panel tag-detail-panel">
          <TagDetailsContent selectedTag={selectedTag} selectedTagDetails={selectedTagDetails} />
        </section>
      </section>
    </div>
  )
}

export function ProcessingTab({
  processingStats,
  backlogEtaSeconds,
  queueCounts,
  failedJobsTotal,
  queueFocus,
  jobs,
  retryingFailed,
  onRetryFailedJobs,
}: {
  processingStats: OverviewPayload['processing_stats']
  backlogEtaSeconds: number | null
  queueCounts: QueueCounts
  failedJobsTotal: number
  queueFocus: JobItem[]
  jobs: JobItem[]
  retryingFailed: boolean
  onRetryFailedJobs: () => void
}) {
  return (
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
            <button className="secondary-button" type="button" onClick={onRetryFailedJobs} disabled={retryingFailed || failedJobsTotal === 0}>
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
              <div>
                <strong>{job.media_id.slice(0, 8)}</strong>
                <small>{formatDate(job.created_at)}</small>
                {job.error_message ? <small className="error-text">{trimText(job.error_message, '', 120)}</small> : null}
              </div>
              <span className={`badge badge-status-${job.status}`}>{job.status}</span>
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}

export function BackupsTab({
  backups,
  onCreateBackup,
}: {
  backups: BackupItem[]
  onCreateBackup: (scope: 'metadata' | 'full') => void
}) {
  return (
    <div className="tab-stack split-stack">
      <section className="glass-panel action-panel">
        <div className="panel-head"><div><span>Бэкапы</span><h2>Telegram backup pipeline</h2></div></div>
        <p className="lede">Metadata-бэкап быстрый и легкий, full backup архивирует больше данных и режется на части для Telegram.</p>
        <div className="button-row">
          <button className="secondary-button" type="button" onClick={() => onCreateBackup('metadata')}>Metadata</button>
          <button className="primary-button" type="button" onClick={() => onCreateBackup('full')}>Full backup</button>
        </div>
      </section>
      <section className="glass-panel list-panel">
        <div className="panel-head"><div><span>История</span><h2>Последние backup-задачи</h2></div></div>
        <div className="list-stack">
          {backups.length ? backups.slice(0, 10).map((backup) => (
            <article key={backup.id} className="list-row">
              <div>
                <strong>{backup.scope}</strong>
                <small>{backup.parts.length} частей · {formatDate(backup.created_at)}</small>
                {backup.error_message ? <small className="error-text">{backup.error_message}</small> : null}
              </div>
              <span className={`badge badge-status-${backup.status}`}>{backup.status}</span>
            </article>
          )) : <EmptyState title="Бэкапов пока нет." description="Создайте metadata или full backup, чтобы увидеть историю задач." />}
        </div>
      </section>
    </div>
  )
}

export function ActivityTab({
  logs,
  topTags,
  onJumpToTag,
}: {
  logs: OverviewPayload['recent_logs']
  topTags: Array<[string, number]>
  onJumpToTag: (tag: string) => void
}) {
  return (
    <div className="tab-stack split-stack">
      <section className="glass-panel list-panel">
        <div className="panel-head"><div><span>Логи</span><h2>Последние события системы</h2></div></div>
        <div className="list-stack">
          {logs.length ? logs.slice(0, 12).map((log) => (
            <article key={log.id} className="list-row">
              <div>
                <strong>{log.event_type}</strong>
                <small>{trimText(log.message, '', 140)}</small>
                <small>{formatDate(log.created_at)}</small>
              </div>
              <span className={`badge badge-severity-${log.severity}`}>{log.severity}</span>
            </article>
          )) : <EmptyState title="Журнал пока пуст." description="Как только появятся операции и фоновые события, они будут видны здесь." />}
        </div>
      </section>
      <section className="glass-panel tags-panel">
        <div className="panel-head"><div><span>Теги</span><h2>Частые теги в текущей выборке</h2></div></div>
        <div className="chip-row spacious">
          {topTags.map(([tag, count]) => (
            <button key={tag} className="tag-chip" type="button" onClick={() => onJumpToTag(tag)}>
              {prettifyTag(tag)} · {count}
            </button>
          ))}
        </div>
      </section>
    </div>
  )
}

export function AdminTab({
  storage,
  driveUsagePercent,
  projectUsageTotal,
  driveBarSegments,
  projectBarSegments,
  orderedProjectBreakdown,
  aiProxySleep,
  memoryGuard,
  resumingAIProxy,
  onResumeAIProxy,
  reindexingAll,
  onReindexAllMedia,
  runtimeConfig,
  runtimeConfigForm,
  onRuntimeValueChange,
  onSaveRuntimeConfig,
  savingRuntimeConfig,
  users,
  newUserForm,
  onNewUserFormChange,
  onCreateUser,
  dangerConfirmation,
  onDangerConfirmationChange,
  onResetLibrary,
  resettingLibrary,
}: {
  storage: DiskUsagePayload | null
  driveUsagePercent: number
  projectUsageTotal: number
  driveBarSegments: StorageSegment[]
  projectBarSegments: StorageSegment[]
  orderedProjectBreakdown: Array<[string, number]>
  aiProxySleep: OverviewPayload['ai_proxy_sleep']
  memoryGuard: OverviewPayload['memory_guard']
  resumingAIProxy: boolean
  onResumeAIProxy: () => void
  reindexingAll: boolean
  onReindexAllMedia: () => void
  runtimeConfig: RuntimeConfigItem[]
  runtimeConfigForm: Record<string, string>
  onRuntimeValueChange: (key: string, value: string) => void
  onSaveRuntimeConfig: (event: FormEvent) => void
  savingRuntimeConfig: boolean
  users: User[]
  newUserForm: { username: string; password: string; telegram: string; role: 'admin' | 'member' }
  onNewUserFormChange: (value: { username: string; password: string; telegram: string; role: 'admin' | 'member' }) => void
  onCreateUser: (event: FormEvent) => void
  dangerConfirmation: string
  onDangerConfirmationChange: (value: string) => void
  onResetLibrary: (event: FormEvent) => void
  resettingLibrary: boolean
}) {
  return (
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
              {orderedProjectBreakdown.map(([name, value]) => (
                <article key={name} className="list-row">
                  <div><strong>{STORAGE_LABELS[name] ?? name}</strong><small>{formatBytes(value)}</small></div>
                  <span className="badge">{Math.max(Math.round((value / Math.max(projectUsageTotal || 1, 1)) * 100), 1)}%</span>
                </article>
              ))}
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
            <button className="secondary-button" type="button" onClick={onResumeAIProxy} disabled={resumingAIProxy || !aiProxySleep.active}>
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
          <button className="secondary-button" type="button" onClick={onReindexAllMedia} disabled={reindexingAll}>
            {reindexingAll ? 'Ставим в очередь...' : 'Переиндексировать всю библиотеку'}
          </button>
        </div>
        <form className="runtime-config-form" onSubmit={onSaveRuntimeConfig}>
          {runtimeConfig.map((item) => (
            <label key={item.key}>
              {item.label}
              {item.kind === 'boolean' ? (
                <select value={runtimeConfigForm[item.key] ?? configValueToInput(item.value)} onChange={(event) => onRuntimeValueChange(item.key, event.target.value)}>
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              ) : item.kind === 'enum' ? (
                <select value={runtimeConfigForm[item.key] ?? configValueToInput(item.value)} onChange={(event) => onRuntimeValueChange(item.key, event.target.value)}>
                  {item.choices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}
                </select>
              ) : (
                <input
                  type={item.kind === 'integer' ? 'number' : 'text'}
                  min={item.min ?? undefined}
                  max={item.max ?? undefined}
                  value={runtimeConfigForm[item.key] ?? configValueToInput(item.value)}
                  onChange={(event) => onRuntimeValueChange(item.key, event.target.value)}
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
        <form className="admin-form" onSubmit={onCreateUser}>
          <label>username<input value={newUserForm.username} onChange={(event) => onNewUserFormChange({ ...newUserForm, username: event.target.value })} required /></label>
          <label>password<input type="password" value={newUserForm.password} onChange={(event) => onNewUserFormChange({ ...newUserForm, password: event.target.value })} required /></label>
          <label>telegram<input value={newUserForm.telegram} onChange={(event) => onNewUserFormChange({ ...newUserForm, telegram: event.target.value })} placeholder="@username" /></label>
          <label>role<select value={newUserForm.role} onChange={(event) => onNewUserFormChange({ ...newUserForm, role: event.target.value as 'admin' | 'member' })}><option value="member">member</option><option value="admin">admin</option></select></label>
          <button className="primary-button" type="submit">Добавить пользователя</button>
        </form>
        <div className="list-stack">
          {users.map((user) => (
            <article key={user.id} className="list-row">
              <div><strong>{user.username}</strong><small>{user.telegram_username ? `@${user.telegram_username}` : 'Telegram не подключен'}</small></div>
              <span className={`badge badge-role-${user.role}`}>{user.role}</span>
            </article>
          ))}
        </div>
      </section>
      <section className="glass-panel admin-panel danger-panel">
        <div className="panel-head"><div><span>Danger Zone</span><h2>Полное удаление базы и всех медиа</h2></div></div>
        <div className="danger-copy">
          <p className="lede">Это удалит всю базу данных, все медиафайлы, архивы, превью, бэкапы, логи и пользователей. Действие необратимо.</p>
          <small>Если сейчас есть активные jobs, система сначала поставит processing на паузу и попросит повторить удаление после завершения текущих задач.</small>
        </div>
        <form className="danger-form" onSubmit={onResetLibrary}>
          <label>
            Введите <strong>DELETE EVERYTHING</strong> для подтверждения
            <input
              value={dangerConfirmation}
              onChange={(event) => onDangerConfirmationChange(event.target.value)}
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
  )
}

export function MediaViewerModal({
  selectedMedia,
  token,
  onClose,
  onReindex,
  safetyForm,
  onSafetyFormChange,
  onSaveSafety,
  savingSafety,
}: {
  selectedMedia: MediaItem | null
  token: string
  onClose: () => void
  onReindex: (mediaId: string) => void
  safetyForm: { rating: SafetyRating; tags: string }
  onSafetyFormChange: (value: { rating: SafetyRating; tags: string }) => void
  onSaveSafety: (event: FormEvent) => void
  savingSafety: boolean
}) {
  if (!selectedMedia) {
    return null
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal glass-panel" onClick={(event) => event.stopPropagation()}>
        <div className="panel-head">
          <div><span>{kindLabel(selectedMedia.kind)}</span><h2>{selectedMedia.original_filename}</h2></div>
          <div className="button-row">
            <button className="secondary-button" type="button" onClick={() => onReindex(selectedMedia.id)}>Reindex</button>
            <button className="ghost-button" type="button" onClick={onClose}>Close</button>
          </div>
        </div>
        <div className="modal-grid">
          <div className="modal-preview">
            {selectedMedia.kind === 'video'
              ? <video controls src={mediaAssetUrl(selectedMedia.file_url, token)} />
              : <img src={mediaAssetUrl(selectedMedia.file_url, token)} alt={selectedMedia.original_filename} />}
          </div>
          <div className="modal-copy">
            <div className="chip-row">
              <span className={`badge badge-${selectedMedia.safety_rating}`}>{ratingLabel(selectedMedia.safety_rating)}</span>
              <span className={`badge badge-status-${selectedMedia.processing_status}`}>{selectedMedia.processing_status}</span>
            </div>
            <form className="safety-form note-block" onSubmit={onSaveSafety}>
              <span>Safety moderation</span>
              <label>
                Rating
                <select value={safetyForm.rating} onChange={(event) => onSafetyFormChange({ ...safetyForm, rating: event.target.value as SafetyRating })}>
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
                  onChange={(event) => onSafetyFormChange({ ...safetyForm, tags: event.target.value })}
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
            <div className="chip-row spacious">{(selectedMedia.tags ?? []).map((tag) => <span key={`${selectedMedia.id}-${tag.kind}-${tag.name}`} className={`tag-chip tag-${tag.kind}`}>{prettifyTag(tag.name)}</span>)}</div>
          </div>
        </div>
      </div>
    </div>
  )
}

export function TagDetailsSheet({
  open,
  selectedTag,
  selectedTagDetails,
  onClose,
}: {
  open: boolean
  selectedTag: TagCatalogItem | null
  selectedTagDetails: Record<string, unknown> | null
  onClose: () => void
}) {
  if (!open || !selectedTag) {
    return null
  }

  return (
    <div className="tag-sheet-backdrop" onClick={onClose}>
      <div className="tag-sheet glass-panel" onClick={(event) => event.stopPropagation()}>
        <TagDetailsContent selectedTag={selectedTag} selectedTagDetails={selectedTagDetails} onClose={onClose} />
      </div>
    </div>
  )
}
