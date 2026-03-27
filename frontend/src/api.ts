import type { BackupItem, DangerResetResponse, DiskUsagePayload, JobItem, MediaItem, MediaListResponse, OverviewPayload, ReindexAllResponse, RetryFailedJobsResponse, RuntimeConfigItem, TagCatalogPayload, TriggerTagBackfillResponse, UploadResponse, User } from './types'

const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined) ??
  (import.meta.env.DEV
    ? 'http://127.0.0.1:5000'
    : typeof window !== 'undefined'
      ? window.location.origin
      : 'https://localhost')

function buildUrl(path: string, params?: Record<string, string | undefined>) {
  const url = new URL(path, API_BASE)
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value) {
        url.searchParams.set(key, value)
      }
    })
  }
  return url.toString()
}

async function request<T>(path: string, options: RequestInit = {}, token?: string, signal?: AbortSignal): Promise<T> {
  const headers = new Headers(options.headers)
  if (!headers.has('Content-Type') && options.body && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(buildUrl(path), {
    ...options,
    headers,
    signal,
  })

  if (!response.ok) {
    const fallback = `Request failed with ${response.status}`
    const text = await response.text()
    throw new Error(text || fallback)
  }

  return (await response.json()) as T
}

type UploadSessionState = {
  upload_id: string
  file_name: string
  file_size: number
  chunk_size: number
  total_parts: number
  uploaded_parts: number[]
  uploaded_bytes: number
  is_complete: boolean
}

const MB = 1024 * 1024
const MAX_PARALLEL_UPLOAD_FILES = 2
const MAX_PARALLEL_UPLOAD_PARTS = 4
const MAX_UPLOAD_RETRIES = 3

function resolveChunkSize(file: File) {
  if (file.size >= 1024 * MB) {
    return 16 * MB
  }
  if (file.size >= 256 * MB) {
    return 8 * MB
  }
  return 4 * MB
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

async function runWithConcurrency<T, R>(items: T[], concurrency: number, worker: (item: T, index: number) => Promise<R>) {
  if (items.length === 0) {
    return [] as R[]
  }

  const results = new Array<R>(items.length)
  let nextIndex = 0
  const workers = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (true) {
      const currentIndex = nextIndex
      nextIndex += 1
      if (currentIndex >= items.length) {
        return
      }
      results[currentIndex] = await worker(items[currentIndex], currentIndex)
    }
  })
  await Promise.all(workers)
  return results
}

function initUpload(token: string, file: File) {
  return request<{ upload: UploadSessionState }>(
    '/api/uploads/init',
    {
      method: 'POST',
      body: JSON.stringify({
        file_name: file.name,
        file_size: file.size,
        last_modified: file.lastModified || undefined,
        content_type: file.type || undefined,
        chunk_size: resolveChunkSize(file),
      }),
    },
    token,
  )
}

function completeUpload(token: string, uploadId: string) {
  return request<UploadResponse>(
    `/api/uploads/${uploadId}/complete`,
    {
      method: 'POST',
    },
    token,
  )
}

function sendChunk(
  token: string,
  uploadId: string,
  partIndex: number,
  payload: Blob,
  onProgress: (loaded: number) => void,
) {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', buildUrl(`/api/uploads/${uploadId}/parts/${partIndex}`))
    xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(event.loaded)
      }
    }
    xhr.onerror = () => reject(new Error('Chunk upload failed'))
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve()
      } else {
        reject(new Error(xhr.responseText || `Chunk upload failed with ${xhr.status}`))
      }
    }
    xhr.send(payload)
  })
}

type ProgressTracker = {
  addConfirmedBytes: (bytes: number) => void
  updateChunkProgress: (key: string, loaded: number) => void
  clearChunkProgress: (key: string) => void
}

async function uploadFileWithResume(token: string, file: File, tracker: ProgressTracker) {
  const { upload } = await initUpload(token, file)
  tracker.addConfirmedBytes(upload.uploaded_bytes)

  const uploadedParts = new Set(upload.uploaded_parts)
  const missingParts = Array.from({ length: upload.total_parts }, (_, partIndex) => partIndex).filter((partIndex) => !uploadedParts.has(partIndex))

  await runWithConcurrency(missingParts, MAX_PARALLEL_UPLOAD_PARTS, async (partIndex) => {
    const chunkStart = partIndex * upload.chunk_size
    const chunkEnd = Math.min(chunkStart + upload.chunk_size, file.size)
    const chunk = file.slice(chunkStart, chunkEnd)
    const chunkKey = `${upload.upload_id}:${partIndex}`

    for (let attempt = 0; attempt < MAX_UPLOAD_RETRIES; attempt += 1) {
      try {
        await sendChunk(token, upload.upload_id, partIndex, chunk, (loaded) => tracker.updateChunkProgress(chunkKey, loaded))
        tracker.clearChunkProgress(chunkKey)
        tracker.addConfirmedBytes(chunk.size)
        return
      } catch (error) {
        tracker.clearChunkProgress(chunkKey)
        if (attempt === MAX_UPLOAD_RETRIES - 1) {
          throw error
        }
        await sleep(400 * (attempt + 1))
      }
    }
  })

  return completeUpload(token, upload.upload_id)
}

export function mediaAssetUrl(path: string | null | undefined, token?: string) {
  if (!path) {
    return ''
  }
  const url = new URL(path, API_BASE)
  if (token) {
    url.searchParams.set('token', token)
  }
  return url.toString()
}

export function getBootstrapStatus() {
  return request<{ needs_bootstrap: boolean }>('/api/auth/bootstrap-status')
}

export function bootstrap(username: string, password: string, telegramUsername: string) {
  return request<{ token: string; user: User }>(
    '/api/auth/bootstrap',
    {
      method: 'POST',
      body: JSON.stringify({
        username,
        password,
        telegram_username: telegramUsername,
      }),
    },
  )
}

export function login(username: string, password: string) {
  return request<{ token: string; user: User }>(
    '/api/auth/login',
    {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    },
  )
}

export function me(token: string) {
  return request<{ user: User }>('/api/auth/me', {}, token)
}

export function getOverview(token: string) {
  return request<OverviewPayload>('/api/dashboard/overview', {}, token)
}

export function listMedia(token: string, params: Record<string, string | undefined>, signal?: AbortSignal) {
  const url = buildUrl('/api/media', params)
  return request<MediaListResponse>(url, {}, token, signal)
}

export function getMedia(token: string, mediaId: string, signal?: AbortSignal) {
  return request<{ item: MediaItem }>(`/api/media/${mediaId}`, {}, token, signal)
}

export function updateMedia(token: string, mediaId: string, payload: { description?: string; safety_rating?: string; safety_tags?: string[] }) {
  return request<{ item: MediaItem }>(
    `/api/media/${mediaId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(payload),
    },
    token,
  )
}

export function listJobs(token: string) {
  return request<{ items: JobItem[] }>('/api/jobs', {}, token)
}

export function retryFailedJobs(token: string) {
  return request<RetryFailedJobsResponse>(
    '/api/jobs/retry-failed',
    {
      method: 'POST',
    },
    token,
  )
}

export function listBackups(token: string) {
  return request<{ items: BackupItem[] }>('/api/backups', {}, token)
}

export function createBackup(token: string, scope: 'metadata' | 'full', sendToTelegram: boolean) {
  return request<{ backup_id: string }>(
    '/api/backups',
    {
      method: 'POST',
      body: JSON.stringify({ scope, send_to_telegram: sendToTelegram }),
    },
    token,
  )
}

export function getStorage(token: string) {
  return request<DiskUsagePayload>('/api/dashboard/storage', {}, token)
}

export function getUsers(token: string) {
  return request<{ items: User[] }>('/api/users', {}, token)
}

export function getRuntimeConfig(token: string) {
  return request<{ items: RuntimeConfigItem[] }>('/api/admin/runtime-config', {}, token)
}

export function updateRuntimeConfig(token: string, updates: Record<string, string | number | boolean>) {
  return request<{ items: RuntimeConfigItem[] }>(
    '/api/admin/runtime-config',
    {
      method: 'PATCH',
      body: JSON.stringify({ updates }),
    },
    token,
  )
}

export function reindexAllMedia(token: string) {
  return request<ReindexAllResponse>(
    '/api/admin/reindex-all',
    {
      method: 'POST',
    },
    token,
  )
}

export function resetLibrary(token: string, confirmation: string) {
  return request<DangerResetResponse>(
    '/api/admin/danger/reset-library',
    {
      method: 'POST',
      body: JSON.stringify({ confirmation }),
    },
    token,
  )
}

export function resumeAIProxy(token: string) {
  return request<{ ai_proxy_sleep: OverviewPayload['ai_proxy_sleep'] }>(
    '/api/admin/ai-proxy/resume',
    {
      method: 'POST',
    },
    token,
  )
}

export function createUser(token: string, payload: { username: string; password: string; role: 'admin' | 'member'; telegram_username: string }) {
  return request<{ user: User }>(
    '/api/users',
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
    token,
  )
}

export function listTags(token: string, params: Record<string, string | undefined>) {
  const url = buildUrl('/api/tags', params)
  return request<TagCatalogPayload>(url, {}, token)
}

export function triggerTagBackfill(token: string) {
  return request<TriggerTagBackfillResponse>(
    '/api/tags/backfill-missing',
    {
      method: 'POST',
    },
    token,
  )
}

export function reindexMedia(token: string, mediaId: string) {
  return request<{ job_id: string }>(
    `/api/media/${mediaId}/reindex`,
    {
      method: 'POST',
    },
    token,
  )
}

export async function uploadFiles(token: string, files: File[], onProgress: (progress: number) => void) {
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0)
  let confirmedBytes = 0
  const chunkProgress = new Map<string, number>()

  const updateProgress = () => {
    const inFlightBytes = Array.from(chunkProgress.values()).reduce((sum, value) => sum + value, 0)
    const totalLoaded = Math.min(confirmedBytes + inFlightBytes, totalBytes || 1)
    onProgress(Math.round((totalLoaded / Math.max(totalBytes, 1)) * 100))
  }

  const tracker: ProgressTracker = {
    addConfirmedBytes(bytes) {
      confirmedBytes += bytes
      updateProgress()
    },
    updateChunkProgress(key, loaded) {
      chunkProgress.set(key, loaded)
      updateProgress()
    },
    clearChunkProgress(key) {
      chunkProgress.delete(key)
      updateProgress()
    },
  }

  const results = await runWithConcurrency(files, MAX_PARALLEL_UPLOAD_FILES, (file) => uploadFileWithResume(token, file, tracker))
  onProgress(100)

  return results.reduce<UploadResponse>(
    (combined, result) => {
      combined.items.push(...result.items)
      combined.archives.push(...result.archives)
      return combined
    },
    { items: [], archives: [] },
  )
}
