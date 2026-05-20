import React, { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import AdminPanel          from './components/AdminPanel'
import HelpButton          from './components/HelpButton'
import LyricsPlayer         from './components/LyricsPlayer'
import ReportModal          from './components/ReportModal'
import singolingLogo from '../images/singoling_logo@2x.png'
import prevIconImg   from '../images/previous_icon@2x.png'
import nextIconImg   from '../images/next_icon@2x.png'
import YouTubePlayer, { YouTubePlayerHandle } from './components/YouTubePlayer'
import AppleMusicPlayer, { AppleMusicPlayerHandle, isAppleMusicAuthorized } from './components/AppleMusicPlayer'
import { api, BackendUser, PlaylistDetail, PlaylistSummary, SongDetail, SongSummary, UserSettings as ApiUserSettings, clearAdminSession, setAdminSession, getAdminHeaders } from './api/client'
import { useFavorites } from './hooks/useFavorites'
import { useListened } from './hooks/useListened'
import { useWordHistory } from './hooks/useWordHistory'
import { useLocalization, useT, useContentT } from './i18n/LocalizationContext'
import { track } from './analytics'
import PrivacyPolicyPage from './components/PrivacyPolicyPage'
import TermsOfServicePage from './components/TermsOfServicePage'
import { TutorialOverlay, TutorialHandle, TutorialStep } from './components/TutorialOverlay'
import PricingPage from './components/PricingPage'

// ── Module-level song cache (survives re-renders, cleared on logout) ──────────
// Key: `{id}:{source}` where source is 'youtube' or 'apple_music'.
const _songCache = new Map<string, SongDetail>()
const _inFlight  = new Map<string, Promise<SongDetail>>()

function _songCacheKey(id: number, source?: string, targetLang?: string): string {
  return `${id}:${source ?? ''}:${targetLang ?? ''}`
}

/** Fetch a song, using the module-level cache. Deduplicates concurrent requests. */
function _fetchSong(id: number, source?: string, targetLang?: string, playlistId?: number): Promise<SongDetail> {
  const key = _songCacheKey(id, source, targetLang)
  const cached = _songCache.get(key)
  if (cached) return Promise.resolve(cached)
  const inflight = _inFlight.get(key)
  if (inflight) return inflight
  const p = api.getSong(id, source, targetLang || undefined, playlistId).then(detail => {
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
          prompt: () => void
        }
      }
    }
    AppleID?: {
      auth: {
        init: (config: {
          clientId: string
          scope: string
          redirectURI: string
          usePopup: boolean
        }) => void
        signIn: () => Promise<{
          authorization: { id_token: string; code: string }
          user?: { name?: { firstName?: string; lastName?: string }; email?: string }
        }>
      }
    }
  }
}

const PASSWORD_SESSION_KEY = 'flowup.password_user.v1'

function youtubeThumbnail(youtubeUrl: string | null): string | null {
  if (!youtubeUrl) return null
  try {
    const u = new URL(youtubeUrl)
    const id = u.hostname === 'youtu.be'
      ? u.pathname.slice(1).split('?')[0]
      : (u.searchParams.get('v') ?? u.pathname.split('/').pop() ?? '')
    return id ? `https://img.youtube.com/vi/${id}/mqdefault.jpg` : null
  } catch {
    return null
  }
}

interface AppSettings {
  excludeStopWordsFromShortcuts: boolean
  pauseOnInspect: boolean
  lastPlaylistId: number | null
  lastSongId: number | null
  preferredSource: 'youtube' | 'apple_music'
  uiLanguage: 'en' | 'tr' | 'ru' | 'es' | 'pt' | 'de'
}

const DEFAULT_SETTINGS: AppSettings = {
  excludeStopWordsFromShortcuts: true,
  pauseOnInspect: true,
  lastPlaylistId: null,
  lastSongId: null,
  preferredSource: 'youtube',
  uiLanguage: 'en',
}

function fromApiSettings(settings: ApiUserSettings): AppSettings {
  return {
    excludeStopWordsFromShortcuts: settings.exclude_stop_words_from_shortcuts,
    pauseOnInspect: settings.pause_on_inspect,
    lastPlaylistId: settings.last_playlist_id ?? null,
    lastSongId: settings.last_song_id ?? null,
    preferredSource: (settings.preferred_source as AppSettings['preferredSource']) ?? 'youtube',
    uiLanguage: ((settings as unknown) as Record<string, unknown>).ui_language as AppSettings['uiLanguage'] ?? 'en',
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
  | { page: 'song'; songId: number; playlistId: number | null }
  | { page: 'settings'; tab: SettingsTab }
  | { page: 'admin'; tab: 'songs' | 'playlists' | 'users' | 'tasks' | 'localizations' | 'reports'; id: number | null }
  | { page: 'subscriptions' }
  | { page: 'privacy' }
  | { page: 'terms' }

function parseAppRoute(pathWithSearch: string): AppRoute {
  const [rawPath, searchStr] = pathWithSearch.split('?')
  const path = rawPath || '/browse'

  const settingsMatch = path.match(/^\/settings(?:\/(preferences|account|subscription|support))?$/)
  if (settingsMatch) {
    const tab = (settingsMatch[1] as SettingsTab) ?? 'preferences'
    return { page: 'settings', tab }
  }
  const adminMatch = path.match(/^\/admin(?:\/(song|playlist|user|task|localization|report)(?:\/(\d+))?)?$/)
  if (adminMatch) {
    const seg = adminMatch[1]
    const id = adminMatch[2] ? Number(adminMatch[2]) : null
    const tab = seg === 'playlist' ? 'playlists' : seg === 'user' ? 'users' : seg === 'task' ? 'tasks' : seg === 'localization' ? 'localizations' : seg === 'report' ? 'reports' : 'songs'
    return { page: 'admin', tab, id }
  }
  if (path === '/browse' || path === '/') return { page: 'browse' }

  const playlistMatch = path.match(/^\/playlist\/(\d+)$/)
  if (playlistMatch) {
    return { page: 'playlist', playlistId: Number(playlistMatch[1]) }
  }

  const songMatch = path.match(/^\/song\/(\d+)$/)
  if (songMatch) {
    const params = new URLSearchParams(searchStr ?? '')
    const pid = params.get('playlist_id')
    return { page: 'song', songId: Number(songMatch[1]), playlistId: pid ? Number(pid) : null }
  }

  if (path === '/subscriptions') return { page: 'subscriptions' }
  if (path === '/privacy') return { page: 'privacy' }
  if (path === '/terms') return { page: 'terms' }

  return { page: 'browse' }
}

function playlistPath(playlistId: number): string {
  return `/playlist/${playlistId}`
}

function songPath(songId: number, playlistId?: number | null): string {
  return playlistId ? `/song/${songId}?playlist_id=${playlistId}` : `/song/${songId}`
}

function adminPath(tab: 'songs' | 'playlists' | 'users' | 'tasks' | 'localizations' | 'reports', id: number | null): string {
  if (tab === 'localizations') return '/admin/localization'
  if (tab === 'reports') return '/admin/report'
  const seg = tab === 'playlists' ? 'playlist' : tab === 'users' ? 'user' : tab === 'tasks' ? 'task' : 'song'
  if (id === null) return `/admin/${seg}`
  return `/admin/${seg}/${id}`
}

function settingsPath(tab: SettingsTab = 'preferences'): string {
  return `/settings/${tab}`
}

// ── Shared auth helpers ────────────────────────────────────────────────────────

function AppLogo() {
  const t = useT()
  return (
    <div className="text-center mb-10">
      <div className="inline-flex items-center justify-center mb-4">
        <img src={singolingLogo} className="h-10 object-contain" alt="SingoLing" />
      </div>
      <p className="text-gray-500 text-sm leading-relaxed max-w-xs mx-auto">
        {t('auth.tagline1')}<br/>
        {t('auth.tagline2')}
      </p>
    </div>
  )
}

function AppleButton({ onAppleLogin, disabled }: { onAppleLogin?: (idToken: string) => Promise<void>; disabled?: boolean }) {
  const clientId = import.meta.env.VITE_APPLE_CLIENT_ID as string | undefined
  const redirectURI = import.meta.env.VITE_APPLE_REDIRECT_URI as string | undefined
  const [appleError, setAppleError] = useState<string | null>(null)

  // Load the Apple SDK and init auth config once on mount.
  useEffect(() => {
    if (!clientId) return
    const init = () => {
      if (!window.AppleID) return
      window.AppleID.auth.init({
        clientId,
        scope: 'name email',
        redirectURI: redirectURI ?? window.location.origin,
        usePopup: true,
      })
    }
    const existing = document.querySelector('script[src*="appleid.cdn-apple.com"]')
    if (window.AppleID) { init() }
    else if (existing) { existing.addEventListener('load', init, { once: true }) }
    else {
      const script = document.createElement('script')
      script.src = 'https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js'
      script.async = true
      script.onload = init
      document.head.appendChild(script)
    }
  }, [clientId, redirectURI])

  const handleClick = useCallback(async () => {
    if (!window.AppleID) return
    try {
      const res = await window.AppleID.auth.signIn()
      if (onAppleLogin) await onAppleLogin(res.authorization.id_token)
    } catch (err: unknown) {
      const code = (err as { error?: string })?.error
      if (code !== 'popup_closed_by_user') setAppleError('Apple sign-in failed — please try again')
    }
  }, [onAppleLogin])

  if (!clientId) return null

  return (
    <div className="space-y-1">
      <button
        type="button"
        onClick={() => { void handleClick() }}
        disabled={disabled}
        className="w-full flex items-center justify-center gap-3 h-12 md:h-11 rounded-xl bg-white text-black text-base md:text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-100 transition-colors"
      >
        <svg viewBox="0 0 24 24" className="w-5 h-5 shrink-0" fill="currentColor">
          <path d="M17.05 20.28c-.98.95-2.05.8-3.08.35-1.09-.46-2.09-.48-3.24 0-1.44.62-2.2.44-3.06-.35C2.79 15.25 3.51 7.7 9.05 7.4c1.35.07 2.29.74 3.08.8 1.18-.24 2.31-.93 3.57-.84 1.51.12 2.65.72 3.4 1.8-3.12 1.87-2.38 5.98.48 7.13-.57 1.55-1.32 3.09-2.54 4zm-3.03-17.6c.06 1.96-1.52 3.6-3.36 3.44-.25-1.8 1.61-3.6 3.36-3.44z"/>
        </svg>
        Continue with Apple
      </button>
      {appleError && (
        <p className="text-red-400 text-xs text-center">{appleError}</p>
      )}
    </div>
  )
}

function GoogleButton({ onGoogleLogin, disabled }: { onGoogleLogin: (credential: string) => Promise<void>; disabled?: boolean }) {
  const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined

  useEffect(() => {
    if (!clientId) return
    const init = () => {
      if (!window.google) return
      window.google.accounts.id.initialize({
        client_id: clientId,
        callback: (response) => { void onGoogleLogin(response.credential) },
        auto_select: false,
      })
    }
    if (window.google) { init() }
    else {
      const existing = document.querySelector('script[src*="accounts.google.com/gsi"]')
      if (existing) { existing.addEventListener('load', init, { once: true }) }
      else {
        const script = document.createElement('script')
        script.src = 'https://accounts.google.com/gsi/client'
        script.async = true
        script.onload = init
        document.head.appendChild(script)
      }
    }
  }, [clientId, onGoogleLogin])

  const handleClick = useCallback(() => {
    if (!window.google) return
    window.google.accounts.id.prompt()
  }, [])

  if (!clientId) return null

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      className="w-full flex items-center justify-center gap-3 h-12 md:h-11 rounded-xl bg-white text-black text-base md:text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-100 transition-colors"
    >
      <svg viewBox="0 0 24 24" className="w-5 h-5 shrink-0" xmlns="http://www.w3.org/2000/svg">
        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
      </svg>
      Continue with Google
    </button>
  )
}

// ── Login screen ──────────────────────────────────────────────────────────────

function LoginScreen({
  onEmailLogin,
  onGoogleLogin,
  onAppleLogin,
  onShowSignUp,
  onShowForgotPassword,
  error,
  busy,
}: {
  onEmailLogin: (email: string, password: string) => Promise<void>
  onGoogleLogin: (credential: string) => Promise<void>
  onAppleLogin: (idToken: string) => Promise<void>
  onShowSignUp: () => void
  onShowForgotPassword: () => void
  error: string | null
  busy: boolean
}) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const t = useT()

  const handleSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault()
    void onEmailLogin(email.trim(), password)
  }, [email, password, onEmailLogin])

  return (
    <div className="min-h-screen flex items-center justify-center p-4"
         style={{ background: 'radial-gradient(ellipse 120% 80% at 50% 110%, #1a1040 0%, #0d0d14 60%)' }}>
      <div className="w-full max-w-md">
        <AppLogo />

        <div className="rounded-2xl border border-gray-800/80 p-8 shadow-2xl space-y-5"
             style={{ background: '#12121f' }}>
          <div>
            <h2 className="text-white font-semibold text-lg mb-1">{t('auth.signIn')}</h2>
          </div>

          {error && (
            <div className="rounded-xl border border-red-900/50 bg-red-950/30 px-4 py-3 text-sm text-red-400">
              {error}
            </div>
          )}

          {/* Social buttons */}
          <div className="space-y-2.5">
            <GoogleButton onGoogleLogin={onGoogleLogin} disabled={busy} />
            <AppleButton onAppleLogin={onAppleLogin} disabled={busy} />
          </div>

          <div className="flex items-center gap-3 text-xs text-gray-600">
            <div className="h-px flex-1 bg-gray-800" />
            <span>{t('auth.or')}</span>
            <div className="h-px flex-1 bg-gray-800" />
          </div>

          <form onSubmit={handleSubmit} className="space-y-3">
            <input
              type="email"
              required
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder={t('auth.emailPlaceholder')}
              className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-3 md:py-2 text-base md:text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
            />
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder={t('auth.passwordPlaceholder')}
              className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-3 md:py-2 text-base md:text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
            />
            <button
              type="submit"
              disabled={busy}
              className="
                w-full py-3.5 md:py-2.5 rounded-xl font-semibold text-base md:text-sm
                bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-800 disabled:text-gray-500
                text-white transition-all duration-150
              "
            >
              {busy ? t('auth.signingIn') : t('auth.signIn')}
            </button>
            <button
              type="button"
              onClick={onShowForgotPassword}
              className="w-full text-center text-xs text-gray-500 hover:text-gray-400 transition-colors"
            >
              {t('auth.forgotPassword')}
            </button>
          </form>

          <p className="text-center text-sm text-gray-500">
            {t('auth.dontHaveAccount')}{' '}
            <button
              type="button"
              onClick={onShowSignUp}
              className="text-indigo-400 hover:text-indigo-300 font-medium transition-colors"
            >
              {t('auth.signUp')}
            </button>
          </p>
        </div>
      </div>
    </div>
  )
}

// ── Forgot-password screen ────────────────────────────────────────────────────

function ForgotPasswordScreen({
  onBack,
}: {
  onBack: () => void
}) {
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const t = useT()

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.forgotPassword(email.trim())
      setSent(true)
    } catch {
      setError(t('auth.somethingWentWrong'))
    } finally {
      setBusy(false)
    }
  }, [email, t])

  return (
    <div className="min-h-screen flex items-center justify-center p-4"
         style={{ background: 'radial-gradient(ellipse 120% 80% at 50% 110%, #1a1040 0%, #0d0d14 60%)' }}>
      <div className="w-full max-w-md">
        <AppLogo />
        <div className="rounded-2xl border border-gray-800/80 p-8 shadow-2xl space-y-5"
             style={{ background: '#12121f' }}>
          <h2 className="text-white font-semibold text-lg">{t('auth.resetPassword')}</h2>

          {sent ? (
            <div className="space-y-4">
              <p className="text-sm text-gray-400">
                {t('auth.resetLinkSent')}
              </p>
              <button
                type="button"
                onClick={onBack}
                className="w-full py-3.5 md:py-2.5 rounded-xl font-semibold text-base md:text-sm bg-gray-800 hover:bg-gray-700 text-white transition-all duration-150"
              >
                {t('auth.backToSignIn')}
              </button>
            </div>
          ) : (
            <>
              {error && (
                <div className="rounded-xl border border-red-900/50 bg-red-950/30 px-4 py-3 text-sm text-red-400">
                  {error}
                </div>
              )}
              <form onSubmit={handleSubmit} className="space-y-3">
                <p className="text-sm text-gray-400">
                  {t('auth.resetPasswordInstruction')}
                </p>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder={t('auth.emailPlaceholder')}
                  className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-3 md:py-2 text-base md:text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
                <button
                  type="submit"
                  disabled={busy}
                  className="w-full py-3.5 md:py-2.5 rounded-xl font-semibold text-base md:text-sm bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-800 disabled:text-gray-500 text-white transition-all duration-150"
                >
                  {busy ? t('auth.sending') : t('auth.sendResetLink')}
                </button>
              </form>
              <p className="text-center text-sm text-gray-500">
                <button
                  type="button"
                  onClick={onBack}
                  className="text-indigo-400 hover:text-indigo-300 font-medium transition-colors"
                >
                  {t('auth.backToSignIn')}
                </button>
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Reset-password screen ─────────────────────────────────────────────────────

function ResetPasswordScreen({
  token,
  onDone,
}: {
  token: string
  onDone: () => void
}) {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const t = useT()

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault()
    if (password !== confirm) { setError(t('auth.passwordsDoNotMatch')); return }
    setBusy(true)
    setError(null)
    try {
      await api.resetPassword(token, password)
      setDone(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('auth.somethingWentWrong'))
    } finally {
      setBusy(false)
    }
  }, [token, password, confirm, t])

  return (
    <div className="min-h-screen flex items-center justify-center p-4"
         style={{ background: 'radial-gradient(ellipse 120% 80% at 50% 110%, #1a1040 0%, #0d0d14 60%)' }}>
      <div className="w-full max-w-md">
        <AppLogo />
        <div className="rounded-2xl border border-gray-800/80 p-8 shadow-2xl space-y-5"
             style={{ background: '#12121f' }}>
          <h2 className="text-white font-semibold text-lg">{t('auth.chooseNewPassword')}</h2>

          {done ? (
            <div className="space-y-4">
              <p className="text-sm text-gray-400">{t('auth.passwordUpdated')}</p>
              <button
                type="button"
                onClick={onDone}
                className="w-full py-3.5 md:py-2.5 rounded-xl font-semibold text-base md:text-sm bg-indigo-600 hover:bg-indigo-500 text-white transition-all duration-150"
              >
                {t('auth.signIn')}
              </button>
            </div>
          ) : (
            <>
              {error && (
                <div className="rounded-xl border border-red-900/50 bg-red-950/30 px-4 py-3 text-sm text-red-400">
                  {error}
                </div>
              )}
              <form onSubmit={handleSubmit} className="space-y-3">
                <input
                  type="password"
                  required
                  minLength={8}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder={t('auth.newPasswordPlaceholder')}
                  className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-3 md:py-2 text-base md:text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
                <input
                  type="password"
                  required
                  minLength={8}
                  value={confirm}
                  onChange={e => setConfirm(e.target.value)}
                  placeholder={t('auth.confirmNewPasswordPlaceholder')}
                  className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-3 md:py-2 text-base md:text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                />
                <button
                  type="submit"
                  disabled={busy}
                  className="w-full py-3.5 md:py-2.5 rounded-xl font-semibold text-base md:text-sm bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-800 disabled:text-gray-500 text-white transition-all duration-150"
                >
                  {busy ? t('auth.updating') : t('auth.setNewPassword')}
                </button>
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Sign-up screen ────────────────────────────────────────────────────────────

function SignUpScreen({
  onRegister,
  onGoogleLogin,
  onAppleLogin,
  onShowSignIn,
  error,
  busy,
}: {
  onRegister: (displayName: string, email: string, password: string) => Promise<void>
  onGoogleLogin: (credential: string) => Promise<void>
  onAppleLogin: (idToken: string) => Promise<void>
  onShowSignIn: () => void
  error: string | null
  busy: boolean
}) {
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const t = useT()

  const handleSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault()
    setLocalError(null)
    if (password !== confirm) {
      setLocalError(t('auth.passwordsDoNotMatch'))
      return
    }
    void onRegister(displayName.trim(), email.trim(), password)
  }, [displayName, email, password, confirm, onRegister])

  const shownError = localError ?? error

  return (
    <div className="min-h-screen flex items-center justify-center p-4"
         style={{ background: 'radial-gradient(ellipse 120% 80% at 50% 110%, #1a1040 0%, #0d0d14 60%)' }}>
      <div className="w-full max-w-md">
        <AppLogo />

        <div className="rounded-2xl border border-gray-800/80 p-8 shadow-2xl space-y-5"
             style={{ background: '#12121f' }}>
          <div>
            <h2 className="text-white font-semibold text-lg mb-1">{t('auth.createAccount')}</h2>
          </div>

          {shownError && (
            <div className="rounded-xl border border-red-900/50 bg-red-950/30 px-4 py-3 text-sm text-red-400">
              {shownError}
            </div>
          )}

          {/* Social buttons */}
          <div className="space-y-2.5">
            <GoogleButton onGoogleLogin={onGoogleLogin} disabled={busy} />
            <AppleButton onAppleLogin={onAppleLogin} disabled={busy} />
          </div>

          <div className="flex items-center gap-3 text-xs text-gray-600">
            <div className="h-px flex-1 bg-gray-800" />
            <span>{t('auth.or')}</span>
            <div className="h-px flex-1 bg-gray-800" />
          </div>

          <form onSubmit={handleSubmit} className="space-y-3">
            <input
              type="text"
              required
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              placeholder={t('auth.namePlaceholder')}
              className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-3 md:py-2 text-base md:text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
            />
            <input
              type="email"
              required
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder={t('auth.emailPlaceholder')}
              className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-3 md:py-2 text-base md:text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
            />
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder={t('auth.passwordMinPlaceholder')}
              className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-3 md:py-2 text-base md:text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
            />
            <input
              type="password"
              required
              minLength={8}
              value={confirm}
              onChange={e => setConfirm(e.target.value)}
              placeholder={t('auth.confirmPasswordPlaceholder')}
              className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-3 md:py-2 text-base md:text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
            />
            <p className="text-center text-xs text-gray-500 leading-relaxed">
              {t('auth.signupAgreementPrefix')}{' '}
              <a
                href="/terms"
                onClick={e => { e.preventDefault(); window.history.pushState(null, '', '/terms'); window.dispatchEvent(new PopStateEvent('popstate')) }}
                className="text-indigo-400 hover:text-indigo-300 transition-colors"
              >{t('auth.termsOfService')}</a>
              {' '}{t('auth.and')}{' '}
              <a
                href="/privacy"
                onClick={e => { e.preventDefault(); window.history.pushState(null, '', '/privacy'); window.dispatchEvent(new PopStateEvent('popstate')) }}
                className="text-indigo-400 hover:text-indigo-300 transition-colors"
              >{t('auth.privacyPolicy')}</a>
            </p>
            <button
              type="submit"
              disabled={busy}
              className="
                w-full py-3.5 md:py-2.5 rounded-xl font-semibold text-base md:text-sm
                bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-800 disabled:text-gray-500
                text-white transition-all duration-150
              "
            >
              {busy ? t('auth.creatingAccount') : t('auth.createAccount')}
            </button>
          </form>

          <p className="text-center text-sm text-gray-500">
            {t('auth.alreadyHaveAccount')}{' '}
            <button
              type="button"
              onClick={onShowSignIn}
              className="text-indigo-400 hover:text-indigo-300 font-medium transition-colors"
            >
              {t('auth.signIn')}
            </button>
          </p>
        </div>
      </div>
    </div>
  )
}

function SourcePicker({
  value,
  onChange,
}: {
  value: AppSettings['preferredSource']
  onChange: (v: AppSettings['preferredSource']) => void
}) {
  const t = useT()
  const options: { value: AppSettings['preferredSource']; label: string; description: string }[] = [
    { value: 'youtube', label: 'YouTube', description: t('settings.sourceYoutubeDesc') },
    { value: 'apple_music', label: 'Apple Music', description: t('settings.sourceAppleMusicDesc') },
  ]
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

const LANG_FLAG: Record<string, string> = {
  ru: '🇷🇺', en: '🇬🇧', es: '🇪🇸', fr: '🇫🇷', de: '🇩🇪',
  it: '🇮🇹', pt: '🇵🇹', ja: '🇯🇵', zh: '🇨🇳', ko: '🇰🇷', tr: '🇹🇷',
  uk: '🇺🇦', nl: '🇳🇱', pl: '🇵🇱', sv: '🇸🇪', ar: '🇸🇦', he: '🇮🇱',
}
function langFlag(code: string): string { return LANG_FLAG[code] ?? '🌐' }

function SongBrowser({
  songs, playlists, activePlaylistId, activePlaylist, loading, error, onSelect, onPrefetch, onSelectPlaylist, onLogout, onOpenSettings, onOpenAdmin, onOpenAccount, isAdmin, user, openedSongIds, favoriteSongIds, toggleFavorite, markAsNotListened, wordsLookedUpCount, onBrowseTargetLang, navigateToPath, track,
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
  user: { display_name: string | null; email: string | null; subscription_tier: string } | null
  openedSongIds: Set<number>
  favoriteSongIds: Set<number>
  toggleFavorite: (id: number) => void
  markAsNotListened: (id: number) => void
  wordsLookedUpCount: number
  onBrowseTargetLang: (musicLang: string, targetLang: string) => void
  navigateToPath: (path: string) => void
  track: (event: string, props?: Record<string, string | number | boolean>) => void
}) {
  const t = useT()
  const tc = useContentT()
  const { language, setLanguage } = useLocalization()
  const listenedCount = activePlaylist
    ? activePlaylist.songs.filter(s => openedSongIds.has(s.song_id)).length
    : 0
  const progressPct = activePlaylist && activePlaylist.song_count > 0
    ? Math.round((listenedCount / activePlaylist.song_count) * 100)
    : 0

  const [openMenuSongId, setOpenMenuSongId] = useState<number | null>(null)
  const [browseReportSongId, setBrowseReportSongId] = useState<number | null>(null)
  const [learnLang, setLearnLang] = useState<string | null>(() => localStorage.getItem('browse.learnLang'))
  const [nativeLang, setNativeLang] = useState<string | null>(() => localStorage.getItem('browse.nativeLang'))
  const playlistsSectionRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (learnLang) localStorage.setItem('browse.learnLang', learnLang)
    else localStorage.removeItem('browse.learnLang')
  }, [learnLang])

  useEffect(() => {
    if (nativeLang) localStorage.setItem('browse.nativeLang', nativeLang)
    else localStorage.removeItem('browse.nativeLang')
  }, [nativeLang])

  const learnLangs = useMemo(() =>
    Array.from(new Set(playlists.map(p => p.language_code).filter((c): c is string => !!c))),
  [playlists])

  const nativeLangs = useMemo(() => {
    if (!learnLang) return []
    const langs = new Set<string>()
    playlists.filter(p => p.language_code === learnLang).forEach(p => p.target_langs.forEach(l => langs.add(l.toLowerCase())))
    return Array.from(langs)
  }, [playlists, learnLang])

  const matchingPlaylists = useMemo(() => {
    if (!learnLang || !nativeLang) return []
    return playlists.filter(p => p.language_code === learnLang && p.target_langs.map(l => l.toLowerCase()).includes(nativeLang))
  }, [playlists, learnLang, nativeLang])

  useEffect(() => {
    if (openMenuSongId === null) return
    const close = () => setOpenMenuSongId(null)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [openMenuSongId])

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
          <span className="text-sm">{t('browser.loadingSongs')}</span>
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
          {/* Section headers for free tier users in playlist view */}
          {user && user.subscription_tier === 'free' && activePlaylistId !== null ? (
            <>
              {/* Trial Songs section */}
              {songs.slice(0, 2).length > 0 && (
                <>
                  <div className="rounded-2xl pl-5 pr-1 h-8 mb-2 mt-1 flex items-center" style={{ background: '#006D36' }}>
                    <span className="text-xs font-semibold uppercase tracking-wider text-white">
                      Trial Songs
                    </span>
                  </div>
                  {songs.slice(0, 2).map(song => (
                    <div
                      key={song.id}
                      className="relative"
                    >
                      <div
                        role="button"
                        tabIndex={0}
                        onClick={() => onSelect(song.id)}
                        onPointerEnter={() => onPrefetch(song.id)}
                        onKeyDown={e => e.key === 'Enter' && onSelect(song.id)}
                        className="
                          w-full text-left rounded-2xl border border-zinc-700/70 p-4 md:p-3
                          active:scale-[0.99] transition-all duration-150 cursor-pointer
                        "
                        style={{ background: '#25262b' }}
                        onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(160,160,160,0.4)'; e.currentTarget.style.background = '#323438' }}
                        onMouseLeave={e => { e.currentTarget.style.borderColor = 'rgba(63,63,70,0.7)'; e.currentTarget.style.background = '#25262b' }}
                      >
                        <div className="flex items-center gap-4">
                          {/* dot indicator */}
                          <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden className="shrink-0">
                            {openedSongIds.has(song.id)
                              ? <circle cx="4" cy="4" r="4" fill="#000000" />
                              : <circle cx="4" cy="4" r="4" fill="white" />}
                          </svg>
                          {/* thumbnail */}
                          <div className="shrink-0 w-12 h-12 rounded-lg overflow-hidden bg-zinc-800 flex items-center justify-center">
                            {youtubeThumbnail(song.youtube_url)
                              ? <img
                                  src={youtubeThumbnail(song.youtube_url)!}
                                  alt=""
                                  className="w-full h-full object-cover"
                                  loading="lazy"
                                />
                              : <svg className="w-5 h-5 text-zinc-600" fill="currentColor" viewBox="0 0 20 20" aria-hidden>
                                  <path d="M18 3a1 1 0 00-1.196-.98l-10 2A1 1 0 006 5v9.114A4.369 4.369 0 005 14c-1.657 0-3 .895-3 2s1.343 2 3 2 3-.895 3-2V7.82l8-1.6v5.894A4.37 4.37 0 0015 12c-1.657 0-3 .895-3 2s1.343 2 3 2 3-.895 3-2V3z" />
                                </svg>
                            }
                          </div>
                          <div className="min-w-0 flex-1">
                            <p className="text-white font-semibold truncate">{song.title}</p>
                            <p className="text-gray-500 text-sm truncate">{song.artist ?? t('browser.unknownArtist')}</p>
                          </div>
                          {favoriteSongIds.has(song.id) && (
                            <svg viewBox="0 0 24 24" className="shrink-0 w-4 h-4" fill="#f87171" aria-label="Favorited">
                              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
                            </svg>
                          )}
                          <span className="text-[10px] font-mono font-medium px-1.5 py-0.5 rounded-md uppercase tracking-wider shrink-0" style={{ color: '#4ade80', background: 'rgba(0,109,54,0.25)', border: '1px solid rgba(0,109,54,0.45)' }}>
                            {t(`language.${song.language_code}`)}
                          </span>
                          {/* 3-dot menu button */}
                          <button
                            onClick={e => { e.stopPropagation(); setOpenMenuSongId(openMenuSongId === song.id ? null : song.id) }}
                            className="shrink-0 p-1 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-white/5 transition-colors"
                            aria-label="More options"
                          >
                            <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current">
                              <circle cx="5" cy="12" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="19" cy="12" r="1.5" />
                            </svg>
                          </button>
                        </div>
                      </div>
                      {/* Dropdown menu */}
                      {openMenuSongId === song.id && (
                        <div
                          onClick={e => e.stopPropagation()}
                          className="absolute right-0 z-30 mt-1 w-52 rounded-xl border border-zinc-700 shadow-xl overflow-hidden"
                          style={{ background: '#1c1d21' }}
                        >
                          <button
                            onClick={() => { toggleFavorite(song.id); setOpenMenuSongId(null) }}
                            className="w-full text-left flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-300 hover:bg-white/5 transition-colors"
                          >
                            <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" fill={favoriteSongIds.has(song.id) ? '#f87171' : 'none'} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
                            </svg>
                            {favoriteSongIds.has(song.id) ? t('browser.removeFromFavorites') : t('browser.addToFavorites')}
                          </button>
                          {openedSongIds.has(song.id) && (
                            <button
                              onClick={() => { markAsNotListened(song.id); setOpenMenuSongId(null) }}
                              className="w-full text-left flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-300 hover:bg-white/5 transition-colors"
                            >
                              <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0 fill-none stroke-current" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="9 11 12 14 22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
                              </svg>
                              {t('browser.markAsNotListened')}
                            </button>
                          )}
                          <div className="border-t border-zinc-700/60 mx-3" />
                          <button
                            onClick={() => { setBrowseReportSongId(song.id); setOpenMenuSongId(null) }}
                            className="w-full text-left flex items-center gap-2.5 px-4 py-2.5 text-sm text-red-400 hover:bg-red-500/10 transition-colors"
                          >
                            <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0 fill-none stroke-current" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
                            </svg>
                            {t('browser.reportProblem')}
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </>
              )}

              {/* Premium List section */}
              {songs.slice(2).length > 0 && (
                <>
                  <div className="rounded-2xl pl-5 pr-1 h-8 mb-2 mt-4 flex items-center justify-between bg-indigo-600">
                    <span className="text-xs font-semibold uppercase tracking-wider text-white">
                      Premium List
                    </span>
                    <button
                      type="button"
                      onClick={() => { navigateToPath('/subscriptions'); track('Premium List Header Upgrade Clicked', { playlist_id: activePlaylistId ?? '' }) }}
                      className="text-xs font-semibold px-3 py-1 rounded-2xl bg-white text-indigo-600 hover:bg-gray-100 transition-colors"
                    >
                      Upgrade
                    </button>
                  </div>
                  {songs.slice(2).map(song => (
                    <div
                      key={song.id}
                      className="relative"
                    >
                      <div
                        role="button"
                        tabIndex={0}
                        onClick={() => onSelect(song.id)}
                        onPointerEnter={() => onPrefetch(song.id)}
                        onKeyDown={e => e.key === 'Enter' && onSelect(song.id)}
                        className="
                          w-full text-left rounded-2xl border border-zinc-700/70 p-4 md:p-3
                          active:scale-[0.99] transition-all duration-150 cursor-pointer
                        "
                        style={{ background: '#25262b' }}
                        onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(160,160,160,0.4)'; e.currentTarget.style.background = '#323438' }}
                        onMouseLeave={e => { e.currentTarget.style.borderColor = 'rgba(63,63,70,0.7)'; e.currentTarget.style.background = '#25262b' }}
                      >
                        <div className="flex items-center gap-4">
                          {/* dot indicator */}
                          <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden className="shrink-0">
                            {openedSongIds.has(song.id)
                              ? <circle cx="4" cy="4" r="4" fill="#000000" />
                              : <circle cx="4" cy="4" r="4" fill="white" />}
                          </svg>
                          {/* thumbnail */}
                          <div className="shrink-0 w-12 h-12 rounded-lg overflow-hidden bg-zinc-800 flex items-center justify-center">
                            {youtubeThumbnail(song.youtube_url)
                              ? <img
                                  src={youtubeThumbnail(song.youtube_url)!}
                                  alt=""
                                  className="w-full h-full object-cover"
                                  loading="lazy"
                                />
                              : <svg className="w-5 h-5 text-zinc-600" fill="currentColor" viewBox="0 0 20 20" aria-hidden>
                                  <path d="M18 3a1 1 0 00-1.196-.98l-10 2A1 1 0 006 5v9.114A4.369 4.369 0 005 14c-1.657 0-3 .895-3 2s1.343 2 3 2 3-.895 3-2V7.82l8-1.6v5.894A4.37 4.37 0 0015 12c-1.657 0-3 .895-3 2s1.343 2 3 2 3-.895 3-2V3z" />
                                </svg>
                            }
                          </div>
                          <div className="min-w-0 flex-1">
                            <p className="text-white font-semibold truncate">{song.title}</p>
                            <p className="text-gray-500 text-sm truncate">{song.artist ?? t('browser.unknownArtist')}</p>
                          </div>
                          {favoriteSongIds.has(song.id) && (
                            <svg viewBox="0 0 24 24" className="shrink-0 w-4 h-4" fill="#f87171" aria-label="Favorited">
                              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
                            </svg>
                          )}
                          <span className="text-[10px] font-mono font-medium px-1.5 py-0.5 rounded-md uppercase tracking-wider shrink-0" style={{ color: '#4ade80', background: 'rgba(0,109,54,0.25)', border: '1px solid rgba(0,109,54,0.45)' }}>
                            {t(`language.${song.language_code}`)}
                          </span>
                          {/* 3-dot menu button */}
                          <button
                            onClick={e => { e.stopPropagation(); setOpenMenuSongId(openMenuSongId === song.id ? null : song.id) }}
                            className="shrink-0 p-1 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-white/5 transition-colors"
                            aria-label="More options"
                          >
                            <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current">
                              <circle cx="5" cy="12" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="19" cy="12" r="1.5" />
                            </svg>
                          </button>
                        </div>
                      </div>
                      {/* Dropdown menu */}
                      {openMenuSongId === song.id && (
                        <div
                          onClick={e => e.stopPropagation()}
                          className="absolute right-0 z-30 mt-1 w-52 rounded-xl border border-zinc-700 shadow-xl overflow-hidden"
                          style={{ background: '#1c1d21' }}
                        >
                          <button
                            onClick={() => { toggleFavorite(song.id); setOpenMenuSongId(null) }}
                            className="w-full text-left flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-300 hover:bg-white/5 transition-colors"
                          >
                            <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" fill={favoriteSongIds.has(song.id) ? '#f87171' : 'none'} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
                            </svg>
                            {favoriteSongIds.has(song.id) ? t('browser.removeFromFavorites') : t('browser.addToFavorites')}
                          </button>
                          {openedSongIds.has(song.id) && (
                            <button
                              onClick={() => { markAsNotListened(song.id); setOpenMenuSongId(null) }}
                              className="w-full text-left flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-300 hover:bg-white/5 transition-colors"
                            >
                              <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0 fill-none stroke-current" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="9 11 12 14 22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
                              </svg>
                              {t('browser.markAsNotListened')}
                            </button>
                          )}
                          <div className="border-t border-zinc-700/60 mx-3" />
                          <button
                            onClick={() => { setBrowseReportSongId(song.id); setOpenMenuSongId(null) }}
                            className="w-full text-left flex items-center gap-2.5 px-4 py-2.5 text-sm text-red-400 hover:bg-red-500/10 transition-colors"
                          >
                            <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0 fill-none stroke-current" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
                            </svg>
                            {t('browser.reportProblem')}
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </>
              )}
            </>
          ) : (
            /* Default rendering for non-free users or browse view */
            songs.map(song => (
            <div
              key={song.id}
              className="relative"
            >
              <div
                role="button"
                tabIndex={0}
                onClick={() => onSelect(song.id)}
                onPointerEnter={() => onPrefetch(song.id)}
                onKeyDown={e => e.key === 'Enter' && onSelect(song.id)}
                className="
                  w-full text-left rounded-2xl border border-zinc-700/70 p-4 md:p-3
                  active:scale-[0.99] transition-all duration-150 cursor-pointer
                "
                style={{ background: '#25262b' }}
                onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(160,160,160,0.4)'; e.currentTarget.style.background = '#323438' }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = 'rgba(63,63,70,0.7)'; e.currentTarget.style.background = '#25262b' }}
              >
                <div className="flex items-center gap-4">
                  {/* dot indicator */}
                  <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden className="shrink-0">
                    {openedSongIds.has(song.id)
                      ? <circle cx="4" cy="4" r="4" fill="#000000" />
                      : <circle cx="4" cy="4" r="4" fill="white" />}
                  </svg>
                  {/* thumbnail */}
                  <div className="shrink-0 w-12 h-12 rounded-lg overflow-hidden bg-zinc-800 flex items-center justify-center">
                    {youtubeThumbnail(song.youtube_url)
                      ? <img
                          src={youtubeThumbnail(song.youtube_url)!}
                          alt=""
                          className="w-full h-full object-cover"
                          loading="lazy"
                        />
                      : <svg className="w-5 h-5 text-zinc-600" fill="currentColor" viewBox="0 0 20 20" aria-hidden>
                          <path d="M18 3a1 1 0 00-1.196-.98l-10 2A1 1 0 006 5v9.114A4.369 4.369 0 005 14c-1.657 0-3 .895-3 2s1.343 2 3 2 3-.895 3-2V7.82l8-1.6v5.894A4.37 4.37 0 0015 12c-1.657 0-3 .895-3 2s1.343 2 3 2 3-.895 3-2V3z" />
                        </svg>
                    }
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-white font-semibold truncate">{song.title}</p>
                    <p className="text-gray-500 text-sm truncate">{song.artist ?? t('browser.unknownArtist')}</p>
                  </div>
                  {favoriteSongIds.has(song.id) && (
                    <svg viewBox="0 0 24 24" className="shrink-0 w-4 h-4" fill="#f87171" aria-label="Favorited">
                      <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
                    </svg>
                  )}
                  <span className="text-[10px] font-mono font-medium px-1.5 py-0.5 rounded-md uppercase tracking-wider shrink-0" style={{ color: '#4ade80', background: 'rgba(0,109,54,0.25)', border: '1px solid rgba(0,109,54,0.45)' }}>
                    {t(`language.${song.language_code}`)}
                  </span>
                  {/* 3-dot menu button */}
                  <button
                    onClick={e => { e.stopPropagation(); setOpenMenuSongId(openMenuSongId === song.id ? null : song.id) }}
                    className="shrink-0 p-1 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-white/5 transition-colors"
                    aria-label="More options"
                  >
                    <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current">
                      <circle cx="5" cy="12" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="19" cy="12" r="1.5" />
                    </svg>
                  </button>
                </div>
              </div>
              {/* Dropdown menu */}
              {openMenuSongId === song.id && (
                <div
                  onClick={e => e.stopPropagation()}
                  className="absolute right-0 z-30 mt-1 w-52 rounded-xl border border-zinc-700 shadow-xl overflow-hidden"
                  style={{ background: '#1c1d21' }}
                >
                  <button
                    onClick={() => { toggleFavorite(song.id); setOpenMenuSongId(null) }}
                    className="w-full text-left flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-300 hover:bg-white/5 transition-colors"
                  >
                    <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" fill={favoriteSongIds.has(song.id) ? '#f87171' : 'none'} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
                    </svg>
                    {favoriteSongIds.has(song.id) ? t('browser.removeFromFavorites') : t('browser.addToFavorites')}
                  </button>
                  {openedSongIds.has(song.id) && (
                    <button
                      onClick={() => { markAsNotListened(song.id); setOpenMenuSongId(null) }}
                      className="w-full text-left flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-300 hover:bg-white/5 transition-colors"
                    >
                      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0 fill-none stroke-current" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="9 11 12 14 22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
                      </svg>
                      {t('browser.markAsNotListened')}
                    </button>
                  )}
                  <div className="border-t border-zinc-700/60 mx-3" />
                  <button
                    onClick={() => { setBrowseReportSongId(song.id); setOpenMenuSongId(null) }}
                    className="w-full text-left flex items-center gap-2.5 px-4 py-2.5 text-sm text-red-400 hover:bg-red-500/10 transition-colors"
                  >
                    <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0 fill-none stroke-current" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
                    </svg>
                    {t('browser.reportProblem')}
                  </button>
                </div>
              )}
            </div>
          ))
          )}
        </div>
      )}
    </>
  )

  return (
    <div className="min-h-screen" style={{ background: '#050608' }}>
      {/* Header */}
      <header className="sticky top-0 z-20 border-b border-gray-900" style={{ background: '#050608' }}>
        <div className="max-w-[972px] mx-auto w-full px-3 py-3 md:px-4 md:py-4 flex items-center justify-between gap-2 md:gap-4">
          <div className="flex items-center gap-3">
            {(activePlaylist || (activePlaylistId !== null && loading)) && (
              <button onClick={() => onSelectPlaylist(null)} className="text-gray-500 hover:text-gray-300 transition-colors mr-1" aria-label="Back to all playlists">
                <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
                  <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/>
                </svg>
              </button>
            )}
            <img src={singolingLogo} className="h-6 md:h-8 object-contain" alt="SingoLing" />
          </div>
          <div className="flex items-center gap-2 md:gap-3">
            {isAdmin && (
              <button
                type="button"
                onClick={onOpenAdmin}
                className="text-xs text-amber-500 hover:text-amber-300 transition-colors"
              >
                {t('nav.admin')}
              </button>
            )}
            <select
              value={language}
              onChange={e => { setLanguage(e.target.value as 'en' | 'tr' | 'ru' | 'es' | 'pt' | 'de'); e.currentTarget.blur() }}
              className="text-xs rounded-lg border border-gray-700/70 bg-gray-800/70 px-2 py-1 text-gray-300 focus:outline-none focus:border-gray-500 cursor-pointer hidden sm:block"
              aria-label="UI language"
            >
              <option value="en">EN</option>
              <option value="tr">TR</option>
              <option value="ru">RU</option>
              <option value="es">ES</option>
              <option value="pt">PT</option>
              <option value="de">DE</option>
            </select>
            <button
              type="button"
              onClick={onOpenSettings}
              className="text-xs text-gray-500 hover:text-gray-200 transition-colors"
            >
              {t('nav.preferences')}
            </button>
            {user?.display_name && (
              <button type="button" onClick={onOpenAccount} className="text-xs text-gray-500 hover:text-gray-300 transition-colors truncate hidden sm:inline-block max-w-[140px] md:max-w-[180px]">{user.display_name}</button>
            )}
            <button onClick={onLogout} className="text-xs text-gray-600 hover:text-gray-400 transition-colors hidden sm:inline-block">
              {t('nav.signOut')}
            </button>
          </div>
        </div>
      </header>

      {/* Content */}
      <div className="px-3 sm:px-4 pt-6 pb-10 max-w-[972px] mx-auto">
        {activePlaylistId !== null && activePlaylist === null && loading ? (
          <div className="flex gap-8 items-start">
            {/* Left skeleton */}
            <div className="w-72 shrink-0">
              <div className="h-11" />
              <div className="w-full aspect-square rounded-2xl mb-5 animate-pulse" style={{ background: '#1c1d21' }} />
              <div className="h-5 rounded-lg mb-2 animate-pulse w-3/4" style={{ background: '#1c1d21' }} />
              <div className="h-3 rounded-lg mb-5 animate-pulse w-1/2" style={{ background: '#1c1d21' }} />
              <div className="h-9 rounded-xl mb-5 animate-pulse" style={{ background: '#1c1d21' }} />
            </div>
            {/* Right skeleton */}
            <div className="flex-1 min-w-0">
              <div className="h-7 rounded-lg mb-4 animate-pulse w-24" style={{ background: '#1c1d21' }} />
              <div className="space-y-2">
                {Array.from({ length: 6 }).map((_, i) => (
                  <div key={i} className="h-[72px] rounded-2xl animate-pulse" style={{ background: '#1c1d21' }} />
                ))}
              </div>
            </div>
          </div>
        ) : activePlaylist ? (
          <div className="flex flex-col lg:flex-row gap-6 lg:gap-8 items-start">
            {/* Left column — playlist detail */}
            <div className="w-full lg:w-80 shrink-0 lg:sticky lg:top-20 lg:overflow-y-auto lg:no-scrollbar lg:pb-10" style={{ maxHeight: 'calc(100vh - 5rem)' }}>
              {/* Spacer — matches height of the "Songs" heading row in the right column (desktop only) */}
              <div className="hidden lg:block h-11" />
              {/* Cover image */}
              {activePlaylist.cover_image_url ? (
                <img
                  src={activePlaylist.cover_image_url}
                  alt={tc(activePlaylist.name)}
                  className="w-full aspect-square rounded-2xl mb-5 object-cover"
                />
              ) : (
                <div
                  className="w-full aspect-square rounded-2xl mb-5 flex items-center justify-center select-none"
                  style={{ background: 'linear-gradient(135deg, #003d1f 0%, #006D36 50%, #003d1f 100%)' }}
                >
                  <span className="text-white/30 text-6xl font-bold uppercase">
                    {tc(activePlaylist.name).charAt(0)}
                  </span>
                </div>
              )}

              {/* Title */}
              <h1 className="text-white font-bold text-xl leading-tight mb-3">{tc(activePlaylist.name)}</h1>

              {/* Badges */}
              <div className="flex flex-wrap gap-1.5 mb-5">
                {activePlaylist.language_code && (
                  <span className="text-[10px] font-mono font-medium px-1.5 py-0.5 rounded-md uppercase tracking-wider" style={{ color: '#4ade80', background: 'rgba(0,109,54,0.25)', border: '1px solid rgba(0,109,54,0.45)' }}>
                    {activePlaylist.language_code}
                  </span>
                )}
                {activePlaylist.difficulty_level && (
                  <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-md uppercase tracking-wider" style={{ color: '#fb923c', background: 'rgba(251,146,60,0.15)', border: '1px solid rgba(251,146,60,0.35)' }}>
                    {tc(activePlaylist.difficulty_level)}
                  </span>
                )}
              </div>

              {/* Play button */}
              {songs.length > 0 && (
                <button
                  type="button"
                  onClick={() => { track('Playlist Play Button Clicked', { playlist_id: activePlaylistId ?? '' }); onSelect(songs[0].id) }}
                  className="w-full flex items-center justify-center gap-2 rounded-xl active:scale-[0.98] transition-all text-white font-medium text-sm py-3 mb-6"
                  style={{ background: '#006D36' }}
                  onMouseEnter={e => (e.currentTarget.style.background = '#008a44')}
                  onMouseLeave={e => (e.currentTarget.style.background = '#006D36')}
                >
                  <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current shrink-0">
                    <path d="M8 5v14l11-7z"/>
                  </svg>
                  {t('browser.play')}
                </button>
              )}

              {/* Stats */}
              <div className="rounded-2xl border border-zinc-700/70 divide-y divide-zinc-700/70 mb-4" style={{ background: '#25262b' }}>
                <div className="px-4 py-3 flex items-center justify-between">
                  <span className="text-xs text-gray-500">{t('browser.songs')}</span>
                  <span className="text-sm text-white font-medium">{activePlaylist.song_count}</span>
                </div>
                <div className="px-4 py-3 flex items-center justify-between">
                  <span className="text-xs text-gray-500">{t('browser.progress')}</span>
                  <span className="text-sm text-white font-medium">{progressPct}%</span>
                </div>
                <div className="px-4 py-3 flex items-center justify-between">
                  <span className="text-xs text-gray-500">{t('browser.wordsLookedUp')}</span>
                  <span className="text-sm text-white font-medium">{wordsLookedUpCount}</span>
                </div>
              </div>

              {/* Description */}
              {activePlaylist.description && (
                <p className="text-gray-400 text-sm leading-relaxed">{tc(activePlaylist.description)}</p>
              )}


            </div>

            {/* Right column — song list */}
            <div className="flex-1 min-w-0">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="text-white font-semibold text-base md:text-lg">{t('browser.songs')}</h2>
              </div>
              {songList}
            </div>
          </div>
        ) : (
          /* ── Discover view ── */
          <div className="pt-2">
            {/* Section 1 — I want to improve */}
            <section className="mb-10">
              <h2 className="text-xl font-bold text-white mb-1">{t('browse.learnTitle')}</h2>
              <p className="text-sm text-gray-500 mb-5">{t('browse.learnSubtitle')}</p>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 max-w-2xl">
                {learnLangs.map(code => {
                  const plCount = playlists.filter(p => p.language_code === code && p.target_langs.length > 0).length
                  const selected = learnLang === code
                  return (
                    <button
                      key={code}
                      type="button"
                      onClick={() => { track('Learn Language Selected', { language: code }); setLearnLang(code); setNativeLang(null) }}
                      className={`rounded-2xl border p-5 flex items-center gap-4 transition-all cursor-pointer text-left ${selected ? 'border-transparent' : 'border-zinc-700/70 hover:border-white/15'}`}
                      style={{ background: selected ? '#488DC7' : '#18191f' }}
                      onMouseEnter={e => { if (!selected) (e.currentTarget as HTMLElement).style.background = '#22232a' }}
                      onMouseLeave={e => { if (!selected) (e.currentTarget as HTMLElement).style.background = '#18191f' }}
                    >
                      <span className="text-4xl shrink-0" role="img" aria-label={code}>{langFlag(code)}</span>
                      <div className="min-w-0">
                        <p className="text-white font-semibold text-sm">{t(`language.${code}`)}</p>
                        {plCount > 0 && <p className="text-gray-300 text-xs mt-0.5">{plCount} {plCount === 1 ? t('browse.playlist') : t('browse.playlists')}</p>}
                      </div>
                      {selected && (
                        <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0 ml-auto" fill="rgba(255,255,255,0.7)">
                          <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                        </svg>
                      )}
                    </button>
                  )
                })}
              </div>
            </section>

            {/* Section 2 skeleton — before learnLang is chosen */}
            {!learnLang && (
              <section className="mb-10 opacity-40 pointer-events-none select-none">
                <div className="h-7 rounded-lg mb-1 animate-pulse w-44" style={{ background: '#1c1d21' }} />
                <div className="h-4 rounded-lg mb-5 animate-pulse w-64" style={{ background: '#1c1d21' }} />
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 max-w-2xl">
                  {[0, 1, 2].map(i => (
                    <div key={i} className="rounded-2xl border border-zinc-700/70 p-5 flex items-center gap-4" style={{ background: '#18191f' }}>
                      <div className="w-10 h-10 rounded-full animate-pulse shrink-0" style={{ background: '#1c1d21' }} />
                      <div className="flex-1 min-w-0">
                        <div className="h-3.5 rounded-md animate-pulse mb-1.5 w-3/4" style={{ background: '#1c1d21' }} />
                        <div className="h-3 rounded-md animate-pulse w-1/2" style={{ background: '#1c1d21' }} />
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Section 2 — I speak */}
            {learnLang && nativeLangs.length > 0 && (
              <section className="mb-10">
                <h2 className="text-xl font-bold text-white mb-1">{t('browse.speakTitle')}</h2>
                <p className="text-sm text-gray-500 mb-5">{t('browse.speakSubtitle')}</p>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {nativeLangs.map(code => {
                    const selected = nativeLang === code
                    return (
                      <button
                        key={code}
                        type="button"
                        onClick={() => { track('Native Language Selected', { language: code }); setNativeLang(code); if (learnLang) onBrowseTargetLang(learnLang, code) }}
                        className={`rounded-2xl border p-5 flex items-center gap-4 transition-all cursor-pointer text-left ${selected ? 'border-transparent' : 'border-zinc-700/70 hover:border-white/15'}`}
                        style={{ background: selected ? '#488DC7' : '#18191f' }}
                        onMouseEnter={e => { if (!selected) (e.currentTarget as HTMLElement).style.background = '#22232a' }}
                        onMouseLeave={e => { if (!selected) (e.currentTarget as HTMLElement).style.background = '#18191f' }}
                      >
                        <span className="text-4xl shrink-0" role="img" aria-label={code}>{langFlag(code)}</span>
                        <div className="min-w-0">
                          <p className="text-white font-semibold text-sm">{t(`language.${code}`)}</p>
                        </div>
                        {selected && (
                          <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0 ml-auto" fill="rgba(255,255,255,0.7)">
                            <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                          </svg>
                        )}
                      </button>
                    )
                  })}
                </div>
              </section>
            )}

            {/* Section 3 skeleton — before nativeLang is chosen */}
            {learnLang && !nativeLang && (
              <section className="opacity-40 pointer-events-none select-none">
                <div className="h-7 rounded-lg mb-5 animate-pulse w-44" style={{ background: '#1c1d21' }} />
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-5">
                  {[0, 1, 2].map(i => (
                    <div key={i} className="rounded-2xl border border-zinc-700/70 overflow-hidden" style={{ background: '#18191f' }}>
                      <div className="w-full aspect-square animate-pulse" style={{ background: '#1c1d21' }} />
                      <div className="p-4">
                        <div className="flex gap-1.5 mb-2">
                          <div className="h-4 rounded-md animate-pulse w-16" style={{ background: '#1c1d21' }} />
                          <div className="h-4 rounded-md animate-pulse w-12" style={{ background: '#1c1d21' }} />
                        </div>
                        <div className="h-4 rounded-md animate-pulse mb-2 w-3/4" style={{ background: '#1c1d21' }} />
                        <div className="h-3 rounded-md animate-pulse mb-1 w-full" style={{ background: '#1c1d21' }} />
                        <div className="h-3 rounded-md animate-pulse w-2/3" style={{ background: '#1c1d21' }} />
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Section 3 — Playlists */}
            {learnLang && nativeLang && (
              <section ref={playlistsSectionRef}>
                <div className="flex items-baseline gap-3 mb-5">
                  <h2 className="text-xl font-bold text-white">{t('browse.playlistsTitle')}</h2>
                  {matchingPlaylists.length > 0 && <span className="text-sm text-gray-500">{matchingPlaylists.length} {t('browse.available')}</span>}
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-5">
                  {matchingPlaylists.map(pl => (
                    <button
                      key={pl.id}
                      type="button"
                      onClick={() => { track('Playlist Selected', { playlist_id: pl.id, learn_lang: learnLang ?? '', native_lang: nativeLang ?? '' }); onSelectPlaylist(pl.id) }}
                      className="w-full text-left rounded-2xl border border-zinc-700/70 overflow-hidden transition-all hover:border-zinc-500/60"
                      style={{ background: '#18191f' }}
                      onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#22232a' }}
                      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = '#18191f' }}
                    >
                      {pl.cover_image_url ? (
                        <img src={pl.cover_image_url} alt={tc(pl.name)} className="w-full aspect-square object-cover" />
                      ) : (
                        <div className="w-full aspect-square flex items-center justify-center select-none" style={{ background: 'linear-gradient(135deg, #003d1f 0%, #006D36 50%, #003d1f 100%)' }}>
                          <span className="text-white/20 text-7xl font-bold uppercase">{tc(pl.name).charAt(0)}</span>
                        </div>
                      )}
                      <div className="p-4">
                        <div className="flex gap-1.5 mb-2 flex-wrap">
                          {pl.difficulty_level && (
                            <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-md uppercase tracking-wider" style={{ color: '#fb923c', background: 'rgba(251,146,60,0.15)', border: '1px solid rgba(251,146,60,0.35)' }}>
                              {tc(pl.difficulty_level)}
                            </span>
                          )}
                          <span className="text-[10px] font-mono font-medium px-1.5 py-0.5 rounded-md tracking-wider" style={{ color: '#a5b4fc', background: 'rgba(165,180,252,0.1)', border: '1px solid rgba(165,180,252,0.2)' }}>
                            {pl.song_count} {pl.song_count === 1 ? t('browse.song') : t('browse.songs')}
                          </span>
                        </div>
                        <h3 className="text-white font-semibold text-base leading-snug mb-2 line-clamp-2 break-words">{tc(pl.name)}</h3>
                        {pl.description && (
                          <p className="text-gray-500 text-sm leading-relaxed line-clamp-2">{tc(pl.description)}</p>
                        )}
                      </div>
                    </button>
                  ))}
                  {/* Placeholder slots to fill up to 3 — desktop only */}
                  {Array.from({ length: Math.max(0, 3 - matchingPlaylists.length) }).map((_, i) => (
                    <div
                      key={`placeholder-${i}`}
                      className="hidden lg:block rounded-2xl border border-dashed border-zinc-700/60"
                      style={{ aspectRatio: '1 / 1.45', background: '#18191f' }}
                    />
                  ))}
                </div>
              </section>
            )}
          </div>
        )}
      </div>
      <ReportModal
        open={browseReportSongId !== null}
        onClose={() => setBrowseReportSongId(null)}
        payload={{ kind: 'song', song_id: browseReportSongId ?? undefined }}
      />
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
    <div className="rounded-2xl border border-gray-800/80 p-5 md:p-4" style={{ background: '#12121f' }}>
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
            shrink-0 inline-flex h-8 w-14 md:h-7 md:w-12 items-center rounded-full transition-colors
            ${value ? 'bg-green-500' : 'bg-gray-700'}
          `}
        >
          <span
            className={`
              inline-block h-6 w-6 md:h-5 md:w-5 rounded-full bg-white transition-transform
              ${value ? 'translate-x-7 md:translate-x-6' : 'translate-x-1'}
            `}
          />
        </button>
      </div>
    </div>
  )
}

function LanguagePicker() {
  const { language, setLanguage } = useLocalization()
  const t = useT()
  const langs: { code: 'en' | 'tr' | 'ru' | 'es' | 'pt' | 'de'; label: string }[] = [
    { code: 'en', label: 'English' },
    { code: 'tr', label: 'Türkçe' },
    { code: 'ru', label: 'Русский' },
    { code: 'es', label: 'Español' },
    { code: 'pt', label: 'Português' },
    { code: 'de', label: 'Deutsch' },
  ]
  return (
    <div className="rounded-2xl border border-gray-800/80 p-4" style={{ background: '#12121f' }}>
      <p className="text-white font-medium mb-1">{t('settings.uiLanguage')}</p>
      <p className="text-xs text-gray-500 mb-3 leading-relaxed">{t('settings.uiLanguageDesc')}</p>
      
      {/* Mobile: dropdown select */}
      <select
        value={language}
        onChange={e => setLanguage(e.target.value as typeof language)}
        className="sm:hidden w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-3 text-base text-white focus:outline-none focus:border-indigo-500"
      >
        {langs.map(l => (
          <option key={l.code} value={l.code}>{l.label}</option>
        ))}
      </select>

      {/* Tablet/Desktop: wrapped button grid */}
      <div className="hidden sm:flex flex-wrap gap-2">
        {langs.map(l => (
          <button
            key={l.code}
            onClick={() => setLanguage(l.code)}
            className={`px-4 py-2 rounded-xl text-sm font-medium transition-colors ${language === l.code ? 'bg-indigo-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'}`}
          >
            {l.label}
          </button>
        ))}
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
  onShowPricing,
  onUserUpdate,
}: {
  settings: AppSettings
  onUpdate: (patch: Partial<AppSettings>) => void
  onBack: () => void
  onLogout: () => void
  user: BackendUser | null
  activeTab: SettingsTab
  onTabChange: (tab: SettingsTab) => void
  onShowPricing: () => void
  onUserUpdate?: (user: BackendUser) => void
}) {
  const [supportForm, setSupportForm] = useState({ subject: '', message: '' })
  const [supportSent, setSupportSent] = useState(false)
  const [supportSending, setSupportSending] = useState(false)
  const [supportError, setSupportError] = useState<string | null>(null)
  const t = useT()

  const tabs: { key: SettingsTab; label: string; icon: React.ReactNode }[] = [
    {
      key: 'preferences',
      label: t('settings.preferences'),
      icon: (
        <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current shrink-0">
          <path d="M19.14 12.94a7.43 7.43 0 000-1.88l2.03-1.58a.5.5 0 00.12-.64l-1.92-3.32a.5.5 0 00-.6-.22l-2.39.96a7.36 7.36 0 00-1.63-.94l-.36-2.54A.5.5 0 0013.9 2h-3.8a.5.5 0 00-.49.42l-.36 2.54a7.36 7.36 0 00-1.63.94l-2.39-.96a.5.5 0 00-.6.22L2.71 8.48a.5.5 0 00.12.64l2.03 1.58a7.43 7.43 0 000 1.88l-2.03 1.58a.5.5 0 00-.12.64l1.92 3.32a.5.5 0 00.6.22l2.39-.96c.5.39 1.05.71 1.63.94l.36 2.54a.5.5 0 00.49.42h3.8a.5.5 0 00.49-.42l.36-2.54c.58-.23 1.13-.55 1.63-.94l2.39.96a.5.5 0 00.6-.22l1.92-3.32a.5.5 0 00-.12-.64l-2.03-1.58zM12 15.5A3.5 3.5 0 1112 8a3.5 3.5 0 010 7.5z"/>
        </svg>
      ),
    },
    {
      key: 'account',
      label: t('settings.account'),
      icon: (
        <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current shrink-0">
          <path d="M12 12c2.7 0 4.8-2.1 4.8-4.8S14.7 2.4 12 2.4 7.2 4.5 7.2 7.2 9.3 12 12 12zm0 2.4c-3.2 0-9.6 1.6-9.6 4.8v2.4h19.2v-2.4c0-3.2-6.4-4.8-9.6-4.8z"/>
        </svg>
      ),
    },
    {
      key: 'subscription',
      label: t('settings.subscription'),
      icon: (
        <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current shrink-0">
          <path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H4v-6h16v6zm0-10H4V6h16v2z"/>
        </svg>
      ),
    },
    {
      key: 'support',
      label: t('settings.support'),
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
          <button onClick={onLogout} className="text-xs text-gray-600 hover:text-gray-400 transition-colors">{t('nav.signOut')}</button>
        </div>
        </div>
      </header>

      <div className="flex flex-1 min-h-0 justify-center">
      <div className="flex flex-col md:flex-row w-full max-w-4xl min-h-0">
        {/* Mobile horizontal tab bar */}
        <div className="md:hidden flex border-b border-gray-900 overflow-x-auto no-scrollbar shrink-0" style={{ background: '#050608' }}>
          {tabs.map(tab => (
            <button
              key={tab.key}
              type="button"
              onClick={() => onTabChange(tab.key)}
              className={`flex items-center gap-2 px-4 py-3 text-sm whitespace-nowrap border-b-2 transition-colors shrink-0 ${
                activeTab === tab.key
                  ? 'border-white text-white'
                  : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>

        {/* Desktop vertical sidebar */}
        <nav className="hidden md:flex w-48 shrink-0 border-r border-gray-900 py-4 px-2 flex-col gap-0.5" style={{ background: '#050608' }}>
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
              <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wider mb-4">{t('settings.preferences')}</h2>
              <div className="rounded-2xl border border-gray-800/80 p-4" style={{ background: '#12121f' }}>
                <p className="text-white font-medium mb-1">{t('settings.musicSource')}</p>
                <p className="text-xs text-gray-500 mb-3 leading-relaxed">{t('settings.musicSourceDesc')}</p>
                <SourcePicker value={settings.preferredSource} onChange={v => onUpdate({ preferredSource: v })} />
              </div>
              <SettingRow
                title={t('settings.prioritizeContentWords')}
                description={t('settings.prioritizeContentWordsDesc')}
                value={settings.excludeStopWordsFromShortcuts}
                onChange={(next) => onUpdate({ excludeStopWordsFromShortcuts: next })}
              />
              <SettingRow
                title={t('settings.pauseOnInspect')}
                description={t('settings.pauseOnInspectDesc')}
                value={settings.pauseOnInspect}
                onChange={(next) => onUpdate({ pauseOnInspect: next })}
              />
              <LanguagePicker />
            </div>
          )}

          {activeTab === 'account' && (
            <div className="max-w-xl w-full flex flex-col min-h-full">
              <div className="space-y-3 flex-1">
              <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wider mb-4">{t('settings.account')}</h2>
              <div className="rounded-2xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                <div className="flex items-center gap-4 mb-5">
                  <div className="w-12 h-12 rounded-full bg-gray-700 flex items-center justify-center shrink-0">
                    <svg viewBox="0 0 24 24" className="w-6 h-6 fill-gray-400">
                      <path d="M12 12c2.7 0 4.8-2.1 4.8-4.8S14.7 2.4 12 2.4 7.2 4.5 7.2 7.2 9.3 12 12 12zm0 2.4c-3.2 0-9.6 1.6-9.6 4.8v2.4h19.2v-2.4c0-3.2-6.4-4.8-9.6-4.8z"/>
                    </svg>
                  </div>
                  <div>
                    <p className="text-white font-medium">{user?.display_name ?? t('settings.unknownUser')}</p>
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
                      <span className="text-sm text-gray-300">{t('settings.appleMusic')}</span>
                    </div>
                    {isAppleMusicAuthorized() ? (
                      <span className="text-xs text-green-400 font-medium">{t('settings.connected')}</span>
                    ) : (
                      <span className="text-xs text-gray-500">{t('settings.notConnected')}</span>
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
                  {t('nav.signOut')}
                </button>
              </div>
            </div>
          )}

          {activeTab === 'subscription' && (
            <div className="max-w-xl w-full space-y-3">
              <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wider mb-4">{t('settings.subscription')}</h2>
              
              {/* Current Plan Status */}
              <div className="rounded-2xl border border-gray-800/80 p-6" style={{ background: '#12121f' }}>
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 to-blue-500 flex items-center justify-center">
                      <svg viewBox="0 0 24 24" className="w-5 h-5 fill-white">
                        <path d="M12 2L2 7v10c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V7l-10-5z"/>
                      </svg>
                    </div>
                    <div>
                      <p className="text-white font-semibold capitalize">{user?.subscription_tier || 'free'} Plan</p>
                      <p className="text-xs text-gray-500">
                        {user?.subscription_status === 'active' ? 'Active' : 
                         user?.subscription_status === 'past_due' ? 'Past Due' : 
                         user?.subscription_status === 'canceled' ? 'Canceled' : 
                         user?.subscription_tier === 'free' ? 'Free Trial' : 'Inactive'}
                      </p>
                    </div>
                  </div>
                  {user?.subscription_tier === 'free' && (
                    <button
                      onClick={() => {
                        track('Upgrade Selected', {
                          source: 'settings_subscription',
                        })
                        onShowPricing()
                      }}
                      className="px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white text-sm font-medium rounded-lg transition-colors"
                    >
                      Upgrade
                    </button>
                  )}
                </div>

                {/* Subscription Details */}
                {user && user.subscription_tier !== 'free' && (
                  <div className="space-y-2 pt-4 border-t border-gray-800">
                    {user.subscription_platform && (
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-500">Platform</span>
                        <span className="text-white capitalize">{user.subscription_platform}</span>
                      </div>
                    )}
                    {user.subscription_started_at && (
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-500">Started</span>
                        <span className="text-white">
                          {new Date(user.subscription_started_at).toLocaleDateString('en-US', { 
                            year: 'numeric', 
                            month: 'short', 
                            day: 'numeric' 
                          })}
                        </span>
                      </div>
                    )}
                    {user.subscription_expires_at && user.subscription_tier !== 'lifetime' && (
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-500">
                          {user.subscription_cancel_at_period_end ? 'Expires' : 'Renews'}
                        </span>
                        <span className="text-white">
                          {new Date(user.subscription_expires_at).toLocaleDateString('en-US', { 
                            year: 'numeric', 
                            month: 'short', 
                            day: 'numeric' 
                          })}
                        </span>
                      </div>
                    )}
                    {user.subscription_tier === 'lifetime' && (
                      <div className="flex justify-between text-sm">
                        <span className="text-gray-500">Access</span>
                        <span className="text-green-400 font-medium">Lifetime</span>
                      </div>
                    )}
                  </div>
                )}

                {/* Sync Subscription Button */}
                <div className="pt-4 border-t border-gray-800">
                  <button
                    onClick={async () => {
                      const btn = document.querySelector('[data-sync-btn]') as HTMLButtonElement
                      if (!btn || btn.disabled) return
                      
                      btn.disabled = true
                      const originalText = btn.textContent
                      btn.textContent = 'Syncing...'
                      
                      try {
                        const freshUser = await api.syncSubscription()
                        btn.textContent = '✓ Synced'
                        setTimeout(() => {
                          btn.textContent = originalText
                          btn.disabled = false
                        }, 2000)
                        
                        // Update user data
                        onUserUpdate?.(freshUser)
                      } catch (error) {
                        console.error('Failed to sync subscription:', error)
                        btn.textContent = '✗ Failed'
                        setTimeout(() => {
                          btn.textContent = originalText
                          btn.disabled = false
                        }, 2000)
                      }
                    }}
                    data-sync-btn
                    className="w-full px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Sync Subscription
                  </button>
                  <p className="text-xs text-gray-500 mt-2 text-center">
                    Refresh subscription status from Paddle
                  </p>
                </div>

                {/* Free Plan Features */}
                {user?.subscription_tier === 'free' && (
                  <div className="pt-4 border-t border-gray-800">
                    <p className="text-xs text-gray-500 mb-2">With Premium you get:</p>
                    <ul className="space-y-1.5">
                      <li className="text-sm text-gray-400 flex items-center gap-2">
                        <svg viewBox="0 0 20 20" className="w-4 h-4 fill-green-500 flex-shrink-0">
                          <path d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"/>
                        </svg>
                        <span>Unlimited word translations</span>
                      </li>
                      <li className="text-sm text-gray-400 flex items-center gap-2">
                        <svg viewBox="0 0 20 20" className="w-4 h-4 fill-green-500 flex-shrink-0">
                          <path d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"/>
                        </svg>
                        <span>Full lyrics access</span>
                      </li>
                      <li className="text-sm text-gray-400 flex items-center gap-2">
                        <svg viewBox="0 0 20 20" className="w-4 h-4 fill-green-500 flex-shrink-0">
                          <path d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"/>
                        </svg>
                        <span>All languages supported</span>
                      </li>
                    </ul>
                  </div>
                )}
              </div>

              {/* Manage Subscription (for active subscriptions) */}
              {user && user.subscription_tier !== 'free' && user.subscription_platform === 'paddle' && (
                <div className="rounded-2xl border border-gray-800/80 p-6" style={{ background: '#12121f' }}>
                  <p className="text-white font-medium mb-2">Manage Subscription</p>
                  <p className="text-sm text-gray-500 mb-4">
                    To update your payment method, billing information, or cancel your subscription, 
                    visit the Paddle billing portal.
                  </p>
                  <a
                    href="https://www.paddle.com/support/manage-subscription"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-block px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white text-sm font-medium rounded-lg transition-colors"
                  >
                    Open Billing Portal →
                  </a>
                </div>
              )}
            </div>
          )}

          {activeTab === 'support' && (
            <div className="max-w-xl w-full space-y-3">
              <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wider mb-4">{t('settings.support')}</h2>
              {supportSent ? (
                <div className="rounded-2xl border border-gray-800/80 p-8 flex flex-col items-center text-center" style={{ background: '#12121f' }}>
                  <svg viewBox="0 0 24 24" className="w-10 h-10 fill-green-500 mb-3">
                    <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                  </svg>
                  <p className="text-white font-semibold mb-1">{t('settings.messageSent')}</p>
                  <p className="text-gray-500 text-sm">{t('settings.messageReply')}</p>
                  <button
                    type="button"
                    onClick={() => { setSupportSent(false); setSupportForm({ subject: '', message: '' }) }}
                    className="mt-4 text-xs text-gray-500 hover:text-gray-300 transition-colors"
                  >
                    {t('settings.sendAnotherMessage')}
                  </button>
                </div>
              ) : (
                <div className="rounded-2xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                  <p className="text-white font-medium mb-1">{t('settings.contactUs')}</p>
                  <p className="text-xs text-gray-500 mb-4 leading-relaxed">{t('settings.contactUsDesc')}</p>
                  <div className="space-y-3">
                    <div>
                      <label className="block text-xs text-gray-500 mb-1.5" htmlFor="support-subject">{t('settings.subject')}</label>
                      <input
                        id="support-subject"
                        type="text"
                        value={supportForm.subject}
                        onChange={e => setSupportForm(f => ({ ...f, subject: e.target.value }))}
                        placeholder={t('settings.subjectPlaceholder')}
                        className="w-full rounded-xl border border-gray-700 bg-gray-900/60 px-3 py-2.5 text-sm text-white placeholder-gray-600 outline-none focus:border-gray-500 transition-colors"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-gray-500 mb-1.5" htmlFor="support-message">{t('settings.message')}</label>
                      <textarea
                        id="support-message"
                        rows={5}
                        value={supportForm.message}
                        onChange={e => setSupportForm(f => ({ ...f, message: e.target.value }))}
                        placeholder={t('settings.messagePlaceholder')}
                        className="w-full rounded-xl border border-gray-700 bg-gray-900/60 px-3 py-2.5 text-sm text-white placeholder-gray-600 outline-none focus:border-gray-500 transition-colors resize-none"
                      />
                    </div>
                    <button
                      type="button"
                      disabled={!supportForm.subject.trim() || !supportForm.message.trim() || supportSending}
                      onClick={async () => {
                        setSupportSending(true)
                        setSupportError(null)
                        try {
                          await api.createReport({
                            kind: 'support',
                            message: `${supportForm.subject.trim()}\n\n${supportForm.message.trim()}`,
                          })
                          setSupportSent(true)
                          track('Support Ticket Submitted', { category: 'settings' })
                        } catch {
                          setSupportError('Something went wrong, please try again.')
                        } finally {
                          setSupportSending(false)
                        }
                      }}
                      className="w-full rounded-xl bg-white text-black text-sm font-medium py-2.5 hover:bg-gray-100 disabled:bg-gray-800 disabled:text-gray-500 transition-colors"
                    >
                      {supportSending ? 'Sending…' : t('settings.sendMessage')}
                    </button>
                    {supportError && <p className="text-red-400 text-xs pt-1">{supportError}</p>}
                  </div>
                </div>
              )}

              <div className="space-y-4 pt-2">
                <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Legal</h2>
                <div className="rounded-2xl border border-gray-800/80 divide-y divide-gray-800/60" style={{ background: '#12121f' }}>
                  <a
                    href="/terms"
                    onClick={e => { e.preventDefault(); window.history.pushState(null, '', '/terms'); window.dispatchEvent(new PopStateEvent('popstate')) }}
                    className="flex items-center justify-between px-5 py-3.5 hover:bg-white/5 transition-colors group"
                  >
                    <span className="text-sm text-gray-300 group-hover:text-white transition-colors">Terms of Service</span>
                    <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current text-gray-600 group-hover:text-gray-400 transition-colors shrink-0">
                      <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z"/>
                    </svg>
                  </a>
                  <a
                    href="/privacy"
                    onClick={e => { e.preventDefault(); window.history.pushState(null, '', '/privacy'); window.dispatchEvent(new PopStateEvent('popstate')) }}
                    className="flex items-center justify-between px-5 py-3.5 hover:bg-white/5 transition-colors group"
                  >
                    <span className="text-sm text-gray-300 group-hover:text-white transition-colors">Privacy Policy</span>
                    <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current text-gray-600 group-hover:text-gray-400 transition-colors shrink-0">
                      <path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z"/>
                    </svg>
                  </a>
                </div>
              </div>
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
  song, positionInPlaylist, user, onBack, onLogout, onOpenSettings, onOpenAdmin, onOpenAccount, isAdmin, onPrev, onNext, canPrev, canNext, settings, onUpdate, storedMusicUserToken, onMusicUserToken, favoriteSongIds, toggleFavorite, targetLang, onTargetLangChange, onGoToBrowse, playlistName, onGoToPlaylist, onShowPricing, onBackToTrial,
}: {
  song: SongDetail
  positionInPlaylist?: number
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
  favoriteSongIds: Set<number>
  toggleFavorite: (id: number) => void
  targetLang?: string
  onTargetLangChange?: (lang: string) => void
  onGoToBrowse: () => void
  playlistName?: string | null
  onGoToPlaylist?: () => void
  onShowPricing?: () => void
  onBackToTrial?: () => void
}) {
  const [infoVisible, setInfoVisible] = useState(false)
  const [playerMenuOpen, setPlayerMenuOpen] = useState(false)
  const [playerReportOpen, setPlayerReportOpen] = useState(false)
  const [showPlayerTutorial, setShowPlayerTutorial] = useState(false)
  const [tutorialKey, setTutorialKey] = useState(0)
  const tutorialRef = useRef<TutorialHandle>(null)
  const tutorialStepRef = useRef(0)
  const autoPausedRef = useRef(false)
  const t = useT()
  const { language, setLanguage } = useLocalization()

  // ── Tutorial ──────────────────────────────────────────────────────────────────
  const tutorialSteps: TutorialStep[] = [
    { id: 'lyrics-word',        target: '[data-tutorial="lyrics-word"]',        text: t('tutorial.word'),          padding: 8, scrollIntoView: true, interactive: true },
    { id: 'lyrics-word-peek',   target: '[data-tutorial="lyrics-word-peek"]',   text: t('tutorial.peek'),          padding: 8, interactive: true },
    { id: 'line-translate',     target: '[data-tutorial="line-translate"]',     text: t('tutorial.lineTranslate'), padding: 10, side: 'right', interactive: true },
    { id: 'apple-music-toggle', target: '[data-tutorial="apple-music-toggle"]', text: t('tutorial.sourceToggle'), padding: 6, side: 'bottom' },
    { id: 'shortcuts-panel',    target: '[data-tutorial="shortcuts-panel"]',    text: t('tutorial.shortcuts'),    padding: 8, side: 'left' },
  ]

  // Autopause when tutorial starts
  useEffect(() => {
    if (showPlayerTutorial && isPlaying) togglePlay()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showPlayerTutorial])

  useEffect(() => {
    if (showPlayerTutorial) track('Tutorial Started')
  }, [showPlayerTutorial, tutorialKey])

  const handleTutorialClose = () => {
    track('Tutorial Skipped')
    localStorage.setItem('tutorial_player_seen', '1')
    setShowPlayerTutorial(false)
    if (!isPlaying) togglePlay()
  }

  const handleTutorialComplete = () => {
    track('Tutorial Completed')
    localStorage.setItem('tutorial_player_seen', '1')
    setShowPlayerTutorial(false)
    if (!isPlaying) togglePlay()
  }

  // ── Analytics refs ────────────────────────────────────────────────────────────
  const songStartedRef = useRef(false)
  const songCompletedRef = useRef(false)
  const maxPosRef = useRef(0)
  const durationMsRef = useRef(0)
  const prevEffectiveSourceRef = useRef<'youtube' | 'apple_music' | null>(null)

  useEffect(() => {
    if (!playerMenuOpen) return
    const close = () => setPlayerMenuOpen(false)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [playerMenuOpen])

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

  // ── Analytics effects ─────────────────────────────────────────────────────────

  // Reset per-song analytics state when the song changes
  useEffect(() => {
    return () => {
      // Runs when song.id changes (previous song cleanup) or on unmount
      const dur = durationMsRef.current
      const maxPos = maxPosRef.current
      if (dur > 0 && maxPos > 0 && songStartedRef.current && !songCompletedRef.current) {
        const pct = maxPos / dur
        if (pct < 0.25) {
          track('Song Abandoned', {
            seconds_played: Math.round(maxPos / 1000),
            abandonment_pct: Math.round(pct * 100),
          })
        }
      }
      songStartedRef.current = false
      songCompletedRef.current = false
      maxPosRef.current = 0
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [song.id])

  // Keep duration ref in sync
  useEffect(() => { durationMsRef.current = durationMs }, [durationMs])

  // Track max position reached and fire Song Started / Song Completed
  useEffect(() => {
    if (positionMs > maxPosRef.current) maxPosRef.current = positionMs
  }, [positionMs])

  useEffect(() => {
    if (!isPlaying || songStartedRef.current) return
    songStartedRef.current = true
    track('Song Started', {
      song_id: song.id,
      source: effectiveSource,
      source_lang: song.language.code,
      target_lang: targetLang ?? '',
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPlaying])

  useEffect(() => {
    if (songCompletedRef.current || durationMs <= 0 || positionMs <= 0) return
    const pct = positionMs / durationMs
    if (pct < 0.9) return
    songCompletedRef.current = true
    track('Song Completed', {
      duration: Math.round(durationMs / 1000),
      completion_pct: Math.round(pct * 100),
    })
  }, [positionMs, durationMs])

  // Playback Source Switched — fire when effectiveSource changes after first render
  useEffect(() => {
    const prev = prevEffectiveSourceRef.current
    prevEffectiveSourceRef.current = effectiveSource
    if (prev === null || prev === effectiveSource) return
    track('Playback Source Switched', { from: prev, to: effectiveSource })
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
        track('Previous Song')
        handlePrev()
        return
      }

      if (e.key === 'ArrowRight') {
        e.preventDefault()
        if (e.repeat || !canNext) return
        track('Next Song', { trigger: 'keyboard' })
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
        <div className="max-w-[1200px] mx-auto w-full px-3 py-3 md:px-4 md:py-4 flex items-center justify-between gap-2 md:gap-4">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="text-gray-500 hover:text-gray-300 transition-colors mr-1" aria-label="Back">
            <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
              <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/>
            </svg>
          </button>
          <button
            type="button"
            onClick={onGoToBrowse}
            className="hover:opacity-70 shrink-0"
            aria-label="Go to browse"
          >
            <img src={singolingLogo} className="h-6 md:h-8 object-contain" alt="SingoLing" />
          </button>
          {playlistName && (
            <>
              <span className="text-zinc-600 text-sm select-none hidden sm:inline">/</span>
              <button
                type="button"
                onClick={onGoToPlaylist}
                className="text-sm text-zinc-400 hover:text-white transition-colors truncate max-w-[100px] sm:max-w-[140px] md:max-w-[180px] hidden sm:block"
              >
                {playlistName}
              </button>
            </>
          )}
        </div>
        <div className="flex items-center gap-2 md:gap-3">
          {isAdmin && (
            <button
              type="button"
              onClick={onOpenAdmin}
              className="text-xs text-amber-500 hover:text-amber-300 transition-colors"
            >
              {t('nav.admin')}
            </button>
          )}
          {isAdmin && (
            <button
              type="button"
              onClick={() => { localStorage.removeItem('tutorial_player_seen'); setShowPlayerTutorial(true); setTutorialKey(k => k + 1) }}
              className="text-xs text-pink-500 hover:text-pink-300 transition-colors"
            >
              Replay tutorial
            </button>
          )}
          {/* Target language selector — admin only */}
          {isAdmin && song.target_langs && song.target_langs.length > 0 && onTargetLangChange && (
            <select
              value={targetLang ?? ''}
              onChange={e => { onTargetLangChange(e.target.value); e.currentTarget.blur() }}
              className="text-xs rounded-lg border border-indigo-700/70 bg-gray-800/70 px-2 py-1 text-indigo-300 focus:outline-none focus:border-indigo-500 cursor-pointer"
              aria-label="Translation language"
            >
              {song.target_langs.map(lang => (
                <option key={lang} value={lang}>{lang.toUpperCase()}</option>
              ))}
            </select>
          )}
          {/* UI language selector */}
          <select
            value={language}
            onChange={e => { setLanguage(e.target.value as 'en' | 'tr' | 'ru' | 'es' | 'pt' | 'de'); e.currentTarget.blur() }}
            className="text-xs rounded-lg border border-gray-700/70 bg-gray-800/70 px-2 py-1 text-gray-300 focus:outline-none focus:border-gray-500 cursor-pointer"
            aria-label="UI language"
          >
            <option value="en">EN</option>
            <option value="tr">TR</option>
            <option value="ru">RU</option>
            <option value="es">ES</option>
            <option value="pt">PT</option>
            <option value="de">DE</option>
          </select>
          {/* Inline source switcher — always visible; unavailable sources are dimmed */}
          {(() => {
            const opts: { value: AppSettings['preferredSource']; activeClass: string; available: boolean; label: string }[] = [
              { value: 'youtube',     label: 'YouTube',     activeClass: 'text-red-400',  available: !!song.youtube_url },
              { value: 'apple_music', label: 'Apple Music', activeClass: 'text-gray-200', available: !!song.apple_music_url },
            ]
            return (
              <div data-tutorial="apple-music-toggle" className="flex items-center gap-0.5 rounded-lg bg-gray-800/70 p-0.5">
                {opts.map(opt => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => { if (!opt.available) return; track('Playback Source Switched', { from: effectiveSource, to: opt.value }); onUpdate({ preferredSource: opt.value }) }}
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
            {t('nav.preferences')}
          </button>
          {user?.display_name && <button type="button" onClick={onOpenAccount} className="text-xs text-gray-500 hover:text-gray-300 transition-colors hidden sm:inline-block">{user.display_name}</button>}
          <button onClick={onLogout} className="text-xs text-gray-600 hover:text-gray-400 transition-colors hidden sm:inline-block">{t('nav.signOut')}</button>
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
          <section className="relative rounded-md border border-zinc-700/70 p-6 min-w-0 min-h-[210px] lg:min-h-[240px]" style={{ background: '#25262b' }}>
          {/* 3-dot options menu — upper right corner */}
          <div className="absolute top-3 right-3 z-10">
              <button
                type="button"
                aria-label="Song options"
                onClick={e => { e.stopPropagation(); setPlayerMenuOpen(v => !v) }}
                className="w-8 h-8 flex items-center justify-center rounded-full text-zinc-400 hover:text-zinc-100 hover:bg-white/10 transition-colors"
              >
                <svg viewBox="0 0 20 20" className="w-5 h-5 fill-current" aria-hidden>
                  <circle cx="10" cy="4" r="1.5"/>
                  <circle cx="10" cy="10" r="1.5"/>
                  <circle cx="10" cy="16" r="1.5"/>
                </svg>
              </button>
              {playerMenuOpen && (
                <div
                  className="absolute right-0 md:right-0 top-10 z-50 w-screen md:w-auto md:min-w-[200px] left-0 md:left-auto rounded-none md:rounded-xl border-x-0 md:border-x border-zinc-700 py-1.5 shadow-2xl"
                  style={{ background: '#1c1d21' }}
                  onClick={e => e.stopPropagation()}
                >
                  <button
                    type="button"
                    className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-zinc-300 hover:bg-white/8 transition-colors"
                    onClick={() => { toggleFavorite(song.id); setPlayerMenuOpen(false) }}
                  >
                    <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" fill={favoriteSongIds.has(song.id) ? '#f87171' : 'none'} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
                    </svg>
                    {favoriteSongIds.has(song.id) ? t('browser.removeFromFavorites') : t('browser.addToFavorites')}
                  </button>
                  <button
                    type="button"
                    className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-zinc-300 hover:bg-white/8 transition-colors"
                    onClick={() => setPlayerMenuOpen(false)}
                  >
                    <svg viewBox="0 0 20 20" className="w-4 h-4 shrink-0 fill-current text-zinc-400" aria-hidden>
                      <path fillRule="evenodd" d="M3 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm0 4a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1z" clipRule="evenodd"/>
                    </svg>
                    {t('browser.markAsNotListened')}
                  </button>
                  <div className="border-t border-zinc-700/60 mx-3 my-1" />
                  <button
                    type="button"
                    className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-red-400 hover:bg-red-500/10 transition-colors"
                    onClick={() => { setPlayerMenuOpen(false); setPlayerReportOpen(true) }}
                  >
                    <svg viewBox="0 0 20 20" className="w-4 h-4 shrink-0 fill-current" aria-hidden>
                      <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-3a1 1 0 00-1 1v.5a1 1 0 002 0V11a1 1 0 00-1-1z" clipRule="evenodd"/>
                    </svg>
                    {t('browser.reportProblem')}
                  </button>
                </div>
              )}
            </div>
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
            <div className="min-w-0 flex-1">
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
              onClick={() => { track('Previous Song'); handlePrev() }}
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
              onClick={() => { track('Next Song', { trigger: 'button' }); handleNext() }}
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
            positionInPlaylist={positionInPlaylist}
            targetLang={targetLang}
            themeBackground={lyricsTheme.panelGradient}
            themeAsideBackground={lyricsTheme.asideGradient}
            accentTextColor={lyricsTheme.accentTextColor}
            filterStopWordsForIndexing={settings.excludeStopWordsFromShortcuts}
            onInfoVisibilityChange={setInfoVisible}
            onFirstLineActive={() => { if (localStorage.getItem('tutorial_player_seen') !== '1') setShowPlayerTutorial(true) }}
            onWordLookupClosed={() => { if (showPlayerTutorial && tutorialStepRef.current === 0) tutorialRef.current?.advance() }}
            onWordPeekCompleted={() => { if (showPlayerTutorial && tutorialStepRef.current === 1) tutorialRef.current?.advance() }}
            onLineTranslatePeekCompleted={() => { if (showPlayerTutorial && tutorialStepRef.current === 2) tutorialRef.current?.advance() }}
            onLineTranslateClosed={() => { if (showPlayerTutorial && tutorialStepRef.current === 2) tutorialRef.current?.advance() }}
            onSeek={seekTo}
            onTogglePlayback={togglePlay}
            onShowPricing={onShowPricing}
            onBackToTrial={onBackToTrial}
          />
        </section>
      </main>
      <ReportModal
        open={playerReportOpen}
        onClose={() => setPlayerReportOpen(false)}
        payload={{ kind: 'song', song_id: song.id }}
      />
      <TutorialOverlay
        ref={tutorialRef}
        key={tutorialKey}
        steps={tutorialSteps}
        open={showPlayerTutorial}
        onClose={handleTutorialClose}
        onComplete={handleTutorialComplete}
        onStepChange={(i) => { tutorialStepRef.current = i }}
      />
    </div>
  )
}

// ── Root App ──────────────────────────────────────────────────────────────────

export default function App() {
  const { language, setLanguage } = useLocalization()
  const [currentPath, setCurrentPath] = useState(() => (typeof window === 'undefined' ? '/browse' : ((window.location.pathname || '/browse') + window.location.search)))
  const [adminOpen, setAdminOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_SETTINGS)
  const [credentialUser, setCredentialUser] = useState<BackendUser | null>(() => {
    try {
      const raw = localStorage.getItem(PASSWORD_SESSION_KEY)
      if (!raw) return null
      const user = JSON.parse(raw) as BackendUser
      // Restore admin Bearer token if the stored user object has one
      if (user.is_admin && user.admin_token) {
        setAdminSession(user.admin_token)
      }
      return user
    } catch {
      return null
    }
  })
  const [loginBusy, setLoginBusy] = useState(false)
  const [loginError, setLoginError] = useState<string | null>(null)
  const [showSignUp, setShowSignUp] = useState(() =>
    typeof window !== 'undefined' && window.location.pathname === '/signup'
  )
  const [showForgotPassword, setShowForgotPassword] = useState(false)
  const [resetToken, setResetToken] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null
    return new URLSearchParams(window.location.search).get('reset_token')
  })

  const { favoriteSongIds, toggleFavorite } = useFavorites(!!credentialUser)
  const { listenedSongIds, markListened, unmarkListened } = useListened(!!credentialUser)
  const { entries: wordLookupEntries } = useWordHistory(!!credentialUser)

  const [songs,        setSongs]        = useState<SongSummary[]>([])
  const [playlists,    setPlaylists]    = useState<PlaylistSummary[]>(() => {
    try {
      const raw = localStorage.getItem('flowup_playlists_cache')
      return raw ? (JSON.parse(raw) as PlaylistSummary[]) : []
    } catch { return [] }
  })
  const [activePlaylistId, setActivePlaylistId] = useState<number | null>(null)
  const [activePlaylist, setActivePlaylist] = useState<PlaylistDetail | null>(null)
  // null = use playlist default; '' = no translation; 'en' etc = user-chosen override
  const [overrideTargetLang, setOverrideTargetLang] = useState<string | null>(null)
  // Maps music language code → preferred target language chosen in /browse (e.g. { ru: 'tr' })
  const [browseTargetLangMap, setBrowseTargetLangMap] = useState<Record<string, string>>(() => {
    try {
      const raw = localStorage.getItem('browse.targetLangMap')
      return raw ? JSON.parse(raw) as Record<string, string> : {}
    } catch { return {} }
  })

  const playlistWordCount = useMemo(() => {
    if (!activePlaylist) return 0
    const songIdSet = new Set(activePlaylist.songs.map(s => s.song_id))
    return new Set(
      wordLookupEntries
        .filter(e => e.song_id !== null && songIdSet.has(e.song_id))
        .map(e => `${e.lemma}|${e.language}|${e.target_lang}`)
    ).size
  }, [activePlaylist, wordLookupEntries])
  const [playlistsLoading, setPlaylistsLoading] = useState(false)
  const [playlistDetailLoading, setPlaylistDetailLoading] = useState(false)
  const [songsError,   setSongsError]   = useState<string | null>(null)
  const [activeSong,   setActiveSong]   = useState<SongDetail | null>(null)

  const availableTargetLangs = useMemo(
    () => activePlaylist?.target_langs ?? activeSong?.target_langs ?? [],
    [activePlaylist, activeSong]
  )
  const effectiveTargetLang = overrideTargetLang !== null
    ? (overrideTargetLang || undefined)
    : (() => {
        const songLangCode = activeSong?.language.code
        const browsePref = songLangCode ? browseTargetLangMap[songLangCode] : undefined
        const lowerPref = browsePref?.toLowerCase()
        const match = lowerPref ? availableTargetLangs.find(l => l.toLowerCase() === lowerPref) : undefined
        return match ?? availableTargetLangs[0] ?? browsePref
      })()

  const [songLoading,  setSongLoading]  = useState(false)
  const [lastSelectedSongId, setLastSelectedSongId] = useState<number | null>(null)
  const [settingsHydrated, setSettingsHydrated] = useState(false)
  const restoreDoneRef = useRef(false)
  const playlistCacheRef = useRef<Map<number, PlaylistDetail>>(new Map())
  const route = useMemo(() => parseAppRoute(currentPath), [currentPath])

  const navigateToPath = useCallback((path: string, replace = false) => {
    if (typeof window === 'undefined') return
    if ((window.location.pathname + window.location.search) === path) return
    if (replace) {
      window.history.replaceState(null, '', path)
      setCurrentPath(path)
      return
    }
    window.history.pushState(null, '', path)
    setCurrentPath(path)
  }, [])

  // Auto-sync subscription after successful Paddle checkout
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('subscribed') === 'true' && credentialUser) {
      console.log('[Paddle] Detected successful checkout, syncing subscription...')
      api.syncSubscription()
        .then((updatedUser) => {
          console.log('[Paddle] Subscription synced:', updatedUser.subscription_tier)
          setCredentialUser(updatedUser)
          // Remove the parameter and navigate to browse
          navigateToPath('/browse')
        })
        .catch((err) => {
          console.error('[Paddle] Failed to sync subscription:', err)
        })
    }
  }, [credentialUser, navigateToPath])

  useEffect(() => {
    const onPopState = () => {
      const path = (window.location.pathname || '/browse') + window.location.search
      setCurrentPath(path)
      setShowSignUp(window.location.pathname === '/signup')
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const appUser = credentialUser ? { display_name: credentialUser.display_name, email: credentialUser.email, subscription_tier: credentialUser.subscription_tier } : null
  const tc = useContentT()

  const settingsOwnerSpotifyId = credentialUser?.spotify_id ?? null
  const isAuthenticated = !!credentialUser
  const isAdmin = Boolean(credentialUser?.is_admin)

  // On mount, correct the URL to /login or /signup when showing auth screens
  useEffect(() => {
    if (isAuthenticated) return
    const path = window.location.pathname
    if (path !== '/login' && path !== '/signup' && path !== '/privacy' && path !== '/terms') {
      const target = showSignUp ? '/signup' : '/login'
      window.history.replaceState(null, '', target)
      setCurrentPath(target)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // After auth, redirect away from /login or /signup
  useEffect(() => {
    if (!isAuthenticated) return
    const path = window.location.pathname
    if (path === '/login' || path === '/signup') {
      window.history.replaceState(null, '', '/browse')
      setCurrentPath('/browse')
    }
  }, [isAuthenticated])

  const loadSongs = useCallback(async () => {
    setSongsError(null)
    try {
      const nextSongs = await api.listSongs()
      setSongs(nextSongs)
    } catch (e) {
      setSongsError(e instanceof Error ? e.message : 'Failed to load songs')
    }
  }, [])

  const loadPlaylists = useCallback(async () => {
    setPlaylistsLoading(true)
    setSongsError(null)
    try {
      const pls = await api.listPlaylists()
      setPlaylists(pls)
      try { localStorage.setItem('flowup_playlists_cache', JSON.stringify(pls)) } catch { /* ignore */ }
    } catch (e) {
      setSongsError(e instanceof Error ? e.message : 'Failed to load playlists')
    } finally {
      setPlaylistsLoading(false)
    }
  }, [])

  const updateSettings = useCallback((patch: Partial<AppSettings>) => {
    setSettings(prev => ({ ...prev, ...patch }))
    if (!settingsOwnerSpotifyId) return
    api.updateUserSettings(toApiSettingsPatch(patch)).catch(() => {
      // Non-fatal: keep optimistic UI state if backend update fails.
    })
  }, [settingsOwnerSpotifyId])

  const handleEmailLogin = useCallback(async (email: string, password: string) => {
    setLoginBusy(true)
    setLoginError(null)
    try {
      const user = await api.loginWithEmailPassword({ email, password })
      if (user.is_admin && user.admin_token) {
        setAdminSession(user.admin_token)
      } else {
        clearAdminSession()
      }
      setCredentialUser(user)
      localStorage.setItem(PASSWORD_SESSION_KEY, JSON.stringify(user))
      if (user.preferred_lang) setLanguage(user.preferred_lang as 'en' | 'tr' | 'ru' | 'es' | 'pt' | 'de')
      track('Login', { method: 'email' })
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
      const user = await api.loginWithGoogle(credential, language)
      if (user.is_admin && user.admin_token) {
        setAdminSession(user.admin_token)
      } else {
        clearAdminSession()
      }
      setCredentialUser(user)
      localStorage.setItem(PASSWORD_SESSION_KEY, JSON.stringify(user))
      if (user.preferred_lang) setLanguage(user.preferred_lang as 'en' | 'tr' | 'ru' | 'es' | 'pt' | 'de')
      track('Login', { method: 'google' })
    } catch (e) {
      setLoginError(e instanceof Error ? e.message : 'Google sign-in failed')
    } finally {
      setLoginBusy(false)
    }
  }, [])

  const handleAppleLogin = useCallback(async (idToken: string) => {
    setLoginBusy(true)
    setLoginError(null)
    try {
      const user = await api.loginWithApple(idToken, language)
      if (user.is_admin && user.admin_token) {
        setAdminSession(user.admin_token)
      } else {
        clearAdminSession()
      }
      setCredentialUser(user)
      localStorage.setItem(PASSWORD_SESSION_KEY, JSON.stringify(user))
      if (user.preferred_lang) setLanguage(user.preferred_lang as 'en' | 'tr' | 'ru' | 'es' | 'pt' | 'de')
      track('Login', { method: 'apple' })
    } catch (e) {
      setLoginError(e instanceof Error ? e.message : 'Apple sign-in failed')
    } finally {
      setLoginBusy(false)
    }
  }, [])

  const handleRegister = useCallback(async (displayName: string, email: string, password: string) => {
    setLoginBusy(true)
    setLoginError(null)
    try {
      const user = await api.register({ display_name: displayName, email, password, lang: language })
      if (user.is_admin && user.admin_token) {
        setAdminSession(user.admin_token)
      } else {
        clearAdminSession()
      }
      setCredentialUser(user)
      localStorage.setItem(PASSWORD_SESSION_KEY, JSON.stringify(user))
      if (user.preferred_lang) setLanguage(user.preferred_lang as 'en' | 'tr' | 'ru' | 'es' | 'pt' | 'de')
      track('Sign Up', { method: 'email' })
    } catch (e) {
      setLoginError(e instanceof Error ? e.message : 'Failed to create account')
    } finally {
      setLoginBusy(false)
    }
  }, [])

  const handleLogout = useCallback(() => {
    track('Logout')
    clearAdminSession()
    setAdminOpen(false)
    setCredentialUser(null)
    setSettingsOpen(false)
    setActiveSong(null)
    _songCache.clear()
    _inFlight.clear()
    localStorage.removeItem(PASSWORD_SESSION_KEY)
    localStorage.removeItem('flowup.openedSongs.v1')
    localStorage.removeItem('flowup_favorite_songs')
    sessionStorage.clear()
    navigateToPath('/browse', true)
  }, [navigateToPath])

  const handleMusicUserToken = useCallback((token: string | null) => {
    if (!credentialUser) return
    const updated: BackendUser = { ...credentialUser, apple_music_user_token: token }
    setCredentialUser(updated)
    localStorage.setItem(PASSWORD_SESSION_KEY, JSON.stringify(updated))
    api.saveAppleMusicToken(token).catch(console.error)
    if (token) track('Apple Music Connected')
    else track('Apple Music Disconnected')
  }, [credentialUser])

  const handleBrowseTargetLang = useCallback((musicLang: string, targetLang: string) => {
    setBrowseTargetLangMap(prev => {
      const next = { ...prev, [musicLang]: targetLang }
      try { localStorage.setItem('browse.targetLangMap', JSON.stringify(next)) } catch { /* ignore */ }
      return next
    })
  }, [])

  const handleSelectSong = useCallback(async (id: number, options?: { updateRoute?: boolean; playlistId?: number | null }) => {
    markListened(id)
    const effectivePlaylistId = options?.playlistId ?? activePlaylistId
    if (activePlaylist) {
      const position = activePlaylist.songs.findIndex(s => s.song_id === id)
      track('Song Selected From Playlist', { song_id: id, position_in_playlist: position + 1 })
    }
    // Navigate immediately so the UI responds at once; song data loads in background.
    if (options?.updateRoute !== false) {
      navigateToPath(songPath(id, activePlaylist?.id))
    }
    const source = settings.preferredSource
    const songSummary = songs.find(s => s.id === id)
    const songLang = songSummary?.language_code ?? activePlaylist?.language_code
    const browsePref = songLang ? browseTargetLangMap[songLang] : undefined
    const lowerPref = browsePref?.toLowerCase()
    const matchedLang = lowerPref ? availableTargetLangs.find(l => l.toLowerCase() === lowerPref) : undefined
    const targetLang = overrideTargetLang !== null
      ? (overrideTargetLang || undefined)
      : matchedLang ?? availableTargetLangs[0] ?? browsePref
    const key = _songCacheKey(id, source, targetLang)
    const cached = _songCache.get(key)
    // Skip cache if: (1) we have playlist context AND (2) cached version shows locked lyrics
    // This ensures free users see unlocked trial songs immediately on reload
    const canUseCache = cached && (!effectivePlaylistId || cached.lyrics_unlocked)
    if (canUseCache) {
      // Instant render from cache.
      setActiveSong(cached)
      setSongLoading(false)
      setLastSelectedSongId(id)
      return
    }
    setSongLoading(true)
    setActiveSong(null)
    try {
      const detail = await _fetchSong(id, source, targetLang, effectivePlaylistId ?? undefined)
      setActiveSong(detail)
      setLastSelectedSongId(detail.id)
    } catch (e) {
      setSongsError(e instanceof Error ? e.message : 'Failed to load song')
      navigateToPath('/browse')
    } finally {
      setSongLoading(false)
    }
  }, [settings.preferredSource, navigateToPath, availableTargetLangs, overrideTargetLang, songs, activePlaylist, activePlaylistId, browseTargetLangMap, markListened, track])

  const handlePrefetchSong = useCallback((id: number) => {
    const source = settings.preferredSource
    void _fetchSong(id, source, effectiveTargetLang, activePlaylistId ?? undefined).catch(() => {})
  }, [settings.preferredSource, activePlaylistId, effectiveTargetLang])

  // Re-fetch active song lyrics when source preference changes so the player
  // immediately gets the right per-source timestamps. Invalidate stale cache entry.
  useEffect(() => {
    if (!activeSong) return
    const source = settings.preferredSource
    const targetLang = effectiveTargetLang
    const key = _songCacheKey(activeSong.id, source, targetLang)
    _songCache.delete(key)  // force fresh fetch for new source
    void _fetchSong(activeSong.id, source, targetLang, activePlaylistId ?? undefined).then(d => { setActiveSong(d) }).catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.preferredSource, activePlaylistId])

  // Re-fetch active song when the effective target language changes (e.g. after
  // the initial no-target-lang fetch reveals available target langs, causing
  // effectiveTargetLang to go from undefined → 'tr').
  useEffect(() => {
    if (!activeSong || !effectiveTargetLang) return
    const source = settings.preferredSource
    void _fetchSong(activeSong.id, source, effectiveTargetLang, activePlaylistId ?? undefined).then(d => { setActiveSong(d) }).catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveTargetLang, activePlaylistId])

  useEffect(() => {
    restoreDoneRef.current = false
    setSettingsHydrated(false)
  }, [settingsOwnerSpotifyId])

  // Fetch playlist list once authenticated; songs are loaded for admin panel
  useEffect(() => {
    if (!isAuthenticated) return
    void loadPlaylists()
    if (isAdmin) void loadSongs()
  }, [isAuthenticated, isAdmin, loadPlaylists, loadSongs])

  useEffect(() => {
    // Reset target lang override when switching playlists so playlist default applies
    setOverrideTargetLang(null)
    if (!activePlaylistId) {
      setActivePlaylist(null)
      setPlaylistDetailLoading(false)
      return
    }
    const cached = playlistCacheRef.current.get(activePlaylistId)
    if (cached) {
      setActivePlaylist(cached)
      setPlaylistDetailLoading(false)
      return
    }
    setActivePlaylist(null)
    setPlaylistDetailLoading(true)
    api.getPlaylist(activePlaylistId)
      .then(pl => {
        playlistCacheRef.current.set(activePlaylistId, pl)
        setActivePlaylist(pl)
        setPlaylistDetailLoading(false)
      })
      .catch(() => { setActivePlaylist(null); setPlaylistDetailLoading(false) })
  }, [activePlaylistId])

  // Sync user to backend (non-fatal if backend is down)
  useEffect(() => {
    if (!credentialUser?.spotify_id) return
    api.getUserSettings()
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
      if (route.playlistId !== null && route.playlistId !== activePlaylistId) {
        setActivePlaylistId(route.playlistId)
      }
      if (activeSong?.id !== route.songId) {
        void handleSelectSong(route.songId, { updateRoute: false, playlistId: route.playlistId })
      }
      return
    }

    // subscriptions, privacy, terms - preserve current navigation state
    if (route.page === 'subscriptions' || route.page === 'privacy' || route.page === 'terms') {
      setSettingsOpen(false)
      setAdminOpen(false)
      return
    }

    // browse
    setSettingsOpen(false)
    setAdminOpen(false)
    setActiveSong(null)
    setActivePlaylistId(null)
  }, [isAuthenticated, isAdmin, activeSong?.id, handleSelectSong, navigateToPath, route])

  useEffect(() => {
    if (!isAuthenticated || route.page !== 'browse') return
    track('Browse Opened')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.page, isAuthenticated])

  useEffect(() => {
    if (!isAuthenticated || route.page !== 'admin' || !isAdmin) return
    track('Admin Panel Opened')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.page])

  const handleSelectPlaylist = useCallback((id: number | null) => {
    navigateToPath(id !== null ? playlistPath(id) : '/browse')
  }, [navigateToPath])

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
        youtube_url: entry.youtube_url,
        apple_music_url: entry.apple_music_url,
      }
    })
  }, [activePlaylist, songs])

  const activeSongIndex = useMemo(() => {
    if (!activeSong) return -1
    return displayedSongs.findIndex(s => s.id === activeSong.id)
  }, [activeSong, displayedSongs])

  const handlePrevSong = useCallback(() => {
    if (activeSongIndex <= 0) return
    track('Previous Song')
    void handleSelectSong(displayedSongs[activeSongIndex - 1].id)
  }, [activeSongIndex, displayedSongs, handleSelectSong])

  const handleNextSong = useCallback(() => {
    if (activeSongIndex < 0 || activeSongIndex >= displayedSongs.length - 1) return
    track('Next Song', { trigger: 'button' })
    void handleSelectSong(displayedSongs[activeSongIndex + 1].id)
  }, [activeSongIndex, displayedSongs, handleSelectSong])

  // Prefetch the next song in the playlist shortly after the current song loads.
  useEffect(() => {
    if (!activeSong || activeSongIndex < 0) return
    const next = displayedSongs[activeSongIndex + 1]
    if (!next) return
    const source = settings.preferredSource
    const key = _songCacheKey(next.id, source, effectiveTargetLang)
    if (_songCache.has(key) || _inFlight.has(key)) return
    const timer = window.setTimeout(() => {
      void _fetchSong(next.id, source, effectiveTargetLang, activePlaylistId ?? undefined).catch(() => {})
    }, 500)
    return () => window.clearTimeout(timer)
  }, [activeSong, activeSongIndex, displayedSongs, settings.preferredSource, effectiveTargetLang, activePlaylistId])

  // Legal pages are accessible without authentication
  if (currentPath.startsWith('/privacy')) return <PrivacyPolicyPage onBack={() => window.history.back()} />
  if (currentPath.startsWith('/terms')) return <TermsOfServicePage onBack={() => window.history.back()} />

  // Subscriptions page requires authentication
  if (currentPath.startsWith('/subscriptions') && isAuthenticated) {
    return (
      <PricingPage
        user={credentialUser}
        onClose={() => navigateToPath(activeSong ? songPath(activeSong.id) : activePlaylistId !== null ? playlistPath(activePlaylistId) : '/browse')}
        onUserUpdate={(user) => setCredentialUser(user)}
        isPage={true}
      />
    )
  }

  if (!isAuthenticated) {
    if (resetToken) {
      return (
        <ResetPasswordScreen
          token={resetToken}
          onDone={() => {
            setResetToken(null)
            window.history.replaceState({}, '', window.location.pathname)
          }}
        />
      )
    }
    if (showForgotPassword) {
      return (
        <ForgotPasswordScreen
          onBack={() => { setShowForgotPassword(false); setLoginError(null) }}
        />
      )
    }
    if (showSignUp) {
      return (
        <SignUpScreen
          onRegister={handleRegister}
          onGoogleLogin={handleGoogleLogin}
          onAppleLogin={handleAppleLogin}
          onShowSignIn={() => { setShowSignUp(false); setLoginError(null); navigateToPath('/login') }}
          error={loginError}
          busy={loginBusy}
        />
      )
    }
    return (
      <LoginScreen
        onEmailLogin={handleEmailLogin}
        onGoogleLogin={handleGoogleLogin}
        onAppleLogin={handleAppleLogin}
        onShowSignUp={() => { setShowSignUp(true); setLoginError(null); navigateToPath('/signup') }}
        onShowForgotPassword={() => { setShowForgotPassword(true); setLoginError(null) }}
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
        user={credentialUser}
        activeTab={route.page === 'settings' ? route.tab : 'preferences'}
        onTabChange={(t) => navigateToPath(settingsPath(t))}
        onShowPricing={() => navigateToPath('/subscriptions')}
        onUserUpdate={(user) => setCredentialUser(user)}
      />
    )
  }

  if (adminOpen && isAdmin) {
    const hasAdminSession = Object.keys(getAdminHeaders()).length > 0
    if (!hasAdminSession) {
      return (
        <div className="min-h-screen flex flex-col items-center justify-center gap-6" style={{ background: '#050608' }}>
          <p className="text-gray-300 text-base">Your admin session expired.</p>
          <p className="text-gray-500 text-sm">Please log out and log back in to access the admin panel.</p>
          <button
            onClick={handleLogout}
            className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium transition-colors"
          >
            Log out
          </button>
        </div>
      )
    }
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
    // Calculate position in playlist (0-indexed internally, used as 1-indexed in analytics)
    const positionInPlaylist = activePlaylist?.songs.findIndex(s => s.song_id === activeSong.id) ?? -1
    
    return (
      <>
        <PlayerView
          song={activeSong}
          positionInPlaylist={positionInPlaylist >= 0 ? positionInPlaylist : undefined}
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
          favoriteSongIds={favoriteSongIds}
          toggleFavorite={toggleFavorite}
          targetLang={effectiveTargetLang}
          onTargetLangChange={(lang) => {
            track('Target Language Changed', { from: effectiveTargetLang ?? '', to: lang })
            setOverrideTargetLang(lang)
          }}
          onGoToBrowse={() => navigateToPath('/browse')}
          playlistName={activePlaylist ? tc(activePlaylist.name) : null}
          onGoToPlaylist={activePlaylistId !== null ? () => navigateToPath(playlistPath(activePlaylistId)) : undefined}
          onShowPricing={() => navigateToPath('/subscriptions')}
          onBackToTrial={() => {
            // Navigate to first song in current playlist
            if (activePlaylist && activePlaylist.songs.length > 0) {
              const firstSongId = activePlaylist.songs[0].song_id
              navigateToPath(songPath(firstSongId), true)
            }
          }}
        />
        <HelpButton />
      </>
    )
  }

  return (
    <>
      <SongBrowser
        songs={displayedSongs}
        playlists={playlists}
        activePlaylistId={activePlaylistId}
        activePlaylist={activePlaylist}
        loading={playlistsLoading || playlistDetailLoading}
        error={songsError}
        onSelect={handleSelectSong}
        onPrefetch={handlePrefetchSong}
        onSelectPlaylist={handleSelectPlaylist}
        onOpenAdmin={() => navigateToPath(activePlaylistId !== null ? adminPath('playlists', activePlaylistId) : '/admin')}
        isAdmin={isAdmin}
        onOpenSettings={() => navigateToPath('/settings')}
        onOpenAccount={() => navigateToPath('/settings/account')}
        onLogout={handleLogout}
        user={appUser}
        openedSongIds={listenedSongIds}
        favoriteSongIds={favoriteSongIds}
        toggleFavorite={toggleFavorite}
        markAsNotListened={unmarkListened}
        wordsLookedUpCount={playlistWordCount}
        onBrowseTargetLang={handleBrowseTargetLang}
        navigateToPath={navigateToPath}
        track={track}
      />
      <HelpButton />
    </>
  )
}
