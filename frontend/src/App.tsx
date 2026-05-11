import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import AdminPanel          from './components/AdminPanel'
import LyricsPlayer         from './components/LyricsPlayer'
import singolingLogo from '../images/singoling_logo@2x.png'
import prevIconImg   from '../images/previous_icon@2x.png'
import nextIconImg   from '../images/next_icon@2x.png'
import YouTubePlayer, { YouTubePlayerHandle } from './components/YouTubePlayer'
import AppleMusicPlayer, { AppleMusicPlayerHandle, isAppleMusicAuthorized } from './components/AppleMusicPlayer'
import { api, BackendUser, PlaylistDetail, PlaylistSummary, SongDetail, SongSummary, UserSettings as ApiUserSettings, clearAdminSession, setAdminSession } from './api/client'

// ── Module-level song cache (survives re-renders, cleared on logout) ──────────
// Key: `{id}:{source}` where source is 'youtube' or 'apple_music'.
const _songCache = new Map<string, SongDetail>()
const _inFlight  = new Map<string, Promise<SongDetail>>()

function _songCacheKey(id: number, source?: string): string {
  return `${id}:${source ?? ''}`
}

/** Fetch a song, using the module-level cache. Deduplicates concurrent requests. */
function _fetchSong(id: number, source?: string): Promise<SongDetail> {
  const key = _songCacheKey(id, source)
  const cached = _songCache.get(key)
  if (cached) return Promise.resolve(cached)
  const inflight = _inFlight.get(key)
  if (inflight) return inflight
  const p = api.getSong(id, source).then(detail => {
    _songCache.set(key, detail)
    _inFlight.delete(key)
    return detail
  }).catch(err => {
    _inFlight.delete(key)
    throw err
  })
  _inFlight.set(key, p)
  return p
}

// ── Google Identity Services global type ──────────────────────────────────────
declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: {
            client_id: string
            callback: (response: { credential: string }) => void
            auto_select?: boolean
          }) => void
          renderButton: (
            parent: HTMLElement,
            options: { theme?: string; size?: string; width?: number; text?: string }
          ) => void
          disableAutoSelect: () => void
        }
      }
    }
  }
}

const PASSWORD_SESSION_KEY = 'flowup.password_user.v1'

interface AppSettings {
  excludeStopWordsFromShortcuts: boolean
  pauseOnInspect: boolean
  lastPlaylistId: number | null
  lastSongId: number | null
  preferredSource: 'youtube' | 'apple_music'
}

const DEFAULT_SETTINGS: AppSettings = {
  excludeStopWordsFromShortcuts: true,
  pauseOnInspect: true,
  lastPlaylistId: null,
  lastSongId: null,
  preferredSource: 'youtube',
}

function fromApiSettings(settings: ApiUserSettings): AppSettings {
  return {
    excludeStopWordsFromShortcuts: settings.exclude_stop_words_from_shortcuts,
    pauseOnInspect: settings.pause_on_inspect,
    lastPlaylistId: settings.last_playlist_id ?? null,
    lastSongId: settings.last_song_id ?? null,
    preferredSource: (settings.preferred_source as AppSettings['preferredSource']) ?? 'youtube',
  }
}

function toApiSettingsPatch(patch: Partial<AppSettings>): Partial<ApiUserSettings> {
  const out: Partial<ApiUserSettings> = {}
  if (patch.excludeStopWordsFromShortcuts !== undefined) {
    out.exclude_stop_words_from_shortcuts = patch.excludeStopWordsFromShortcuts
  }
  if (patch.pauseOnInspect !== undefined) {
    out.pause_on_inspect = patch.pauseOnInspect
  }
  if (patch.lastPlaylistId !== undefined) {
    out.last_playlist_id = patch.lastPlaylistId
  }
  if (patch.lastSongId !== undefined) {
    out.last_song_id = patch.lastSongId
  }
  if (patch.preferredSource !== undefined) {
    out.preferred_source = patch.preferredSource
  }
  return out
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatMs(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  return `${min}:${sec.toString().padStart(2, '0')}`
}

type SettingsTab = 'preferences' | 'account' | 'subscription' | 'support'

type AppRoute =
  | { page: 'browse' }
  | { page: 'playlist'; playlistId: number }
  | { page: 'song'; songId: number }
  | { page: 'settings'; tab: SettingsTab }
  | { page: 'admin'; tab: 'songs' | 'playlists' | 'users' | 'tasks'; id: number | null }

function parseAppRoute(pathname: string): AppRoute {
  const path = pathname || '/browse'

  const settingsMatch = path.match(/^\/settings(?:\/(preferences|account|subscription|support))?$/)
  if (settingsMatch) {
    const tab = (settingsMatch[1] as SettingsTab) ?? 'preferences'
    return { page: 'settings', tab }
  }
  const adminMatch = path.match(/^\/admin(?:\/(song|playlist|user|task)(?:\/(\d+))?)?$/)
  if (adminMatch) {
    const seg = adminMatch[1]
    const id = adminMatch[2] ? Number(adminMatch[2]) : null
    const tab = seg === 'playlist' ? 'playlists' : seg === 'user' ? 'users' : seg === 'task' ? 'tasks' : 'songs'
    return { page: 'admin', tab, id }
  }
  if (path === '/browse' || path === '/') return { page: 'browse' }

  const playlistMatch = path.match(/^\/playlist\/(\d+)$/)
  if (playlistMatch) {
    return { page: 'playlist', playlistId: Number(playlistMatch[1]) }
  }

  const songMatch = path.match(/^\/song\/(\d+)$/)
  if (songMatch) {
    return { page: 'song', songId: Number(songMatch[1]) }
  }

  return { page: 'browse' }
}

function playlistPath(playlistId: number): string {
  return `/playlist/${playlistId}`
}

function songPath(songId: number): string {
  return `/song/${songId}`
}

function adminPath(tab: 'songs' | 'playlists' | 'users' | 'tasks', id: number | null): string {
  const seg = tab === 'playlists' ? 'playlist' : tab === 'users' ? 'user' : tab === 'tasks' ? 'task' : 'song'
  if (id === null) return `/admin/${seg}`
  return `/admin/${seg}/${id}`
}

function settingsPath(tab: SettingsTab = 'preferences'): string {
  return `/settings/${tab}`
}

// ── Login screen ──────────────────────────────────────────────────────────────

function LoginScreen({
  onEmailLogin,
  onGoogleLogin,
  error,
  busy,
}: {
  onEmailLogin: (email: string, password: string) => Promise<void>
  onGoogleLogin: (credential: string) => Promise<void>
  error: string | null
  busy: boolean
}) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const googleBtnRef = useRef<HTMLDivElement>(null)

  const handleSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault()
    void onEmailLogin(email.trim(), password)
  }, [email, password, onEmailLogin])

  // Initialise Google Identity Services once the GSI script has loaded.
  useEffect(() => {
    const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined
    if (!clientId || !googleBtnRef.current) return

    const init = () => {
      if (!window.google || !googleBtnRef.current) return
      window.google.accounts.id.initialize({
        client_id: clientId,
        callback: (response) => { void onGoogleLogin(response.credential) },
        auto_select: false,
      })
      window.google.accounts.id.renderButton(googleBtnRef.current, {
        theme: 'filled_black',
        size: 'large',
        width: 360,
        text: 'continue_with',
      })
    }

    if (window.google) {
      init()
    } else {
      // Wait for the async GSI script to finish loading.
      const script = document.querySelector('script[src*="accounts.google.com/gsi"]')
      script?.addEventListener('load', init, { once: true })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="min-h-screen flex items-center justify-center p-4"
         style={{ background: 'radial-gradient(ellipse 120% 80% at 50% 110%, #1a1040 0%, #0d0d14 60%)' }}>
      <div className="w-full max-w-md">
        <div className="text-center mb-10">
          <div className="inline-flex items-center gap-2 mb-4">
            <div className="w-9 h-9 rounded-xl bg-indigo-600 flex items-center justify-center shadow-lg shadow-indigo-900/50">
              <svg viewBox="0 0 24 24" className="w-5 h-5 fill-white">
                <path d="M12 3a9 9 0 100 18A9 9 0 0012 3zm-1 13V8l6 4-6 4z"/>
              </svg>
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-white">
              Singo<span className="text-indigo-400">Ling</span>
            </h1>
          </div>
          <p className="text-gray-500 text-sm leading-relaxed max-w-xs mx-auto">
            Learn languages through music.<br/>
            Real lyrics. Real grammar. Real context.
          </p>
        </div>

        <div className="rounded-2xl border border-gray-800/80 p-8 shadow-2xl space-y-6"
             style={{ background: '#12121f' }}>
          <div>
            <h2 className="text-white font-semibold text-base mb-1">Sign in</h2>
            <p className="text-gray-500 text-sm leading-relaxed">
              {[
                (import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined) && 'Google',
                'email + password',
              ].filter(Boolean).join(', or ')}.
            </p>
          </div>

          {error && (
            <div className="mb-4 rounded-xl border border-red-900/50 bg-red-950/30 px-4 py-3 text-sm text-red-400">
              {error}
            </div>
          )}

          {/* Google Sign-In button — rendered by GSI SDK; hidden if no client ID */}
          {(import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined) && (
            <div className="flex justify-center">
              <div ref={googleBtnRef} />
            </div>
          )}

          <div className="flex items-center gap-3 text-xs text-gray-600">
            <div className="h-px flex-1 bg-gray-800" />
            <span>or</span>
            <div className="h-px flex-1 bg-gray-800" />
          </div>

          <form onSubmit={handleSubmit} className="space-y-3">
            <input
              type="email"
              required
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="Email"
              className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
            />
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="Password"
              className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
            />
            <button
              type="submit"
              disabled={busy}
              className="
                w-full py-2.5 rounded-xl font-semibold text-sm
                bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-800 disabled:text-gray-500
                text-white transition-all duration-150
              "
            >
              {busy ? 'Signing in…' : 'Continue with email'}
            </button>
          </form>

        </div>
      </div>
    </div>
  )
}

const SOURCE_OPTIONS: { value: AppSettings['preferredSource']; label: string; description: string }[] = [
  { value: 'youtube', label: 'YouTube', description: 'Embed YouTube videos when available' },
  { value: 'apple_music', label: 'Apple Music', description: 'Use Apple Music (requires subscription)' },
]

function SourcePicker({
  value,
  onChange,
}: {
  value: AppSettings['preferredSource']
  onChange: (v: AppSettings['preferredSource']) => void
}) {
  const options = SOURCE_OPTIONS
  return (
    <div className="space-y-2">
      {options.map(opt => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`
            w-full text-left rounded-xl border px-4 py-3 transition-all duration-150
            ${value === opt.value
              ? opt.value === 'youtube' ? 'border-red-500/60 bg-red-950/20' : 'border-white/60 bg-gray-800/30'
              : 'border-gray-700 bg-gray-900/40 hover:border-gray-600'}
          `}
        >
          <div className="flex items-center gap-3">
            {opt.value === 'youtube' ? (
              <svg viewBox="0 0 24 24" className={`w-5 h-5 shrink-0 fill-current transition-colors ${value === opt.value ? 'text-red-400' : 'text-gray-600'}`} aria-hidden>
                <path d="M21.58 7.19a2.8 2.8 0 0 0-1.97-1.98C17.86 4.75 12 4.75 12 4.75s-5.86 0-7.61.46A2.8 2.8 0 0 0 2.42 7.2 29.4 29.4 0 0 0 2 12a29.4 29.4 0 0 0 .42 4.81 2.8 2.8 0 0 0 1.97 1.98c1.75.46 7.61.46 7.61.46s5.86 0 7.61-.46a2.8 2.8 0 0 0 1.97-1.98A29.4 29.4 0 0 0 22 12a29.4 29.4 0 0 0-.42-4.81ZM10 15.5v-7l6 3.5-6 3.5Z" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" className={`w-5 h-5 shrink-0 fill-current transition-colors ${value === opt.value ? 'text-gray-200' : 'text-gray-600'}`} aria-hidden>
                <path d="M16.37 1.43c0 1.14-.47 2.24-1.22 3.04-.76.79-1.8 1.35-2.94 1.27-.15-1.09.36-2.23 1.09-3 .76-.8 2.01-1.37 3.07-1.31ZM19.08 17.22c-.42.97-.63 1.4-1.18 2.26-.77 1.2-1.86 2.7-3.21 2.71-1.2.01-1.5-.78-3.13-.77-1.62.01-1.95.79-3.15.78-1.35-.01-2.37-1.36-3.14-2.56-2.16-3.34-2.38-7.27-1.06-9.29.94-1.44 2.43-2.28 3.84-2.28 1.44 0 2.35.8 3.54.8 1.15 0 1.85-.8 3.53-.8 1.26 0 2.6.69 3.54 1.89-3.11 1.71-2.61 6.18.42 7.26Z" />
              </svg>
            )}
            <div>
              <p className="text-white text-sm font-medium">{opt.label}</p>
              <p className="text-gray-500 text-xs mt-0.5">{opt.description}</p>
            </div>
          </div>
        </button>
      ))}
    </div>
  )
}

// ── Song browser ──────────────────────────────────────────────────────────────

function SourceAvailabilityIcons({ song }: { song: SongSummary }) {
  const sources = [
    song.youtube_url ? { key: 'youtube', label: 'YouTube', className: 'text-red-500 bg-white' } : null,
    song.apple_music_url ? { key: 'apple_music', label: 'Apple Music', className: 'text-black bg-white' } : null,
  ].filter(Boolean) as { key: string; label: string; className: string }[]

  if (sources.length === 0) return null

  return (
    <div className="flex items-center gap-1.5 shrink-0" aria-label="Available sources">
      {sources.map(source => (
        <span
          key={source.key}
          title={source.label}
          aria-label={source.label}
          className={`inline-flex h-6 w-6 items-center justify-center rounded-full ${source.className}`}
        >
          {source.key === 'youtube' ? (
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 fill-current" aria-hidden>
              <path d="M21.58 7.19a2.8 2.8 0 0 0-1.97-1.98C17.86 4.75 12 4.75 12 4.75s-5.86 0-7.61.46A2.8 2.8 0 0 0 2.42 7.2 29.4 29.4 0 0 0 2 12a29.4 29.4 0 0 0 .42 4.81 2.8 2.8 0 0 0 1.97 1.98c1.75.46 7.61.46 7.61.46s5.86 0 7.61-.46a2.8 2.8 0 0 0 1.97-1.98A29.4 29.4 0 0 0 22 12a29.4 29.4 0 0 0-.42-4.81ZM10 15.5v-7l6 3.5-6 3.5Z" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 fill-current" aria-hidden>
              <path d="M16.37 1.43c0 1.14-.47 2.24-1.22 3.04-.76.79-1.8 1.35-2.94 1.27-.15-1.09.36-2.23 1.09-3 .76-.8 2.01-1.37 3.07-1.31ZM19.08 17.22c-.42.97-.63 1.4-1.18 2.26-.77 1.2-1.86 2.7-3.21 2.71-1.2.01-1.5-.78-3.13-.77-1.62.01-1.95.79-3.15.78-1.35-.01-2.37-1.36-3.14-2.56-2.16-3.34-2.38-7.27-1.06-9.29.94-1.44 2.43-2.28 3.84-2.28 1.44 0 2.35.8 3.54.8 1.15 0 1.85-.8 3.53-.8 1.26 0 2.6.69 3.54 1.89-3.11 1.71-2.61 6.18.42 7.26Z" />
            </svg>
          )}
        </span>
      ))}
    </div>
  )
}

function SongBrowser({
  songs, playlists, activePlaylistId, activePlaylist, loading, error, onSelect, onPrefetch, onSelectPlaylist, onLogout, onOpenSettings, onOpenAdmin, onOpenAccount, isAdmin, user, openedSongIds,
}: {
  songs: SongSummary[]
  playlists: PlaylistSummary[]
  activePlaylistId: number | null
  activePlaylist: PlaylistDetail | null
  loading: boolean
  error: string | null
  onSelect: (id: number) => void
  onPrefetch: (id: number) => void
  onSelectPlaylist: (id: number | null) => void
  onLogout: () => void
  onOpenSettings: () => void
  onOpenAdmin: () => void
  onOpenAccount: () => void
  isAdmin: boolean
  user: { display_name: string | null; email: string | null } | null
  openedSongIds: Set<number>
}) {
  const listenedCount = activePlaylist
    ? activePlaylist.songs.filter(s => openedSongIds.has(s.song_id)).length
    : 0

  const songList = (
    <>
      {error && (
        <div className="mb-4 rounded-xl border border-amber-900/50 bg-amber-950/20 px-4 py-3 text-sm text-amber-400">
          {error}
          <p className="mt-1 text-amber-600 text-xs">
            Make sure the backend is running:{' '}
            <code className="font-mono">cd backend && uvicorn main:app --reload</code>
          </p>
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-3 py-8 text-gray-600">
          <div className="w-5 h-5 border-2 border-gray-700 border-t-indigo-500 rounded-full animate-spin" />
          <span className="text-sm">Loading songs...</span>
        </div>
      ) : songs.length === 0 && !error ? (
        <div className="rounded-2xl border border-zinc-700/70 p-8 text-center" style={{ background: '#25262b' }}>
          <p className="text-gray-500 text-sm mb-2">No songs in the database yet.</p>
          <p className="text-gray-700 text-xs">
            Run the pipeline to add songs:<br/>
            <code className="font-mono text-gray-600">
              python pipeline/generate_song_data.py --api-url http://localhost:8000 ...
            </code>
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {songs.map(song => (
            <button
              key={song.id}
              onClick={() => onSelect(song.id)}
              onPointerEnter={() => onPrefetch(song.id)}
              className="
                w-full text-left rounded-2xl border border-zinc-700/70 p-4
                hover:border-indigo-700/60 hover:bg-indigo-950/10
                active:scale-[0.99] transition-all duration-150
              "
              style={{ background: '#25262b' }}
            >
              <div className="flex items-center gap-3">
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden className="shrink-0">
                  {openedSongIds.has(song.id)
                    ? <circle cx="6" cy="6" r="5" fill="none" stroke="#6366f1" strokeWidth="1.5" />
                    : <circle cx="6" cy="6" r="6" fill="#6366f1" />}
                </svg>
                <div className="min-w-0 flex-1">
                  <p className="text-white font-semibold truncate">{song.title}</p>
                  <p className="text-gray-500 text-sm truncate">{song.artist ?? 'Unknown artist'}</p>
                </div>
                <SourceAvailabilityIcons song={song} />
                <span className="text-[10px] font-mono font-medium text-indigo-400 bg-indigo-950/60 border border-indigo-900/50 px-1.5 py-0.5 rounded-md uppercase tracking-wider shrink-0">
                  {song.language_name}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
    </>
  )

  return (
    <div className="min-h-screen" style={{ background: '#050608' }}>
      {/* Header */}
      <header className="sticky top-0 z-20 border-b border-gray-900" style={{ background: '#050608' }}>
        <div className="max-w-[1200px] mx-auto w-full px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            {activePlaylist && (
              <button onClick={() => onSelectPlaylist(null)} className="text-gray-500 hover:text-gray-300 transition-colors mr-1" aria-label="Back to all playlists">
                <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
                  <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/>
                </svg>
              </button>
            )}
            <img src={singolingLogo} className="h-7 object-contain" alt="SingoLing" />
          </div>
          <div className="flex items-center gap-3">
            {isAdmin && (
              <button
                type="button"
                onClick={onOpenAdmin}
                className="text-xs text-amber-500 hover:text-amber-300 transition-colors"
              >
                Admin
              </button>
            )}
            <button
              type="button"
              onClick={onOpenSettings}
              className="text-xs text-gray-500 hover:text-gray-200 transition-colors"
            >
              Preferences
            </button>
            {user?.display_name && (
              <button type="button" onClick={onOpenAccount} className="text-xs text-gray-500 hover:text-gray-300 transition-colors">{user.display_name}</button>
            )}
            <button onClick={onLogout} className="text-xs text-gray-600 hover:text-gray-400 transition-colors">
              Sign out
            </button>
          </div>
        </div>
      </header>

      {/* Content */}
      <div className="px-4 pt-6 pb-10 max-w-[1200px] mx-auto">
        {activePlaylist ? (
          <div className="flex gap-8 items-start">
            {/* Left column — playlist detail */}
            <div className="w-72 shrink-0 sticky top-20">
              {/* Cover image */}
              {activePlaylist.cover_image_url ? (
                <img
                  src={activePlaylist.cover_image_url}
                  alt={activePlaylist.name}
                  className="w-full aspect-square rounded-2xl mb-5 object-cover"
                />
              ) : (
                <div
                  className="w-full aspect-square rounded-2xl mb-5 flex items-center justify-center select-none"
                  style={{ background: 'linear-gradient(135deg, #1e1b4b 0%, #312e81 50%, #1e1b4b 100%)' }}
                >
                  <span className="text-white/30 text-6xl font-bold uppercase">
                    {activePlaylist.name.charAt(0)}
                  </span>
                </div>
              )}

              {/* Title */}
              <h1 className="text-white font-bold text-xl leading-tight mb-1">{activePlaylist.name}</h1>

              {/* Badges */}
              <div className="flex flex-wrap gap-1.5 mb-3">
                {activePlaylist.language_code && (
                  <span className="text-[10px] font-mono font-medium text-indigo-400 bg-indigo-950/60 border border-indigo-900/50 px-1.5 py-0.5 rounded-md uppercase tracking-wider">
                    {activePlaylist.language_code}
                  </span>
                )}
                {activePlaylist.difficulty_level && (
                  <span className="text-[10px] font-medium text-emerald-400 bg-emerald-950/60 border border-emerald-900/50 px-1.5 py-0.5 rounded-md uppercase tracking-wider">
                    {activePlaylist.difficulty_level}
                  </span>
                )}
              </div>

              {/* Description */}
              {activePlaylist.description && (
                <p className="text-gray-400 text-sm leading-relaxed mb-4">{activePlaylist.description}</p>
              )}

              {/* Play button */}
              {songs.length > 0 && (
                <button
                  type="button"
                  onClick={() => onSelect(songs[0].id)}
                  className="w-full flex items-center justify-center gap-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 active:scale-[0.98] transition-all text-white font-medium text-sm py-2.5 mb-5"
                >
                  <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current shrink-0">
                    <path d="M8 5v14l11-7z"/>
                  </svg>
                  Play
                </button>
              )}

              {/* Stats */}
              <div className="rounded-2xl border border-zinc-700/70 divide-y divide-zinc-700/70" style={{ background: '#25262b' }}>
                <div className="px-4 py-3 flex items-center justify-between">
                  <span className="text-xs text-gray-500">Songs</span>
                  <span className="text-sm text-white font-medium">{activePlaylist.song_count}</span>
                </div>
                <div className="px-4 py-3 flex items-center justify-between">
                  <span className="text-xs text-gray-500">Listened</span>
                  <span className="text-sm text-white font-medium">{listenedCount}</span>
                </div>
                <div className="px-4 py-3 flex items-center justify-between">
                  <span className="text-xs text-gray-500">Words looked up</span>
                  <span className="text-sm text-gray-500 font-medium">–</span>
                </div>
              </div>

              {/* Playlist switcher */}
              {playlists.length > 1 && (
                <div className="mt-4">
                  <label className="text-xs text-gray-600 block mb-1.5" htmlFor="playlist-select-col">Switch playlist</label>
                  <select
                    id="playlist-select-col"
                    value={activePlaylistId ?? ''}
                    onChange={e => onSelectPlaylist(e.target.value ? Number(e.target.value) : null)}
                    className="w-full rounded-lg border border-gray-700 bg-gray-900/80 px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-indigo-500"
                  >
                    <option value="">All songs</option>
                    {playlists.map(pl => (
                      <option key={pl.id} value={pl.id}>
                        {pl.name} ({pl.song_count})
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>

            {/* Right column — song list */}
            <div className="flex-1 min-w-0">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="text-white font-semibold text-lg">Songs</h2>
              </div>
              {songList}
            </div>
          </div>
        ) : (
          <div className="max-w-2xl mx-auto">
            {playlists.length > 0 && (
              <div className="mb-6 grid grid-cols-1 gap-2">
                <p className="text-xs text-gray-600 uppercase tracking-wider font-medium mb-1">Playlists</p>
                {playlists.map(pl => (
                  <button
                    key={pl.id}
                    type="button"
                    onClick={() => onSelectPlaylist(pl.id)}
                    className="w-full text-left rounded-xl border border-zinc-700/70 px-4 py-3 hover:border-indigo-700/60 hover:bg-indigo-950/10 active:scale-[0.99] transition-all"
                    style={{ background: '#25262b' }}
                  >
                    <div className="flex items-center gap-3">
                      {pl.cover_image_url ? (
                        <img src={pl.cover_image_url} alt={pl.name} className="w-10 h-10 rounded-lg object-cover shrink-0" />
                      ) : (
                        <div className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0 select-none" style={{ background: 'linear-gradient(135deg, #1e1b4b 0%, #312e81 100%)' }}>
                          <span className="text-white/40 text-sm font-bold uppercase">{pl.name.charAt(0)}</span>
                        </div>
                      )}
                      <div className="min-w-0 flex-1">
                        <p className="text-white font-medium truncate">{pl.name}</p>
                        <p className="text-gray-500 text-xs truncate">{pl.song_count} songs{pl.language_code ? ` · ${pl.language_code.toUpperCase()}` : ''}{pl.difficulty_level ? ` · ${pl.difficulty_level}` : ''}</p>
                      </div>
                      <svg viewBox="0 0 24 24" className="w-4 h-4 fill-gray-600 shrink-0">
                        <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z"/>
                      </svg>
                    </div>
                  </button>
                ))}
              </div>
            )}
            {songList}
          </div>
        )}
      </div>
    </div>
  )
}

function SettingRow({
  title,
  description,
  value,
  onChange,
}: {
  title: string
  description: string
  value: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <div className="rounded-2xl border border-gray-800/80 p-4" style={{ background: '#12121f' }}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-white font-medium">{title}</p>
          <p className="text-xs text-gray-500 mt-1 leading-relaxed">{description}</p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={value}
          onClick={() => onChange(!value)}
          className={`
            shrink-0 inline-flex h-7 w-12 items-center rounded-full transition-colors
            ${value ? 'bg-green-500' : 'bg-gray-700'}
          `}
        >
          <span
            className={`
              inline-block h-5 w-5 rounded-full bg-white transition-transform
              ${value ? 'translate-x-6' : 'translate-x-1'}
            `}
          />
        </button>
      </div>
    </div>
  )
}

function SettingsPage({
  settings,
  onUpdate,
  onBack,
  onLogout,
  user,
  activeTab,
  onTabChange,
}: {
  settings: AppSettings
  onUpdate: (patch: Partial<AppSettings>) => void
  onBack: () => void
  onLogout: () => void
  user: { display_name: string | null; email: string | null } | null
  activeTab: SettingsTab
  onTabChange: (tab: SettingsTab) => void
}) {
  const [supportForm, setSupportForm] = useState({ subject: '', message: '' })
  const [supportSent, setSupportSent] = useState(false)

  const tabs: { key: SettingsTab; label: string; icon: React.ReactNode }[] = [
    {
      key: 'preferences',
      label: 'Preferences',
      icon: (
        <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current shrink-0">
          <path d="M19.14 12.94a7.43 7.43 0 000-1.88l2.03-1.58a.5.5 0 00.12-.64l-1.92-3.32a.5.5 0 00-.6-.22l-2.39.96a7.36 7.36 0 00-1.63-.94l-.36-2.54A.5.5 0 0013.9 2h-3.8a.5.5 0 00-.49.42l-.36 2.54a7.36 7.36 0 00-1.63.94l-2.39-.96a.5.5 0 00-.6.22L2.71 8.48a.5.5 0 00.12.64l2.03 1.58a7.43 7.43 0 000 1.88l-2.03 1.58a.5.5 0 00-.12.64l1.92 3.32a.5.5 0 00.6.22l2.39-.96c.5.39 1.05.71 1.63.94l.36 2.54a.5.5 0 00.49.42h3.8a.5.5 0 00.49-.42l.36-2.54c.58-.23 1.13-.55 1.63-.94l2.39.96a.5.5 0 00.6-.22l1.92-3.32a.5.5 0 00-.12-.64l-2.03-1.58zM12 15.5A3.5 3.5 0 1112 8a3.5 3.5 0 010 7.5z"/>
        </svg>
      ),
    },
    {
      key: 'account',
      label: 'Account',
      icon: (
        <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current shrink-0">
          <path d="M12 12c2.7 0 4.8-2.1 4.8-4.8S14.7 2.4 12 2.4 7.2 4.5 7.2 7.2 9.3 12 12 12zm0 2.4c-3.2 0-9.6 1.6-9.6 4.8v2.4h19.2v-2.4c0-3.2-6.4-4.8-9.6-4.8z"/>
        </svg>
      ),
    },
    {
      key: 'subscription',
      label: 'Subscription',
      icon: (
        <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current shrink-0">
          <path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H4v-6h16v6zm0-10H4V6h16v2z"/>
        </svg>
      ),
    },
    {
      key: 'support',
      label: 'Support',
      icon: (
        <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current shrink-0">
          <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 17h-2v-2h2v2zm2.07-7.75l-.9.92C13.45 12.9 13 13.5 13 15h-2v-.5c0-1.1.45-2.1 1.17-2.83l1.24-1.26c.37-.36.59-.86.59-1.41 0-1.1-.9-2-2-2s-2 .9-2 2H8c0-2.21 1.79-4 4-4s4 1.79 4 4c0 .88-.36 1.68-.93 2.25z"/>
        </svg>
      ),
    },
  ]

  return (
    <div className="h-screen flex flex-col overflow-hidden" style={{ background: '#050608' }}>
      <header className="sticky top-0 z-20 border-b border-gray-900 shrink-0" style={{ background: '#050608' }}>
        <div className="max-w-4xl mx-auto w-full px-4 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="text-gray-500 hover:text-gray-300 transition-colors mr-1" aria-label="Back">
            <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
              <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/>
            </svg>
          </button>
          <img src={singolingLogo} className="h-7 object-contain" alt="SingoLing" />
        </div>
        <div className="flex items-center gap-3">
          {user?.display_name && <button type="button" onClick={() => onTabChange('account')} className="text-xs text-gray-500 hover:text-gray-300 transition-colors">{user.display_name}</button>}
          <button onClick={onLogout} className="text-xs text-gray-600 hover:text-gray-400 transition-colors">Sign out</button>
        </div>
        </div>
      </header>

      <div className="flex flex-1 min-h-0 justify-center">
        <div className="flex w-full max-w-4xl min-h-0">
        {/* Left sidebar */}
        <nav className="w-48 shrink-0 border-r border-gray-900 py-4 px-2 flex flex-col gap-0.5" style={{ background: '#050608' }}>
          {tabs.map(t => (
            <button
              key={t.key}
              type="button"
              onClick={() => onTabChange(t.key)}
              className={`flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-sm transition-colors text-left ${
                activeTab === t.key
                  ? 'bg-gray-800 text-white'
                  : 'text-gray-500 hover:text-gray-300 hover:bg-gray-900'
              }`}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </nav>

        {/* Content */}
        <main className="flex-1 overflow-y-auto px-6 py-6 flex flex-col">
          {activeTab === 'preferences' && (
            <div className="max-w-xl w-full space-y-3">
              <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wider mb-4">Preferences</h2>
              <div className="rounded-2xl border border-gray-800/80 p-4" style={{ background: '#12121f' }}>
                <p className="text-white font-medium mb-1">Music source</p>
                <p className="text-xs text-gray-500 mb-3 leading-relaxed">Choose whether to use YouTube or Apple Music.</p>
                <SourcePicker value={settings.preferredSource} onChange={v => onUpdate({ preferredSource: v })} />
              </div>
              <SettingRow
                title="Prioritize content words for 1-9 shortcuts"
                description="When on, shortcut numbers skip common stop words (pronouns, prepositions, conjunctions) and target more meaningful words first."
                value={settings.excludeStopWordsFromShortcuts}
                onChange={(next) => onUpdate({ excludeStopWordsFromShortcuts: next })}
              />
              <SettingRow
                title="Pause playback while inspecting lyrics"
                description="When on, playback pauses while definition/translation panels are open and resumes when you close them."
                value={settings.pauseOnInspect}
                onChange={(next) => onUpdate({ pauseOnInspect: next })}
              />
            </div>
          )}

          {activeTab === 'account' && (
            <div className="max-w-xl w-full flex flex-col min-h-full">
              <div className="space-y-3 flex-1">
              <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wider mb-4">Account</h2>
              <div className="rounded-2xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                <div className="flex items-center gap-4 mb-5">
                  <div className="w-12 h-12 rounded-full bg-gray-700 flex items-center justify-center shrink-0">
                    <svg viewBox="0 0 24 24" className="w-6 h-6 fill-gray-400">
                      <path d="M12 12c2.7 0 4.8-2.1 4.8-4.8S14.7 2.4 12 2.4 7.2 4.5 7.2 7.2 9.3 12 12 12zm0 2.4c-3.2 0-9.6 1.6-9.6 4.8v2.4h19.2v-2.4c0-3.2-6.4-4.8-9.6-4.8z"/>
                    </svg>
                  </div>
                  <div>
                    <p className="text-white font-medium">{user?.display_name ?? 'Unknown user'}</p>
                    <p className="text-xs text-gray-500 mt-0.5">{user?.email ?? ''}</p>
                  </div>
                </div>
                <div className="border-t border-gray-800 pt-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2.5">
                      {/* Apple logo */}
                      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0 fill-gray-400">
                        <path d="M17.05 20.28c-.98.95-2.05.8-3.08.35-1.09-.46-2.09-.48-3.24 0-1.44.62-2.2.44-3.06-.35C2.79 15.25 3.51 7.7 9.05 7.4c1.4.07 2.38.79 3.19.8 1.21-.23 2.37-.97 3.67-.84 1.57.19 2.75.87 3.52 2.16-3.21 1.93-2.45 5.97.62 7.12-.58 1.53-1.34 3.05-3 3.64zM12.03 7.25c-.15-2.23 1.66-4.07 3.74-4.25.29 2.58-2.34 4.5-3.74 4.25z"/>
                      </svg>
                      <span className="text-sm text-gray-300">Apple Music</span>
                    </div>
                    {isAppleMusicAuthorized() ? (
                      <span className="text-xs text-green-400 font-medium">Connected</span>
                    ) : (
                      <span className="text-xs text-gray-500">Not connected</span>
                    )}
                  </div>
                </div>
              </div>
              </div>
              <div className="pt-4 pb-2">
                <button
                  type="button"
                  onClick={onLogout}
                  className="w-full rounded-xl border border-gray-700 px-4 py-2.5 text-sm text-gray-400 hover:text-white hover:border-gray-500 transition-colors text-left"
                >
                  Sign out
                </button>
              </div>
            </div>
          )}

          {activeTab === 'subscription' && (
            <div className="max-w-xl w-full space-y-3">
              <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wider mb-4">Subscription</h2>
              <div className="rounded-2xl border border-gray-800/80 p-8 flex flex-col items-center text-center" style={{ background: '#12121f' }}>
                <div className="w-14 h-14 rounded-2xl bg-gray-800 flex items-center justify-center mb-4">
                  <svg viewBox="0 0 24 24" className="w-7 h-7 fill-gray-500">
                    <path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H4v-6h16v6zm0-10H4V6h16v2z"/>
                  </svg>
                </div>
                <p className="text-white font-semibold mb-1">Subscription management</p>
                <p className="text-gray-500 text-sm leading-relaxed">Subscription details and billing will be available here soon.</p>
              </div>
            </div>
          )}

          {activeTab === 'support' && (
            <div className="max-w-xl w-full space-y-3">
              <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wider mb-4">Support</h2>
              {supportSent ? (
                <div className="rounded-2xl border border-gray-800/80 p-8 flex flex-col items-center text-center" style={{ background: '#12121f' }}>
                  <svg viewBox="0 0 24 24" className="w-10 h-10 fill-green-500 mb-3">
                    <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                  </svg>
                  <p className="text-white font-semibold mb-1">Message sent</p>
                  <p className="text-gray-500 text-sm">We'll get back to you as soon as possible.</p>
                  <button
                    type="button"
                    onClick={() => { setSupportSent(false); setSupportForm({ subject: '', message: '' }) }}
                    className="mt-4 text-xs text-gray-500 hover:text-gray-300 transition-colors"
                  >
                    Send another message
                  </button>
                </div>
              ) : (
                <div className="rounded-2xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                  <p className="text-white font-medium mb-1">Contact us</p>
                  <p className="text-xs text-gray-500 mb-4 leading-relaxed">Have a question or found an issue? We're happy to help.</p>
                  <div className="space-y-3">
                    <div>
                      <label className="block text-xs text-gray-500 mb-1.5" htmlFor="support-subject">Subject</label>
                      <input
                        id="support-subject"
                        type="text"
                        value={supportForm.subject}
                        onChange={e => setSupportForm(f => ({ ...f, subject: e.target.value }))}
                        placeholder="e.g. Bug report, Feature request…"
                        className="w-full rounded-xl border border-gray-700 bg-gray-900/60 px-3 py-2.5 text-sm text-white placeholder-gray-600 outline-none focus:border-gray-500 transition-colors"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-gray-500 mb-1.5" htmlFor="support-message">Message</label>
                      <textarea
                        id="support-message"
                        rows={5}
                        value={supportForm.message}
                        onChange={e => setSupportForm(f => ({ ...f, message: e.target.value }))}
                        placeholder="Describe your issue or question…"
                        className="w-full rounded-xl border border-gray-700 bg-gray-900/60 px-3 py-2.5 text-sm text-white placeholder-gray-600 outline-none focus:border-gray-500 transition-colors resize-none"
                      />
                    </div>
                    <button
                      type="button"
                      disabled={!supportForm.subject.trim() || !supportForm.message.trim()}
                      onClick={() => setSupportSent(true)}
                      className="w-full rounded-xl bg-white text-black text-sm font-medium py-2.5 hover:bg-gray-100 disabled:bg-gray-800 disabled:text-gray-500 transition-colors"
                    >
                      Send message
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </main>
        </div>{/* end max-width wrapper */}
      </div>
    </div>
  )
}

// ── Progress bar ──────────────────────────────────────────────────────────────

function ProgressBar({ posMs, durMs, onSeek }: { posMs: number; durMs: number; onSeek: (ms: number) => void }) {
  const pct = durMs > 0 ? Math.min((posMs / durMs) * 100, 100) : 0
  const handleClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    onSeek(Math.floor(frac * durMs))
  }, [durMs, onSeek])
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs font-mono text-zinc-400 w-10 text-right shrink-0">{formatMs(posMs)}</span>
      <div className="flex-1 h-2 bg-black rounded-full cursor-pointer group" onClick={handleClick}>
        <div className="h-full bg-zinc-100 rounded-full relative transition-all duration-100 group-hover:bg-white"
             style={{ width: `${pct}%` }}>
          <div className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-white opacity-0 group-hover:opacity-100 translate-x-1/2 transition-opacity" />
        </div>
      </div>
      <span className="text-xs font-mono text-zinc-400 w-10 shrink-0">{formatMs(durMs)}</span>
    </div>
  )
}

// ── Album-art color extraction ──────────────────────────────────────────────

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255
  const max = Math.max(r, g, b), min = Math.min(r, g, b)
  let h = 0, s = 0
  const l = (max + min) / 2
  if (max !== min) {
    const d = max - min
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
    switch (max) {
      case r: h = ((g - b) / d + (g < b ? 6 : 0)) / 6; break
      case g: h = ((b - r) / d + 2) / 6;                break
      case b: h = ((r - g) / d + 4) / 6;                break
    }
  }
  return [Math.round(h * 360), Math.round(s * 100), Math.round(l * 100)]
}

function toPaletteSampleUrl(rawUrl: string | null): string | null {
  if (!rawUrl) return null
  if (rawUrl.startsWith('/api/image-proxy?url=')) return rawUrl
  if (rawUrl.startsWith('data:') || rawUrl.startsWith('blob:')) return rawUrl

  try {
    const parsed = new URL(rawUrl, window.location.origin)
    if (parsed.origin === window.location.origin) return parsed.toString()
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return `/api/image-proxy?url=${encodeURIComponent(parsed.toString())}`
    }
    return rawUrl
  } catch {
    return rawUrl
  }
}

function useAlbumLyricsTheme(albumArtUrl: string | null): [{ panelGradient: string; asideGradient: string; accentTextColor: string }] {
  const [theme, setTheme] = useState({
    panelGradient: 'linear-gradient(180deg, hsl(215, 64%, 26%) 0%, hsl(215, 60%, 17%) 100%)',
    asideGradient: 'linear-gradient(180deg, hsl(215, 64%, 17%) 0%, hsl(215, 60%, 11%) 100%)',
    accentTextColor: 'hsl(320, 88%, 38%)',
  })
  const requestSeqRef = useRef(0)

  useEffect(() => {
    const reqId = ++requestSeqRef.current
    const applyTheme = (next: { panelGradient: string; asideGradient: string; accentTextColor: string }) => {
      if (requestSeqRef.current !== reqId) return
      setTheme(next)
    }

    const sampleUrl = toPaletteSampleUrl(albumArtUrl)
    if (!sampleUrl) {
      applyTheme({
        panelGradient: 'linear-gradient(180deg, hsl(215, 64%, 26%) 0%, hsl(215, 60%, 17%) 100%)',
        asideGradient: 'linear-gradient(180deg, hsl(215, 64%, 17%) 0%, hsl(215, 60%, 11%) 100%)',
        accentTextColor: 'hsl(320, 88%, 38%)',
      })
      return
    }

    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      try {
        const SIZE = 48
        const canvas = document.createElement('canvas')
        canvas.width = canvas.height = SIZE
        const ctx = canvas.getContext('2d')
        if (!ctx) return
        ctx.drawImage(img, 0, 0, SIZE, SIZE)
        let data: Uint8ClampedArray
        try {
          data = ctx.getImageData(0, 0, SIZE, SIZE).data
        } catch (err) {
          console.error('Palette extraction getImageData error:', err)
          applyTheme({
            panelGradient: 'linear-gradient(180deg, hsl(215, 64%, 26%) 0%, hsl(215, 60%, 17%) 100%)',
            asideGradient: 'linear-gradient(180deg, hsl(215, 64%, 17%) 0%, hsl(215, 60%, 11%) 100%)',
            accentTextColor: 'hsl(320, 88%, 38%)',
          })
          return
        }

        // Two-pass: always find the most vivid pixel available.
        // Pass 1: prefer pixels with decent saturation and mid lightness.
        // Pass 2 (fallback): take the most saturated pixel from ALL pixels.
        let bestVividScore = -1
        let bestVividH = 0, bestVividS = 0, bestVividL = 0
        let bestAnyScore = -1
        let bestAnyH = 215, bestAnyS = 0, bestAnyL = 35
        let totalR = 0, totalG = 0, totalB = 0, totalCount = 0

        for (let i = 0; i < data.length; i += 4) {
          const r = data[i]
          const g = data[i + 1]
          const b = data[i + 2]
          const a = data[i + 3]
          if (a < 32) continue
          const [h, s, l] = rgbToHsl(r, g, b)
          totalR += r; totalG += g; totalB += b; totalCount++

          // "Any pixel" best: most saturated, favoring mid-lightness over extremes
          const anyScore = s * 2 + Math.min(l, 100 - l)
          if (anyScore > bestAnyScore) {
            bestAnyScore = anyScore
            bestAnyH = h; bestAnyS = s; bestAnyL = l
          }

          // "Vivid" best: must be reasonably saturated and not near-black/near-white
          if (s >= 20 && l >= 15 && l <= 88) {
            const vividScore = s * 3 + Math.min(l, 85) * 0.4
            if (vividScore > bestVividScore) {
              bestVividScore = vividScore
              bestVividH = h; bestVividS = s; bestVividL = l
            }
          }
        }

        let chosenHue: number, chosenSat: number, chosenLight: number
        if (bestVividScore >= 0) {
          // Found a vivid pixel — use it
          chosenHue = bestVividH; chosenSat = bestVividS; chosenLight = bestVividL
        } else if (bestAnyScore >= 0) {
          // No vivid pixel, but use most saturated overall
          chosenHue = bestAnyH; chosenSat = bestAnyS; chosenLight = bestAnyL
        } else if (totalCount > 0) {
          // Totally blank/transparent — use average
          ;[chosenHue, chosenSat, chosenLight] = rgbToHsl(totalR / totalCount, totalG / totalCount, totalB / totalCount)
        } else {
          chosenHue = 215; chosenSat = 55; chosenLight = 35
        }

        // Pure single-hue gradient — NO hue rotation, no color-family drift.
        // Only saturation and lightness vary, so orange stays orange, red stays red, etc.
        // This mirrors how Spotify's Now Playing screen stays true to album color.
        const bgSat  = Math.min(72, Math.max(30, chosenSat * 0.72))
        const topL   = Math.min(30, Math.max(14, chosenLight * 0.20 + 10))
        const midL   = Math.max(8,  topL * 0.55)
        const btmL   = Math.max(4,  midL * 0.55)
        const midSat = Math.max(24, bgSat * 0.70)
        const btmSat = Math.max(16, bgSat * 0.48)
        // Accent: saturated and bright enough to read on the dark bg
        const accentSat   = Math.min(100, Math.max(88, chosenSat))
        const accentLight = Math.min(44, Math.max(34, chosenLight * 0.35 + 18))
        applyTheme({
          panelGradient: `linear-gradient(160deg, hsl(${chosenHue}, ${bgSat}%, ${topL}%) 0%, hsl(${chosenHue}, ${midSat}%, ${midL}%) 62%, hsl(${chosenHue}, ${btmSat}%, ${btmL}%) 100%)`,
          asideGradient: `linear-gradient(150deg, hsl(${chosenHue}, ${bgSat}%, ${Math.max(topL * 0.65, 6)}%) 0%, hsl(${chosenHue}, ${midSat}%, ${Math.max(midL * 0.65, 3)}%) 100%)`,

          accentTextColor: `hsl(${chosenHue}, ${accentSat}%, ${accentLight}%)`,
        })
      } catch {
        applyTheme({
          panelGradient: 'linear-gradient(180deg, hsl(215, 64%, 26%) 0%, hsl(215, 60%, 17%) 100%)',
          asideGradient: 'linear-gradient(180deg, hsl(215, 64%, 17%) 0%, hsl(215, 60%, 11%) 100%)',
          accentTextColor: 'hsl(320, 88%, 38%)',
        })
      }
    }
    img.onerror = () => {
      applyTheme({
        panelGradient: 'linear-gradient(180deg, hsl(215, 64%, 26%) 0%, hsl(215, 60%, 17%) 100%)',
        asideGradient: 'linear-gradient(180deg, hsl(215, 64%, 17%) 0%, hsl(215, 60%, 11%) 100%)',
        accentTextColor: 'hsl(320, 88%, 38%)',
      })
    }
    img.src = sampleUrl
  }, [albumArtUrl])

  return [theme]
}

// ── Player view ────────────────────────────────────────────────────────────────

function PlayerView({
  song, user, onBack, onLogout, onOpenSettings, onOpenAdmin, onOpenAccount, isAdmin, onPrev, onNext, canPrev, canNext, settings, onUpdate, storedMusicUserToken, onMusicUserToken,
}: {
  song: SongDetail
  user: { display_name: string | null; email: string | null } | null
  onBack: () => void
  onLogout: () => void
  onOpenSettings: () => void
  onOpenAdmin: () => void
  onOpenAccount: () => void
  isAdmin: boolean
  onPrev: () => void
  onNext: () => void
  canPrev: boolean
  canNext: boolean
  settings: AppSettings
  onUpdate: (patch: Partial<AppSettings>) => void
  storedMusicUserToken?: string | null
  onMusicUserToken?: (token: string | null) => void
}) {
  const [infoVisible, setInfoVisible] = useState(false)
  const autoPausedRef = useRef(false)

  const shouldLogPlaybackDebug = import.meta.env.DEV || localStorage.getItem('flowup_debug_playback') === '1'
  const logPlaybackDebug = (message: string, data?: unknown) => {
    if (!shouldLogPlaybackDebug) return
    if (data === undefined) {
      console.debug('[SingoLing][PlayerView]', message)
      return
    }
    console.debug('[SingoLing][PlayerView]', message, data)
  }

  // Determine which source to actually use for this song
  const effectiveSource = useMemo((): 'youtube' | 'apple_music' => {
    if (settings.preferredSource === 'apple_music' && song.apple_music_url) return 'apple_music'
    if (song.youtube_url) return 'youtube'
    if (song.apple_music_url) return 'apple_music'
    return 'youtube'
  }, [settings.preferredSource, song.youtube_url, song.apple_music_url])

  useEffect(() => {
    logPlaybackDebug('Playback context', {
      songId: song.id,
      title: song.title,
      artist: song.artist,
      preferredSource: settings.preferredSource,
      effectiveSource,
      hasYoutubeUrl: Boolean(song.youtube_url),
      hasAppleMusicUrl: Boolean(song.apple_music_url),
    })
  }, [song.id, song.title, song.artist, settings.preferredSource, effectiveSource, song.youtube_url, song.apple_music_url])

  // ── YouTube player ───────────────────────────────────────────────────────────
  const ytRef = useRef<YouTubePlayerHandle>(null)
  const [ytPositionMs, setYtPositionMs] = useState(0)
  const [ytDurationMs, setYtDurationMs] = useState(0)
  const [ytPlaying, setYtPlaying] = useState(false)
  const [ytReady, setYtReady] = useState(false)

  const handleYtReady = useCallback(() => {
    setYtReady(true)
    logPlaybackDebug('YouTube player ready', { songId: song.id, youtubeUrl: song.youtube_url })
  }, [song.id, song.youtube_url])

  const handleYtPlayStateChange = useCallback((playing: boolean) => {
    setYtPlaying(playing)
    logPlaybackDebug('YouTube play state changed', { songId: song.id, playing })
  }, [song.id])

  // ── Apple Music player ───────────────────────────────────────────────────────
  const amRef = useRef<AppleMusicPlayerHandle>(null)
  const [amPositionMs, setAmPositionMs] = useState(0)
  const [amDurationMs, setAmDurationMs] = useState(0)
  const [amPlaying, setAmPlaying] = useState(false)
  const [amReady, setAmReady] = useState(false)
  const [amArtworkUrl, setAmArtworkUrl] = useState<string | null>(null)
  // Once the user has successfully played Apple Music once, the audio context
  // is unlocked for this page session. Subsequent song navigations can then
  // auto-play without requiring another user gesture.
  const amEverPlayedRef = useRef(false)
  const [amAutoPlay, setAmAutoPlay] = useState(false)
  // Mirror amAutoPlay into a ref so the song.id effect (below) can read the
  // current value synchronously without adding it to deps.
  const amAutoPlayRef = useRef(false)
  useEffect(() => { amAutoPlayRef.current = amAutoPlay }, [amAutoPlay])

  // Wrap onPrev/onNext: if AM is active and audio context is already unlocked,
  // mark the next song for auto-play before navigating.
  const handlePrev = useCallback(() => {
    if (effectiveSource === 'apple_music' && amEverPlayedRef.current) {
      setAmAutoPlay(true)
      setPendingPlay(true)
    }
    onPrev()
  }, [effectiveSource, onPrev])

  const handleNext = useCallback(() => {
    if (effectiveSource === 'apple_music' && amEverPlayedRef.current) {
      setAmAutoPlay(true)
      setPendingPlay(true)
    }
    onNext()
  }, [effectiveSource, onNext])

  // Once Apple Music starts playing for the first time, mark the audio context
  // as unlocked so we can auto-play on future song navigations.
  useEffect(() => {
    if (amPlaying) amEverPlayedRef.current = true
  }, [amPlaying])

  // Reset Apple Music ready state when the URL changes so the parent
  // transport is disabled until the queue is loaded.
  useEffect(() => {
    setAmReady(false)
    setAmPlaying(false)
    setAmArtworkUrl(null)
    // amAutoPlay is already set by handlePrevSong/handleNextSong before the
    // URL changes; it will be consumed by AppleMusicPlayer's initAndPlay.
    // Clear it after a short delay so it doesn't linger.
    const t = setTimeout(() => setAmAutoPlay(false), 5000)
    return () => clearTimeout(t)
  }, [song.apple_music_url])

  // Combined values depending on active source
  const isPlaying  = effectiveSource === 'apple_music' ? amPlaying  : ytPlaying
  const positionMs = effectiveSource === 'apple_music' ? amPositionMs : ytPositionMs
  const durationMs = effectiveSource === 'apple_music' ? amDurationMs : ytDurationMs
  const isReady    = effectiveSource === 'apple_music' ? amReady    : ytReady

  const [pendingPlay, setPendingPlay] = useState(false)

  // Clear pending indicator once playback actually starts, with a short
  // debounce. MusicKit can briefly fire intermediate states (waiting/stalled)
  // just before firing "playing", which would cause a flash of the play icon
  // if we cleared pendingPlay immediately on the first playing event.
  useEffect(() => {
    if (!isPlaying) return
    const t = setTimeout(() => setPendingPlay(false), 400)
    return () => clearTimeout(t)
  }, [isPlaying])

  // Also clear on song change — but not during an AM auto-play transition
  // (pendingPlay should persist until the new song actually starts playing).
  useEffect(() => {
    if (!amAutoPlayRef.current) setPendingPlay(false)
  }, [song.id])

  const togglePlay = useCallback(() => {
    logPlaybackDebug('Toggle play requested', { effectiveSource, ytPlaying, amPlaying })
    if (effectiveSource === 'apple_music') {
      if (amPlaying) amRef.current?.pause()
      else { setPendingPlay(true); amRef.current?.play() }
    } else {
      if (ytPlaying) ytRef.current?.pause()
      else { setPendingPlay(true); ytRef.current?.play() }
    }
  }, [effectiveSource, ytPlaying, amPlaying])

  const seekTo = useCallback((ms: number) => {
    logPlaybackDebug('Seek requested', { effectiveSource, ms })
    if (effectiveSource === 'apple_music') amRef.current?.seekTo(ms)
    else ytRef.current?.seekTo(ms)
  }, [effectiveSource])

  // Pause-on-inspect
  useEffect(() => {
    if (!settings.pauseOnInspect) {
      autoPausedRef.current = false
      return
    }

    if (infoVisible) {
      if (isPlaying) {
        if (effectiveSource === 'apple_music') amRef.current?.pause()
        else ytRef.current?.pause()
        autoPausedRef.current = true
      }
      return
    }

    if (autoPausedRef.current) {
      if (effectiveSource === 'apple_music') amRef.current?.play()
      else ytRef.current?.play()
      autoPausedRef.current = false
    }
  }, [infoVisible, isPlaying, effectiveSource, settings.pauseOnInspect])

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement ||
        (e.target instanceof HTMLElement && e.target.isContentEditable)
      ) return

      if (e.key === ' ') {
        e.preventDefault()
        if (e.repeat || !isReady) return
        togglePlay()
        return
      }

      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        if (e.repeat || !canPrev) return
        handlePrev()
        return
      }

      if (e.key === 'ArrowRight') {
        e.preventDefault()
        if (e.repeat || !canNext) return
        handleNext()
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [handlePrev, handleNext, canPrev, canNext, togglePlay, isReady])

  const coverArtUrl = useMemo(() => {
    if (effectiveSource === 'apple_music' && amArtworkUrl) return amArtworkUrl
    if (!song.youtube_url) return null
    try {
      const u = new URL(song.youtube_url)
      const id = u.hostname === 'youtu.be'
        ? u.pathname.slice(1).split('?')[0]
        : (u.searchParams.get('v') ?? u.pathname.split('/').pop() ?? '')
      return id ? `https://img.youtube.com/vi/${id}/mqdefault.jpg` : null
    } catch {
      return null
    }
  }, [effectiveSource, amArtworkUrl, song.youtube_url])
  const [lyricsTheme] = useAlbumLyricsTheme(coverArtUrl)
  const hasYouTubePanel = !!song.youtube_url
  const showRightMediaPanel = effectiveSource === 'youtube' && !!song.youtube_url

  return (
    <div className="h-screen flex flex-col overflow-hidden" style={{ background: '#050608', transition: 'background 1.2s ease' }}>
      {/* Header */}
      <header className="sticky top-0 z-20 border-b border-gray-900" style={{ background: '#050608' }}>
        <div className="max-w-[1200px] mx-auto w-full px-4 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="text-gray-500 hover:text-gray-300 transition-colors mr-1" aria-label="Back">
            <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
              <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/>
            </svg>
          </button>
          <img src={singolingLogo} className="h-7 object-contain" alt="SingoLing" />
        </div>
        <div className="flex items-center gap-3">
          {isAdmin && (
            <button
              type="button"
              onClick={onOpenAdmin}
              className="text-xs text-amber-500 hover:text-amber-300 transition-colors"
            >
              Admin
            </button>
          )}
          {/* Inline source switcher — always visible; unavailable sources are dimmed */}
          {(() => {
            const opts: { value: AppSettings['preferredSource']; activeClass: string; available: boolean; label: string }[] = [
              { value: 'youtube',     label: 'YouTube',     activeClass: 'text-red-400',  available: !!song.youtube_url },
              { value: 'apple_music', label: 'Apple Music', activeClass: 'text-gray-200', available: !!song.apple_music_url },
            ]
            return (
              <div className="flex items-center gap-0.5 rounded-lg bg-gray-800/70 p-0.5">
                {opts.map(opt => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => opt.available && onUpdate({ preferredSource: opt.value })}
                    disabled={!opt.available}
                    aria-label={opt.label}
                    className={`px-2 py-1 rounded-md transition-all ${
                      effectiveSource === opt.value
                        ? `${opt.activeClass} bg-white/10`
                        : opt.available ? 'text-gray-500 hover:text-gray-300'
                        : 'text-gray-700 cursor-not-allowed'
                    }`}
                  >
                    {opt.value === 'youtube' ? (
                      <svg viewBox="0 0 24 24" className="h-4 w-4 fill-current" aria-hidden>
                        <path d="M21.58 7.19a2.8 2.8 0 0 0-1.97-1.98C17.86 4.75 12 4.75 12 4.75s-5.86 0-7.61.46A2.8 2.8 0 0 0 2.42 7.2 29.4 29.4 0 0 0 2 12a29.4 29.4 0 0 0 .42 4.81 2.8 2.8 0 0 0 1.97 1.98c1.75.46 7.61.46 7.61.46s5.86 0 7.61-.46a2.8 2.8 0 0 0 1.97-1.98A29.4 29.4 0 0 0 22 12a29.4 29.4 0 0 0-.42-4.81ZM10 15.5v-7l6 3.5-6 3.5Z" />
                      </svg>
                    ) : (
                      <svg viewBox="0 0 24 24" className="h-4 w-4 fill-current" aria-hidden>
                        <path d="M16.37 1.43c0 1.14-.47 2.24-1.22 3.04-.76.79-1.8 1.35-2.94 1.27-.15-1.09.36-2.23 1.09-3 .76-.8 2.01-1.37 3.07-1.31ZM19.08 17.22c-.42.97-.63 1.4-1.18 2.26-.77 1.2-1.86 2.7-3.21 2.71-1.2.01-1.5-.78-3.13-.77-1.62.01-1.95.79-3.15.78-1.35-.01-2.37-1.36-3.14-2.56-2.16-3.34-2.38-7.27-1.06-9.29.94-1.44 2.43-2.28 3.84-2.28 1.44 0 2.35.8 3.54.8 1.15 0 1.85-.8 3.53-.8 1.26 0 2.6.69 3.54 1.89-3.11 1.71-2.61 6.18.42 7.26Z" />
                      </svg>
                    )}
                  </button>
                ))}
              </div>
            )
          })()}
          <button
            type="button"
            onClick={onOpenSettings}
            className="text-xs text-gray-500 hover:text-gray-200 transition-colors"
          >
            Preferences
          </button>
          {user?.display_name && <button type="button" onClick={onOpenAccount} className="text-xs text-gray-500 hover:text-gray-300 transition-colors">{user.display_name}</button>}
          <button onClick={onLogout} className="text-xs text-gray-600 hover:text-gray-400 transition-colors">Sign out</button>
        </div>
        </div>
      </header>

      <main className="flex-1 min-h-0 p-4 max-w-[1200px] mx-auto w-full flex flex-col gap-3">

        {/* Controls + YouTube row */}
        <div
          className="controls-media-row"
          style={{
            ['--media-col' as string]: hasYouTubePanel ? (showRightMediaPanel ? '410px' : '0px') : '0px',
            ['--media-gap' as string]: showRightMediaPanel ? '0.75rem' : '0px',
          }}
        >

          {/* Player controls — takes remaining width */}
          <section className="rounded-md border border-zinc-700/70 p-6 min-w-0 min-h-[210px] lg:min-h-[240px]" style={{ background: '#25262b' }}>
          <div className="flex items-center gap-4 mb-5">
            {coverArtUrl ? (
              <img src={coverArtUrl} alt="Album art" className="w-16 h-16 rounded object-cover border border-black/40" />
            ) : (
              <div className="w-16 h-16 rounded bg-black/40 flex items-center justify-center">
                <svg viewBox="0 0 24 24" className="w-7 h-7 fill-gray-500">
                  <path d="M12 3a9 9 0 100 18A9 9 0 0012 3zm-1 13V8l6 4-6 4z"/>
                </svg>
              </div>
            )}
            <div className="min-w-0">
              <p className="text-zinc-100 text-2xl leading-tight font-semibold truncate">
                {song.title}
              </p>
              <p className="text-zinc-300 text-lg leading-tight truncate">
                {song.artist ?? ''}
              </p>
            </div>
          </div>
          <ProgressBar posMs={positionMs} durMs={durationMs} onSeek={seekTo} />
          <div className="flex items-center justify-center gap-5 mt-6">
            <button
              onClick={handlePrev}
              disabled={!canPrev}
              aria-label="Previous song"
              className="w-10 h-10 rounded-full flex items-center justify-center hover:bg-black/20 disabled:opacity-40 disabled:cursor-not-allowed text-gray-100 transition-all"
            >
              <img src={prevIconImg} className="w-6 h-6 object-contain" alt="" />
            </button>
            <button
              onClick={togglePlay}
              disabled={!isReady}
              aria-label={isPlaying ? 'Pause' : 'Play'}
              className="
                w-14 h-14 rounded-full flex items-center justify-center
                bg-white hover:bg-zinc-200 active:scale-95
                disabled:bg-zinc-700 disabled:text-zinc-500
                text-black shadow-lg transition-all duration-150
              "
            >
              {pendingPlay && !isPlaying ? (
                <div className="w-5 h-5 border-2 border-zinc-400/60 border-t-black rounded-full animate-spin" />
              ) : isPlaying ? (
                <svg viewBox="0 0 24 24" className="w-8 h-8 fill-current">
                  <rect x="6" y="5" width="4" height="14" rx="1.5"/>
                  <rect x="14" y="5" width="4" height="14" rx="1.5"/>
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" className="w-8 h-8 fill-current">
                  <path d="M6 3.5v17l14-8.5z"/>
                </svg>
              )}
            </button>
            <button
              onClick={handleNext}
              disabled={!canNext}
              aria-label="Next song"
              className="w-10 h-10 rounded-full flex items-center justify-center hover:bg-black/20 disabled:opacity-40 disabled:cursor-not-allowed text-gray-100 transition-all"
            >
              <img src={nextIconImg} className="w-6 h-6 object-contain" alt="" />
            </button>
          </div>
        </section>

          {hasYouTubePanel && (
            <aside
              className={`overflow-hidden bg-black min-h-[210px] lg:min-h-[240px] min-w-0 transition-[opacity,transform,border-color] duration-300 ease-out flex flex-col ${
                showRightMediaPanel
                  ? 'rounded-md border border-zinc-700/70 opacity-100 translate-x-0'
                  : 'rounded-md border border-zinc-700/0 opacity-0 translate-x-2 pointer-events-none'
              }`}
              aria-hidden={!showRightMediaPanel}
            >
              {showRightMediaPanel ? (
                <YouTubePlayer
                  ref={ytRef}
                  youtubeUrl={song.youtube_url!}
                  onReady={handleYtReady}
                  onTimeUpdate={setYtPositionMs}
                  onDurationChange={setYtDurationMs}
                  onPlayStateChange={handleYtPlayStateChange}
                />
              ) : null}
            </aside>
          )}

        </div>{/* end controls + media row */}

        {/* Apple Music player — audio-only when playing; visible when auth/error UI is needed */}
        {effectiveSource === 'apple_music' && song.apple_music_url && (
          <AppleMusicPlayer
            ref={amRef}
            appleMusicUrl={song.apple_music_url}
            onReady={() => setAmReady(true)}
            onTimeUpdate={(posMs, durMs) => { setAmPositionMs(posMs); setAmDurationMs(durMs) }}
            onPlayStateChange={setAmPlaying}
            onArtworkUrl={setAmArtworkUrl}
            autoPlay={amAutoPlay}
            storedMusicUserToken={storedMusicUserToken}
            onMusicUserToken={onMusicUserToken}
          />
        )}

        {/* Lyrics panel */}
        <section className="rounded-md overflow-hidden flex-1 min-h-0 flex flex-col" style={{ background: lyricsTheme.panelGradient }}>
          <LyricsPlayer
            currentPositionMs={positionMs}
            durationMs={durationMs}
            isPlaying={isPlaying}
            songData={song}
            themeBackground={lyricsTheme.panelGradient}
            themeAsideBackground={lyricsTheme.asideGradient}
            accentTextColor={lyricsTheme.accentTextColor}
            filterStopWordsForIndexing={settings.excludeStopWordsFromShortcuts}
            onInfoVisibilityChange={setInfoVisible}
            onSeek={seekTo}
            onTogglePlayback={togglePlay}
          />
        </section>
      </main>
    </div>
  )
}

// ── Root App ──────────────────────────────────────────────────────────────────

export default function App() {
  const [currentPath, setCurrentPath] = useState(() => (typeof window === 'undefined' ? '/browse' : (window.location.pathname || '/browse')))
  const [adminOpen, setAdminOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_SETTINGS)
  const [credentialUser, setCredentialUser] = useState<BackendUser | null>(() => {
    try {
      const raw = localStorage.getItem(PASSWORD_SESSION_KEY)
      return raw ? (JSON.parse(raw) as BackendUser) : null
    } catch {
      return null
    }
  })
  const [loginBusy, setLoginBusy] = useState(false)
  const [loginError, setLoginError] = useState<string | null>(null)

  const [songs,        setSongs]        = useState<SongSummary[]>([])
  const [playlists,    setPlaylists]    = useState<PlaylistSummary[]>([])
  const [activePlaylistId, setActivePlaylistId] = useState<number | null>(null)
  const [activePlaylist, setActivePlaylist] = useState<PlaylistDetail | null>(null)
  const [songsLoading, setSongsLoading] = useState(false)
  const [playlistsLoading, setPlaylistsLoading] = useState(false)
  const [playlistDetailLoading, setPlaylistDetailLoading] = useState(false)
  const [songsError,   setSongsError]   = useState<string | null>(null)
  const [activeSong,   setActiveSong]   = useState<SongDetail | null>(null)
  const [songLoading,  setSongLoading]  = useState(false)
  const [lastSelectedSongId, setLastSelectedSongId] = useState<number | null>(null)
  const [openedSongIds, setOpenedSongIds] = useState<Set<number>>(() => {
    try {
      const raw = localStorage.getItem('flowup.openedSongs.v1')
      return raw ? new Set<number>(JSON.parse(raw)) : new Set<number>()
    } catch { return new Set<number>() }
  })
  const [settingsHydrated, setSettingsHydrated] = useState(false)
  const restoreDoneRef = useRef(false)
  const route = useMemo(() => parseAppRoute(currentPath), [currentPath])

  const navigateToPath = useCallback((path: string, replace = false) => {
    if (typeof window === 'undefined') return
    if (window.location.pathname === path) return
    if (replace) {
      window.history.replaceState(null, '', path)
      setCurrentPath(path)
      return
    }
    window.history.pushState(null, '', path)
    setCurrentPath(path)
  }, [])

  useEffect(() => {
    const onPopState = () => setCurrentPath(window.location.pathname || '/browse')
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const appUser = credentialUser ? { display_name: credentialUser.display_name, email: credentialUser.email } : null

  const settingsOwnerSpotifyId = credentialUser?.spotify_id ?? null
  const isAuthenticated = !!credentialUser
  const isAdmin = Boolean(credentialUser?.is_admin)

  const loadSongs = useCallback(async () => {
    setSongsLoading(true)
    setSongsError(null)
    try {
      const nextSongs = await api.listSongs()
      setSongs(nextSongs)
    } catch (e) {
      setSongsError(e instanceof Error ? e.message : 'Failed to load songs')
    } finally {
      setSongsLoading(false)
    }
  }, [])

  const loadPlaylists = useCallback(async () => {
    setPlaylistsLoading(true)
    setSongsError(null)
    try {
      const pls = await api.listPlaylists()
      setPlaylists(pls)
    } catch (e) {
      setSongsError(e instanceof Error ? e.message : 'Failed to load playlists')
    } finally {
      setPlaylistsLoading(false)
    }
  }, [])

  const updateSettings = useCallback((patch: Partial<AppSettings>) => {
    setSettings(prev => ({ ...prev, ...patch }))
    if (!settingsOwnerSpotifyId) return
    api.updateUserSettings(settingsOwnerSpotifyId, toApiSettingsPatch(patch)).catch(() => {
      // Non-fatal: keep optimistic UI state if backend update fails.
    })
  }, [settingsOwnerSpotifyId])

  const handleEmailLogin = useCallback(async (email: string, password: string) => {
    setLoginBusy(true)
    setLoginError(null)
    try {
      const user = await api.loginWithEmailPassword({ email, password })
      if (user.is_admin) {
        setAdminSession(email.trim().toLowerCase(), password)
      } else {
        clearAdminSession()
      }
      setCredentialUser(user)
      localStorage.setItem(PASSWORD_SESSION_KEY, JSON.stringify(user))
    } catch (e) {
      setLoginError(e instanceof Error ? e.message : 'Failed to sign in with email/password')
    } finally {
      setLoginBusy(false)
    }
  }, [])

  const handleGoogleLogin = useCallback(async (credential: string) => {
    setLoginBusy(true)
    setLoginError(null)
    try {
      const user = await api.loginWithGoogle(credential)
      clearAdminSession()
      setCredentialUser(user)
      localStorage.setItem(PASSWORD_SESSION_KEY, JSON.stringify(user))
    } catch (e) {
      setLoginError(e instanceof Error ? e.message : 'Google sign-in failed')
    } finally {
      setLoginBusy(false)
    }
  }, [])

  const handleLogout = useCallback(() => {
    clearAdminSession()
    setAdminOpen(false)
    setCredentialUser(null)
    setSettingsOpen(false)
    setActiveSong(null)
    _songCache.clear()
    _inFlight.clear()
    localStorage.removeItem(PASSWORD_SESSION_KEY)
    navigateToPath('/browse', true)
  }, [navigateToPath])

  const handleMusicUserToken = useCallback((token: string | null) => {
    if (!credentialUser) return
    const updated: BackendUser = { ...credentialUser, apple_music_user_token: token }
    setCredentialUser(updated)
    localStorage.setItem(PASSWORD_SESSION_KEY, JSON.stringify(updated))
    api.saveAppleMusicToken(credentialUser.spotify_id, token).catch(console.error)
  }, [credentialUser])

  const handleSelectSong = useCallback(async (id: number, options?: { updateRoute?: boolean }) => {
    setOpenedSongIds(prev => {
      if (prev.has(id)) return prev
      const next = new Set(prev)
      next.add(id)
      try { localStorage.setItem('flowup.openedSongs.v1', JSON.stringify([...next])) } catch {}
      return next
    })
    // Navigate immediately so the UI responds at once; song data loads in background.
    if (options?.updateRoute !== false) {
      navigateToPath(songPath(id))
    }
    const source = settings.preferredSource
    const key = _songCacheKey(id, source)
    const cached = _songCache.get(key)
    if (cached) {
      // Instant render from cache.
      setActiveSong(cached)
      setSongLoading(false)
      setLastSelectedSongId(id)
      return
    }
    setSongLoading(true)
    setActiveSong(null)
    try {
      const detail = await _fetchSong(id, source)
      setActiveSong(detail)
      setLastSelectedSongId(detail.id)
    } catch (e) {
      setSongsError(e instanceof Error ? e.message : 'Failed to load song')
      navigateToPath('/browse')
    } finally {
      setSongLoading(false)
    }
  }, [settings.preferredSource, navigateToPath])

  const handlePrefetchSong = useCallback((id: number) => {
    const source = settings.preferredSource
    void _fetchSong(id, source).catch(() => {})
  }, [settings.preferredSource])

  // Re-fetch active song lyrics when source preference changes so the player
  // immediately gets the right per-source timestamps. Invalidate stale cache entry.
  useEffect(() => {
    if (!activeSong) return
    const source = settings.preferredSource
    const key = _songCacheKey(activeSong.id, source)
    _songCache.delete(key)  // force fresh fetch for new source
    void _fetchSong(activeSong.id, source).then(d => { setActiveSong(d) }).catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.preferredSource])

  useEffect(() => {
    restoreDoneRef.current = false
    setSettingsHydrated(false)
  }, [settingsOwnerSpotifyId])

  // Fetch song and playlist lists once authenticated
  useEffect(() => {
    if (!isAuthenticated) return
    void loadSongs()
    void loadPlaylists()
  }, [isAuthenticated, loadPlaylists, loadSongs])

  useEffect(() => {
    if (!activePlaylistId) {
      setActivePlaylist(null)
      setPlaylistDetailLoading(false)
      return
    }
    setActivePlaylist(null)
    setPlaylistDetailLoading(true)
    api.getPlaylist(activePlaylistId)
      .then(pl => { setActivePlaylist(pl); setPlaylistDetailLoading(false) })
      .catch(() => { setActivePlaylist(null); setPlaylistDetailLoading(false) })
  }, [activePlaylistId])

  // Sync user to backend (non-fatal if backend is down)
  useEffect(() => {
    if (!credentialUser?.spotify_id) return
    api.getUserSettings(credentialUser.spotify_id)
      .then((loaded) => {
        setSettings(fromApiSettings(loaded))
        setSettingsHydrated(true)
      })
      .catch(() => {
        setSettingsHydrated(true)
      })
  }, [credentialUser?.spotify_id])

  useEffect(() => {
    if (!isAuthenticated || !settingsHydrated || restoreDoneRef.current) return
    restoreDoneRef.current = true

    // Only restore last playlist when the URL doesn't already specify one
    if (settings.lastPlaylistId !== null && route.page === 'browse') {
      navigateToPath(playlistPath(settings.lastPlaylistId), true)
    }

    if (settings.lastSongId !== null && window.location.pathname === '/') {
      void handleSelectSong(settings.lastSongId)
      setLastSelectedSongId(settings.lastSongId)
    }
  }, [
    isAuthenticated,
    settingsHydrated,
    settings.lastPlaylistId,
    settings.lastSongId,
    handleSelectSong,
    navigateToPath,
    route,
  ])

  useEffect(() => {
    if (!isAuthenticated) return

    if (route.page === 'settings') {
      setSettingsOpen(true)
      setAdminOpen(false)
      return
    }

    if (route.page === 'admin') {
      if (isAdmin) {
        setAdminOpen(true)
        setSettingsOpen(false)
      } else {
        setAdminOpen(false)
        setSettingsOpen(false)
        navigateToPath('/browse', true)
      }
      return
    }

    if (route.page === 'playlist') {
      setSettingsOpen(false)
      setAdminOpen(false)
      setActiveSong(null)
      setActivePlaylistId(route.playlistId)
      return
    }

    if (route.page === 'song') {
      setSettingsOpen(false)
      setAdminOpen(false)
      if (activeSong?.id !== route.songId) {
        void handleSelectSong(route.songId, { updateRoute: false })
      }
      return
    }

    // browse
    setSettingsOpen(false)
    setAdminOpen(false)
    setActiveSong(null)
    setActivePlaylistId(null)
  }, [isAuthenticated, isAdmin, activeSong?.id, handleSelectSong, navigateToPath, route])

  useEffect(() => {
    if (!settingsHydrated) return
    if (activePlaylistId === settings.lastPlaylistId) return
    updateSettings({ lastPlaylistId: activePlaylistId })
  }, [activePlaylistId, settings.lastPlaylistId, settingsHydrated, updateSettings])

  useEffect(() => {
    if (!settingsHydrated) return
    if (lastSelectedSongId === settings.lastSongId) return
    updateSettings({ lastSongId: lastSelectedSongId })
  }, [lastSelectedSongId, settings.lastSongId, settingsHydrated, updateSettings])

  useEffect(() => {
    if (activeSong && route.page === 'song') {
      document.title = `${activeSong.title} — ${activeSong.artist} | SingoLing`
    } else if (route.page === 'settings') {
      document.title = 'Settings | SingoLing'
    } else if (route.page === 'admin') {
      document.title = 'Admin | SingoLing'
    } else {
      document.title = 'SingoLing'
    }
  }, [activeSong?.id, activeSong?.title, activeSong?.artist, route.page])

  const displayedSongs = useMemo(() => {
    if (!activePlaylist) return songs
    const byId = new Map(songs.map(s => [s.id, s]))
    return activePlaylist.songs.map((entry) => {
      const full = byId.get(entry.song_id)
      if (full) return full
      return {
        id: entry.song_id,
        spotify_uri: entry.spotify_uri,
        title: entry.title,
        artist: entry.artist,
        language_code: activePlaylist.language_code ?? 'ru',
        language_name: (activePlaylist.language_code ?? 'ru').toUpperCase(),
        youtube_url: null,
        apple_music_url: null,
      }
    })
  }, [activePlaylist, songs])

  const activeSongIndex = useMemo(() => {
    if (!activeSong) return -1
    return displayedSongs.findIndex(s => s.id === activeSong.id)
  }, [activeSong, displayedSongs])

  const handlePrevSong = useCallback(() => {
    if (activeSongIndex <= 0) return
    void handleSelectSong(displayedSongs[activeSongIndex - 1].id)
  }, [activeSongIndex, displayedSongs, handleSelectSong])

  const handleNextSong = useCallback(() => {
    if (activeSongIndex < 0 || activeSongIndex >= displayedSongs.length - 1) return
    void handleSelectSong(displayedSongs[activeSongIndex + 1].id)
  }, [activeSongIndex, displayedSongs, handleSelectSong])

  // Prefetch the next song in the playlist ~2s after the current song loads.
  const prefetchedSongRef = useRef<number | null>(null)
  useEffect(() => {
    if (!activeSong || activeSongIndex < 0) return
    if (prefetchedSongRef.current === activeSong.id) return
    prefetchedSongRef.current = activeSong.id
    const next = displayedSongs[activeSongIndex + 1]
    if (!next) return
    const source = settings.preferredSource
    const timer = window.setTimeout(() => {
      void _fetchSong(next.id, source).catch(() => {})
    }, 2000)
    return () => window.clearTimeout(timer)
  }, [activeSong, activeSongIndex, displayedSongs, settings.preferredSource])

  if (!isAuthenticated) {
    return (
      <LoginScreen
        onEmailLogin={handleEmailLogin}
        onGoogleLogin={handleGoogleLogin}
        error={loginError}
        busy={loginBusy}
      />
    )
  }

  if (settingsOpen) {
    return (
      <SettingsPage
        settings={settings}
        onUpdate={updateSettings}
        onBack={() => navigateToPath(activeSong ? songPath(activeSong.id) : activePlaylistId !== null ? playlistPath(activePlaylistId) : '/browse')}
        onLogout={handleLogout}
        user={appUser}
        activeTab={route.page === 'settings' ? route.tab : 'preferences'}
        onTabChange={(t) => navigateToPath(settingsPath(t))}
      />
    )
  }

  if (adminOpen && isAdmin) {
    return (
      <AdminPanel
        songs={songs}
        playlists={playlists}
        onBack={() => navigateToPath(activeSong ? songPath(activeSong.id) : activePlaylistId !== null ? playlistPath(activePlaylistId) : '/browse')}
        onLogout={handleLogout}
        onRefreshSongs={loadSongs}
        onRefreshPlaylists={loadPlaylists}
        user={appUser}
        routeTab={route.page === 'admin' ? route.tab : 'songs'}
        routeObjectId={route.page === 'admin' ? route.id : null}
        onNavigateRoute={(tab, id) => navigateToPath(adminPath(tab, id))}
      />
    )
  }

  if (songLoading && !activeSong) {
    return (
      <div className="min-h-screen flex items-center justify-center"
           style={{ background: 'radial-gradient(ellipse 120% 80% at 50% 110%, #1a1040 0%, #0d0d14 60%)' }}>
        <div className="flex flex-col items-center gap-4 text-gray-400">
          <svg className="w-10 h-10 animate-spin text-indigo-500" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
          </svg>
          <span className="text-sm">Loading song…</span>
        </div>
      </div>
    )
  }

  if (activeSong) {
    return (
      <PlayerView
        song={activeSong}
        user={appUser}
        onBack={() => navigateToPath(activePlaylistId !== null ? playlistPath(activePlaylistId) : '/browse')}
        onLogout={handleLogout}
        onOpenSettings={() => navigateToPath('/settings')}
        onOpenAccount={() => navigateToPath('/settings/account')}
        onOpenAdmin={() => navigateToPath(adminPath('songs', activeSong.id))}
        isAdmin={isAdmin}
        onPrev={handlePrevSong}
        onNext={handleNextSong}
        canPrev={activeSongIndex > 0}
        canNext={activeSongIndex >= 0 && activeSongIndex < displayedSongs.length - 1}
        settings={settings}
        onUpdate={updateSettings}
        storedMusicUserToken={credentialUser?.apple_music_user_token ?? null}
        onMusicUserToken={handleMusicUserToken}
      />
    )
  }

  return (
    <SongBrowser
      songs={displayedSongs}
      playlists={playlists}
      activePlaylistId={activePlaylistId}
      activePlaylist={activePlaylist}
      loading={songsLoading || playlistsLoading || playlistDetailLoading}
      error={songsError}
      onSelect={handleSelectSong}
      onPrefetch={handlePrefetchSong}
      onSelectPlaylist={(id) => navigateToPath(id !== null ? playlistPath(id) : '/browse')}
      onOpenAdmin={() => navigateToPath(activePlaylistId !== null ? adminPath('playlists', activePlaylistId) : '/admin')}
      isAdmin={isAdmin}
      onOpenSettings={() => navigateToPath('/settings')}
      onOpenAccount={() => navigateToPath('/settings/account')}
      onLogout={handleLogout}
      user={appUser}
      openedSongIds={openedSongIds}
    />
  )
}
