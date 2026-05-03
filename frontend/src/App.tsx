import { useState, useCallback, useEffect, useRef } from 'react'
import { useSpotifyAuth }   from './hooks/useSpotifyAuth'
import { useSpotifyPlayer } from './hooks/useSpotifyPlayer'
import LyricsPlayer         from './components/LyricsPlayer'
import { api, SongDetail, SongSummary } from './api/client'

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatMs(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  return `${min}:${sec.toString().padStart(2, '0')}`
}

// ── Login screen ──────────────────────────────────────────────────────────────

function LoginScreen({ onLogin, error }: { onLogin: () => void; error: string | null }) {
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
              Flow<span className="text-indigo-400">Up</span>
            </h1>
          </div>
          <p className="text-gray-500 text-sm leading-relaxed max-w-xs mx-auto">
            Learn languages through music.<br/>
            Real lyrics. Real grammar. Real context.
          </p>
        </div>

        <div className="rounded-2xl border border-gray-800/80 p-8 shadow-2xl"
             style={{ background: '#12121f' }}>
          <h2 className="text-white font-semibold text-base mb-1">Connect Spotify</h2>
          <p className="text-gray-500 text-sm mb-6 leading-relaxed">
            Sign in with your Spotify account. Spotify Premium is required for SDK playback.
          </p>

          {error && (
            <div className="mb-4 rounded-xl border border-red-900/50 bg-red-950/30 px-4 py-3 text-sm text-red-400">
              {error}
            </div>
          )}

          <button
            onClick={onLogin}
            className="
              w-full py-3 rounded-xl font-semibold text-sm flex items-center justify-center gap-3
              bg-[#1DB954] hover:bg-[#1ed760] active:scale-[0.98]
              text-black transition-all duration-150 shadow-lg shadow-green-900/20
            "
          >
            <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
              <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
            </svg>
            Continue with Spotify
          </button>

          <p className="text-gray-700 text-xs text-center mt-4">
            Scopes requested: streaming, playback-state, profile
          </p>
        </div>
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

function SongBrowser({
  songs, loading, error, onSelect, onLogout, user,
}: {
  songs: SongSummary[]
  loading: boolean
  error: string | null
  onSelect: (id: number) => void
  onLogout: () => void
  user: { display_name: string | null } | null
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
            Flow<span className="text-indigo-400">Up</span>
          </h1>
        </div>
        <div className="flex items-center gap-3">
          {user?.display_name && (
            <span className="text-xs text-gray-500">{user.display_name}</span>
          )}
          <button onClick={onLogout} className="text-xs text-gray-600 hover:text-gray-400 transition-colors">
            Sign out
          </button>
        </div>
      </div>

      <h2 className="text-white font-semibold text-lg mb-4">Choose a song</h2>

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

// ── Player view ───────────────────────────────────────────────────────────────

function PlayerView({
  song, user, onBack, onLogout,
}: {
  song: SongDetail
  user: { display_name: string | null; images: { url: string }[] } | null
  onBack: () => void
  onLogout: () => void
}) {
  const [trackUri, setTrackUri] = useState(song.spotify_uri)
  const [loading,  setLoading]  = useState(false)
  const [infoVisible, setInfoVisible] = useState(false)
  const autoPausedRef = useRef(false)
  const player = useSpotifyPlayer(localStorage.getItem('sp_access_token'))

  const handleLoadTrack = useCallback(async () => {
    setLoading(true)
    await player.loadAndPlayTrack(trackUri)
    setLoading(false)
  }, [player, trackUri])

  useEffect(() => {
    if (infoVisible) {
      if (player.isPlaying) {
        player.pause()
        autoPausedRef.current = true
      }
      return
    }

    if (autoPausedRef.current) {
      player.resume()
      autoPausedRef.current = false
    }
  }, [infoVisible, player.isPlaying, player.pause, player.resume])

  return (
    <div className="min-h-screen flex flex-col" style={{ background: '#0d0d14' }}>

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
          <span className="font-bold text-white text-sm">Flow<span className="text-indigo-400">Up</span></span>
        </div>
        <div className="flex items-center gap-3">
          {player.isReady && (
            <span className="flex items-center gap-1.5 text-xs text-green-400">
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
              Connected
            </span>
          )}
          {user?.display_name && <span className="text-xs text-gray-500">{user.display_name}</span>}
          <button onClick={onLogout} className="text-xs text-gray-600 hover:text-gray-400 transition-colors">Sign out</button>
        </div>
      </header>

      {player.error && (
        <div className="mx-4 mt-4 rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">
          {player.error}
        </div>
      )}

      <main className="flex-1 p-4 max-w-2xl mx-auto w-full space-y-4">

        {/* Track URI loader */}
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

        {/* Player controls */}
        <section className="rounded-2xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
          <div className="flex items-center gap-4 mb-5">
            {player.albumArtUrl ? (
              <img src={player.albumArtUrl} alt="Album art" className="w-14 h-14 rounded-xl object-cover shadow-lg" />
            ) : (
              <div className="w-14 h-14 rounded-xl bg-gray-800 flex items-center justify-center">
                <svg viewBox="0 0 24 24" className="w-6 h-6 fill-gray-600">
                  <path d="M12 3a9 9 0 100 18A9 9 0 0012 3zm-1 13V8l6 4-6 4z"/>
                </svg>
              </div>
            )}
            <div className="min-w-0">
              <p className="text-white font-semibold truncate">{player.currentTrackName ?? song.title}</p>
              <p className="text-gray-500 text-sm truncate">{player.currentTrackArtist ?? (song.artist ?? 'Load a track to begin')}</p>
            </div>
          </div>
          <ProgressBar posMs={player.currentPositionMs} durMs={player.durationMs} onSeek={player.seekTo} />
          <div className="flex items-center justify-center mt-5">
            <button
              onClick={player.togglePlay}
              disabled={!player.isReady}
              aria-label={player.isPlaying ? 'Pause' : 'Play'}
              className="
                w-14 h-14 rounded-full flex items-center justify-center
                bg-indigo-600 hover:bg-indigo-500 active:scale-95
                disabled:bg-gray-800 disabled:text-gray-600
                text-white shadow-lg shadow-indigo-900/40 transition-all duration-150
              "
            >
              {player.isPlaying ? (
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
                <kbd className="font-mono bg-gray-800 text-gray-400 px-1 py-0.5 rounded text-[10px]">0</kbd>
                {' '}for line translation,{' '}
                <kbd className="font-mono bg-gray-800 text-gray-400 px-1 py-0.5 rounded text-[10px]">1</kbd>
                –
                <kbd className="font-mono bg-gray-800 text-gray-400 px-1 py-0.5 rounded text-[10px]">9</kbd>
                {' '}to inspect a word
              </p>
            </div>
            <span className="text-xs font-mono text-indigo-400 bg-indigo-950/50 border border-indigo-900/50 px-2.5 py-1 rounded-lg">
              {formatMs(player.currentPositionMs)}
            </span>
          </div>
          <LyricsPlayer
            currentPositionMs={player.currentPositionMs}
            songData={song}
            onInfoVisibilityChange={setInfoVisible}
            onSeek={player.seekTo}
          />
        </section>
      </main>
    </div>
  )
}

// ── Root App ──────────────────────────────────────────────────────────────────

export default function App() {
  const auth = useSpotifyAuth()

  const [songs,        setSongs]        = useState<SongSummary[]>([])
  const [songsLoading, setSongsLoading] = useState(false)
  const [songsError,   setSongsError]   = useState<string | null>(null)
  const [activeSong,   setActiveSong]   = useState<SongDetail | null>(null)
  const [songLoading,  setSongLoading]  = useState(false)

  // Fetch song list once authenticated
  useEffect(() => {
    if (!auth.isAuthenticated) return
    setSongsLoading(true)
    setSongsError(null)
    api.listSongs()
      .then(setSongs)
      .catch(e => setSongsError(e instanceof Error ? e.message : 'Failed to load songs'))
      .finally(() => setSongsLoading(false))
  }, [auth.isAuthenticated])

  // Sync user to backend (non-fatal if backend is down)
  useEffect(() => {
    if (!auth.isAuthenticated || !auth.user || !auth.accessToken) return
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
    }).catch(() => { /* backend may not be running */ })
  }, [auth.isAuthenticated, auth.user, auth.accessToken])

  const handleSelectSong = useCallback(async (id: number) => {
    setSongLoading(true)
    try {
      const detail = await api.getSong(id)
      setActiveSong(detail)
    } catch (e) {
      setSongsError(e instanceof Error ? e.message : 'Failed to load song')
    } finally {
      setSongLoading(false)
    }
  }, [])

  if (auth.isLoading)  return <LoadingScreen message="Restoring session..." />
  if (!auth.isAuthenticated) return <LoginScreen onLogin={auth.login} error={auth.error} />
  if (songLoading)     return <LoadingScreen message="Loading song..." />

  if (activeSong) {
    return (
      <PlayerView
        song={activeSong}
        user={auth.user}
        onBack={() => setActiveSong(null)}
        onLogout={auth.logout}
      />
    )
  }

  return (
    <SongBrowser
      songs={songs}
      loading={songsLoading}
      error={songsError}
      onSelect={handleSelectSong}
      onLogout={auth.logout}
      user={auth.user}
    />
  )
}
