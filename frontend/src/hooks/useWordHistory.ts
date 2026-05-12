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

function saveEntries(entries: WordHistoryEntry[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries))
  } catch { /* storage full or unavailable */ }
}

export function useWordHistory(isAuthenticated = false) {
  // Start empty — only server (DB) data marks words as looked up.
  // Optimistic in-session lookups are still added to state immediately via recordLookup.
  const [entries, setEntries] = useState<WordHistoryEntry[]>([])
  // Prevent duplicate backend fetches on StrictMode double-mount
  const fetchedRef = useRef(false)

  // On mount: fetch from backend (authoritative source).
  useEffect(() => {
    if (!isAuthenticated) return
    if (fetchedRef.current) return
    fetchedRef.current = true

    api.getWordLookups().then(serverEntries => {
      const serverList = serverEntries.map(e => ({ ...e, song_title: '', song_artist: null }))
      serverList.sort((a, b) => b.looked_up_at - a.looked_up_at)
      setEntries(serverList)
      saveEntries(serverList)
    }).catch(() => { /* unauthenticated or offline — entries stay empty */ })
  }, [isAuthenticated])

  const recordLookup = useCallback((word: SongWord, song: SongDetail, targetLang: string) => {
    const newEntry: WordHistoryEntry = {
      lemma: word.lemma,
      language: song.language.code,
      target_lang: targetLang,
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
        e => !(e.lemma === newEntry.lemma && e.language === newEntry.language && e.target_lang === newEntry.target_lang),
      )
      const updated = [newEntry, ...filtered]
      saveEntries(updated)
      return updated
    })

    // Persist to backend async (fire-and-forget)
    api.recordWordLookup({
      lemma: newEntry.lemma,
      language: newEntry.language,
      target_lang: newEntry.target_lang,
      display_form: newEntry.display_form,
      definition: newEntry.definition,
      grammar: newEntry.grammar,
      song_id: newEntry.song_id,
    }).catch(() => { /* offline — will be re-synced on next app load */ })
  }, [])

  return { entries, recordLookup }
}
