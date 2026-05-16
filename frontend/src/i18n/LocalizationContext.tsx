import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api, LocalizationItem } from '../api/client'

export type UILanguage = 'en' | 'tr' | 'ru' | 'es' | 'pt' | 'de'

const LANG_STORAGE_KEY = 'flowup.uiLanguage'
const LOC_CACHE_KEY = 'flowup.localizations'

function detectBrowserLang(): UILanguage {
  const primary = (navigator.language ?? '').split('-')[0].toLowerCase()
  if (primary === 'tr') return 'tr'
  if (primary === 'ru') return 'ru'
  if (primary === 'es') return 'es'
  if (primary === 'pt') return 'pt'
  if (primary === 'de') return 'de'
  return 'en'
}

function readStoredLang(): UILanguage {
  try {
    const v = localStorage.getItem(LANG_STORAGE_KEY)
    if (v === 'en' || v === 'tr' || v === 'ru' || v === 'es' || v === 'pt' || v === 'de') return v
  } catch {}
  return detectBrowserLang()
}

function readCachedLocalizations(): Record<string, LocalizationItem> {
  try {
    const raw = localStorage.getItem(LOC_CACHE_KEY)
    if (raw) return JSON.parse(raw) as Record<string, LocalizationItem>
  } catch {}
  return {}
}

interface LocalizationContextValue {
  language: UILanguage
  setLanguage: (lang: UILanguage) => void
  t: (key: string) => string
  /** Resolves [[loc.key]] references inside DB content strings. */
  tc: (text: string) => string
  localizations: Record<string, LocalizationItem>
  isLoading: boolean
}

const LocalizationContext = createContext<LocalizationContextValue>({
  language: 'en',
  setLanguage: () => {},
  t: (key) => key,
  tc: (text) => text,
  localizations: {},
  isLoading: true,
})

interface Props {
  children: React.ReactNode
}

export function LocalizationProvider({ children }: Props) {
  const [language, setLanguageState] = useState<UILanguage>(readStoredLang)
  const cached = readCachedLocalizations()
  const [localizations, setLocalizations] = useState<Record<string, LocalizationItem>>(cached)
  const [isLoading, setIsLoading] = useState(Object.keys(cached).length === 0)
  const fetchedRef = useRef(false)

  useEffect(() => {
    if (fetchedRef.current) return
    fetchedRef.current = true
    api.getLocalizations()
      .then(items => {
        const map: Record<string, LocalizationItem> = {}
        for (const item of items) map[item.key] = item
        setLocalizations(map)
        try { localStorage.setItem(LOC_CACHE_KEY, JSON.stringify(map)) } catch {}
      })
      .catch(err => console.warn('[i18n] Failed to load localizations:', err))
      .finally(() => setIsLoading(false))
  }, [])

  const setLanguage = useCallback((lang: UILanguage) => {
    try { localStorage.setItem(LANG_STORAGE_KEY, lang) } catch {}
    setLanguageState(lang)
  }, [])

  const t = useCallback(
    (key: string): string => {
      const entry = localizations[key]
      if (!entry) return key
      return entry[language] || entry.en || key
    },
    [localizations, language],
  )

  const tc = useCallback(
    (text: string): string => text.replace(/\[\[([^\]]+)\]\]/g, (_, key: string) => {
      const entry = localizations[key]
      if (!entry) return key
      return entry[language] || entry.en || key
    }),
    [localizations, language],
  )

  return (
    <LocalizationContext.Provider value={{ language, setLanguage, t, tc, localizations, isLoading }}>
      {children}
    </LocalizationContext.Provider>
  )
}

export function useLocalization() {
  return useContext(LocalizationContext)
}

/** Shorthand hook: just returns the `t()` translation function. */
export function useT() {
  return useContext(LocalizationContext).t
}

/**
 * Returns `tc()` — resolves [[loc.key]] placeholders inside DB content strings
 * (e.g. playlist name, description) into the current UI language.
 */
export function useContentT() {
  return useContext(LocalizationContext).tc
}

/** Storage key for external consumers (e.g. AppSettings sync). */
export { LANG_STORAGE_KEY }
