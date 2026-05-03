// Global ambient declarations for the Spotify Web Playback SDK.
// No `export` — keeps every declaration in the global scope so
// `window.Spotify.Player` resolves cleanly in all source files.
// Full reference: https://developer.spotify.com/documentation/web-playback-sdk/reference

declare namespace Spotify {
  interface PlayerInit {
    name: string
    getOAuthToken: (cb: (token: string) => void) => void
    volume?: number
  }

  interface WebPlaybackTrack {
    uri: string
    name: string
    duration_ms: number
    artists: Array<{ name: string; uri: string }>
    album: {
      name: string
      uri: string
      images: Array<{ url: string; height: number; width: number }>
    }
  }

  interface WebPlaybackState {
    context: { uri: string; metadata: Record<string, unknown> }
    disallows: Record<string, boolean>
    paused: boolean
    position: number
    duration: number
    repeat_mode: 0 | 1 | 2
    shuffle: boolean
    track_window: {
      current_track: WebPlaybackTrack
      previous_tracks: WebPlaybackTrack[]
      next_tracks: WebPlaybackTrack[]
    }
  }

  interface WebPlaybackError  { message: string }
  interface WebPlaybackReady  { device_id: string }

  class Player {
    constructor(options: PlayerInit)
    connect(): Promise<boolean>
    disconnect(): void
    addListener(event: 'ready',                cb: (data: WebPlaybackReady) => void): void
    addListener(event: 'not_ready',            cb: (data: WebPlaybackReady) => void): void
    addListener(event: 'player_state_changed', cb: (state: WebPlaybackState | null) => void): void
    addListener(event: 'initialization_error', cb: (err: WebPlaybackError) => void): void
    addListener(event: 'authentication_error', cb: (err: WebPlaybackError) => void): void
    addListener(event: 'account_error',        cb: (err: WebPlaybackError) => void): void
    addListener(event: 'playback_error',       cb: (err: WebPlaybackError) => void): void
    removeListener(event: string): void
    getCurrentState(): Promise<WebPlaybackState | null>
    setName(name: string): Promise<void>
    getVolume(): Promise<number>
    setVolume(volume: number): Promise<void>
    pause(): Promise<void>
    resume(): Promise<void>
    togglePlay(): Promise<void>
    seek(position_ms: number): Promise<void>
    previousTrack(): Promise<void>
    nextTrack(): Promise<void>
  }
}

// Extend the browser's Window with the SDK globals
interface Window {
  Spotify: typeof Spotify
  onSpotifyWebPlaybackSDKReady: () => void
}
