import { useCallback, useMemo, useState } from 'react'
import type { SongDetail, SongWord } from '../api/client'

const STORAGE_KEY = 'flowup_word_history'

export interface WordHistoryEntry {
  lemma: string
  display_form: string
  definition: string | null
  grammar: string | null
  song_id: number
  song_title: string
  song_artist: string | null
  language: string
  looked_up_at: string
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

export function useWordHistory() {
  const [entries, setEntries] = useState<WordHistoryEntry[]>(loadEntries)

  const lookedUpLemmas = useMemo(() => new Set(entries.map(e => e.lemma)), [entries])

  const recordLookup = useCallback((word: SongWord, song: SongDetail) => {
    setEntries(prev => {
      const newEntry: WordHistoryEntry = {
        lemma: word.lemma,
        display_form: word.display_form,
        definition: word.dictionary_definition,
        grammar: word.grammar,
        song_id: song.id,
        song_title: song.title,
        song_artist: song.artist,
        language: song.language.code,
        looked_up_at: new Date().toISOString(),
      }
      // Keep most recent entry per lemma, newest at front
      const filtered = prev.filter(e => e.lemma !== word.lemma)
      const updated = [newEntry, ...filtered]
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(updated))
      } catch {
        // storage full or unavailable
      }
      return updated
    })
  }, [])

  return { entries, lookedUpLemmas, recordLookup }
}
