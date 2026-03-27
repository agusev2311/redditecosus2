import { type ChangeEvent, type DragEvent, type FormEvent, startTransition, useDeferredValue, useEffect, useRef, useState } from 'react'

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
  me,
  reindexMedia,
  reindexAllMedia,
  resetLibrary,
  resumeAIProxy,
  retryFailedJobs,
  triggerTagBackfill,
  updateMedia,
  updateRuntimeConfig,
  uploadFiles,
} from './api'
import type { BackupItem, DiskUsagePayload, JobItem, MediaItem, OverviewPayload, RuntimeConfigItem, SafetyRating, TagCatalogPayload, User } from './types'
import {
  ActivityTab,
  AdminTab,
  AppSidebar,
  AuthScreen,
  BackupsTab,
  FeedTab,
  LibraryTab,
  MediaViewerModal,
  ProcessingTab,
  TagsTab,
  TagDetailsSheet,
  WorkspaceAlerts,
  WorkspaceHeader,
} from './workspace-ui'
import {
  TOKEN_KEY,
  appendUniqueMedia,
  buildStorageSegments,
  buildUploadNotice,
  configValueToInput,
  emptyOverview,
  emptyTagCatalog,
  extractSafetyTags,
  formatDuration,
  formatMetric,
  isCompactScreen,
  orderedProjectBreakdown,
  parseTagInput,
  selectedTagDetails as getSelectedTagDetails,
  toDateInputString,
  topTagsFromMedia,
  workspaceTabs,
} from './workspace-helpers'

function App() {
  const [token, setToken] = useState<string>(() => localStorage.getItem(TOKEN_KEY) ?? '')
  const [currentUser, setCurrentUser] = useState<User | null>(null)
  const [needsBootstrap, setNeedsBootstrap] = useState<boolean | null>(null)
  const [overview, setOverview] = useState<OverviewPayload>(emptyOverview)
  const [media, setMedia] = useState<MediaItem[]>([])
  const [feedItems, setFeedItems] = useState<MediaItem[]>([])
  const [mediaCursor, setMediaCursor] = useState<string | null>(null)
  const [mediaHasMore, setMediaHasMore] = useState(false)
  const [feedCursor, setFeedCursor] = useState<string | null>(null)
  const [feedHasMore, setFeedHasMore] = useState(false)
  const [jobs, setJobs] = useState<JobItem[]>([])
  const [backups, setBackups] = useState<BackupItem[]>([])
  const [storage, setStorage] = useState<DiskUsagePayload | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfigItem[]>([])
  const [runtimeConfigForm, setRuntimeConfigForm] = useState<Record<string, string>>({})
  const [tagCatalog, setTagCatalog] = useState<TagCatalogPayload>(emptyTagCatalog)
  const [selectedTag, setSelectedTag] = useState<TagCatalogPayload['items'][number] | null>(null)
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
  const [loadingMedia, setLoadingMedia] = useState(false)
  const [loadingMoreMedia, setLoadingMoreMedia] = useState(false)
  const [loadingFeed, setLoadingFeed] = useState(false)
  const [loadingMoreFeed, setLoadingMoreFeed] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [authForm, setAuthForm] = useState({ username: '', password: '', telegram: '' })
  const [newUserForm, setNewUserForm] = useState({ username: '', password: '', telegram: '', role: 'member' as 'admin' | 'member' })
  const [activeTab, setActiveTab] = useState<'library' | 'feed' | 'tags' | 'processing' | 'backups' | 'activity' | 'admin'>('library')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [retryingFailed, setRetryingFailed] = useState(false)
  const [savingRuntimeConfig, setSavingRuntimeConfig] = useState(false)
  const [reindexingAll, setReindexingAll] = useState(false)
  const [resumingAIProxy, setResumingAIProxy] = useState(false)
  const [dangerConfirmation, setDangerConfirmation] = useState('')
  const [resettingLibrary, setResettingLibrary] = useState(false)
  const mediaLoadMoreCursorRef = useRef<string | null>(null)
  const feedLoadMoreCursorRef = useRef<string | null>(null)

  const deferredSearch = useDeferredValue(searchInput)
  const deferredTagSearch = useDeferredValue(tagSearch)
  const isAdmin = currentUser?.role === 'admin'

  const clearWorkspaceState = () => {
    setOverview(emptyOverview)
    setStorage(null)
    setUsers([])
    setRuntimeConfig([])
    setRuntimeConfigForm({})
    setMedia([])
    setFeedItems([])
    setMediaCursor(null)
    setMediaHasMore(false)
    setFeedCursor(null)
    setFeedHasMore(false)
    setLoadingMedia(false)
    setLoadingMoreMedia(false)
    setLoadingFeed(false)
    setLoadingMoreFeed(false)
    setTagCatalog(emptyTagCatalog)
    setSelectedTag(null)
    setSelectedMedia(null)
    setViewerOpen(false)
    setTagDetailOpen(false)
    setJobs([])
    setBackups([])
    setSearchInput('')
    setKindFilter('')
    setRatingFilter('')
    setStatusFilter('')
    setFeedFrom('')
    setFeedTo('')
    setTagSearch('')
    setTagKindFilter('')
    setTagDescribedFilter('')
    setActiveTab('library')
    setMobileSidebarOpen(false)
    setDragActive(false)
    mediaLoadMoreCursorRef.current = null
    feedLoadMoreCursorRef.current = null
  }

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
    void me(token).then((data) => setCurrentUser(data.user)).catch(() => {
      localStorage.removeItem(TOKEN_KEY)
      setToken('')
      setCurrentUser(null)
    })
  }, [token])

  useEffect(() => {
    if (!token || !currentUser) return
    let cancelled = false
    const loadOverviewData = async () => {
      try {
        const overviewPayload = await getOverview(token)
        if (!cancelled) {
          setOverview(overviewPayload)
        }
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'Failed to refresh dashboard')
        }
      }
    }
    void loadOverviewData()
    const timer = window.setInterval(() => void loadOverviewData(), 12000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [token, currentUser, refreshNonce])

  useEffect(() => {
    if (!token || !currentUser || activeTab !== 'library') return
    const controller = new AbortController()
    let cancelled = false
    mediaLoadMoreCursorRef.current = null
    setLoadingMedia(true)
    setLoadingMoreMedia(false)
    const loadLibrary = async () => {
      try {
        const payload = await listMedia(
          token,
          {
            q: deferredSearch || undefined,
            kind: kindFilter || undefined,
            rating: ratingFilter || undefined,
            status: statusFilter || undefined,
            limit: '48',
          },
          controller.signal,
        )
        if (cancelled) return
        startTransition(() => setMedia(payload.items))
        setMediaCursor(payload.next_cursor ?? null)
        setMediaHasMore(payload.has_more)
      } catch (reason) {
        if (!cancelled && !controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : 'Failed to load library')
        }
      } finally {
        if (!cancelled && !controller.signal.aborted) {
          setLoadingMedia(false)
        }
      }
    }
    void loadLibrary()
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [token, currentUser, activeTab, deferredSearch, kindFilter, ratingFilter, statusFilter, refreshNonce])

  useEffect(() => {
    if (!token || !currentUser || activeTab !== 'feed') return
    const controller = new AbortController()
    let cancelled = false
    feedLoadMoreCursorRef.current = null
    setLoadingFeed(true)
    setLoadingMoreFeed(false)
    const loadFeed = async () => {
      try {
        const payload = await listMedia(
          token,
          {
            created_from: feedFrom || undefined,
            created_to: feedTo || undefined,
            limit: '36',
          },
          controller.signal,
        )
        if (cancelled) return
        startTransition(() => setFeedItems(payload.items))
        setFeedCursor(payload.next_cursor ?? null)
        setFeedHasMore(payload.has_more)
      } catch (reason) {
        if (!cancelled && !controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : 'Failed to load feed')
        }
      } finally {
        if (!cancelled && !controller.signal.aborted) {
          setLoadingFeed(false)
        }
      }
    }
    void loadFeed()
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [token, currentUser, activeTab, feedFrom, feedTo, refreshNonce])

  useEffect(() => {
    if (!token || !currentUser || activeTab !== 'processing') return
    let cancelled = false
    const loadProcessing = async () => {
      try {
        const jobsPayload = await listJobs(token)
        if (!cancelled) {
          setJobs(jobsPayload.items)
        }
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'Failed to load processing queue')
        }
      }
    }
    void loadProcessing()
    const timer = window.setInterval(() => void loadProcessing(), 12000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [token, currentUser, activeTab, refreshNonce])

  useEffect(() => {
    if (!token || !currentUser || activeTab !== 'backups') return
    let cancelled = false
    void listBackups(token).then((payload) => {
      if (!cancelled) {
        setBackups(payload.items)
      }
    }).catch((reason) => {
      if (!cancelled) {
        setError(reason instanceof Error ? reason.message : 'Failed to load backups')
      }
    })
    return () => {
      cancelled = true
    }
  }, [token, currentUser, activeTab, refreshNonce])

  useEffect(() => {
    if (!token || !currentUser || activeTab !== 'tags') return
    let cancelled = false
    void listTags(token, {
      q: deferredTagSearch || undefined,
      kind: tagKindFilter || undefined,
      described: tagDescribedFilter || undefined,
      limit: '240',
    }).then((payload) => {
      if (!cancelled) {
        setTagCatalog(payload)
      }
    }).catch((reason) => {
      if (!cancelled) {
        setError(reason instanceof Error ? reason.message : 'Failed to load tags')
      }
    })
    return () => {
      cancelled = true
    }
  }, [token, currentUser, activeTab, deferredTagSearch, tagKindFilter, tagDescribedFilter, refreshNonce])

  useEffect(() => {
    if (!token || !currentUser || !isAdmin || activeTab !== 'admin') return
    let cancelled = false
    void Promise.all([getStorage(token), getUsers(token), getRuntimeConfig(token)]).then(([storagePayload, usersPayload, runtimeConfigPayload]) => {
      if (cancelled) return
      setStorage(storagePayload)
      setUsers(usersPayload.items)
      setRuntimeConfig(runtimeConfigPayload.items)
      setRuntimeConfigForm((current) => (
        Object.keys(current).length
          ? current
          : Object.fromEntries(runtimeConfigPayload.items.map((item) => [item.key, configValueToInput(item.value)]))
      ))
    }).catch((reason) => {
      if (!cancelled) {
        setError(reason instanceof Error ? reason.message : 'Failed to load admin dashboard')
      }
    })
    return () => {
      cancelled = true
    }
  }, [token, currentUser, isAdmin, activeTab, refreshNonce])

  useEffect(() => {
    if (isAdmin) return
    setStorage(null)
    setUsers([])
    setRuntimeConfig([])
    setRuntimeConfigForm({})
  }, [isAdmin])

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

  const handleLoadMoreMedia = async () => {
    if (!token || !mediaHasMore || !mediaCursor) return
    const requestedCursor = mediaCursor
    if (mediaLoadMoreCursorRef.current === requestedCursor) return
    mediaLoadMoreCursorRef.current = requestedCursor
    setLoadingMoreMedia(true)
    try {
      const payload = await listMedia(token, {
        q: deferredSearch || undefined,
        kind: kindFilter || undefined,
        rating: ratingFilter || undefined,
        status: statusFilter || undefined,
        limit: '48',
        cursor: requestedCursor,
      })
      startTransition(() => setMedia((current) => appendUniqueMedia(current, payload.items)))
      const cursorAdvanced = payload.next_cursor && payload.next_cursor !== requestedCursor
      setMediaCursor(cursorAdvanced ? (payload.next_cursor ?? null) : null)
      setMediaHasMore(Boolean(payload.has_more && cursorAdvanced))
    } catch (reason) {
      if (mediaLoadMoreCursorRef.current === requestedCursor) {
        mediaLoadMoreCursorRef.current = null
      }
      setError(reason instanceof Error ? reason.message : 'Failed to load more media')
    } finally {
      setLoadingMoreMedia(false)
    }
  }

  const handleLoadMoreFeed = async () => {
    if (!token || !feedHasMore || !feedCursor) return
    const requestedCursor = feedCursor
    if (feedLoadMoreCursorRef.current === requestedCursor) return
    feedLoadMoreCursorRef.current = requestedCursor
    setLoadingMoreFeed(true)
    try {
      const payload = await listMedia(token, {
        created_from: feedFrom || undefined,
        created_to: feedTo || undefined,
        limit: '36',
        cursor: requestedCursor,
      })
      startTransition(() => setFeedItems((current) => appendUniqueMedia(current, payload.items)))
      const cursorAdvanced = payload.next_cursor && payload.next_cursor !== requestedCursor
      setFeedCursor(cursorAdvanced ? (payload.next_cursor ?? null) : null)
      setFeedHasMore(Boolean(payload.has_more && cursorAdvanced))
    } catch (reason) {
      if (feedLoadMoreCursorRef.current === requestedCursor) {
        feedLoadMoreCursorRef.current = null
      }
      setError(reason instanceof Error ? reason.message : 'Failed to load more feed items')
    } finally {
      setLoadingMoreFeed(false)
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
        clearWorkspaceState()
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

  const handleSelectTag = (item: TagCatalogPayload['items'][number]) => {
    startTransition(() => {
      setSelectedTag(item)
      if (isCompactScreen()) {
        setTagDetailOpen(true)
      }
    })
  }

  const handleSelectLeaderboard = (item: TagCatalogPayload['items'][number]) => {
    handleSelectTag(item)
    startTransition(() => setTagSearch(item.name))
  }

  const openMedia = (item: MediaItem) => {
    startTransition(() => {
      setSelectedMedia(item)
      setViewerOpen(true)
    })
    void (async () => {
      if (!token) return
      try {
        const response = await getMedia(token, item.id)
        setMedia((current) => current.map((entry) => (entry.id === response.item.id ? { ...entry, ...response.item } : entry)))
        setFeedItems((current) => current.map((entry) => (entry.id === response.item.id ? { ...entry, ...response.item } : entry)))
        setSelectedMedia((current) => (current && current.id === item.id ? response.item : current))
      } catch (reason) {
        setError((current) => (current || (reason instanceof Error ? reason.message : 'Failed to load media details')))
      }
    })()
  }

  const handleActivityJumpToTag = (tag: string) => {
    startTransition(() => setSearchInput(tag.replaceAll('_', ' ')))
    setActiveTab('library')
  }

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY)
    setToken('')
    setCurrentUser(null)
    clearWorkspaceState()
    setNotice('')
    setError('')
  }

  const processingStats = overview.processing_stats
  const aiProxySleep = overview.ai_proxy_sleep
  const memoryGuard = overview.memory_guard
  const processor = overview.processor
  const queueCounts = {
    queued: processingStats.queued,
    processing: processingStats.processing,
    complete: processingStats.complete,
    failed: processingStats.failed,
  }
  const completedMedia = overview.counts.ai_ready
  const nsfwMedia = overview.counts.media_by_safety.nsfw
  const backlogCount = processingStats.queued + processingStats.processing
  const failedJobsTotal = processingStats.failed
  const backlogEtaSeconds = processingStats.avg_total_seconds && processingStats.workers ? Math.round((backlogCount * processingStats.avg_total_seconds) / Math.max(processingStats.workers, 1)) : null
  const aiCoverage = overview.counts.media ? Math.round((completedMedia / overview.counts.media) * 100) : 0
  const uploadPhaseLabel = uploading ? (uploadProgress >= 100 ? 'Сервер завершает импорт' : 'Идет загрузка') : 'Ожидание'
  const uploadPhaseValue = uploading ? (uploadProgress >= 100 ? 'Байты приняты' : `${uploadProgress}%`) : 'Ready'
  const processingStatusBanner = overview.processing_paused
    ? 'Обработка поставлена на паузу вручную. Новые задачи не стартуют, пока вы не снимете Processing paused в админке.'
    : memoryGuard.active
      ? `Обработка приостановлена memory guard: сейчас доступно ${formatMetric(memoryGuard.memory.available_mb)} MB. Автовозврат после подъема выше ${memoryGuard.resume_available_mb} MB.`
      : aiProxySleep.active
        ? `AI proxy cooldown активен до ${new Date(aiProxySleep.sleep_until ?? '').toLocaleString('ru-RU')}. Осталось ${formatDuration(aiProxySleep.remaining_seconds ?? 0)}.`
        : backlogCount > 0 && !processor.active
          ? 'Очередь стоит, потому что processor не подает heartbeat. Проверьте сервис processor в Docker и его логи.'
          : ''

  const driveUsagePercent = storage?.drive_total ? Math.round((storage.drive_used / storage.drive_total) * 100) : 0
  const projectUsageTotal = storage?.project.total ?? 0
  const orderedProjectItems = orderedProjectBreakdown(storage)
  const driveBarSegments = storage
    ? buildStorageSegments(
        [
          { key: 'media', bytes: storage.project.media ?? 0 },
          { key: 'archives', bytes: storage.project.archives ?? 0 },
          { key: 'thumbnails', bytes: storage.project.thumbnails ?? 0 },
          { key: 'backups', bytes: storage.project.backups ?? 0 },
          { key: 'database', bytes: storage.project.database ?? 0 },
          { key: 'logs', bytes: storage.project.logs ?? 0 },
          { key: 'incoming', bytes: storage.project.incoming ?? 0 },
          { key: 'other_on_drive', bytes: storage.other_on_drive },
          { key: 'free', bytes: storage.drive_free },
        ],
        storage.drive_total,
      )
    : []
  const projectBarSegments = projectUsageTotal
    ? buildStorageSegments(orderedProjectItems.map(([key, bytes]) => ({ key, bytes })), projectUsageTotal)
    : []
  const topTags = topTagsFromMedia(media)
  const leaderboardTags = tagCatalog.leaderboard.length ? tagCatalog.leaderboard : []
  const queueFocus = jobs.filter((job) => job.status === 'failed' || job.status === 'processing').slice(0, 8)
  const tabs = workspaceTabs(isAdmin)
  const currentTab = tabs.find((tab) => tab.id === activeTab) ?? tabs[0]
  const selectedTagPayload = getSelectedTagDetails(selectedTag)

  if (needsBootstrap === null) return <div className="loading-screen">Loading workspace...</div>

  if (!token || !currentUser) {
    return (
      <AuthScreen
        needsBootstrap={needsBootstrap}
        authForm={authForm}
        setAuthForm={setAuthForm}
        onSubmit={handleAuthSubmit}
        notice={notice}
        error={error}
      />
    )
  }

  return (
    <main className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''} ${mobileSidebarOpen ? 'sidebar-open' : ''}`}>
      <button className={`sidebar-backdrop ${mobileSidebarOpen ? 'visible' : ''}`} type="button" onClick={() => setMobileSidebarOpen(false)} aria-label="Close navigation" />
      <AppSidebar
        currentUser={currentUser}
        sidebarCollapsed={sidebarCollapsed}
        tabs={tabs}
        activeTab={activeTab}
        counts={overview.counts}
        aiCoverage={aiCoverage}
        backlogCount={backlogCount}
        nsfwMedia={nsfwMedia}
        onToggleCollapse={() => setSidebarCollapsed((value) => !value)}
        onCloseMobile={() => setMobileSidebarOpen(false)}
        onSelectTab={(tab) => {
          setActiveTab(tab)
          setMobileSidebarOpen(false)
        }}
        onRefresh={() => setRefreshNonce((value) => value + 1)}
        onLogout={logout}
      />
      <section className="app-main">
        <WorkspaceHeader currentTab={currentTab} mediaCount={overview.counts.media} aiCoverage={aiCoverage} backlogCount={backlogCount} onOpenSidebar={() => setMobileSidebarOpen(true)} />
        <WorkspaceAlerts error={error} notice={notice} warning={processingStatusBanner} />
        {activeTab === 'library' ? (
          <LibraryTab
            overview={overview}
            aiCoverage={aiCoverage}
            completedMedia={completedMedia}
            nsfwMedia={nsfwMedia}
            queueCounts={queueCounts}
            topTags={topTags}
            dragActive={dragActive}
            onDragOver={(event) => {
              event.preventDefault()
              setDragActive(true)
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={handleDrop}
            onPick={handlePick}
            uploadProgress={uploadProgress}
            uploadPhaseLabel={uploadPhaseLabel}
            uploadPhaseValue={uploadPhaseValue}
            searchInput={searchInput}
            onSearchChange={(value) => startTransition(() => setSearchInput(value))}
            kindFilter={kindFilter}
            onKindFilterChange={setKindFilter}
            ratingFilter={ratingFilter}
            onRatingFilterChange={setRatingFilter}
            statusFilter={statusFilter}
            onStatusFilterChange={setStatusFilter}
            loadingMedia={loadingMedia}
            media={media}
            selectedMediaId={selectedMedia?.id ?? null}
            token={token}
            onOpenMedia={openMedia}
            mediaHasMore={mediaHasMore}
            onLoadMoreMedia={() => void handleLoadMoreMedia()}
            loadingMoreMedia={loadingMoreMedia}
          />
        ) : null}
        {activeTab === 'feed' ? (
          <FeedTab
            feedFrom={feedFrom}
            onFeedFromChange={setFeedFrom}
            feedTo={feedTo}
            onFeedToChange={setFeedTo}
            onApplyPreset={applyFeedPreset}
            loadingFeed={loadingFeed}
            feedItems={feedItems}
            feedHasMore={feedHasMore}
            onLoadMoreFeed={() => void handleLoadMoreFeed()}
            loadingMoreFeed={loadingMoreFeed}
            token={token}
            onOpenMedia={openMedia}
          />
        ) : null}
        {activeTab === 'tags' ? (
          <TagsTab
            tagCatalog={tagCatalog}
            leaderboardTags={leaderboardTags}
            backfillingTags={backfillingTags}
            onBackfillTags={() => void handleBackfillTags()}
            tagSearch={tagSearch}
            onTagSearchChange={(value) => startTransition(() => setTagSearch(value))}
            tagKindFilter={tagKindFilter}
            onTagKindFilterChange={setTagKindFilter}
            tagDescribedFilter={tagDescribedFilter}
            onTagDescribedFilterChange={setTagDescribedFilter}
            selectedTag={selectedTag}
            selectedTagDetails={selectedTagPayload}
            onSelectTag={handleSelectTag}
            onSelectLeaderboard={handleSelectLeaderboard}
          />
        ) : null}
        {activeTab === 'processing' ? (
          <ProcessingTab
            processingStats={processingStats}
            backlogEtaSeconds={backlogEtaSeconds}
            queueCounts={queueCounts}
            failedJobsTotal={failedJobsTotal}
            queueFocus={queueFocus}
            jobs={jobs}
            retryingFailed={retryingFailed}
            onRetryFailedJobs={() => void handleRetryFailedJobs()}
          />
        ) : null}
        {activeTab === 'backups' ? (
          <BackupsTab backups={backups} onCreateBackup={(scope) => void handleCreateBackup(scope)} />
        ) : null}
        {activeTab === 'activity' ? (
          <ActivityTab logs={overview.recent_logs} topTags={topTags} onJumpToTag={handleActivityJumpToTag} />
        ) : null}
        {activeTab === 'admin' && currentUser.role === 'admin' ? (
          <AdminTab
            storage={storage}
            driveUsagePercent={driveUsagePercent}
            projectUsageTotal={projectUsageTotal}
            driveBarSegments={driveBarSegments}
            projectBarSegments={projectBarSegments}
            orderedProjectBreakdown={orderedProjectItems}
            aiProxySleep={aiProxySleep}
            memoryGuard={memoryGuard}
            resumingAIProxy={resumingAIProxy}
            onResumeAIProxy={() => void handleResumeAIProxy()}
            reindexingAll={reindexingAll}
            onReindexAllMedia={() => void handleReindexAllMedia()}
            runtimeConfig={runtimeConfig}
            runtimeConfigForm={runtimeConfigForm}
            onRuntimeValueChange={(key, value) => setRuntimeConfigForm((current) => ({ ...current, [key]: value }))}
            onSaveRuntimeConfig={handleSaveRuntimeConfig}
            savingRuntimeConfig={savingRuntimeConfig}
            users={users}
            newUserForm={newUserForm}
            onNewUserFormChange={setNewUserForm}
            onCreateUser={handleCreateUser}
            dangerConfirmation={dangerConfirmation}
            onDangerConfirmationChange={setDangerConfirmation}
            onResetLibrary={handleResetLibrary}
            resettingLibrary={resettingLibrary}
          />
        ) : null}
      </section>
      {viewerOpen ? (
        <MediaViewerModal
          selectedMedia={selectedMedia}
          token={token}
          onClose={() => setViewerOpen(false)}
          onReindex={(mediaId) => void handleReindex(mediaId)}
          safetyForm={safetyForm}
          onSafetyFormChange={setSafetyForm}
          onSaveSafety={handleSaveSafety}
          savingSafety={savingSafety}
        />
      ) : null}
      <TagDetailsSheet open={tagDetailOpen} selectedTag={selectedTag} selectedTagDetails={selectedTagPayload} onClose={() => setTagDetailOpen(false)} />
    </main>
  )
}

export default App
