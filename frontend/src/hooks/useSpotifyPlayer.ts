import { useState, useEffect, useRef, useCallback } from 'react'

export interface PlayerState {
  isReady: boolean
  isPlaying: boolean
  currentPositionMs: number
  durationMs: number
  deviceId: string | null
  error: string | null
  currentTrackName: string | null
  currentTrackArtist: string | null
  albumArtUrl: string | null
}

export interface PlayerControls {
  togglePlay: () => Promise<void>
  seekTo: (ms: number) => Promise<void>
  loadAndPlayTrack: (trackUri: string) => Promise<void>
}

const SPOTIFY_SDK_ID = 'spotify-web-playback-sdk'

/**
 * Injects the Spotify Web Playback SDK, initialises a Player instance with the
 * supplied OAuth token, and exposes a smooth real-time playback position.
 *
 * Position tracking strategy: on every `player_state_changed` event we record
 * the authoritative position + a local timestamp, then a 100 ms interval
 * linearly extrapolates from that base so the UI stays smooth without polling
 * the SDK constantly.
 */
export function useSpotifyPlayer(token: string | null): PlayerState & PlayerControls {
  const [state, setState] = useState<PlayerState>({
    isReady: false,
    isPlaying: false,
    currentPositionMs: 0,
    durationMs: 0,
    deviceId: null,
    error: null,
    currentTrackName: null,
    currentTrackArtist: null,
    albumArtUrl: null,
  })

  // Refs for the extrapolation ticker
  const basePositionRef  = useRef(0)
  const baseTimestampRef = useRef(0)
  const isPlayingRef     = useRef(false)
  const deviceIdRef      = useRef<string | null>(null)
  const playerRef        = useRef<Spotify.Player | null>(null)

  // ── 100 ms position ticker ────────────────────────────────────────────────
  useEffect(() => {
    const id = setInterval(() => {
      if (!isPlayingRef.current) return
      const extrapolated = basePositionRef.current + (Date.now() - baseTimestampRef.current)
      setState(prev => ({ ...prev, currentPositionMs: extrapolated }))
    }, 100)
    return () => clearInterval(id)
  }, [])

  // ── SDK initialisation ────────────────────────────────────────────────────
  useEffect(() => {
    if (!token) return

    const initPlayer = () => {
      const player = new window.Spotify.Player({
        name: 'FlowUp — Russian Music Learner',
        getOAuthToken: cb => cb(token),
        volume: 0.6,
      })

      player.addListener('ready', ({ device_id }) => {
        deviceIdRef.current = device_id
        setState(prev => ({ ...prev, isReady: true, deviceId: device_id, error: null }))
      })

      player.addListener('not_ready', () => {
        setState(prev => ({ ...prev, isReady: false }))
      })

      player.addListener('player_state_changed', state => {
        if (!state) return
        basePositionRef.current  = state.position
        baseTimestampRef.current = Date.now()
        isPlayingRef.current     = !state.paused

        const track = state.track_window.current_track
        setState(prev => ({
          ...prev,
          isPlaying:         !state.paused,
          currentPositionMs: state.position,
          durationMs:        state.duration,
          currentTrackName:  track?.name   ?? null,
          currentTrackArtist: track?.artists[0]?.name ?? null,
          albumArtUrl:       track?.album?.images?.[0]?.url ?? null,
        }))
      })

      player.addListener('initialization_error', ({ message }) => {
        setState(prev => ({ ...prev, error: `Initialization error: ${message}` }))
      })

      player.addListener('authentication_error', ({ message }) => {
        setState(prev => ({ ...prev, error: `Authentication error: ${message}. Token may have expired.` }))
      })

      player.addListener('account_error', ({ message }) => {
        setState(prev => ({
          ...prev,
          error: `Account error: ${message}. Spotify Premium is required for SDK playback.`,
        }))
      })

      player.addListener('playback_error', ({ message }) => {
        console.error('Playback error:', message)
      })

      player.connect()
      playerRef.current = player
    }

    // Inject SDK script if not already present
    if (!document.getElementById(SPOTIFY_SDK_ID)) {
      const script = document.createElement('script')
      script.id    = SPOTIFY_SDK_ID
      script.src   = 'https://sdk.scdn.co/spotify-player.js'
      script.async = true
      document.head.appendChild(script)
    }

    if (window.Spotify) {
      initPlayer()
    } else {
      window.onSpotifyWebPlaybackSDKReady = initPlayer
    }

    return () => {
      playerRef.current?.disconnect()
      playerRef.current = null
    }
  }, [token])

  // ── Controls ──────────────────────────────────────────────────────────────

  const togglePlay = useCallback(async () => {
    await playerRef.current?.togglePlay()
  }, [])

  const seekTo = useCallback(async (ms: number) => {
    basePositionRef.current  = ms
    baseTimestampRef.current = Date.now()
    await playerRef.current?.seek(ms)
  }, [])

  /**
   * Transfers Spotify playback to this SDK device, then starts the given track.
   * Requires token scopes: streaming, user-modify-playback-state
   */
  const loadAndPlayTrack = useCallback(async (trackUri: string) => {
    const deviceId = deviceIdRef.current
    if (!deviceId || !token) return

    // Transfer playback silently, then queue the track
    await fetch('https://api.spotify.com/v1/me/player', {
      method: 'PUT',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_ids: [deviceId], play: false }),
    })

    await fetch(`https://api.spotify.com/v1/me/player/play?device_id=${deviceId}`, {
      method: 'PUT',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ uris: [trackUri], position_ms: 0 }),
    })
  }, [token])

  return { ...state, togglePlay, seekTo, loadAndPlayTrack }
}
