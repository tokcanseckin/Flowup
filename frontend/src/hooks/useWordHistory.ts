import { useCallback, useEffect, useRef, useState } from 'react'
import type { SongDetail, SongWord, WordLookupEntry } from '../api/client'
import { api } from '../api/client'

const STORAGE_KEY = 'flowup_word_history'

// WordHistoryEntry shape stored in localStorage — mirrors WordLookupEntry
// plus legacy fields (song_title, song_artist) kept for backwards compat.
export interface WordHistoryEntry extends WordLookupEntry {
  song_title: string
  song_artist: string | null
}

function loadEntries(): WordHistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    return JSON.parse(raw) as WordHistoryEntry[]
  } catch {
    return []
  }
}

function saveEntries(entries: WordHistoryEntry[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries))
  } catch { /* storage full or unavailable */ }
}

export function useWordHistory() {
  const [entries, setEntries] = useState<WordHistoryEntry[]>(loadEntries)
  // Prevent duplicate backend fetches on StrictMode double-mount
  const fetchedRef = useRef(false)

  // On mount: fetch from backend and merge (backend is authoritative).
  // Silently falls back to localStorage if user is not authenticated or offline.
  useEffect(() => {
    if (fetchedRef.current) return
    fetchedRef.current = true

    api.getWordLookups().then(serverEntries => {
      // Build a map from server data (lemma|language → entry)
      const map = new Map<string, WordHistoryEntry>()
      for (const e of serverEntries) {
        const key = `${e.lemma}|${e.language}`
        map.set(key, { ...e, song_title: '', song_artist: null })
      }
      // Layer in any local-only entries that aren't on the server yet
      // (e.g. recorded while offline)
      const local = loadEntries()
      for (const e of local) {
        const key = `${e.lemma}|${e.language}`
        if (!map.has(key)) map.set(key, e)
      }
      const merged = Array.from(map.values()).sort((a, b) => b.looked_up_at - a.looked_up_at)
      setEntries(merged)
      saveEntries(merged)
    }).catch(() => { /* unauthenticated or offline — keep localStorage */ })
  }, [])

  const recordLookup = useCallback((word: SongWord, song: SongDetail) => {
    const newEntry: WordHistoryEntry = {
      lemma: word.lemma,
      language: song.language.code,
      display_form: word.display_form,
      definition: word.dictionary_definition,
      grammar: word.grammar,
      song_id: song.id,
      song_title: song.title,
      song_artist: song.artist,
      looked_up_at: Math.floor(Date.now() / 1000),
    }

    // Update state + localStorage immediately (optimistic)
    setEntries(prev => {
      const filtered = prev.filter(
        e => !(e.lemma === newEntry.lemma && e.language === newEntry.language),
      )
      const updated = [newEntry, ...filtered]
      saveEntries(updated)
      return updated
    })

    // Persist to backend async (fire-and-forget)
    api.recordWordLookup({
      lemma: newEntry.lemma,
      language: newEntry.language,
      display_form: newEntry.display_form,
      definition: newEntry.definition,
      grammar: newEntry.grammar,
      song_id: newEntry.song_id,
    }).catch(() => { /* offline — will be re-synced on next app load */ })
  }, [])

  return { entries, recordLookup }
}
