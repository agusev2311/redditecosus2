export type UserRole = 'admin' | 'member'
export type MediaKind = 'image' | 'gif' | 'video'
export type SafetyRating = 'sfw' | 'questionable' | 'nsfw' | 'unknown'
export type ProcessingStatus = 'pending' | 'processing' | 'complete' | 'failed'

export interface User {
  id: number
  username: string
  role: UserRole
  telegram_username?: string | null
  created_at?: string | null
}

export interface MediaTag {
  name: string
  kind: 'semantic' | 'technical' | 'safety'
}

export interface MediaItem {
  id: string
  kind: MediaKind
  original_filename: string
  source_path?: string | null
  file_size: number
  width?: number | null
  height?: number | null
  duration_seconds?: number | null
  blur_score?: number | null
  safety_rating: SafetyRating
  description?: string | null
  description_ru?: string | null
  description_en?: string | null
  technical_notes?: string | null
  processing_status: ProcessingStatus
  normalized_timestamp?: string | null
  thumbnail_url?: string | null
  file_url: string
  ai_payload?: Record<string, unknown> | null
  created_at?: string | null
  tags?: MediaTag[]
}

export interface JobItem {
  id: string
  media_id: string
  status: 'queued' | 'processing' | 'complete' | 'failed'
  attempts: number
  error_message?: string | null
  created_at?: string | null
  completed_at?: string | null
}

export interface LogItem {
  id: number
  event_type: string
  severity: string
  message: string
  created_at?: string | null
}

export interface ProcessingStats {
  workers: number
  queued: number
  processing: number
  failed: number
  complete: number
  completed_last_24h: number
  failed_last_24h: number
  recent_failure_events: number
  throughput_per_hour_24h?: number | null
  avg_total_seconds?: number | null
  p95_total_seconds?: number | null
  avg_ai_seconds?: number | null
  p95_ai_seconds?: number | null
  avg_frames?: number | null
  avg_prompt_tokens?: number | null
  avg_completion_tokens?: number | null
  avg_reasoning_tokens?: number | null
  oldest_queued_seconds?: number | null
}

export interface OverviewPayload {
  counts: {
    media: number
    ai_ready: number
    media_by_kind: {
      image: number
      gif: number
      video: number
    }
    media_by_status: {
      pending: number
      processing: number
      complete: number
      failed: number
    }
    media_by_safety: {
      sfw: number
      questionable: number
      nsfw: number
      unknown: number
    }
    users: number
    jobs: number
  }
  processing_stats: ProcessingStats
  recent_logs: LogItem[]
  prompt_preview: string
}

export interface DiskUsagePayload {
  drive_total: number
  drive_free: number
  drive_used: number
  other_on_drive: number
  project: Record<string, number>
  per_user: Array<{ username: string; kind: string; bytes: number }>
}

export interface BackupItem {
  id: string
  scope: 'metadata' | 'full'
  status: 'queued' | 'running' | 'complete' | 'failed'
  parts: string[]
  manifest: Record<string, unknown>
  error_message?: string | null
  owner_id?: number | null
  created_at?: string | null
  completed_at?: string | null
}

export interface UploadResponse {
  items: MediaItem[]
  archives: Array<{ archive_id: string; media_ids: string[] }>
}

export interface RetryFailedJobsResponse {
  failed_jobs_total: number
  failed_media_total: number
  queued_jobs: number
  queued_media_ids: string[]
  skipped_active_media: number
  skipped_missing_media: number
}

export interface RuntimeConfigItem {
  key: string
  label: string
  description: string
  kind: 'string' | 'integer' | 'boolean' | 'enum' | 'timezone'
  value: string | number | boolean
  default: string | number | boolean
  min?: number | null
  max?: number | null
  choices: string[]
}

export interface ReindexAllResponse {
  total_media: number
  queued_jobs: number
  skipped_active_media: number
}

export interface DangerResetResponse {
  deleted: boolean
  paused: boolean
  processing_jobs: number
  queued_jobs: number
  media_count: number
  user_count: number
  message: string
  confirmation_phrase: string
}
