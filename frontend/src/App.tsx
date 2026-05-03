import { useState, useCallback } from 'react'
import { useSpotifyPlayer } from './hooks/useSpotifyPlayer'
import LyricsPlayer from './components/LyricsPlayer'
import songData from './data/song_data.json'

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatMs(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  return `${min}:${sec.toString().padStart(2, '0')}`
}

// ── Auth / Token screen ───────────────────────────────────────────────────────

interface AuthScreenProps {
  onConnect: (token: string) => void
}

function AuthScreen({ onConnect }: AuthScreenProps) {
  const [input, setInput] = useState('')

  return (
    <div className="min-h-screen flex items-center justify-center p-4"
         style={{ background: 'radial-gradient(ellipse 120% 80% at 50% 110%, #1a1040 0%, #0d0d14 60%)' }}>
      <div className="w-full max-w-md">

        {/* Logo mark */}
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
            Learn Russian through music.<br/>
            Real lyrics. Real grammar. Real context.
          </p>
        </div>

        {/* Card */}
        <div className="rounded-2xl border border-gray-800/80 p-8 shadow-2xl"
             style={{ background: '#12121f' }}>

          <h2 className="text-white font-semibold text-base mb-1">Connect Spotify</h2>
          <p className="text-gray-500 text-sm mb-5 leading-relaxed">
            Grab a temporary token from{' '}
            <span className="text-indigo-400 font-medium">developer.spotify.com/console</span>.
            Required scopes:{' '}
            <code className="bg-gray-800 text-indigo-300 text-xs px-1.5 py-0.5 rounded">streaming</code>
            {' '}<code className="bg-gray-800 text-indigo-300 text-xs px-1.5 py-0.5 rounded">user-modify-playback-state</code>
          </p>

          <textarea
            className="
              w-full rounded-xl border border-gray-700 bg-gray-900/70 px-4 py-3
              text-white text-sm font-mono placeholder-gray-600 resize-none h-24
              focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30
              transition-all
            "
            placeholder="Paste your access token here…"
            value={input}
            onChange={e => setInput(e.target.value)}
            spellCheck={false}
          />

          <button
            onClick={() => input.trim() && onConnect(input.trim())}
            disabled={!input.trim()}
            className="
              mt-4 w-full py-3 rounded-xl font-semibold text-sm
              bg-indigo-600 hover:bg-indigo-500 active:scale-[0.98]
              disabled:bg-gray-800 disabled:text-gray-600
              text-white transition-all duration-150 shadow-lg shadow-indigo-900/30
            "
          >
            Connect Player
          </button>

          <p className="text-gray-700 text-xs text-center mt-4">
            Spotify Premium account required for SDK playback
          </p>
        </div>
      </div>
    </div>
  )
}

// ── Progress bar ──────────────────────────────────────────────────────────────

interface ProgressBarProps {
  posMs: number
  durMs: number
  onSeek: (ms: number) => void
}

function ProgressBar({ posMs, durMs, onSeek }: ProgressBarProps) {
  const pct = durMs > 0 ? Math.min((posMs / durMs) * 100, 100) : 0

  const handleClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    onSeek(Math.floor(frac * durMs))
  }, [durMs, onSeek])

  return (
    <div className="flex items-center gap-3">
      <span className="text-xs font-mono text-gray-500 w-9 text-right shrink-0">{formatMs(posMs)}</span>
      <div
        className="flex-1 h-1.5 bg-gray-800 rounded-full cursor-pointer group"
        onClick={handleClick}
      >
        <div
          className="h-full bg-indigo-500 rounded-full relative transition-all duration-100 group-hover:bg-indigo-400"
          style={{ width: `${pct}%` }}
        >
          <div className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-white opacity-0 group-hover:opacity-100 translate-x-1/2 transition-opacity" />
        </div>
      </div>
      <span className="text-xs font-mono text-gray-500 w-9 shrink-0">{formatMs(durMs)}</span>
    </div>
  )
}

// ── Main App ──────────────────────────────────────────────────────────────────

export default function App() {
  const [token, setToken]       = useState<string | null>(null)
  const [trackUri, setTrackUri] = useState(songData.spotify_uri)
  const [loading, setLoading]   = useState(false)

  const player = useSpotifyPlayer(token)

  const handleLoadTrack = useCallback(async () => {
    setLoading(true)
    await player.loadAndPlayTrack(trackUri)
    setLoading(false)
  }, [player, trackUri])

  if (!token) {
    return <AuthScreen onConnect={setToken} />
  }

  return (
    <div className="min-h-screen text-white" style={{ background: '#0d0d14' }}>

      {/* ── Header ────────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-30 border-b border-gray-800/60 backdrop-blur-md"
              style={{ background: 'rgba(13,13,20,0.85)' }}>
        <div className="max-w-3xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-indigo-600 flex items-center justify-center">
              <svg viewBox="0 0 24 24" className="w-4 h-4 fill-white">
                <path d="M12 3a9 9 0 100 18A9 9 0 0012 3zm-1 13V8l6 4-6 4z"/>
              </svg>
            </div>
            <span className="font-bold tracking-tight">
              Flow<span className="text-indigo-400">Up</span>
            </span>
          </div>

          {/* Connection status */}
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                player.isReady ? 'bg-emerald-400' : 'bg-amber-400 animate-pulse'
              }`}
            />
            <span className="text-xs text-gray-500">
              {player.isReady ? 'Player ready' : 'Connecting…'}
            </span>
            <button
              onClick={() => setToken(null)}
              className="ml-3 text-xs text-gray-600 hover:text-gray-400 transition-colors"
            >
              Disconnect
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8 space-y-5">

        {/* ── Error banner ─────────────────────────────────────────────────── */}
        {player.error && (
          <div className="rounded-xl border border-red-800/50 bg-red-950/30 px-5 py-4 text-sm text-red-300">
            <span className="font-medium">Error: </span>{player.error}
          </div>
        )}

        {/* ── Track loader ──────────────────────────────────────────────────── */}
        <section className="rounded-2xl border border-gray-800/80 p-5"
                 style={{ background: '#12121f' }}>
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-3">
            Track URI
          </p>
          <div className="flex gap-3">
            <input
              className="
                flex-1 rounded-xl border border-gray-700 bg-gray-900/70 px-4 py-2.5
                text-white text-sm font-mono placeholder-gray-600
                focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30
                transition-all
              "
              value={trackUri}
              onChange={e => setTrackUri(e.target.value)}
              placeholder="spotify:track:…"
              spellCheck={false}
            />
            <button
              onClick={handleLoadTrack}
              disabled={!player.isReady || loading}
              className="
                shrink-0 px-5 py-2.5 rounded-xl text-sm font-semibold
                bg-indigo-600 hover:bg-indigo-500 active:scale-[0.97]
                disabled:bg-gray-800 disabled:text-gray-600
                text-white transition-all duration-150
              "
            >
              {loading ? 'Loading…' : 'Load Track'}
            </button>
          </div>
        </section>

        {/* ── Player controls ───────────────────────────────────────────────── */}
        <section className="rounded-2xl border border-gray-800/80 p-5"
                 style={{ background: '#12121f' }}>

          {/* Album art + track info */}
          <div className="flex items-center gap-4 mb-5">
            {player.albumArtUrl ? (
              <img
                src={player.albumArtUrl}
                alt="Album art"
                className="w-14 h-14 rounded-xl object-cover shadow-lg"
              />
            ) : (
              <div className="w-14 h-14 rounded-xl bg-gray-800 flex items-center justify-center">
                <svg viewBox="0 0 24 24" className="w-6 h-6 fill-gray-600">
                  <path d="M12 3a9 9 0 100 18A9 9 0 0012 3zm-1 13V8l6 4-6 4z"/>
                </svg>
              </div>
            )}
            <div className="min-w-0">
              <p className="text-white font-semibold truncate">
                {player.currentTrackName ?? songData.title}
              </p>
              <p className="text-gray-500 text-sm truncate">
                {player.currentTrackArtist ?? 'Load a track to begin'}
              </p>
            </div>
          </div>

          {/* Progress */}
          <ProgressBar
            posMs={player.currentPositionMs}
            durMs={player.durationMs}
            onSeek={player.seekTo}
          />

          {/* Transport */}
          <div className="flex items-center justify-center mt-5">
            <button
              onClick={player.togglePlay}
              disabled={!player.isReady}
              aria-label={player.isPlaying ? 'Pause' : 'Play'}
              className="
                w-14 h-14 rounded-full flex items-center justify-center
                bg-indigo-600 hover:bg-indigo-500 active:scale-95
                disabled:bg-gray-800 disabled:text-gray-600
                text-white shadow-lg shadow-indigo-900/40
                transition-all duration-150
              "
            >
              {player.isPlaying ? (
                <svg viewBox="0 0 24 24" className="w-6 h-6 fill-current">
                  <rect x="6"  y="5" width="4" height="14" rx="1.5"/>
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

        {/* ── Lyrics panel ──────────────────────────────────────────────────── */}
        <section className="rounded-2xl border border-gray-800/80 overflow-hidden"
                 style={{ background: '#12121f' }}>

          {/* Panel header */}
          <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800/80">
            <div>
              <h2 className="font-semibold text-white">{songData.title}</h2>
              <p className="text-xs text-gray-600 mt-0.5">
                Press{' '}
                <kbd className="font-mono bg-gray-800 text-gray-400 px-1 py-0.5 rounded text-[10px]">1</kbd>
                –
                <kbd className="font-mono bg-gray-800 text-gray-400 px-1 py-0.5 rounded text-[10px]">9</kbd>
                {' '}to inspect a word in the active line
              </p>
            </div>
            <span className="text-xs font-mono text-indigo-400 bg-indigo-950/50 border border-indigo-900/50 px-2.5 py-1 rounded-lg">
              {formatMs(player.currentPositionMs)}
            </span>
          </div>

          <LyricsPlayer currentPositionMs={player.currentPositionMs} />
        </section>
      </main>
    </div>
  )
}
