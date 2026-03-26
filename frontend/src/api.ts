import type { BackupItem, DangerResetResponse, DiskUsagePayload, JobItem, MediaItem, OverviewPayload, ReindexAllResponse, RetryFailedJobsResponse, RuntimeConfigItem, UploadResponse, User } from './types'

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

async function request<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
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
  })

  if (!response.ok) {
    const fallback = `Request failed with ${response.status}`
    const text = await response.text()
    throw new Error(text || fallback)
  }

  return (await response.json()) as T
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

export function listMedia(token: string, params: Record<string, string | undefined>) {
  const url = buildUrl('/api/media', params)
  return request<{ items: MediaItem[] }>(url, {}, token)
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

export function reindexMedia(token: string, mediaId: string) {
  return request<{ job_id: string }>(
    `/api/media/${mediaId}/reindex`,
    {
      method: 'POST',
    },
    token,
  )
}

export function uploadFiles(token: string, files: File[], onProgress: (progress: number) => void) {
  return new Promise<UploadResponse>((resolve, reject) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))

    const xhr = new XMLHttpRequest()
    xhr.open('POST', buildUrl('/api/media/upload'))
    xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100))
      }
    }
    xhr.onerror = () => reject(new Error('Upload failed'))
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as UploadResponse)
      } else {
        reject(new Error(xhr.responseText || `Upload failed with ${xhr.status}`))
      }
    }
    xhr.send(form)
  })
}
