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

export interface OverviewPayload {
  counts: {
    media: number
    users: number
    jobs: number
  }
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

