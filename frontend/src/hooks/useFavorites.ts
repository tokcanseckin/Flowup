import { useState, useCallback, useEffect } from 'react'
import { api, getUserAuthHeaders } from '../api/client'

const STORAGE_KEY = 'flowup_favorite_songs'

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
 * Manages favorite song IDs.
 * - Persists to localStorage for immediate feedback and offline support.
 * - Syncs to the backend when the user is authenticated.
 * - `isAuthenticated` should be true when the user is logged in.
 */
export function useFavorites(isAuthenticated: boolean) {
  const [favoriteSongIds, setFavoriteSongIds] = useState<Set<number>>(() => loadIds())
  const [synced, setSynced] = useState(false)

  // On mount (or when auth state changes), load favorites from the backend.
  useEffect(() => {
    if (!isAuthenticated) {
      setSynced(false)
      return
    }
    const headers = getUserAuthHeaders()
    if (!Object.keys(headers).length) {
      setSynced(false)
      return
    }
    api.getFavorites(headers)
      .then(({ song_ids }) => {
        const serverSet = new Set(song_ids)
        setFavoriteSongIds(serverSet)
        saveIds(serverSet)
        setSynced(true)
      })
      .catch(() => {
        // fall back to localStorage — keep whatever was there
        setSynced(false)
      })
  }, [isAuthenticated])

  const toggleFavorite = useCallback((songId: number) => {
    setFavoriteSongIds(prev => {
      const next = new Set(prev)
      const isFav = next.has(songId)
      if (isFav) {
        next.delete(songId)
      } else {
        next.add(songId)
      }
      saveIds(next)

      // Sync to backend if authenticated
      const headers = getUserAuthHeaders()
      if (Object.keys(headers).length) {
        if (isFav) {
          api.removeFavorite(songId, headers).catch(() => { /* silent */ })
        } else {
          api.addFavorite(songId, headers).catch(() => { /* silent */ })
        }
      }

      return next
    })
  }, [])

  return { favoriteSongIds, toggleFavorite, synced }
}
