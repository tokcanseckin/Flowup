/**
 * SingoLing backend API client.
 *
 * All requests go to /api (proxied to http://localhost:8000 by Vite).
 * Uses typed interfaces that mirror the backend Pydantic response models.
 */

// ── Types ─────────────────────────────────────────────────────────────────────

export interface SongLanguage {
  code: string
  name: string
  script: string
  direction: 'ltr' | 'rtl'
}

export interface SongWord {
  key: number
  display_form: string
  lemma: string
  grammar: string | null
  dictionary_definition: string | null
}

export interface SongLine {
  id: number
  position: number
  start_time_ms: number
  end_time_ms: number
  original_line: string
  phonetic_line: string | null
  translation: string
  words: SongWord[]
  source?: string | null
}

export interface SongSummary {
  id: number
  spotify_uri: string
  title: string
  artist: string | null
  language_code: string
  language_name: string
  youtube_url: string | null
  apple_music_url: string | null
}

export interface SongDetail {
  id: number
  spotify_uri: string
  title: string
  artist: string | null
  language: SongLanguage
  lines: SongLine[]
  youtube_url: string | null
  apple_music_url: string | null
  target_langs: string[]
  lyrics_unlocked?: boolean  // Subscription access control
  upgrade_cta?: {
    title: string
    message: string
    cta: string
    url: string
    back_to_trial_url: string
    highlight_features: string[]
  }
}

export interface PlaylistSongEntry {
  position: number
  song_id: number
  spotify_uri: string
  title: string
  artist: string | null
  youtube_url: string | null
  apple_music_url: string | null
}

export interface PlaylistSummary {
  id: number
  spotify_playlist_id: string | null
  name: string
  description: string | null
  cover_image_url: string | null
  difficulty_level: string | null
  language_code: string | null
  target_lang: string | null
  target_langs: string[]
  is_hidden: boolean
  song_count: number
}

export interface PlaylistDetail extends PlaylistSummary {
  songs: PlaylistSongEntry[]
}

export interface BackendUser {
  id: number
  spotify_id: string
  display_name: string | null
  email: string | null
  has_password: boolean
  needs_onboarding: boolean
  is_admin: boolean
  spotify_enabled: boolean
  apple_music_user_token: string | null
  admin_token: string | null
  preferred_lang: string
  // Subscription fields
  subscription_tier: string
  subscription_status: string | null
  subscription_platform: string | null
  subscription_external_id: string | null
  subscription_started_at: string | null
  subscription_expires_at: string | null
  subscription_cancel_at_period_end: boolean
  original_platform: string | null
}

export interface PricingData {
  product_id: string
  product_name: string
  monthly: {
    id: string
    amount: number
    currency: string
  } | null
  annual: {
    id: string
    amount: number
    currency: string
  } | null
  lifetime: {
    id: string
    amount: number
    currency: string
  } | null
}

export interface AdminUser {
  id: number
  spotify_id: string
  display_name: string | null
  email: string | null
  has_password: boolean
  is_admin: boolean
  access_status: string
  created_at: number
}

export interface AdminSongDetail extends SongDetail {
  playlist_ids: number[]
  source_lines: { source: string; lines: SongLine[] }[]
}

export interface UserSettings {
  exclude_stop_words_from_shortcuts: boolean
  pause_on_inspect: boolean
  last_playlist_id: number | null
  last_song_id: number | null
  preferred_source: 'spotify' | 'youtube' | 'apple_music'
}

export interface WordLookupEntry {
  lemma: string
  language: string       // source language (song language code, e.g. "ru", "tr")
  target_lang: string    // definition language (e.g. "en", "tr")
  display_form: string
  definition: string | null
  grammar: string | null
  song_id: number | null
  looked_up_at: number   // Unix seconds
}

export interface LocalizationItem {
  key: string
  en: string
  tr: string
  ru: string
  es: string
  pt: string
  de: string
}

export interface ReportCreate {
  kind: string
  song_id?: number | null
  word?: string | null
  lemma?: string | null
  context?: string | null
  message?: string | null
}

export interface AdminReport {
  id: number
  kind: string
  user_id: number | null
  user_display_name: string | null
  song_id: number | null
  song_title: string | null
  word: string | null
  lemma: string | null
  context: string | null
  message: string | null
  created_at: number
  status: string
}

export interface AlignmentTask {
  id: number
  status: 'pending' | 'processing' | 'done' | 'failed'
  artist: string
  title: string
  display_title: string | null
  youtube_url: string
  lang: string
  spotify_uri: string | null
  target_lang: string
  plain_lyrics: string | null
  claimed_at: number | null
  completed_at: number | null
  result_lrc: string | null
  error: string | null
  created_at: number
}

const ADMIN_SESSION_KEY = 'flowup.admin.token.v2'

export function getAdminHeaders(): HeadersInit {
  if (typeof window === 'undefined') return {}
  const token = window.localStorage.getItem(ADMIN_SESSION_KEY)
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export function setAdminSession(token: string) {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(ADMIN_SESSION_KEY, token)
}

/** Returns auth headers for any authenticated user (uses stored admin_token from BackendUser). */
export function getUserAuthHeaders(): HeadersInit {
  if (typeof window === 'undefined') return {}
  // Try Bearer token first (stored at login for all users)
  const adminToken = window.localStorage.getItem(ADMIN_SESSION_KEY)
  if (adminToken) return { Authorization: `Bearer ${adminToken}` }
  // Fall back to stored user object's admin_token (non-admin users)
  try {
    const raw = window.localStorage.getItem('flowup.password_user.v1')
    if (raw) {
      const user = JSON.parse(raw) as { admin_token?: string | null }
      if (user.admin_token) return { Authorization: `Bearer ${user.admin_token}` }
    }
  } catch { /* ignore */ }
  return {}
}

export function clearAdminSession() {
  if (typeof window === 'undefined') return
  window.localStorage.removeItem(ADMIN_SESSION_KEY)
  // Also clear old Basic auth key if present
  window.localStorage.removeItem('flowup.admin.basic.v1')
}

// ── Client ────────────────────────────────────────────────────────────────────

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options?.headers ?? {}),
    },
  })
  if (!r.ok) {
    const body = await r.json().catch(() => ({ detail: r.statusText })) as { detail?: string }
    throw new Error(body.detail ?? `API error ${r.status}`)
  }
  if (r.status === 204 || r.headers.get('content-length') === '0') {
    return undefined as unknown as T
  }
  return r.json() as Promise<T>
}

export const api = {
  health: (): Promise<{ status: string }> =>
    apiFetch('/health'),

  getPricing: (): Promise<PricingData> =>
    apiFetch('/pricing'),

  syncSubscription: (): Promise<BackendUser> =>
    apiFetch('/sync-subscription', { method: 'POST' }),

  listSongs: (): Promise<SongSummary[]> =>
    apiFetch('/songs'),

  getSong: (id: number, source?: string, targetLang?: string): Promise<SongDetail> => {
    const params = new URLSearchParams()
    if (source && source !== 'spotify') params.set('source', source)
    if (targetLang) params.set('target_lang', targetLang)
    const qs = params.toString()
    return apiFetch(`/songs/${id}${qs ? `?${qs}` : ''}`)
  },

  listPlaylists: (targetLang?: string): Promise<PlaylistSummary[]> => {
    const qs = targetLang ? `?target_lang=${encodeURIComponent(targetLang)}` : ''
    return apiFetch(`/playlists${qs}`)
  },

  adminListPlaylists: (): Promise<PlaylistSummary[]> =>
    apiFetch('/admin/playlists', { headers: getAdminHeaders() }),

  getPlaylist: (id: number): Promise<PlaylistDetail> =>
    apiFetch(`/playlists/${id}`),

  createPlaylist: (body: {
    spotify_playlist_id?: string | null
    name: string
    description?: string | null
    difficulty_level?: string | null
    language_code?: string | null
    target_langs?: string[]
    is_hidden?: boolean
    song_ids?: number[]
  }): Promise<PlaylistDetail> =>
    apiFetch('/playlists', {
      method: 'POST',
      body: JSON.stringify(body),
      headers: getAdminHeaders(),
    }),

  updatePlaylist: (playlistId: number, body: {
    name?: string
    description?: string | null
    difficulty_level?: string | null
    language_code?: string | null
    target_langs?: string[]
    is_hidden?: boolean
  }): Promise<PlaylistDetail> =>
    apiFetch(`/playlists/${playlistId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
      headers: getAdminHeaders(),
    }),

  deletePlaylist: (playlistId: number): Promise<void> =>
    fetch(`/api/playlists/${playlistId}`, { method: 'DELETE', headers: getAdminHeaders() }).then(async r => {
      if (!r.ok) {
        const body = await r.json().catch(() => ({ detail: r.statusText })) as { detail?: string }
        throw new Error(body.detail ?? `API error ${r.status}`)
      }
    }),

  uploadPlaylistCover: (playlistId: number, file: File): Promise<void> => {
    const form = new FormData()
    form.append('file', file)
    return fetch(`/api/playlists/${playlistId}/cover`, {
      method: 'POST',
      headers: getAdminHeaders(),
      body: form,
    }).then(async r => {
      if (!r.ok) {
        const body = await r.json().catch(() => ({ detail: r.statusText })) as { detail?: string }
        throw new Error(body.detail ?? `API error ${r.status}`)
      }
    })
  },

  deletePlaylistCover: (playlistId: number): Promise<void> =>
    fetch(`/api/playlists/${playlistId}/cover`, { method: 'DELETE', headers: getAdminHeaders() }).then(async r => {
      if (!r.ok) {
        const body = await r.json().catch(() => ({ detail: r.statusText })) as { detail?: string }
        throw new Error(body.detail ?? `API error ${r.status}`)
      }
    }),

  syncUser: (body: {
    spotify_id: string
    display_name: string | null
    email: string | null
    access_token: string
    refresh_token: string
    expires_in: number
  }): Promise<BackendUser> =>
    apiFetch('/users/sync', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  loginWithEmailPassword: (body: {
    email: string
    password: string
  }): Promise<BackendUser> =>
    apiFetch('/auth/login', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  register: (body: {
    display_name: string
    email: string
    password: string
    lang?: string
  }): Promise<BackendUser> =>
    apiFetch('/auth/register', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  forgotPassword: (email: string): Promise<void> =>
    apiFetch('/auth/forgot-password', {
      method: 'POST',
      body: JSON.stringify({ email }),
    }),

  resetPassword: (token: string, password: string): Promise<void> =>
    apiFetch('/auth/reset-password', {
      method: 'POST',
      body: JSON.stringify({ token, password }),
    }),

  loginWithGoogle: (idToken: string, lang?: string): Promise<BackendUser> =>
    apiFetch('/auth/google', {
      method: 'POST',
      body: JSON.stringify({ id_token: idToken, lang }),
    }),

  loginWithApple: (idToken: string, lang?: string): Promise<BackendUser> =>
    apiFetch('/auth/apple', {
      method: 'POST',
      body: JSON.stringify({ id_token: idToken, lang }),
    }),

  completeOnboarding: (body: {
    spotify_id: string
    email: string
    password: string
  }): Promise<BackendUser> =>
    apiFetch('/auth/complete-onboarding', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getUserSettings: (): Promise<UserSettings> =>
    apiFetch('/me/settings', { headers: getUserAuthHeaders() }),

  updateUserSettings: (
    patch: Partial<UserSettings>,
  ): Promise<UserSettings> =>
    apiFetch('/me/settings', {
      method: 'PUT',
      body: JSON.stringify(patch),
      headers: getUserAuthHeaders(),
    }),

  saveAppleMusicToken: (token: string | null): Promise<void> =>
    apiFetch('/me/apple-music-token', {
      method: 'PUT',
      body: JSON.stringify({ token }),
      headers: getUserAuthHeaders(),
    }),

  updatePreferredLang: (lang: string): Promise<void> =>
    apiFetch('/me/lang', {
      method: 'PATCH',
      body: JSON.stringify({ lang }),
      headers: getUserAuthHeaders(),
    }),

  getWordLookups: (): Promise<WordLookupEntry[]> =>
    apiFetch('/me/word-lookups', { headers: getUserAuthHeaders() }),

  recordWordLookup: (entry: Omit<WordLookupEntry, 'looked_up_at'>): Promise<void> =>
    apiFetch('/me/word-lookups', {
      method: 'POST',
      body: JSON.stringify(entry),
      headers: getUserAuthHeaders(),
    }),

  updateSongSources: (
    songId: number,
    body: { youtube_url?: string | null; apple_music_url?: string | null },
  ): Promise<SongSummary> =>
    apiFetch(`/songs/${songId}/sources`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  bulkUpdateSongSources: (songs: Array<{
    spotify_id: string
    youtube_url?: string | null
    apple_music_url?: string | null
  }>): Promise<{ updated: number; not_found: string[] }> =>
    apiFetch('/songs/bulk-sources', {
      method: 'POST',
      body: JSON.stringify({ songs }),
    }),

  getAdminSong: (songId: number): Promise<AdminSongDetail> =>
    apiFetch(`/admin/songs/${songId}`, { headers: getAdminHeaders() }),

  createAdminSong: (body: {
    title: string
    artist?: string | null
    spotify_uri?: string | null
    language_code?: string
    language_name?: string
    language_script?: string
    language_direction?: string
    youtube_url?: string | null
    apple_music_url?: string | null
    playlist_ids?: number[]
  }): Promise<AdminSongDetail> =>
    apiFetch('/admin/songs', {
      method: 'POST',
      body: JSON.stringify(body),
      headers: getAdminHeaders(),
    }),

  deleteAdminSong: (songId: number): Promise<void> =>
    fetch(`/api/admin/songs/${songId}`, { method: 'DELETE', headers: getAdminHeaders() }).then(async r => {
      if (!r.ok) {
        const body = await r.json().catch(() => ({ detail: r.statusText })) as { detail?: string }
        throw new Error(body.detail ?? `API error ${r.status}`)
      }
    }),

  updateAdminSong: (songId: number, body: {
    title?: string
    artist?: string | null
    spotify_uri?: string
    youtube_url?: string | null
    apple_music_url?: string | null
    target_langs?: string[]
    playlist_ids?: number[]
  }): Promise<AdminSongDetail> =>
    apiFetch(`/admin/songs/${songId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
      headers: getAdminHeaders(),
    }),

  findYouTubeUrl: (songId: number): Promise<{ url: string | null }> =>
    apiFetch(`/admin/songs/${songId}/find-youtube`, {
      method: 'POST',
      headers: getAdminHeaders(),
    }),

  findAppleMusicUrl: (songId: number): Promise<{ url: string | null }> =>
    apiFetch(`/admin/songs/${songId}/find-apple-music`, {
      method: 'POST',
      headers: getAdminHeaders(),
    }),

  updateAdminLyrics: (songId: number, body: {
    lines: Array<{
      id: number
      position: number
      start_time_ms: number
      end_time_ms: number
      original_line: string
      phonetic_line: string | null
      translation: string
    }>
  }): Promise<AdminSongDetail> =>
    apiFetch(`/admin/songs/${songId}/lyrics`, {
      method: 'PUT',
      body: JSON.stringify(body),
      headers: getAdminHeaders(),
    }),

  updateSourceLyrics: (
    songId: number,
    source: string,
    lines: Array<{
      position: number
      start_time_ms: number
      end_time_ms: number
      original_line: string
      phonetic_line: string | null
      translation: string
    }>,
  ): Promise<AdminSongDetail> =>
    apiFetch(`/admin/songs/${songId}/source-lyrics?source=${encodeURIComponent(source)}`, {
      method: 'PUT',
      body: JSON.stringify({ lines }),
      headers: getAdminHeaders(),
    }),

  getSongTargetLangs: (songId: number): Promise<{ target_langs: string[] }> =>
    apiFetch(`/admin/songs/${songId}/target-langs`, { headers: getAdminHeaders() }),

  updateSongTranslations: (
    songId: number,
    targetLang: string,
    lines: Array<{ id: number; text: string }>,
  ): Promise<{ ok: boolean }> =>
    apiFetch(`/admin/songs/${songId}/translations?target_lang=${encodeURIComponent(targetLang)}`, {
      method: 'PUT',
      body: JSON.stringify({ lines }),
      headers: getAdminHeaders(),
    }),

  listAdminUsers: (): Promise<AdminUser[]> =>
    apiFetch('/admin/users', { headers: getAdminHeaders() }),

  getAdminUser: (userId: number): Promise<AdminUser> =>
    apiFetch(`/admin/users/${userId}`, { headers: getAdminHeaders() }),

  updateAdminUser: (userId: number, body: {
    display_name?: string | null
    email?: string | null
    is_admin?: boolean
    access_status?: string
    password?: string | null
  }): Promise<AdminUser> =>
    apiFetch(`/admin/users/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
      headers: getAdminHeaders(),
    }),

  addSongToPlaylist: (playlistId: number, body: { song_id: number; position?: number | null }): Promise<PlaylistDetail> =>
    apiFetch(`/playlists/${playlistId}/songs`, {
      method: 'POST',
      body: JSON.stringify(body),
      headers: getAdminHeaders(),
    }),

  removeSongFromPlaylist: (playlistId: number, songId: number): Promise<PlaylistDetail> =>
    apiFetch(`/playlists/${playlistId}/songs/${songId}`, {
      method: 'DELETE',
      headers: getAdminHeaders(),
    }),

  createAlignmentTask: (body: {
    artist: string
    title: string
    youtube_url: string
    lang?: string
    spotify_uri?: string | null
    target_lang?: string
    plain_lyrics?: string | null
    display_title?: string | null
  }): Promise<AlignmentTask> =>
    apiFetch('/admin/alignment-tasks', {
      method: 'POST',
      body: JSON.stringify(body),
      headers: getAdminHeaders(),
    }),

  listAlignmentTasks: (status?: string): Promise<AlignmentTask[]> =>
    apiFetch(`/admin/alignment-tasks${status ? `?status=${encodeURIComponent(status)}` : ''}`, {
      headers: getAdminHeaders(),
    }),

  getAlignmentTask: (id: number): Promise<AlignmentTask> =>
    apiFetch(`/admin/alignment-tasks/${id}`, { headers: getAdminHeaders() }),

  deleteAlignmentTask: (id: number): Promise<void> =>
    fetch(`/api/admin/alignment-tasks/${id}`, { method: 'DELETE', headers: getAdminHeaders() }).then(async r => {
      if (!r.ok) {
        const body = await r.json().catch(() => ({ detail: r.statusText })) as { detail?: string }
        throw new Error(body.detail ?? `API error ${r.status}`)
      }
    }),

  retryAlignmentTask: (id: number): Promise<AlignmentTask> =>
    apiFetch(`/admin/alignment-tasks/${id}/retry`, {
      method: 'PATCH',
      headers: getAdminHeaders(),
    }),

  getAppleMusicToken: (): Promise<{ token: string }> =>
    apiFetch('/apple-music/token'),

  getFavorites: (headers: HeadersInit): Promise<{ song_ids: number[] }> =>
    apiFetch('/me/favorites', { headers }),

  addFavorite: (songId: number, headers: HeadersInit): Promise<void> =>
    fetch(`/api/me/favorites/${songId}`, { method: 'POST', headers }).then(async r => {
      if (!r.ok && r.status !== 204) {
        const body = await r.json().catch(() => ({ detail: r.statusText })) as { detail?: string }
        throw new Error(body.detail ?? `API error ${r.status}`)
      }
    }),

  removeFavorite: (songId: number, headers: HeadersInit): Promise<void> =>
    fetch(`/api/me/favorites/${songId}`, { method: 'DELETE', headers }).then(async r => {
      if (!r.ok && r.status !== 204) {
        const body = await r.json().catch(() => ({ detail: r.statusText })) as { detail?: string }
        throw new Error(body.detail ?? `API error ${r.status}`)
      }
    }),

  getListened: (headers: HeadersInit): Promise<{ song_ids: number[] }> =>
    apiFetch('/me/listened', { headers }),

  addListened: (songId: number, headers: HeadersInit): Promise<void> =>
    fetch(`/api/me/listened/${songId}`, { method: 'POST', headers }).then(async r => {
      if (!r.ok && r.status !== 204) {
        const body = await r.json().catch(() => ({ detail: r.statusText })) as { detail?: string }
        throw new Error(body.detail ?? `API error ${r.status}`)
      }
    }),

  removeListened: (songId: number, headers: HeadersInit): Promise<void> =>
    fetch(`/api/me/listened/${songId}`, { method: 'DELETE', headers }).then(async r => {
      if (!r.ok && r.status !== 204) {
        const body = await r.json().catch(() => ({ detail: r.statusText })) as { detail?: string }
        throw new Error(body.detail ?? `API error ${r.status}`)
      }
    }),

  getLocalizations: (): Promise<LocalizationItem[]> =>
    apiFetch('/localizations'),

  adminGetLocalizations: (): Promise<LocalizationItem[]> =>
    apiFetch('/admin/localizations', { headers: getAdminHeaders() }),

  upsertLocalization: (key: string, body: { en: string; tr: string; ru: string; es: string; pt: string; de: string }): Promise<LocalizationItem> =>
    apiFetch(`/admin/localizations?key=${encodeURIComponent(key)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAdminHeaders() },
      body: JSON.stringify(body),
    }),

  updateLocalization: (key: string, body: { en: string; tr: string; ru: string; es: string; pt: string; de: string }): Promise<LocalizationItem> =>
    apiFetch(`/admin/localizations/${encodeURIComponent(key)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAdminHeaders() },
      body: JSON.stringify(body),
    }),

  deleteLocalization: (key: string): Promise<void> =>
    fetch(`/api/admin/localizations/${encodeURIComponent(key)}`, {
      method: 'DELETE',
      headers: getAdminHeaders(),
    }).then(async r => {
      if (!r.ok && r.status !== 204) {
        const body = await r.json().catch(() => ({ detail: r.statusText })) as { detail?: string }
        throw new Error(body.detail ?? `API error ${r.status}`)
      }
    }),

  // ── Reports ────────────────────────────────────────────────────────────────

  createReport: (body: ReportCreate): Promise<{ id: number }> =>
    apiFetch('/reports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getUserAuthHeaders() },
      body: JSON.stringify(body),
    }),

  listAdminReports: (status?: string): Promise<AdminReport[]> =>
    apiFetch(`/admin/reports${status ? `?status=${encodeURIComponent(status)}` : ''}`, {
      headers: getAdminHeaders(),
    }),

  updateReportStatus: (id: number, status: string): Promise<AdminReport> =>
    apiFetch(`/admin/reports/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...getAdminHeaders() },
      body: JSON.stringify({ status }),
    }),
}

