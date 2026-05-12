import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api, LocalizationItem } from '../api/client'

export type UILanguage = 'en' | 'tr' | 'ru'

const LANG_STORAGE_KEY = 'flowup.uiLanguage'

function readStoredLang(): UILanguage {
  try {
    const v = localStorage.getItem(LANG_STORAGE_KEY)
    if (v === 'en' || v === 'tr' || v === 'ru') return v
  } catch {}
  return 'en'
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
  const [localizations, setLocalizations] = useState<Record<string, LocalizationItem>>({})
  const [isLoading, setIsLoading] = useState(true)
  const fetchedRef = useRef(false)

  useEffect(() => {
    if (fetchedRef.current) return
    fetchedRef.current = true
    api.getLocalizations()
      .then(items => {
        const map: Record<string, LocalizationItem> = {}
        for (const item of items) map[item.key] = item
        setLocalizations(map)
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
