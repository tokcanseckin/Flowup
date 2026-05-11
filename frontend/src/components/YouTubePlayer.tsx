/**
 * YouTubePlayer — minimal iframe embed that exposes play/pause via the
 * YouTube IFrame API (postMessage).  The parent must supply a `youtubeUrl`
 * (any YouTube link format) and receives `onReady`, `onTimeUpdate`, and
 * imperative handles via a forwarded ref.
 */

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'

// ── helpers ────────────────────────────────────────────────────────────────────

function extractVideoId(url: string): string | null {
  try {
    const u = new URL(url)
    // youtu.be/VIDEO_ID
    if (u.hostname === 'youtu.be') return u.pathname.slice(1).split('?')[0]
    // youtube.com/watch?v=VIDEO_ID
    const v = u.searchParams.get('v')
    if (v) return v
    // youtube.com/embed/VIDEO_ID
    const parts = u.pathname.split('/')
    const embedIdx = parts.indexOf('embed')
    if (embedIdx !== -1) return parts[embedIdx + 1] ?? null
  } catch {
    // not a URL — treat the string itself as a video ID
    if (/^[A-Za-z0-9_-]{11}$/.test(url)) return url
  }
  return null
}

function describeYouTubeError(code: number): string {
  switch (code) {
    case 2:
      return 'YouTube error 2: Invalid video ID or request parameter.'
    case 5:
      return 'YouTube error 5: HTML5 playback failed for this video.'
    case 100:
      return 'YouTube error 100: Video not found or removed.'
    case 101:
    case 150:
      return 'YouTube error 101/150: Embedding blocked. Possible causes: (1) the owner restricted embedding to specific domains — localhost is often excluded even when the "embed" option appears enabled on YouTube; (2) the video requires sign-in or is age-restricted. Try the video on a deployed domain, or use a different video.'
    default:
      return `YouTube playback error (code ${code}).`
  }
}

function shouldLogYouTubeDebug(): boolean {
  if (import.meta.env.DEV) return true
  if (typeof window === 'undefined') return false
  return window.localStorage.getItem('flowup_debug_playback') === '1'
}

function logYouTubeDebug(message: string, data?: unknown) {
  if (!shouldLogYouTubeDebug()) return
  if (data === undefined) {
    console.debug('[SingoLing][YouTube]', message)
    return
  }
  console.debug('[SingoLing][YouTube]', message, data)
}

// ── types ──────────────────────────────────────────────────────────────────────

export interface YouTubePlayerHandle {
  play: () => void
  pause: () => void
  seekTo: (ms: number) => void
}

interface Props {
  youtubeUrl: string
  /** Called once the player reports it is ready (buffered). */
  onReady?: () => void
  /** Fires roughly every 250 ms with the current position in ms. */
  onTimeUpdate?: (positionMs: number) => void
  /** Fires when the player starts or stops playing. */
  onPlayStateChange?: (playing: boolean) => void
  /** Fires once (or whenever it changes) with the video duration in ms. */
  onDurationChange?: (durationMs: number) => void
}

declare namespace YT {
  interface OnStateChangeEvent {
    data: number
  }

  interface OnErrorEvent {
    data: number
  }

  interface Player {
    playVideo(): void
    pauseVideo(): void
    seekTo(seconds: number, allowSeekAhead: boolean): void
    getCurrentTime?(): number
    getDuration?(): number
    getPlayerState?(): number
    getVideoUrl?(): string
    destroy(): void
  }

  interface PlayerConstructor {
    new (
      element: HTMLElement,
      options: {
        videoId: string
        playerVars?: Record<string, number>
        events?: {
          onReady?: () => void
          onStateChange?: (event: OnStateChangeEvent) => void
          onError?: (event: OnErrorEvent) => void
        }
      },
    ): Player
  }

  interface YTNamespace {
    Player: PlayerConstructor
    PlayerState: {
      UNSTARTED: number
      ENDED: number
      PLAYING: number
      PAUSED: number
      BUFFERING: number
      CUED: number
    }
  }
}

declare global {
  interface Window {
    YT: YT.YTNamespace
    onYouTubeIframeAPIReady: (() => void) | undefined
  }
}

// ── component ─────────────────────────────────────────────────────────────────

const YouTubePlayer = forwardRef<YouTubePlayerHandle, Props>(function YouTubePlayer(
  { youtubeUrl, onReady, onTimeUpdate, onPlayStateChange, onDurationChange },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const ytRef = useRef<YT.Player | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const readyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const playbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const lastLoggedSecondRef = useRef(-1)
  // Ad detection state
  const expectedVideoIdRef = useRef<string | null>(null)
  const prevYTStateRef = useRef<number>(-2)
  const hasPlayedSongRef = useRef(false)
  const suspiciousTransitionRef = useRef(false)

  // Expose imperative API to parent
  useImperativeHandle(ref, () => ({
    play: () => ytRef.current?.playVideo(),
    pause: () => ytRef.current?.pauseVideo(),
    seekTo: (ms: number) => ytRef.current?.seekTo(ms / 1000, true),
  }))

  // Load / reload player whenever the URL changes
  useEffect(() => {
    setError(null)
    setWarning(null)
    const videoId = extractVideoId(youtubeUrl)
    logYouTubeDebug('Player init requested', { youtubeUrl, videoId })
    if (!videoId) {
      setError('Invalid YouTube URL')
      logYouTubeDebug('Invalid YouTube URL supplied', { youtubeUrl })
      return
    }
    const resolvedVideoId = videoId

    function startPolling() {
      if (timerRef.current) clearInterval(timerRef.current)
      let reportedDurMs = 0
      timerRef.current = setInterval(() => {
        const player = ytRef.current
        if (!player) return
        const posS = player.getCurrentTime?.()
        if (posS !== undefined) {
          const posMs = Math.floor(posS * 1000)
          onTimeUpdate?.(posMs)
          const sec = Math.floor(posS)
          if (sec >= 0 && sec % 5 === 0 && sec !== lastLoggedSecondRef.current) {
            lastLoggedSecondRef.current = sec
            logYouTubeDebug('Playback progress', { sec, videoId: resolvedVideoId })
          }
        }
        const durS = player.getDuration?.()
        if (durS && durS > 0) {
          const durMs = Math.floor(durS * 1000)
          if (durMs !== reportedDurMs) {
            reportedDurMs = durMs
            onDurationChange?.(durMs)
          }
        }
      }, 250)
    }

    function clearTimers() {
      if (readyTimerRef.current) { clearTimeout(readyTimerRef.current); readyTimerRef.current = null }
      if (playbackTimerRef.current) { clearTimeout(playbackTimerRef.current); playbackTimerRef.current = null }
    }

    function buildPlayer() {
      if (!containerRef.current) return
      logYouTubeDebug('Building iframe player', { videoId: resolvedVideoId })
      // destroy previous instance
      ytRef.current?.destroy()
      ytRef.current = null

      // If onReady never fires the video likely can't be embedded at all
      clearTimers()
      readyTimerRef.current = setTimeout(() => {
        setError('Player timed out — the video may not be embeddable or your connection is slow.')
        onPlayStateChange?.(false)
        logYouTubeDebug('Ready timeout', { videoId: resolvedVideoId })
      }, 15_000)

      const div = document.createElement('div')
      containerRef.current.innerHTML = ''
      containerRef.current.appendChild(div)

      // Reset ad-detection state for this video
      expectedVideoIdRef.current = resolvedVideoId
      prevYTStateRef.current = -2
      hasPlayedSongRef.current = false
      suspiciousTransitionRef.current = false

      ytRef.current = new window.YT.Player(div, {
        videoId: resolvedVideoId,
        playerVars: {
          autoplay: 0,
          controls: 0,
          rel: 0,
          modestbranding: 1,
          iv_load_policy: 3,
          fs: 0,
        },
        events: {
          onReady: () => {
            logYouTubeDebug('Player ready', { videoId: resolvedVideoId })
            // Cancel the ready-timeout — iframe loaded successfully
            if (readyTimerRef.current) { clearTimeout(readyTimerRef.current); readyTimerRef.current = null }
            onReady?.()
            startPolling()
            if (document.hasFocus()) {
              ytRef.current?.playVideo()
              // If playback doesn't start within 7 s the video is likely
              // age-restricted, requires sign-in, or is region-blocked.
              playbackTimerRef.current = setTimeout(() => {
                const state = ytRef.current?.getPlayerState?.()
                // -1 = unstarted, 5 = video cued — neither means it's playing
                if (state === -1 || state === 5 || state === undefined) {
                  setWarning('Video loaded but playback did not start — it may be age-restricted, require sign-in, or be region-blocked.')
                  logYouTubeDebug('Playback stuck after ready', { videoId: resolvedVideoId, state })
                }
              }, 7_000)
            }
          },
          onStateChange: (e: YT.OnStateChangeEvent) => {
            const curr = e.data
            const prev = prevYTStateRef.current
            const UNSTARTED = -1, PLAYING = 1, BUFFERING = 3

            logYouTubeDebug('State changed', { videoId: resolvedVideoId, state: curr, prev })

            // Step 1: Flag suspicious re-init (UNSTARTED→BUFFERING after song has played)
            if (prev === UNSTARTED && curr === BUFFERING && hasPlayedSongRef.current) {
              suspiciousTransitionRef.current = true
              logYouTubeDebug('Suspicious transition detected (possible ad injection)', { videoId: resolvedVideoId })
            }

            // Step 2: Update prevState; skip deep checks for non-active states
            prevYTStateRef.current = curr
            if (curr !== PLAYING && curr !== BUFFERING) {
              if (playbackTimerRef.current) { clearTimeout(playbackTimerRef.current); playbackTimerRef.current = null }
              onPlayStateChange?.(false)
              return
            }

            // Steps 3 & 4: ID and duration checks — only reliable during PLAYING
            let idMismatch = false
            let shortDuration = false
            if (curr === PLAYING) {
              // ID check via getVideoUrl (public IFrame API)
              const liveUrl = ytRef.current?.getVideoUrl?.()
              if (liveUrl && expectedVideoIdRef.current) {
                const liveId = extractVideoId(liveUrl)
                idMismatch = liveId !== null && liveId !== expectedVideoIdRef.current
                if (idMismatch) logYouTubeDebug('Ad detected: video ID mismatch', { expected: expectedVideoIdRef.current, live: liveId })
              }
              // Duration check: ads are universally < 60 s
              const durS = ytRef.current?.getDuration?.() ?? 0
              shortDuration = durS > 0 && durS < 60
              if (shortDuration && suspiciousTransitionRef.current) {
                logYouTubeDebug('Ad detected: short duration after suspicious transition', { durS })
              }
            }

            // Step 5: Verdict
            const isAd = idMismatch || (shortDuration && suspiciousTransitionRef.current)

            // Clear playback-stuck timer on any active state
            if (curr === PLAYING || curr === BUFFERING) {
              if (playbackTimerRef.current) { clearTimeout(playbackTimerRef.current); playbackTimerRef.current = null }
            }

            if (isAd) {
              onPlayStateChange?.(false)
            } else {
              if (curr === PLAYING) {
                hasPlayedSongRef.current = true
                suspiciousTransitionRef.current = false
                setWarning(null)
              }
              onPlayStateChange?.(curr === PLAYING)
            }
          },
          onError: (e: YT.OnErrorEvent) => {
            const message = describeYouTubeError(e.data)
            setError(message)
            onPlayStateChange?.(false)
            if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
            logYouTubeDebug('Player error', { videoId: resolvedVideoId, code: e.data, message, youtubeUrl })
          },
        },
      })
    }

    if (window.YT?.Player) {
      logYouTubeDebug('YT API already loaded; building immediately', { videoId: resolvedVideoId })
      buildPlayer()
    } else {
      // Inject API script once
      if (!document.getElementById('yt-iframe-api')) {
        const script = document.createElement('script')
        script.id = 'yt-iframe-api'
        script.src = 'https://www.youtube.com/iframe_api'
        document.head.appendChild(script)
        logYouTubeDebug('Injected YT Iframe API script')
      }
      window.onYouTubeIframeAPIReady = buildPlayer
      logYouTubeDebug('Waiting for YT API ready callback', { videoId: resolvedVideoId })
    }

    return () => {
      clearTimers()
      if (timerRef.current) clearInterval(timerRef.current)
      ytRef.current?.destroy()
      ytRef.current = null
      logYouTubeDebug('Player cleaned up', { videoId: resolvedVideoId })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [youtubeUrl])

  return (
    <div className="w-full h-full flex flex-col space-y-2">
      {error ? (
        <div className="rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      ) : (
        <div className="relative flex-1 min-h-0">
          <div
            ref={containerRef}
            className="absolute inset-0 bg-black [&_iframe]:w-full [&_iframe]:h-full"
          />
        </div>
      )}
      {!error && warning && (
        <div className="rounded-xl border border-yellow-900/50 bg-yellow-950/20 px-4 py-3 text-sm text-yellow-400">
          {warning}
        </div>
      )}
    </div>
  )
})

export default YouTubePlayer
