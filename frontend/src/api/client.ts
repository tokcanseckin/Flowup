/**
 * FlowUp backend API client.
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
  start_time_ms: number
  end_time_ms: number
  original_line: string
  phonetic_line: string | null
  translation: string
  words: SongWord[]
}

export interface SongSummary {
  id: number
  spotify_uri: string
  title: string
  artist: string | null
  language_code: string
  language_name: string
}

export interface SongDetail {
  id: number
  spotify_uri: string
  title: string
  artist: string | null
  language: SongLanguage
  lines: SongLine[]
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

  getSong: (id: number): Promise<SongDetail> =>
    apiFetch(`/songs/${id}`),

  listPlaylists: (): Promise<PlaylistSummary[]> =>
    apiFetch('/playlists'),

  getPlaylist: (id: number): Promise<PlaylistDetail> =>
    apiFetch(`/playlists/${id}`),

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
}
