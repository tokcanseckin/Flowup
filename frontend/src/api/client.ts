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
}

export interface PlaylistSongEntry {
  position: number
  song_id: number
  spotify_uri: string
  title: string
  artist: string | null
}

export interface PlaylistSummary {
  id: number
  spotify_playlist_id: string | null
  name: string
  description: string | null
  difficulty_level: string | null
  language_code: string | null
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
}

export interface AdminUser {
  id: number
  spotify_id: string
  display_name: string | null
  email: string | null
  has_password: boolean
  is_admin: boolean
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

const ADMIN_SESSION_KEY = 'flowup.admin.basic.v1'

export function getAdminHeaders(): HeadersInit {
  if (typeof window === 'undefined') return {}
  const encoded = window.sessionStorage.getItem(ADMIN_SESSION_KEY)
  return encoded ? { Authorization: `Basic ${encoded}` } : {}
}

export function setAdminSession(email: string, password: string) {
  if (typeof window === 'undefined') return
  window.sessionStorage.setItem(ADMIN_SESSION_KEY, btoa(`${email}:${password}`))
}

export function clearAdminSession() {
  if (typeof window === 'undefined') return
  window.sessionStorage.removeItem(ADMIN_SESSION_KEY)
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
  return r.json() as Promise<T>
}

export const api = {
  health: (): Promise<{ status: string }> =>
    apiFetch('/health'),

  listSongs: (): Promise<SongSummary[]> =>
    apiFetch('/songs'),

  getSong: (id: number, source?: string): Promise<SongDetail> =>
    apiFetch(`/songs/${id}${source && source !== 'spotify' ? `?source=${encodeURIComponent(source)}` : ''}`),

  listPlaylists: (): Promise<PlaylistSummary[]> =>
    apiFetch('/playlists'),

  getPlaylist: (id: number): Promise<PlaylistDetail> =>
    apiFetch(`/playlists/${id}`),

  createPlaylist: (body: {
    spotify_playlist_id?: string | null
    name: string
    description?: string | null
    difficulty_level?: string | null
    language_code?: string | null
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

  loginWithGoogle: (idToken: string): Promise<BackendUser> =>
    apiFetch('/auth/google', {
      method: 'POST',
      body: JSON.stringify({ id_token: idToken }),
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

  getUserSettings: (spotifyId: string): Promise<UserSettings> =>
    apiFetch(`/users/${encodeURIComponent(spotifyId)}/settings`),

  updateUserSettings: (
    spotifyId: string,
    patch: Partial<UserSettings>,
  ): Promise<UserSettings> =>
    apiFetch(`/users/${encodeURIComponent(spotifyId)}/settings`, {
      method: 'PUT',
      body: JSON.stringify(patch),
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
    playlist_ids?: number[]
  }): Promise<AdminSongDetail> =>
    apiFetch(`/admin/songs/${songId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
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

  listAdminUsers: (): Promise<AdminUser[]> =>
    apiFetch('/admin/users', { headers: getAdminHeaders() }),

  getAdminUser: (userId: number): Promise<AdminUser> =>
    apiFetch(`/admin/users/${userId}`, { headers: getAdminHeaders() }),

  updateAdminUser: (userId: number, body: {
    display_name?: string | null
    email?: string | null
    is_admin?: boolean
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

  getAppleMusicToken: (): Promise<{ token: string }> =>
    apiFetch('/apple-music/token'),
}
