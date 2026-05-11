import { useState, useCallback, useEffect } from 'react'
import { api, getUserAuthHeaders } from '../api/client'

const STORAGE_KEY = 'flowup.openedSongs.v1'

function loadIds(): Set<number> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return new Set()
    const arr = JSON.parse(raw)
    if (!Array.isArray(arr)) return new Set()
    return new Set(arr.filter((v): v is number => typeof v === 'number'))
  } catch {
    return new Set()
  }
}

function saveIds(ids: Set<number>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...ids]))
  } catch {
    // ignore storage errors
  }
}

/**
 * Manages listened song IDs.
 * - Persists to localStorage for immediate feedback and offline support.
 * - Syncs to/from the backend when the user is authenticated.
 */
export function useListened(isAuthenticated: boolean) {
  const [listenedSongIds, setListenedSongIds] = useState<Set<number>>(() => loadIds())

  // On mount (or when auth state changes), load listened songs from the backend.
  useEffect(() => {
    if (!isAuthenticated) return
    const headers = getUserAuthHeaders()
    if (!Object.keys(headers).length) return
    api.getListened(headers)
      .then(({ song_ids }) => {
        const serverSet = new Set(song_ids)
        // Merge localStorage (local-only songs not yet synced) into server state
        const local = loadIds()
        const merged = new Set([...serverSet, ...local])
        setListenedSongIds(merged)
        saveIds(merged)
        // Push any local-only IDs to the backend
        const unsynced = [...local].filter(id => !serverSet.has(id))
        if (unsynced.length) {
          const h = getUserAuthHeaders()
          unsynced.forEach(id => api.addListened(id, h).catch(() => {}))
        }
      })
      .catch(() => {
        // fall back to localStorage
      })
  }, [isAuthenticated])

  const markListened = useCallback((songId: number) => {
    setListenedSongIds(prev => {
      if (prev.has(songId)) return prev
      const next = new Set(prev)
      next.add(songId)
      saveIds(next)
      const headers = getUserAuthHeaders()
      if (Object.keys(headers).length) {
        api.addListened(songId, headers).catch(() => {})
      }
      return next
    })
  }, [])

  const unmarkListened = useCallback((songId: number) => {
    setListenedSongIds(prev => {
      if (!prev.has(songId)) return prev
      const next = new Set(prev)
      next.delete(songId)
      saveIds(next)
      const headers = getUserAuthHeaders()
      if (Object.keys(headers).length) {
        api.removeListened(songId, headers).catch(() => {})
      }
      return next
    })
  }, [])

  return { listenedSongIds, markListened, unmarkListened }
}
