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
      return 'YouTube error 101/150: Video owner disabled embedding.'
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
    console.debug('[FlowUp][YouTube]', message)
    return
  }
  console.debug('[FlowUp][YouTube]', message, data)
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
      PLAYING: number
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
  { youtubeUrl, onReady, onTimeUpdate, onPlayStateChange },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const ytRef = useRef<YT.Player | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const lastLoggedSecondRef = useRef(-1)

  // Expose imperative API to parent
  useImperativeHandle(ref, () => ({
    play: () => ytRef.current?.playVideo(),
    pause: () => ytRef.current?.pauseVideo(),
    seekTo: (ms: number) => ytRef.current?.seekTo(ms / 1000, true),
  }))

  // Load / reload player whenever the URL changes
  useEffect(() => {
    setError(null)
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
      }, 250)
    }

    function buildPlayer() {
      if (!containerRef.current) return
      logYouTubeDebug('Building iframe player', { videoId: resolvedVideoId })
      // destroy previous instance
      ytRef.current?.destroy()
      ytRef.current = null

      const div = document.createElement('div')
      containerRef.current.innerHTML = ''
      containerRef.current.appendChild(div)

      ytRef.current = new window.YT.Player(div, {
        videoId: resolvedVideoId,
        playerVars: {
          autoplay: 1,
          controls: 0,
          rel: 0,
          modestbranding: 1,
          iv_load_policy: 3,
          fs: 0,
        },
        events: {
          onReady: () => {
            logYouTubeDebug('Player ready', { videoId: resolvedVideoId })
            onReady?.()
            startPolling()
          },
          onStateChange: (e: YT.OnStateChangeEvent) => {
            // 1 = playing, 2 = paused
            logYouTubeDebug('State changed', { videoId: resolvedVideoId, state: e.data })
            onPlayStateChange?.(e.data === window.YT.PlayerState.PLAYING)
          },
          onError: (e: YT.OnErrorEvent) => {
            const message = describeYouTubeError(e.data)
            setError(message)
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
      if (timerRef.current) clearInterval(timerRef.current)
      ytRef.current?.destroy()
      ytRef.current = null
      logYouTubeDebug('Player cleaned up', { videoId: resolvedVideoId })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [youtubeUrl])

  return (
    <div className="w-full">
      {error ? (
        <div className="rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      ) : (
        <div
          ref={containerRef}
          className="w-full aspect-video rounded-xl overflow-hidden bg-black"
        />
      )}
    </div>
  )
})

export default YouTubePlayer
