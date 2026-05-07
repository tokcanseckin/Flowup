import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { useSpotifyAuth }   from './hooks/useSpotifyAuth'
import { useSpotifyPlayer } from './hooks/useSpotifyPlayer'
import AdminPanel          from './components/AdminPanel'
import LyricsPlayer         from './components/LyricsPlayer'
import YouTubePlayer, { YouTubePlayerHandle } from './components/YouTubePlayer'
import AppleMusicPlayer, { AppleMusicPlayerHandle } from './components/AppleMusicPlayer'
import { api, BackendUser, PlaylistDetail, PlaylistSummary, SongDetail, SongSummary, UserSettings as ApiUserSettings, clearAdminSession, setAdminSession } from './api/client'

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
  preferredSource: 'spotify' | 'youtube' | 'apple_music'
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

type AppRoute =
  | { page: 'browse' }
  | { page: 'song'; songId: number }
  | { page: 'settings' }
  | { page: 'admin'; tab: 'songs' | 'playlists' | 'users' | 'tasks'; id: number | null }

function parseAppRoute(pathname: string): AppRoute {
  const path = pathname || '/browse'

  if (path === '/settings') return { page: 'settings' }
  const adminMatch = path.match(/^\/admin(?:\/(song|playlist|user|task)(?:\/(\d+))?)?$/)
  if (adminMatch) {
    const seg = adminMatch[1]
    const id = adminMatch[2] ? Number(adminMatch[2]) : null
    const tab = seg === 'playlist' ? 'playlists' : seg === 'user' ? 'users' : seg === 'task' ? 'tasks' : 'songs'
    return { page: 'admin', tab, id }
  }
  if (path === '/browse' || path === '/') return { page: 'browse' }

  const songMatch = path.match(/^\/song\/(\d+)$/)
  if (songMatch) {
    return { page: 'song', songId: Number(songMatch[1]) }
  }

  return { page: 'browse' }
}

function songPath(songId: number): string {
  return `/song/${songId}`
}

function adminPath(tab: 'songs' | 'playlists' | 'users' | 'tasks', id: number | null): string {
  const seg = tab === 'playlists' ? 'playlist' : tab === 'users' ? 'user' : tab === 'tasks' ? 'task' : 'song'
  if (id === null) return `/admin/${seg}`
  return `/admin/${seg}/${id}`
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
                (import.meta.env.VITE_SPOTIFY_CLIENT_ID as string | undefined) &&
                  (import.meta.env.VITE_SPOTIFY_CLIENT_ID as string) !== 'your_spotify_client_id_here' &&
                  'Spotify',
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

          {/* Spotify login — hidden; access is managed per-user via spotify_enabled flag */}
        </div>
      </div>
    </div>
  )
}

const SOURCE_OPTIONS: { value: AppSettings['preferredSource']; label: string; description: string }[] = [
  { value: 'spotify', label: 'Spotify', description: 'Use the Spotify Web Player (requires Premium)' },
  { value: 'youtube', label: 'YouTube', description: 'Embed YouTube videos when available' },
  { value: 'apple_music', label: 'Apple Music', description: 'Use Apple Music (requires subscription)' },
]

function SourcePicker({
  value,
  onChange,
  spotifyEnabled = false,
}: {
  value: AppSettings['preferredSource']
  onChange: (v: AppSettings['preferredSource']) => void
  spotifyEnabled?: boolean
}) {
  const options = SOURCE_OPTIONS.filter(o => o.value !== 'spotify' || spotifyEnabled)
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
              ? 'border-indigo-500 bg-indigo-950/40'
              : 'border-gray-700 bg-gray-900/40 hover:border-gray-600'}
          `}
        >
          <div className="flex items-center gap-3">
            <div className={`w-3.5 h-3.5 rounded-full border-2 shrink-0 ${value === opt.value ? 'border-indigo-400 bg-indigo-400' : 'border-gray-600'}`} />
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

function OnboardingScreen({
  spotifyId,
  initialEmail,
  onSubmit,
  error,
  busy,
  onLogout,
}: {
  spotifyId: string
  initialEmail: string
  onSubmit: (email: string, password: string, source: AppSettings['preferredSource']) => Promise<void>
  error: string | null
  busy: boolean
  onLogout: () => void
}) {
  const [step, setStep] = useState<'account' | 'source'>('account')
  const [email, setEmail] = useState(initialEmail)
  const [password, setPassword] = useState('')
  const [source, setSource] = useState<AppSettings['preferredSource']>('youtube')

  const handleAccountSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault()
    setStep('source')
  }, [])

  const handleFinish = useCallback(() => {
    void onSubmit(email.trim(), password, source)
  }, [email, password, source, onSubmit])

  return (
    <div className="min-h-screen flex items-center justify-center p-4" style={{ background: '#0d0d14' }}>
      <div className="w-full max-w-md rounded-2xl border border-gray-800/80 p-8" style={{ background: '#12121f' }}>

        {step === 'account' ? (
          <>
            <h2 className="text-white font-semibold text-lg">Complete your onboarding</h2>
            <p className="text-gray-500 text-sm mt-1 leading-relaxed">
              We need your email and a password to secure your SingoLing account.
            </p>
            <p className="text-[11px] text-gray-600 mt-2 font-mono">Spotify ID: {spotifyId}</p>

            {error && (
              <div className="mt-4 rounded-xl border border-red-900/50 bg-red-950/30 px-4 py-3 text-sm text-red-400">
                {error}
              </div>
            )}

            <form onSubmit={handleAccountSubmit} className="space-y-3 mt-5">
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
                placeholder="Choose a password (min 8 chars)"
                className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
              />
              <button
                type="submit"
                className="w-full py-2.5 rounded-xl font-semibold text-sm bg-indigo-600 hover:bg-indigo-500 text-white transition-all duration-150"
              >
                Next →
              </button>
            </form>
          </>
        ) : (
          <>
            <h2 className="text-white font-semibold text-lg">Choose your music source</h2>
            <p className="text-gray-500 text-sm mt-1 leading-relaxed">
              Pick where you want music to play from. You can change this later in Settings.
            </p>

            {error && (
              <div className="mt-4 rounded-xl border border-red-900/50 bg-red-950/30 px-4 py-3 text-sm text-red-400">
                {error}
              </div>
            )}

            <div className="mt-5">
              <SourcePicker value={source} onChange={setSource} spotifyEnabled={true} />
            </div>

            <button
              type="button"
              disabled={busy}
              onClick={handleFinish}
              className="mt-5 w-full py-2.5 rounded-xl font-semibold text-sm bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-800 disabled:text-gray-500 text-white transition-all duration-150"
            >
              {busy ? 'Saving…' : 'Finish onboarding'}
            </button>

            <button
              type="button"
              onClick={() => setStep('account')}
              className="mt-3 w-full text-xs text-gray-600 hover:text-gray-400 transition-colors"
            >
              ← Back
            </button>
          </>
        )}

        <button
          type="button"
          onClick={onLogout}
          className="mt-4 w-full text-xs text-gray-600 hover:text-gray-400 transition-colors"
        >
          Cancel and sign out
        </button>
      </div>
    </div>
  )
}

// ── Loading screen ────────────────────────────────────────────────────────────

function LoadingScreen({ message }: { message: string }) {
  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: '#0d0d14' }}>
      <div className="text-center">
        <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
        <p className="text-gray-500 text-sm">{message}</p>
      </div>
    </div>
  )
}

// ── Song browser ──────────────────────────────────────────────────────────────

const LANG_BADGE_MAP: Record<string, string> = {
  ru: 'RU', uk: 'UK', de: 'DE', es: 'ES', fr: 'FR',
  it: 'IT', pt: 'PT', nl: 'NL', pl: 'PL', sv: 'SV',
  ja: 'JA', zh: 'ZH', ko: 'KO', tr: 'TR', ar: 'AR', he: 'HE',
}

function SourceAvailabilityIcons({
  song,
  spotifyEnabled,
}: {
  song: SongSummary
  spotifyEnabled: boolean
}) {
  const sources = [
    spotifyEnabled ? { key: 'spotify', label: 'Spotify', className: 'text-emerald-500 bg-white' } : null,
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
          {source.key === 'spotify' ? (
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 fill-current" aria-hidden>
              <path d="M16.84 15.33c-.2 0-.33-.07-.53-.2-1.47-.87-3.33-1.07-5.59-.53-.27.07-.47.13-.73.13-.6 0-1-.47-1-1 0-.47.33-.87.8-1 .27-.07.6-.13.87-.2 2.8-.6 5.13-.33 7 1 .4.2.6.53.6.93 0 .47-.47.87-.87.87Zm1.2-2.93c-.27 0-.47-.07-.73-.2-1.73-1-4.47-1.27-6.8-.67-.27.07-.53.13-.8.13-.67 0-1.13-.53-1.13-1.13 0-.53.33-1 .87-1.13.33-.07.67-.2 1.07-.27 2.73-.6 5.86-.27 8 1 .47.27.73.67.73 1.13 0 .6-.53 1.13-1.2 1.13Zm.87-3.13c-.33 0-.6-.07-.87-.2-2-.93-5.4-1.33-7.73-.73-.33.07-.73.2-1.07.2-.73 0-1.33-.6-1.33-1.33 0-.67.4-1.2 1.07-1.33.4-.13.8-.2 1.27-.33 2.8-.6 6.73-.13 9.13 1 .6.27.93.73.93 1.33 0 .73-.6 1.4-1.4 1.4Z" />
            </svg>
          ) : source.key === 'youtube' ? (
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
  songs, playlists, activePlaylistId, loading, error, onSelect, onSelectPlaylist, onLogout, onOpenSettings, onOpenAdmin, isAdmin, user, spotifyEnabled,
}: {
  songs: SongSummary[]
  playlists: PlaylistSummary[]
  activePlaylistId: number | null
  loading: boolean
  error: string | null
  onSelect: (id: number) => void
  onSelectPlaylist: (id: number | null) => void
  onLogout: () => void
  onOpenSettings: () => void
  onOpenAdmin: () => void
  isAdmin: boolean
  user: { display_name: string | null } | null
  spotifyEnabled: boolean
}) {
  return (
    <div className="min-h-screen p-6 max-w-2xl mx-auto" style={{ background: '#0d0d14' }}>
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-indigo-600 flex items-center justify-center">
            <svg viewBox="0 0 24 24" className="w-4 h-4 fill-white">
              <path d="M12 3a9 9 0 100 18A9 9 0 0012 3zm-1 13V8l6 4-6 4z"/>
            </svg>
          </div>
          <h1 className="text-xl font-bold text-white">
            Singo<span className="text-indigo-400">Ling</span>
          </h1>
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
            className="text-xs text-gray-600 hover:text-gray-300 transition-colors"
          >
            Settings
          </button>
          {user?.display_name && (
            <span className="text-xs text-gray-500">{user.display_name}</span>
          )}
          <button onClick={onLogout} className="text-xs text-gray-600 hover:text-gray-400 transition-colors">
            Sign out
          </button>
        </div>
      </div>

      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-white font-semibold text-lg">Choose a song</h2>
        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-500" htmlFor="playlist-select">Playlist</label>
          <select
            id="playlist-select"
            value={activePlaylistId ?? ''}
            onChange={e => onSelectPlaylist(e.target.value ? Number(e.target.value) : null)}
            className="rounded-lg border border-gray-700 bg-gray-900/80 px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-indigo-500"
          >
            <option value="">All songs</option>
            {playlists.map(pl => (
              <option key={pl.id} value={pl.id}>
                {pl.name} ({pl.song_count})
              </option>
            ))}
          </select>
        </div>
      </div>

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
        <div className="rounded-2xl border border-gray-800/80 p-8 text-center" style={{ background: '#12121f' }}>
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
              className="
                w-full text-left rounded-2xl border border-gray-800/80 p-4
                hover:border-indigo-800/80 hover:bg-indigo-950/20
                active:scale-[0.99] transition-all duration-150
              "
              style={{ background: '#12121f' }}
            >
              <div className="flex items-center gap-3">
                <span className="text-[10px] font-mono font-semibold text-gray-400 bg-gray-800 px-1.5 py-1 rounded" aria-hidden>
                  {LANG_BADGE_MAP[song.language_code] ?? 'INTL'}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-white font-semibold truncate">{song.title}</p>
                  <p className="text-gray-500 text-sm truncate">{song.artist ?? 'Unknown artist'}</p>
                </div>
                <SourceAvailabilityIcons song={song} spotifyEnabled={spotifyEnabled} />
                <span className="text-[10px] font-mono font-medium text-indigo-400 bg-indigo-950/60 border border-indigo-900/50 px-1.5 py-0.5 rounded-md uppercase tracking-wider shrink-0">
                  {song.language_name}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
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
            ${value ? 'bg-indigo-500' : 'bg-gray-700'}
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
  spotifyEnabled,
}: {
  settings: AppSettings
  onUpdate: (patch: Partial<AppSettings>) => void
  onBack: () => void
  onLogout: () => void
  user: { display_name: string | null } | null
  spotifyEnabled: boolean
}) {
  return (
    <div className="min-h-screen p-6 max-w-2xl mx-auto" style={{ background: '#0d0d14' }}>
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="text-gray-500 hover:text-gray-300 transition-colors mr-1" aria-label="Back">
            <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
              <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/>
            </svg>
          </button>
          <div className="w-8 h-8 rounded-xl bg-indigo-600 flex items-center justify-center">
            <svg viewBox="0 0 24 24" className="w-4 h-4 fill-white">
              <path d="M19.14 12.94a7.43 7.43 0 000-1.88l2.03-1.58a.5.5 0 00.12-.64l-1.92-3.32a.5.5 0 00-.6-.22l-2.39.96a7.36 7.36 0 00-1.63-.94l-.36-2.54A.5.5 0 0013.9 2h-3.8a.5.5 0 00-.49.42l-.36 2.54a7.36 7.36 0 00-1.63.94l-2.39-.96a.5.5 0 00-.6.22L2.71 8.48a.5.5 0 00.12.64l2.03 1.58a7.43 7.43 0 000 1.88l-2.03 1.58a.5.5 0 00-.12.64l1.92 3.32a.5.5 0 00.6.22l2.39-.96c.5.39 1.05.71 1.63.94l.36 2.54a.5.5 0 00.49.42h3.8a.5.5 0 00.49-.42l.36-2.54c.58-.23 1.13-.55 1.63-.94l2.39.96a.5.5 0 00.6-.22l1.92-3.32a.5.5 0 00-.12-.64l-2.03-1.58zM12 15.5A3.5 3.5 0 1112 8a3.5 3.5 0 010 7.5z"/>
            </svg>
          </div>
          <h1 className="text-xl font-bold text-white">Settings</h1>
        </div>
        <div className="flex items-center gap-3">
          {user?.display_name && <span className="text-xs text-gray-500">{user.display_name}</span>}
          <button onClick={onLogout} className="text-xs text-gray-600 hover:text-gray-400 transition-colors">Sign out</button>
        </div>
      </div>

      <div className="space-y-3">
        <div className="rounded-2xl border border-gray-800/80 p-4" style={{ background: '#12121f' }}>
          <p className="text-white font-medium mb-1">Music source</p>
          <p className="text-xs text-gray-500 mb-3 leading-relaxed">Choose where songs play from. YouTube is used as fallback when Spotify is unavailable.</p>
          <SourcePicker value={settings.preferredSource} onChange={v => onUpdate({ preferredSource: v })} spotifyEnabled={spotifyEnabled} />
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
      <span className="text-xs font-mono text-gray-500 w-9 text-right shrink-0">{formatMs(posMs)}</span>
      <div className="flex-1 h-1.5 bg-gray-800 rounded-full cursor-pointer group" onClick={handleClick}>
        <div className="h-full bg-indigo-500 rounded-full relative transition-all duration-100 group-hover:bg-indigo-400"
             style={{ width: `${pct}%` }}>
          <div className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-white opacity-0 group-hover:opacity-100 translate-x-1/2 transition-opacity" />
        </div>
      </div>
      <span className="text-xs font-mono text-gray-500 w-9 shrink-0">{formatMs(durMs)}</span>
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

function useAlbumBg(albumArtUrl: string | null): string {
  const [bg, setBg] = useState('#0d0d14')
  useEffect(() => {
    if (!albumArtUrl) { setBg('#0d0d14'); return }
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      try {
        const SIZE = 30
        const canvas = document.createElement('canvas')
        canvas.width = canvas.height = SIZE
        const ctx = canvas.getContext('2d')
        if (!ctx) return
        ctx.drawImage(img, 0, 0, SIZE, SIZE)
        const data = ctx.getImageData(0, 0, SIZE, SIZE).data
        let r = 0, g = 0, b = 0
        const count = SIZE * SIZE
        for (let i = 0; i < data.length; i += 4) {
          r += data[i]; g += data[i + 1]; b += data[i + 2]
        }
        const [h, s] = rgbToHsl(r / count, g / count, b / count)
        const sat = Math.min(s, 70)
        setBg(
          `radial-gradient(ellipse 140% 90% at 50% 115%, ` +
          `hsl(${h},${sat}%,10%) 0%, ` +
          `hsl(${h},${Math.max(sat - 30, 5)}%,5%) 65%)`
        )
      } catch { setBg('#0d0d14') }
    }
    img.onerror = () => setBg('#0d0d14')
    img.src = albumArtUrl
  }, [albumArtUrl])
  return bg
}

// ── Player view ────────────────────────────────────────────────────────────────

function PlayerView({
  song, user, onBack, onLogout, onOpenSettings, onOpenAdmin, isAdmin, onPrev, onNext, canPrev, canNext, settings,
}: {
  song: SongDetail
  user: { display_name: string | null; images: { url: string }[] } | null
  onBack: () => void
  onLogout: () => void
  onOpenSettings: () => void
  onOpenAdmin: () => void
  isAdmin: boolean
  onPrev: () => void
  onNext: () => void
  canPrev: boolean
  canNext: boolean
  settings: AppSettings
}) {
  const [trackUri, setTrackUri] = useState(song.spotify_uri)
  const [loading,  setLoading]  = useState(false)
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
  const effectiveSource = useMemo((): 'spotify' | 'youtube' | 'apple_music' => {
    if (settings.preferredSource === 'youtube' && song.youtube_url) return 'youtube'
    if (settings.preferredSource === 'apple_music' && song.apple_music_url) return 'apple_music'
    return 'spotify'
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

  // ── Spotify player ───────────────────────────────────────────────────────────
  const player = useSpotifyPlayer(localStorage.getItem('sp_access_token'))
  const albumBg = useAlbumBg(player.albumArtUrl)

  // ── YouTube player ───────────────────────────────────────────────────────────
  const ytRef = useRef<YouTubePlayerHandle>(null)
  const [ytPositionMs, setYtPositionMs] = useState(0)
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

  const handleAmTimeUpdate = useCallback((posMs: number, durMs: number) => {
    setAmPositionMs(posMs)
    setAmDurationMs(durMs)
  }, [])

  // Combined values depending on active source
  const isPlaying  = effectiveSource === 'youtube' ? ytPlaying  : effectiveSource === 'apple_music' ? amPlaying  : player.isPlaying
  const positionMs = effectiveSource === 'youtube' ? ytPositionMs : effectiveSource === 'apple_music' ? amPositionMs : player.currentPositionMs
  const durationMs = effectiveSource === 'youtube' ? 0           : effectiveSource === 'apple_music' ? amDurationMs : player.durationMs
  const isReady    = effectiveSource === 'youtube' ? ytReady    : effectiveSource === 'apple_music' ? amReady    : player.isReady

  const togglePlay = useCallback(() => {
    logPlaybackDebug('Toggle play requested', {
      effectiveSource,
      ytPlaying,
      amPlaying,
      spotifyPlaying: player.isPlaying,
    })
    if (effectiveSource === 'youtube') {
      if (ytPlaying) ytRef.current?.pause()
      else ytRef.current?.play()
    } else if (effectiveSource === 'apple_music') {
      if (amPlaying) amRef.current?.pause()
      else amRef.current?.play()
    } else {
      player.togglePlay()
    }
  }, [effectiveSource, ytPlaying, amPlaying, player])

  const seekTo = useCallback((ms: number) => {
    logPlaybackDebug('Seek requested', { effectiveSource, ms })
    if (effectiveSource === 'youtube') ytRef.current?.seekTo(ms)
    else if (effectiveSource === 'apple_music') amRef.current?.seekTo(ms)
    else player.seekTo(ms)
  }, [effectiveSource, player])

  const handleLoadTrack = useCallback(async () => {
    if (effectiveSource !== 'spotify') return
    setLoading(true)
    await player.loadAndPlayTrack(trackUri)
    setLoading(false)
  }, [player, trackUri, effectiveSource])

  useEffect(() => {
    setTrackUri(song.spotify_uri)
  }, [song.spotify_uri])

  // Auto-load Spotify track when player is ready
  useEffect(() => {
    if (effectiveSource !== 'spotify' || !player.isReady) return

    let cancelled = false
    setLoading(true)

    void player.loadAndPlayTrack(song.spotify_uri)
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [effectiveSource, player.isReady, player.loadAndPlayTrack, song.spotify_uri])

  // Pause-on-inspect
  useEffect(() => {
    if (!settings.pauseOnInspect) {
      autoPausedRef.current = false
      return
    }

    if (infoVisible) {
      if (isPlaying) {
        if (effectiveSource === 'youtube') ytRef.current?.pause()
        else if (effectiveSource === 'apple_music') amRef.current?.pause()
        else player.pause()
        autoPausedRef.current = true
      }
      return
    }

    if (autoPausedRef.current) {
      if (effectiveSource === 'youtube') ytRef.current?.play()
      else if (effectiveSource === 'apple_music') amRef.current?.play()
      else player.resume()
      autoPausedRef.current = false
    }
  }, [infoVisible, isPlaying, effectiveSource, player, settings.pauseOnInspect])

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement ||
        (e.target instanceof HTMLElement && e.target.isContentEditable)
      ) return

      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        if (e.repeat || !canPrev) return
        onPrev()
        return
      }

      if (e.key === 'ArrowRight') {
        e.preventDefault()
        if (e.repeat || !canNext) return
        onNext()
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onPrev, onNext, canPrev, canNext])

  return (
    <div className="min-h-screen flex flex-col" style={{ background: effectiveSource === 'spotify' ? albumBg : '#0d0d14', transition: 'background 1.2s ease' }}>

      {/* Header */}
      <header className="glass sticky top-0 z-20 flex items-center justify-between px-6 py-3 border-b border-gray-800/60">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="text-gray-500 hover:text-gray-300 transition-colors mr-1" aria-label="Back">
            <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
              <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/>
            </svg>
          </button>
          <div className="w-7 h-7 rounded-lg bg-indigo-600 flex items-center justify-center">
            <svg viewBox="0 0 24 24" className="w-4 h-4 fill-white">
              <path d="M12 3a9 9 0 100 18A9 9 0 0012 3zm-1 13V8l6 4-6 4z"/>
            </svg>
          </div>
          <span className="font-bold text-white text-sm">Singo<span className="text-indigo-400">Ling</span></span>
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
            className="text-xs text-gray-600 hover:text-gray-300 transition-colors"
          >
            Settings
          </button>
          {effectiveSource === 'spotify' && player.isReady && (
            <span className="flex items-center gap-1.5 text-xs text-green-400">
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
              Spotify
            </span>
          )}
          {effectiveSource === 'youtube' && ytReady && (
            <span className="flex items-center gap-1.5 text-xs text-red-400">
              <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse" />
              YouTube
            </span>
          )}
          {effectiveSource === 'apple_music' && amReady && (
            <span className="flex items-center gap-1.5 text-xs text-pink-400">
              <span className="w-1.5 h-1.5 rounded-full bg-pink-400 animate-pulse" />
              Apple Music
            </span>
          )}
          {user?.display_name && <span className="text-xs text-gray-500">{user.display_name}</span>}
          <button onClick={onLogout} className="text-xs text-gray-600 hover:text-gray-400 transition-colors">Sign out</button>
        </div>
      </header>

      {player.error && effectiveSource === 'spotify' && (
        <div className="mx-4 mt-4 rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">
          {player.error}
        </div>
      )}

      <main className="flex-1 p-4 max-w-[1080px] mx-auto w-full space-y-4">

        {/* YouTube embed */}
        {effectiveSource === 'youtube' && song.youtube_url && (
          <YouTubePlayer
            ref={ytRef}
            youtubeUrl={song.youtube_url}
            onReady={handleYtReady}
            onTimeUpdate={setYtPositionMs}
            onPlayStateChange={handleYtPlayStateChange}
          />
        )}

        {/* Apple Music embed */}
        {effectiveSource === 'apple_music' && song.apple_music_url && (
          <AppleMusicPlayer
            ref={amRef}
            appleMusicUrl={song.apple_music_url}
            onReady={() => setAmReady(true)}
            onTimeUpdate={handleAmTimeUpdate}
            onPlayStateChange={setAmPlaying}
          />
        )}

        {/* Spotify track URI loader (only shown in Spotify mode) */}
        {effectiveSource === 'spotify' && (
          <div className="rounded-2xl border border-gray-800/80 p-4 flex gap-3" style={{ background: '#12121f' }}>
            <input
              className="
                flex-1 rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2
                text-white text-sm font-mono placeholder-gray-600
                focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30
                transition-all
              "
              value={trackUri}
              onChange={e => setTrackUri(e.target.value)}
              placeholder="spotify:track:..."
              spellCheck={false}
            />
            <button
              onClick={handleLoadTrack}
              disabled={!player.isReady || loading}
              className="
                px-4 py-2 rounded-xl text-sm font-semibold
                bg-indigo-600 hover:bg-indigo-500 active:scale-[0.98]
                disabled:bg-gray-800 disabled:text-gray-600
                text-white transition-all duration-150
              "
            >
              {loading ? 'Loading…' : 'Load'}
            </button>
          </div>
        )}

        {/* Player controls */}
        <section className="rounded-2xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
          <div className="flex items-center gap-4 mb-5">
            {effectiveSource === 'spotify' && player.albumArtUrl ? (
              <img src={player.albumArtUrl} alt="Album art" className="w-14 h-14 rounded-xl object-cover shadow-lg" />
            ) : (
              <div className="w-14 h-14 rounded-xl bg-gray-800 flex items-center justify-center">
                <svg viewBox="0 0 24 24" className="w-6 h-6 fill-gray-600">
                  <path d="M12 3a9 9 0 100 18A9 9 0 0012 3zm-1 13V8l6 4-6 4z"/>
                </svg>
              </div>
            )}
            <div className="min-w-0">
              <p className="text-white font-semibold truncate">
                {effectiveSource === 'spotify' ? (player.currentTrackName ?? song.title) : song.title}
              </p>
              <p className="text-gray-500 text-sm truncate">
                {effectiveSource === 'spotify'
                  ? (player.currentTrackArtist ?? (song.artist ?? 'Load a track to begin'))
                  : (song.artist ?? '')}
              </p>
            </div>
          </div>
          <ProgressBar posMs={positionMs} durMs={durationMs} onSeek={seekTo} />
          <div className="flex items-center justify-center gap-3 mt-5">
            <button
              onClick={onPrev}
              disabled={!canPrev}
              aria-label="Previous song"
              className="w-10 h-10 rounded-full flex items-center justify-center bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed text-gray-200 transition-all"
            >
              <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
                <path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/>
              </svg>
            </button>
            <button
              onClick={togglePlay}
              disabled={!isReady}
              aria-label={isPlaying ? 'Pause' : 'Play'}
              className="
                w-14 h-14 rounded-full flex items-center justify-center
                bg-indigo-600 hover:bg-indigo-500 active:scale-95
                disabled:bg-gray-800 disabled:text-gray-600
                text-white shadow-lg shadow-indigo-900/40 transition-all duration-150
              "
            >
              {isPlaying ? (
                <svg viewBox="0 0 24 24" className="w-6 h-6 fill-current">
                  <rect x="6" y="5" width="4" height="14" rx="1.5"/>
                  <rect x="14" y="5" width="4" height="14" rx="1.5"/>
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" className="w-6 h-6 fill-current translate-x-0.5">
                  <path d="M8 5.14v14l11-7-11-7z"/>
                </svg>
              )}
            </button>
            <button
              onClick={onNext}
              disabled={!canNext}
              aria-label="Next song"
              className="w-10 h-10 rounded-full flex items-center justify-center bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed text-gray-200 transition-all"
            >
              <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
                <path d="M16 6h2v12h-2zM6 6v12l8.5-6z"/>
              </svg>
            </button>
          </div>
        </section>

        {/* Lyrics panel */}
        <section className="rounded-2xl border border-gray-800/80 overflow-hidden" style={{ background: '#12121f' }}>
          <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800/80">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="font-semibold text-white">{song.title}</h2>
                <span className="text-[10px] font-mono font-medium text-indigo-400 bg-indigo-950/60 border border-indigo-900/50 px-1.5 py-0.5 rounded-md uppercase tracking-wider">
                  {song.language.name}
                </span>
              </div>
              <p className="text-xs text-gray-600 mt-0.5">
                Press{' '}
                <kbd className="font-mono bg-gray-800 text-gray-400 px-1 py-0.5 rounded text-[10px]">Space</kbd>
                {' '}to play/pause,{' '}
                <kbd className="font-mono bg-gray-800 text-gray-400 px-1 py-0.5 rounded text-[10px]">0</kbd>
                {' '}for line translation,{' '}
                <kbd className="font-mono bg-gray-800 text-gray-400 px-1 py-0.5 rounded text-[10px]">1</kbd>
                –
                <kbd className="font-mono bg-gray-800 text-gray-400 px-1 py-0.5 rounded text-[10px]">9</kbd>
                {' '}to inspect a word
              </p>
            </div>
            <span className="text-xs font-mono text-indigo-400 bg-indigo-950/50 border border-indigo-900/50 px-2.5 py-1 rounded-lg">
              {formatMs(positionMs)}
            </span>
          </div>
          <LyricsPlayer
            currentPositionMs={positionMs}
            songData={song}
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
  const auth = useSpotifyAuth()
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
  const [syncedSpotifyUser, setSyncedSpotifyUser] = useState<BackendUser | null>(null)
  const [loginBusy, setLoginBusy] = useState(false)
  const [loginError, setLoginError] = useState<string | null>(null)
  const [onboardingBusy, setOnboardingBusy] = useState(false)
  const [onboardingError, setOnboardingError] = useState<string | null>(null)

  const [songs,        setSongs]        = useState<SongSummary[]>([])
  const [playlists,    setPlaylists]    = useState<PlaylistSummary[]>([])
  const [activePlaylistId, setActivePlaylistId] = useState<number | null>(null)
  const [activePlaylist, setActivePlaylist] = useState<PlaylistDetail | null>(null)
  const [songsLoading, setSongsLoading] = useState(false)
  const [playlistsLoading, setPlaylistsLoading] = useState(false)
  const [songsError,   setSongsError]   = useState<string | null>(null)
  const [activeSong,   setActiveSong]   = useState<SongDetail | null>(null)
  const [lastSelectedSongId, setLastSelectedSongId] = useState<number | null>(null)
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

  const appUser = useMemo(
    () => auth.user ?? (credentialUser ? { display_name: credentialUser.display_name, images: [] } : null),
    [auth.user, credentialUser]
  )

  const settingsOwnerSpotifyId = auth.user?.id ?? credentialUser?.spotify_id ?? null
  const isAuthenticated = auth.isAuthenticated || !!credentialUser
  const isAdmin = Boolean(credentialUser?.is_admin || syncedSpotifyUser?.is_admin)
  const isSpotifyEnabled = Boolean(credentialUser?.spotify_enabled || syncedSpotifyUser?.spotify_enabled || auth.isAuthenticated)

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
      setActivePlaylistId(prev => prev ?? pls[0]?.id ?? null)
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
      setSyncedSpotifyUser(user)
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
      setSyncedSpotifyUser(user)
    } catch (e) {
      setLoginError(e instanceof Error ? e.message : 'Google sign-in failed')
    } finally {
      setLoginBusy(false)
    }
  }, [])

  const handleCompleteOnboarding = useCallback(async (email: string, password: string, source: AppSettings['preferredSource']) => {
    if (!auth.user?.id) return
    setOnboardingBusy(true)
    setOnboardingError(null)
    try {
      const user = await api.completeOnboarding({
        spotify_id: auth.user.id,
        email,
        password,
      })
      setSyncedSpotifyUser(user)
      localStorage.setItem(PASSWORD_SESSION_KEY, JSON.stringify(user))
      // Persist chosen source
      updateSettings({ preferredSource: source })
    } catch (e) {
      setOnboardingError(e instanceof Error ? e.message : 'Failed to complete onboarding')
    } finally {
      setOnboardingBusy(false)
    }
  }, [auth.user?.id, updateSettings])

  const handleLogout = useCallback(() => {
    auth.logout()
    clearAdminSession()
    setAdminOpen(false)
    setCredentialUser(null)
    setSyncedSpotifyUser(null)
    setSettingsOpen(false)
    setActiveSong(null)
    localStorage.removeItem(PASSWORD_SESSION_KEY)
    navigateToPath('/browse', true)
  }, [auth, navigateToPath])

  const handleSelectSong = useCallback(async (id: number, options?: { updateRoute?: boolean }) => {
    try {
      const source = settings.preferredSource !== 'spotify' ? settings.preferredSource : undefined
      const detail = await api.getSong(id, source)
      setActiveSong(detail)
      setLastSelectedSongId(detail.id)
      if (options?.updateRoute !== false) {
        navigateToPath(songPath(detail.id))
      }
    } catch (e) {
      setSongsError(e instanceof Error ? e.message : 'Failed to load song')
    }
  }, [settings.preferredSource, navigateToPath])

  // Re-fetch active song lyrics when source preference changes so the player
  // immediately gets the right per-source timestamps.
  useEffect(() => {
    if (!activeSong) return
    const source = settings.preferredSource !== 'spotify' ? settings.preferredSource : undefined
    void api.getSong(activeSong.id, source).then(setActiveSong).catch(() => {/* silent */})
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
      return
    }
    api.getPlaylist(activePlaylistId)
      .then(setActivePlaylist)
      .catch(() => setActivePlaylist(null))
  }, [activePlaylistId])

  // Sync user to backend (non-fatal if backend is down)
  useEffect(() => {
    if (!auth.isAuthenticated || !auth.user || !auth.accessToken) return
    const spotifyUserId = auth.user.id
    const refresh   = localStorage.getItem('sp_refresh_token') ?? ''
    const expiresAt = Number(localStorage.getItem('sp_expires_at') ?? 0)
    const expiresIn = Math.max(60, Math.floor((expiresAt - Date.now()) / 1000))
    api.syncUser({
      spotify_id:    auth.user.id,
      display_name:  auth.user.display_name,
      email:         auth.user.email,
      access_token:  auth.accessToken,
      refresh_token: refresh,
      expires_in:    expiresIn,
    })
      .then((synced) => {
        setSyncedSpotifyUser(synced)
        return api.getUserSettings(spotifyUserId)
      })
      .then((loaded) => {
        setSettings(fromApiSettings(loaded))
        setSettingsHydrated(true)
      })
      .catch(() => {
        setSettingsHydrated(true)
        /* backend may not be running */
      })
  }, [auth.isAuthenticated, auth.user, auth.accessToken])

  useEffect(() => {
    if (auth.isAuthenticated || !credentialUser?.spotify_id) return
    api.getUserSettings(credentialUser.spotify_id)
      .then((loaded) => {
        setSettings(fromApiSettings(loaded))
        setSettingsHydrated(true)
      })
      .catch(() => {
        setSettingsHydrated(true)
        /* backend may not be running */
      })
  }, [auth.isAuthenticated, credentialUser?.spotify_id])

  useEffect(() => {
    if (!isAuthenticated || !settingsHydrated || restoreDoneRef.current) return
    restoreDoneRef.current = true

    if (settings.lastPlaylistId !== null) {
      setActivePlaylistId(settings.lastPlaylistId)
    }

    if (settings.lastSongId !== null && route.page !== 'song' && route.page !== 'admin') {
      void handleSelectSong(settings.lastSongId)
      setLastSelectedSongId(settings.lastSongId)
    }
  }, [
    isAuthenticated,
    settingsHydrated,
    settings.lastPlaylistId,
    settings.lastSongId,
    handleSelectSong,
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

  if (auth.isLoading) return <LoadingScreen message="Restoring session..." />

  if (!isAuthenticated) {
    return (
      <LoginScreen
        onEmailLogin={handleEmailLogin}
        onGoogleLogin={handleGoogleLogin}
        error={loginError ?? auth.error}
        busy={loginBusy}
      />
    )
  }

  if (auth.isAuthenticated && syncedSpotifyUser?.needs_onboarding && auth.user) {
    return (
      <OnboardingScreen
        spotifyId={auth.user.id}
        initialEmail={auth.user.email ?? ''}
        onSubmit={handleCompleteOnboarding}
        error={onboardingError}
        busy={onboardingBusy}
        onLogout={handleLogout}
      />
    )
  }

  if (settingsOpen) {
    return (
      <SettingsPage
        settings={settings}
        onUpdate={updateSettings}
        onBack={() => navigateToPath(activeSong ? songPath(activeSong.id) : '/browse')}
        onLogout={handleLogout}
        user={appUser}
        spotifyEnabled={isSpotifyEnabled}
      />
    )
  }

  if (adminOpen && isAdmin) {
    return (
      <AdminPanel
        songs={songs}
        playlists={playlists}
        onBack={() => navigateToPath(activeSong ? songPath(activeSong.id) : '/browse')}
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

  if (activeSong) {
    return (
      <PlayerView
        song={activeSong}
        user={appUser}
        onBack={() => navigateToPath('/browse')}
        onLogout={handleLogout}
        onOpenSettings={() => navigateToPath('/settings')}
        onOpenAdmin={() => navigateToPath(adminPath('songs', activeSong.id))}
        isAdmin={isAdmin}
        onPrev={handlePrevSong}
        onNext={handleNextSong}
        canPrev={activeSongIndex > 0}
        canNext={activeSongIndex >= 0 && activeSongIndex < displayedSongs.length - 1}
        settings={settings}
      />
    )
  }

  return (
    <SongBrowser
      songs={displayedSongs}
      playlists={playlists}
      activePlaylistId={activePlaylistId}
      loading={songsLoading || playlistsLoading}
      error={songsError}
      onSelect={handleSelectSong}
      onSelectPlaylist={setActivePlaylistId}
      onOpenAdmin={() => navigateToPath('/admin')}
      isAdmin={isAdmin}
      onOpenSettings={() => navigateToPath('/settings')}
      onLogout={handleLogout}
      user={appUser}
      spotifyEnabled={isSpotifyEnabled}
    />
  )
}
