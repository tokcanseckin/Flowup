/**
 * Spotify PKCE OAuth hook for FlowUp.
 *
 * Flow:
 *  1. User calls login() → redirected to Spotify authorize page
 *  2. Spotify redirects back with ?code=…&state=…
 *  3. Hook detects the code on mount, exchanges it for tokens via PKCE
 *     (no client_secret needed), stores tokens in localStorage
 *  4. getValidToken() auto-refreshes when the access token is near expiry
 *
 * Required env var:  VITE_SPOTIFY_CLIENT_ID
 */

import { useState, useEffect, useCallback } from 'react'

// ── Config ────────────────────────────────────────────────────────────────────

const CLIENT_ID   = import.meta.env.VITE_SPOTIFY_CLIENT_ID as string
const REDIRECT_URI = window.location.origin  // e.g. http://localhost:5173

const SCOPES = [
  'streaming',
  'user-modify-playback-state',
  'user-read-playback-state',
  'user-read-private',
  'user-read-email',
].join(' ')

const K = {
  ACCESS_TOKEN:   'sp_access_token',
  REFRESH_TOKEN:  'sp_refresh_token',
  EXPIRES_AT:     'sp_expires_at',
  USER:           'sp_user',
  CODE_VERIFIER:  'sp_code_verifier',
  OAUTH_STATE:    'sp_oauth_state',
} as const

// ── Types ─────────────────────────────────────────────────────────────────────

export interface SpotifyUser {
  id: string
  display_name: string | null
  email: string | null
  images: { url: string }[]
}

export interface SpotifyAuthState {
  accessToken: string | null
  user: SpotifyUser | null
  isAuthenticated: boolean
  isLoading: boolean
  error: string | null
}

export interface SpotifyAuthActions {
  login: () => void
  logout: () => void
  /** Returns a valid (possibly refreshed) access token, or null if not authenticated. */
  getValidToken: () => Promise<string | null>
}

// ── PKCE utilities ────────────────────────────────────────────────────────────

function generateRandomString(byteCount: number): string {
  const bytes = new Uint8Array(byteCount)
  crypto.getRandomValues(bytes)
  // base64url-encode (no padding)
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '')
}

async function sha256Base64Url(plain: string): Promise<string> {
  const encoded = new TextEncoder().encode(plain)
  const hash    = await crypto.subtle.digest('SHA-256', encoded)
  return btoa(String.fromCharCode(...new Uint8Array(hash)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '')
}

// ── Spotify API calls ─────────────────────────────────────────────────────────

async function exchangeCode(code: string, verifier: string): Promise<{
  access_token: string
  refresh_token: string
  expires_in: number
}> {
  const body = new URLSearchParams({
    grant_type:    'authorization_code',
    code,
    redirect_uri:  REDIRECT_URI,
    client_id:     CLIENT_ID,
    code_verifier: verifier,
  })
  const r = await fetch('https://accounts.spotify.com/api/token', {
    method:  'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({})) as Record<string, string>
    throw new Error(err.error_description ?? `Token exchange failed (${r.status})`)
  }
  return r.json()
}

async function refreshTokens(refreshToken: string): Promise<{
  access_token: string
  refresh_token?: string
  expires_in: number
}> {
  const body = new URLSearchParams({
    grant_type:    'refresh_token',
    refresh_token: refreshToken,
    client_id:     CLIENT_ID,
  })
  const r = await fetch('https://accounts.spotify.com/api/token', {
    method:  'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({})) as Record<string, string>
    throw new Error(err.error_description ?? `Token refresh failed (${r.status})`)
  }
  return r.json()
}

async function fetchMe(accessToken: string): Promise<SpotifyUser> {
  const r = await fetch('https://api.spotify.com/v1/me', {
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  if (!r.ok) throw new Error(`Failed to fetch Spotify user (${r.status})`)
  return r.json()
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useSpotifyAuth(): SpotifyAuthState & SpotifyAuthActions {
  const [accessToken, setAccessToken] = useState<string | null>(null)
  const [user,        setUser]        = useState<SpotifyUser | null>(null)
  const [isLoading,   setIsLoading]   = useState(true)
  const [error,       setError]       = useState<string | null>(null)

  /** Persist tokens and update state. */
  const storeTokens = useCallback((
    access: string,
    refresh: string,
    expiresIn: number,
    userData: SpotifyUser,
  ) => {
    const expiresAt = Date.now() + (expiresIn - 60) * 1000  // 1 min buffer
    localStorage.setItem(K.ACCESS_TOKEN,  access)
    localStorage.setItem(K.REFRESH_TOKEN, refresh)
    localStorage.setItem(K.EXPIRES_AT,    String(expiresAt))
    localStorage.setItem(K.USER,          JSON.stringify(userData))
    setAccessToken(access)
    setUser(userData)
  }, [])

  const clearTokens = useCallback(() => {
    localStorage.removeItem(K.ACCESS_TOKEN)
    localStorage.removeItem(K.REFRESH_TOKEN)
    localStorage.removeItem(K.EXPIRES_AT)
    localStorage.removeItem(K.USER)
    setAccessToken(null)
    setUser(null)
  }, [])

  // ── Initialization: handle callback or restore session ─────────────────────
  useEffect(() => {
    const init = async () => {
      const params         = new URLSearchParams(window.location.search)
      const code           = params.get('code')
      const returnedState  = params.get('state')
      const storedVerifier = sessionStorage.getItem(K.CODE_VERIFIER)
      const storedState    = sessionStorage.getItem(K.OAUTH_STATE)

      if (code && returnedState && storedVerifier && storedState) {
        // ── OAuth callback path ──────────────────────────────────────────────
        window.history.replaceState({}, '', window.location.pathname)
        sessionStorage.removeItem(K.CODE_VERIFIER)
        sessionStorage.removeItem(K.OAUTH_STATE)

        if (returnedState !== storedState) {
          setError('OAuth state mismatch — possible CSRF. Please try logging in again.')
          setIsLoading(false)
          return
        }
        try {
          const tokens   = await exchangeCode(code, storedVerifier)
          const userData = await fetchMe(tokens.access_token)
          storeTokens(tokens.access_token, tokens.refresh_token, tokens.expires_in, userData)
        } catch (e) {
          setError(e instanceof Error ? e.message : 'Authentication failed')
        }

      } else {
        // ── Restore session from localStorage ────────────────────────────────
        const storedAccess    = localStorage.getItem(K.ACCESS_TOKEN)
        const storedRefresh   = localStorage.getItem(K.REFRESH_TOKEN)
        const storedExpiresAt = localStorage.getItem(K.EXPIRES_AT)
        const storedUser      = localStorage.getItem(K.USER)

        if (storedAccess && storedRefresh && storedExpiresAt && storedUser) {
          const expiresAt = Number(storedExpiresAt)
          if (Date.now() < expiresAt) {
            setAccessToken(storedAccess)
            setUser(JSON.parse(storedUser) as SpotifyUser)
          } else {
            // Expired — attempt refresh
            try {
              const tokens   = await refreshTokens(storedRefresh)
              const userData = await fetchMe(tokens.access_token)
              storeTokens(
                tokens.access_token,
                tokens.refresh_token ?? storedRefresh,
                tokens.expires_in,
                userData,
              )
            } catch {
              clearTokens()  // Refresh failed — force re-login
            }
          }
        }
      }

      setIsLoading(false)
    }

    init()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── login ──────────────────────────────────────────────────────────────────
  const login = useCallback(async () => {
    const verifier  = generateRandomString(64)
    const challenge = await sha256Base64Url(verifier)
    const state     = generateRandomString(16)

    sessionStorage.setItem(K.CODE_VERIFIER, verifier)
    sessionStorage.setItem(K.OAUTH_STATE,   state)

    const params = new URLSearchParams({
      client_id:             CLIENT_ID,
      response_type:         'code',
      redirect_uri:          REDIRECT_URI,
      scope:                 SCOPES,
      code_challenge_method: 'S256',
      code_challenge:        challenge,
      state,
    })
    window.location.href = `https://accounts.spotify.com/authorize?${params}`
  }, [])

  // ── logout ─────────────────────────────────────────────────────────────────
  const logout = useCallback(() => {
    clearTokens()
  }, [clearTokens])

  // ── getValidToken ──────────────────────────────────────────────────────────
  const getValidToken = useCallback(async (): Promise<string | null> => {
    const storedRefresh   = localStorage.getItem(K.REFRESH_TOKEN)
    const storedExpiresAt = localStorage.getItem(K.EXPIRES_AT)
    const storedUser      = localStorage.getItem(K.USER)
    const storedAccess    = localStorage.getItem(K.ACCESS_TOKEN)

    if (!storedRefresh) return null

    if (storedExpiresAt && Date.now() < Number(storedExpiresAt)) {
      return storedAccess
    }

    try {
      const tokens   = await refreshTokens(storedRefresh)
      const userData = storedUser
        ? (JSON.parse(storedUser) as SpotifyUser)
        : await fetchMe(tokens.access_token)
      storeTokens(
        tokens.access_token,
        tokens.refresh_token ?? storedRefresh,
        tokens.expires_in,
        userData,
      )
      return tokens.access_token
    } catch {
      logout()
      return null
    }
  }, [logout, storeTokens])

  return {
    accessToken,
    user,
    isAuthenticated: !!accessToken,
    isLoading,
    error,
    login,
    logout,
    getValidToken,
  }
}
