/**
 * AppleMusicPlayer — MusicKit JS v3 wrapper.
 *
 * Flow:
 *  1. Inject the MusicKit JS script from Apple's CDN.
 *  2. Fetch a developer token from our backend (/api/apple-music/token).
 *  3. Configure MusicKit with that token.
 *  4. If the user has not authorized yet, show an "Authorize with Apple Music"
 *     button that triggers the Apple ID sign-in popup.
 *  5. Once authorized, set the queue from the apple_music_url and start playing.
 *  6. Expose play / pause / seekTo imperatively via a forwarded ref.
 */

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'

// ── Minimal MusicKit v3 type declarations ──────────────────────────────────────

declare global {
  interface Window {
    MusicKit: MusicKitStatic | undefined
  }
}

interface MusicKitStatic {
  configure(config: {
    developerToken: string
    app: { name: string; build: string }
  }): Promise<MusicKitInstance>
  getInstance(): MusicKitInstance
  readonly Events: {
    playbackStateDidChange: string
    playbackTimeDidChange: string
  }
  readonly PlaybackStates: {
    playing: number
    paused: number
    stopped: number
    completed: number
    loading: number
    seeking: number
    waiting: number
    stalled: number
  }
}

interface MusicKitInstance {
  authorize(): Promise<string>
  unauthorize(): Promise<void>
  setQueue(opts: { url?: string; song?: string; startWith?: number }): Promise<void>
  play(): Promise<void>
  pause(): void
  stop(): void
  seekToTime(seconds: number): Promise<void>
  addEventListener(event: string, handler: (ev: Record<string, unknown>) => void): void
  removeEventListener(event: string, handler: (ev: Record<string, unknown>) => void): void
  readonly currentPlaybackTime: number      // seconds
  readonly currentPlaybackDuration: number  // seconds
  readonly playbackState: number
  readonly isAuthorized: boolean
}

// ── helpers ────────────────────────────────────────────────────────────────────

const MUSICKIT_SCRIPT_ID = 'apple-musickit-js'
const MUSICKIT_SRC = 'https://js-cdn.music.apple.com/musickit/v3/musickit.js'

function shouldLogAppleMusicDebug(): boolean {
  if (import.meta.env.DEV) return true
  if (typeof window === 'undefined') return false
  return window.localStorage.getItem('flowup_debug_playback') === '1'
}

function logAppleMusicDebug(message: string, data?: unknown) {
  if (!shouldLogAppleMusicDebug()) return
  if (data === undefined) {
    console.debug('[SingoLing][AppleMusic]', message)
    return
  }
  console.debug('[SingoLing][AppleMusic]', message, data)
}

function loadMusickitScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (document.getElementById(MUSICKIT_SCRIPT_ID)) {
      // Already injected — wait for musickitloaded or resolve immediately if ready
      if (window.MusicKit) resolve()
      else document.addEventListener('musickitloaded', () => resolve(), { once: true })
      return
    }
    const script = document.createElement('script')
    script.id = MUSICKIT_SCRIPT_ID
    script.src = MUSICKIT_SRC
    script.async = true
    script.onerror = () => reject(new Error('Failed to load MusicKit JS'))
    document.addEventListener('musickitloaded', () => resolve(), { once: true })
    document.head.appendChild(script)
  })
}

async function fetchDeveloperToken(): Promise<string> {
  logAppleMusicDebug('Requesting developer token')
  const r = await fetch('/api/apple-music/token')
  if (!r.ok) {
    const body = await r.json().catch(() => ({ detail: r.statusText })) as { detail?: string }
    logAppleMusicDebug('Developer token request failed', { status: r.status, detail: body.detail })
    throw new Error(body.detail ?? `Token fetch failed (${r.status})`)
  }
  const { token } = await r.json() as { token: string }
  logAppleMusicDebug('Developer token received', { tokenLength: token.length })
  return token
}

// Singleton MusicKit instance so we only configure it once per page load.
let _mkInstance: MusicKitInstance | null = null

// ── component ──────────────────────────────────────────────────────────────────

export interface AppleMusicPlayerHandle {
  play: () => void
  pause: () => void
  seekTo: (ms: number) => void
}

interface Props {
  appleMusicUrl: string
  onReady?: () => void
  /** Fires ~4× per second with current position and total duration in ms. */
  onTimeUpdate?: (positionMs: number, durationMs: number) => void
  onPlayStateChange?: (playing: boolean) => void
}

type Status = 'loading' | 'needs-auth' | 'authorizing' | 'playing' | 'error'

const AppleMusicPlayer = forwardRef<AppleMusicPlayerHandle, Props>(function AppleMusicPlayer(
  { appleMusicUrl, onReady, onTimeUpdate, onPlayStateChange },
  ref,
) {
  const [status, setStatus] = useState<Status>('loading')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const mountedRef = useRef(true)

  // Keep latest callbacks in refs to avoid stale closures in event handlers
  const onTimeUpdateRef = useRef(onTimeUpdate)
  const onPlayStateChangeRef = useRef(onPlayStateChange)
  useEffect(() => { onTimeUpdateRef.current = onTimeUpdate }, [onTimeUpdate])
  useEffect(() => { onPlayStateChangeRef.current = onPlayStateChange }, [onPlayStateChange])

  // ── Imperative API ───────────────────────────────────────────────────────────

  useImperativeHandle(ref, () => ({
    play: () => { void _mkInstance?.play() },
    pause: () => { _mkInstance?.pause() },
    seekTo: (ms: number) => { void _mkInstance?.seekToTime(ms / 1000) },
  }))

  // ── Event handlers (stable) ──────────────────────────────────────────────────

  const handleStateChange = useCallback((ev: Record<string, unknown>) => {
    const state = ev.state as number | undefined
    const MK = window.MusicKit
    if (!MK) return
    const playing = state === MK.PlaybackStates.playing
    logAppleMusicDebug('State changed', { state, playing })
    onPlayStateChangeRef.current?.(playing)
  }, [])

  const handleTimeChange = useCallback((ev: Record<string, unknown>) => {
    const posS = (ev.currentPlaybackTime as number | undefined) ?? _mkInstance?.currentPlaybackTime ?? 0
    const durS = _mkInstance?.currentPlaybackDuration ?? 0
    onTimeUpdateRef.current?.(Math.floor(posS * 1000), Math.floor(durS * 1000))
  }, [])

  // ── Load + configure + authorise ─────────────────────────────────────────────

  const initAndPlay = useCallback(async () => {
    if (!mountedRef.current) return
    setStatus('loading')
    setErrorMsg(null)
    logAppleMusicDebug('Init started', { appleMusicUrl })

    try {
      // 1. Load MusicKit JS
      await loadMusickitScript()
      const MK = window.MusicKit
      if (!MK) throw new Error('MusicKit not available after script load')
      logAppleMusicDebug('MusicKit script loaded')

      // 2. Configure (once)
      if (!_mkInstance) {
        const devToken = await fetchDeveloperToken()
        _mkInstance = await MK.configure({
          developerToken: devToken,
          app: { name: 'SingoLing', build: '1.0' },
        })
        logAppleMusicDebug('MusicKit configured')
      } else {
        logAppleMusicDebug('Reusing existing MusicKit instance')
      }

      const music = _mkInstance

      // 3. Attach events
      music.removeEventListener(MK.Events.playbackStateDidChange, handleStateChange)
      music.removeEventListener(MK.Events.playbackTimeDidChange, handleTimeChange)
      music.addEventListener(MK.Events.playbackStateDidChange, handleStateChange)
      music.addEventListener(MK.Events.playbackTimeDidChange, handleTimeChange)

      // 4. Authorise if needed.
      //    authorize() requires a user gesture to open the popup — calling it
      //    from useEffect causes browsers to silently hang the promise.
      //    So if not yet authorized, show the manual button immediately.
      if (!music.isAuthorized) {
        logAppleMusicDebug('Not authorized; showing manual auth button')
        if (!mountedRef.current) return
        setStatus('needs-auth')
        return
      }

      // 5. Set queue + play
      await loadAndPlay(music, appleMusicUrl)
    } catch (e) {
      if (!mountedRef.current) return
      setStatus('error')
      setErrorMsg(e instanceof Error ? e.message : 'Unknown error')
      logAppleMusicDebug('Init failed', { error: e instanceof Error ? e.message : String(e) })
    }
  }, [appleMusicUrl, handleStateChange, handleTimeChange])

  async function loadAndPlay(music: MusicKitInstance, url: string) {
    logAppleMusicDebug('Queueing URL and starting playback', { url })
    await music.setQueue({ url })
    await music.play()
    if (mountedRef.current) {
      setStatus('playing')
      onReady?.()
      logAppleMusicDebug('Playback started')
    }
  }

  const handleAuthorize = useCallback(async () => {
    const MK = window.MusicKit
    if (!MK || !_mkInstance) return
    setStatus('authorizing')
    logAppleMusicDebug('Manual authorize clicked')
    try {
      await _mkInstance.authorize()
      if (!mountedRef.current) return
      await loadAndPlay(_mkInstance, appleMusicUrl)
    } catch (e) {
      if (!mountedRef.current) return
      setStatus('error')
      setErrorMsg(e instanceof Error ? e.message : 'Authorization failed')
      logAppleMusicDebug('Manual authorize failed', { error: e instanceof Error ? e.message : String(e) })
    }
  }, [appleMusicUrl, onReady])

  useEffect(() => {
    logAppleMusicDebug('Status changed', { status })
  }, [status])

  // Init on mount / url change
  useEffect(() => {
    mountedRef.current = true
    void initAndPlay()
    return () => {
      mountedRef.current = false
      // Pause on unmount
      _mkInstance?.pause()
    }
  }, [initAndPlay])

  // ── Render ───────────────────────────────────────────────────────────────────

  if (status === 'error') {
    return (
      <div className="rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">
        Apple Music error: {errorMsg}
      </div>
    )
  }

  if (status === 'loading') {
    return (
      <div className="flex items-center gap-2 py-2 text-gray-500 text-sm">
        <div className="w-4 h-4 border-2 border-gray-700 border-t-pink-500 rounded-full animate-spin" />
        Loading Apple Music…
      </div>
    )
  }

  if (status === 'needs-auth' || status === 'authorizing') {
    return (
      <div className="rounded-2xl border border-gray-800/80 p-4 text-center" style={{ background: '#12121f' }}>
        <p className="text-white text-sm font-medium mb-1">Apple Music authorization required</p>
        <p className="text-gray-500 text-xs mb-4 leading-relaxed">
          Sign in with your Apple ID to play music. An Apple Music subscription is required.
        </p>
        <button
          type="button"
          onClick={() => void handleAuthorize()}
          disabled={status === 'authorizing'}
          className="
            inline-flex items-center gap-2 px-5 py-2.5 rounded-xl
            bg-white hover:bg-gray-100 disabled:bg-gray-800
            text-black disabled:text-gray-500
            text-sm font-semibold transition-all duration-150
          "
        >
          {status === 'authorizing' ? (
            <>
              <div className="w-4 h-4 border-2 border-gray-400 border-t-black rounded-full animate-spin" />
              Authorizing…
            </>
          ) : (
            <>
              {/* Apple logo */}
              <svg viewBox="0 0 814 1000" className="w-4 h-4 fill-current">
                <path d="M788.1 340.9c-5.8 4.5-108.2 62.2-108.2 190.5 0 148.4 130.3 200.9 134.2 202.2-.6 3.2-20.7 71.9-68.7 141.9-42.8 61.6-87.5 123.1-155.5 123.1s-85.5-39.5-164-39.5c-76 0-103.7 40.8-165.9 40.8s-105-57.8-155.5-127.4C46 790.8 0 663.2 0 541.8 0 390.9 91.2 267.6 200.8 267.6c65 0 107.7 43.5 166.7 43.5 56.7 0 109-46.1 176.2-46.1 50.7 0 163.5 14.7 242.9 128.8zM543.9 175c-32.7 45.6-89.7 82.9-163.1 82.9-15.1 0-30.9-1.3-38.9-3.2-.5-8.2-.5-16.5-.5-24.7 0-98.3 58.7-189.5 135.4-225.9 44.7-21.1 95.4-33 143.8-33 3.8 0 7.7.1 11.5.3-1.8 78.5-36 152.4-88.2 203.6z"/>
              </svg>
              Authorize with Apple Music
            </>
          )}
        </button>
      </div>
    )
  }

  // status === 'playing' — player is hidden (audio-only; controls are in the parent)
  return null
})

export default AppleMusicPlayer
