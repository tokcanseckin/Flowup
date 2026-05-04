import { useCallback, useEffect, useMemo, useState } from 'react'

import { AdminSongDetail, AdminUser, PlaylistDetail, PlaylistSummary, SongSummary, api } from '../api/client'

interface Props {
  songs: SongSummary[]
  playlists: PlaylistSummary[]
  onBack: () => void
  onLogout: () => void
  onRefreshSongs: () => Promise<void>
  onRefreshPlaylists: () => Promise<void>
  user: { display_name: string | null } | null
}

type TabKey = 'songs' | 'playlists' | 'users'

type PlaylistDraft = {
  name: string
  description: string
  difficulty_level: string
  language_code: string
}

type SongDraft = {
  spotify_uri: string
  title: string
  artist: string
  youtube_url: string
  apple_music_url: string
  playlist_ids: number[]
}

type UserDraft = {
  display_name: string
  email: string
  is_admin: boolean
  password: string
}

function emptyPlaylistDraft(): PlaylistDraft {
  return {
    name: '',
    description: '',
    difficulty_level: '',
    language_code: '',
  }
}

function tabButtonClass(active: boolean): string {
  return active
    ? 'border-indigo-500 bg-indigo-950/40 text-white'
    : 'border-gray-800 bg-gray-950/30 text-gray-400 hover:border-gray-700 hover:text-gray-200'
}

export default function AdminPanel({
  songs,
  playlists,
  onBack,
  onLogout,
  onRefreshSongs,
  onRefreshPlaylists,
  user,
}: Props) {
  const [tab, setTab] = useState<TabKey>('songs')
  const [searchQuery, setSearchQuery] = useState('')

  const [selectedSongId, setSelectedSongId] = useState<number | null>(songs[0]?.id ?? null)
  const [selectedPlaylistId, setSelectedPlaylistId] = useState<number | null>(playlists[0]?.id ?? null)
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null)

  const [songDraft, setSongDraft] = useState<SongDraft | null>(null)
  const [lyricsDraft, setLyricsDraft] = useState<AdminSongDetail['lines']>([])
  const [songLoading, setSongLoading] = useState(false)
  const [songSaving, setSongSaving] = useState(false)
  const [lyricsSaving, setLyricsSaving] = useState(false)
  const [songError, setSongError] = useState<string | null>(null)

  const [playlistDetail, setPlaylistDetail] = useState<PlaylistDetail | null>(null)
  const [playlistDraft, setPlaylistDraft] = useState<PlaylistDraft>(emptyPlaylistDraft())
  const [newPlaylistDraft, setNewPlaylistDraft] = useState<PlaylistDraft>(emptyPlaylistDraft())
  const [playlistSongIds, setPlaylistSongIds] = useState<number[]>([])
  const [playlistLoading, setPlaylistLoading] = useState(false)
  const [playlistSaving, setPlaylistSaving] = useState(false)
  const [playlistError, setPlaylistError] = useState<string | null>(null)
  const [playlistSongQuery, setPlaylistSongQuery] = useState('')

  const [users, setUsers] = useState<AdminUser[]>([])
  const [selectedUser, setSelectedUser] = useState<AdminUser | null>(null)
  const [userDraft, setUserDraft] = useState<UserDraft | null>(null)
  const [usersLoading, setUsersLoading] = useState(false)
  const [userSaving, setUserSaving] = useState(false)
  const [userError, setUserError] = useState<string | null>(null)

  const filteredSongs = useMemo(() => {
    const needle = searchQuery.trim().toLowerCase()
    if (tab !== 'songs' || !needle) return songs
    return songs.filter(song => `${song.title} ${song.artist ?? ''} ${song.spotify_uri}`.toLowerCase().includes(needle))
  }, [searchQuery, songs, tab])

  const filteredPlaylists = useMemo(() => {
    const needle = searchQuery.trim().toLowerCase()
    if (tab !== 'playlists' || !needle) return playlists
    return playlists.filter(playlist => `${playlist.name} ${playlist.description ?? ''} ${playlist.language_code ?? ''}`.toLowerCase().includes(needle))
  }, [playlists, searchQuery, tab])

  const filteredUsers = useMemo(() => {
    const needle = searchQuery.trim().toLowerCase()
    if (tab !== 'users' || !needle) return users
    return users.filter(item => `${item.display_name ?? ''} ${item.email ?? ''} ${item.spotify_id}`.toLowerCase().includes(needle))
  }, [searchQuery, tab, users])

  const filteredSongsForPlaylist = useMemo(() => {
    const needle = playlistSongQuery.trim().toLowerCase()
    if (!needle) return songs
    return songs.filter(song => `${song.title} ${song.artist ?? ''}`.toLowerCase().includes(needle))
  }, [playlistSongQuery, songs])

  useEffect(() => {
    if (!selectedSongId && songs[0]) setSelectedSongId(songs[0].id)
  }, [selectedSongId, songs])

  useEffect(() => {
    if (!selectedPlaylistId && playlists[0]) setSelectedPlaylistId(playlists[0].id)
  }, [playlists, selectedPlaylistId])

  useEffect(() => {
    if (tab !== 'users') return
    let cancelled = false
    setUsersLoading(true)
    setUserError(null)
    void api.listAdminUsers()
      .then((loaded) => {
        if (cancelled) return
        setUsers(loaded)
        setSelectedUserId(prev => prev ?? loaded[0]?.id ?? null)
      })
      .catch((error: unknown) => {
        if (cancelled) return
        setUserError(error instanceof Error ? error.message : 'Failed to load users')
      })
      .finally(() => {
        if (!cancelled) setUsersLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [tab])

  useEffect(() => {
    if (!selectedSongId) {
      return
    }

    let cancelled = false
    setSongLoading(true)
    setSongError(null)
    void api.getAdminSong(selectedSongId)
      .then((detail) => {
        if (cancelled) return
        setSongDraft({
          spotify_uri: detail.spotify_uri,
          title: detail.title,
          artist: detail.artist ?? '',
          youtube_url: detail.youtube_url ?? '',
          apple_music_url: detail.apple_music_url ?? '',
          playlist_ids: [...detail.playlist_ids],
        })
        setLyricsDraft(detail.lines.map(line => ({ ...line })))
      })
      .catch((error: unknown) => {
        if (cancelled) return
        setSongError(error instanceof Error ? error.message : 'Failed to load song')
      })
      .finally(() => {
        if (!cancelled) setSongLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedSongId])

  useEffect(() => {
    if (!selectedPlaylistId) {
      setPlaylistDetail(null)
      setPlaylistDraft(emptyPlaylistDraft())
      setPlaylistSongIds([])
      return
    }

    let cancelled = false
    setPlaylistLoading(true)
    setPlaylistError(null)
    void api.getPlaylist(selectedPlaylistId)
      .then((detail) => {
        if (cancelled) return
        setPlaylistDetail(detail)
        setPlaylistDraft({
          name: detail.name,
          description: detail.description ?? '',
          difficulty_level: detail.difficulty_level ?? '',
          language_code: detail.language_code ?? '',
        })
        setPlaylistSongIds(detail.songs.map(song => song.song_id))
      })
      .catch((error: unknown) => {
        if (cancelled) return
        setPlaylistError(error instanceof Error ? error.message : 'Failed to load playlist')
      })
      .finally(() => {
        if (!cancelled) setPlaylistLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [selectedPlaylistId])

  useEffect(() => {
    if (!selectedUserId) {
      setSelectedUser(null)
      setUserDraft(null)
      return
    }

    let cancelled = false
    setUsersLoading(true)
    setUserError(null)
    void api.getAdminUser(selectedUserId)
      .then((detail) => {
        if (cancelled) return
        setSelectedUser(detail)
        setUserDraft({
          display_name: detail.display_name ?? '',
          email: detail.email ?? '',
          is_admin: detail.is_admin,
          password: '',
        })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        setUserError(error instanceof Error ? error.message : 'Failed to load user')
      })
      .finally(() => {
        if (!cancelled) setUsersLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [selectedUserId])

  const handleSaveSong = useCallback(async () => {
    if (!selectedSongId || !songDraft) return
    setSongSaving(true)
    setSongError(null)
    try {
      const updated = await api.updateAdminSong(selectedSongId, {
        spotify_uri: songDraft.spotify_uri.trim(),
        title: songDraft.title.trim(),
        artist: songDraft.artist.trim() || null,
        youtube_url: songDraft.youtube_url.trim() || null,
        apple_music_url: songDraft.apple_music_url.trim() || null,
        playlist_ids: songDraft.playlist_ids,
      })
      setLyricsDraft(updated.lines.map(line => ({ ...line })))
      await Promise.all([onRefreshSongs(), onRefreshPlaylists()])
    } catch (error) {
      setSongError(error instanceof Error ? error.message : 'Failed to save song')
    } finally {
      setSongSaving(false)
    }
  }, [onRefreshPlaylists, onRefreshSongs, selectedSongId, songDraft])

  const handleSaveLyrics = useCallback(async () => {
    if (!selectedSongId) return
    setLyricsSaving(true)
    setSongError(null)
    try {
      const updated = await api.updateAdminLyrics(selectedSongId, {
        lines: lyricsDraft.map((line, index) => ({
          id: line.id,
          position: index,
          start_time_ms: line.start_time_ms,
          end_time_ms: line.end_time_ms,
          original_line: line.original_line,
          phonetic_line: line.phonetic_line,
          translation: line.translation,
        })),
      })
      setLyricsDraft(updated.lines.map(line => ({ ...line })))
    } catch (error) {
      setSongError(error instanceof Error ? error.message : 'Failed to save lyrics')
    } finally {
      setLyricsSaving(false)
    }
  }, [lyricsDraft, selectedSongId])

  const handleSavePlaylist = useCallback(async () => {
    if (!selectedPlaylistId || !playlistDetail) return
    setPlaylistSaving(true)
    setPlaylistError(null)
    try {
      await api.updatePlaylist(selectedPlaylistId, {
        name: playlistDraft.name.trim(),
        description: playlistDraft.description.trim() || null,
        difficulty_level: playlistDraft.difficulty_level.trim() || null,
        language_code: playlistDraft.language_code.trim() || null,
      })

      const existingSongIds = new Set(playlistDetail.songs.map(song => song.song_id))
      const nextSongIds = new Set(playlistSongIds)

      for (const songId of playlistSongIds) {
        if (!existingSongIds.has(songId)) {
          await api.addSongToPlaylist(selectedPlaylistId, { song_id: songId })
        }
      }
      for (const songId of existingSongIds) {
        if (!nextSongIds.has(songId)) {
          await api.removeSongFromPlaylist(selectedPlaylistId, songId)
        }
      }

      await onRefreshPlaylists()
      const refreshed = await api.getPlaylist(selectedPlaylistId)
      setPlaylistDetail(refreshed)
      setPlaylistSongIds(refreshed.songs.map(song => song.song_id))
    } catch (error) {
      setPlaylistError(error instanceof Error ? error.message : 'Failed to save playlist')
    } finally {
      setPlaylistSaving(false)
    }
  }, [onRefreshPlaylists, playlistDetail, playlistDraft, playlistSongIds, selectedPlaylistId])

  const handleCreatePlaylist = useCallback(async () => {
    if (!newPlaylistDraft.name.trim()) {
      setPlaylistError('Playlist name is required')
      return
    }
    setPlaylistSaving(true)
    setPlaylistError(null)
    try {
      const created = await api.createPlaylist({
        name: newPlaylistDraft.name.trim(),
        description: newPlaylistDraft.description.trim() || null,
        difficulty_level: newPlaylistDraft.difficulty_level.trim() || null,
        language_code: newPlaylistDraft.language_code.trim() || null,
      })
      await onRefreshPlaylists()
      setSelectedPlaylistId(created.id)
      setNewPlaylistDraft(emptyPlaylistDraft())
      setTab('playlists')
    } catch (error) {
      setPlaylistError(error instanceof Error ? error.message : 'Failed to create playlist')
    } finally {
      setPlaylistSaving(false)
    }
  }, [newPlaylistDraft, onRefreshPlaylists])

  const handleDeletePlaylist = useCallback(async () => {
    if (!selectedPlaylistId) return
    if (!window.confirm('Delete this playlist? Songs will remain in the library.')) return
    setPlaylistSaving(true)
    setPlaylistError(null)
    try {
      await api.deletePlaylist(selectedPlaylistId)
      await onRefreshPlaylists()
      setSelectedPlaylistId(playlists.find(item => item.id !== selectedPlaylistId)?.id ?? null)
    } catch (error) {
      setPlaylistError(error instanceof Error ? error.message : 'Failed to delete playlist')
    } finally {
      setPlaylistSaving(false)
    }
  }, [onRefreshPlaylists, playlists, selectedPlaylistId])

  const handleSaveUser = useCallback(async () => {
    if (!selectedUserId || !userDraft) return
    setUserSaving(true)
    setUserError(null)
    try {
      const updated = await api.updateAdminUser(selectedUserId, {
        display_name: userDraft.display_name.trim() || null,
        email: userDraft.email.trim().toLowerCase() || null,
        is_admin: userDraft.is_admin,
        password: userDraft.password.trim() || null,
      })
      setSelectedUser(updated)
      setUserDraft({
        display_name: updated.display_name ?? '',
        email: updated.email ?? '',
        is_admin: updated.is_admin,
        password: '',
      })
      setUsers(prev => prev.map(item => item.id === updated.id ? updated : item))
    } catch (error) {
      setUserError(error instanceof Error ? error.message : 'Failed to save user')
    } finally {
      setUserSaving(false)
    }
  }, [selectedUserId, userDraft])

  const toggleSongPlaylistMembership = useCallback((playlistId: number) => {
    setSongDraft(prev => prev ? {
      ...prev,
      playlist_ids: prev.playlist_ids.includes(playlistId)
        ? prev.playlist_ids.filter(id => id !== playlistId)
        : [...prev.playlist_ids, playlistId].sort((a, b) => a - b),
    } : prev)
  }, [])

  const togglePlaylistSongMembership = useCallback((songId: number) => {
    setPlaylistSongIds(prev => prev.includes(songId)
      ? prev.filter(id => id !== songId)
      : [...prev, songId].sort((a, b) => a - b))
  }, [])

  const listPlaceholder = tab === 'songs'
    ? 'Search songs'
    : tab === 'playlists'
      ? 'Search playlists'
      : 'Search users'

  return (
    <div className="min-h-screen p-6" style={{ background: '#0d0d14' }}>
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <button onClick={onBack} className="text-gray-500 hover:text-gray-300 transition-colors" aria-label="Back">
              <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
                <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/>
              </svg>
            </button>
            <div>
              <h1 className="text-2xl font-bold text-white">Content Admin</h1>
              <p className="text-sm text-gray-500">Separate edit pages for songs, playlists, and users.</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {user?.display_name && <span className="text-xs text-gray-500">{user.display_name}</span>}
            <button onClick={onLogout} className="text-xs text-gray-600 hover:text-gray-400 transition-colors">Sign out</button>
          </div>
        </div>

        <div className="flex gap-2">
          <button type="button" onClick={() => { setTab('songs'); setSearchQuery('') }} className={`rounded-xl border px-4 py-2 text-sm font-semibold transition-colors ${tabButtonClass(tab === 'songs')}`}>Songs</button>
          <button type="button" onClick={() => { setTab('playlists'); setSearchQuery('') }} className={`rounded-xl border px-4 py-2 text-sm font-semibold transition-colors ${tabButtonClass(tab === 'playlists')}`}>Playlists</button>
          <button type="button" onClick={() => { setTab('users'); setSearchQuery('') }} className={`rounded-xl border px-4 py-2 text-sm font-semibold transition-colors ${tabButtonClass(tab === 'users')}`}>Users</button>
        </div>

        <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
          <section className="rounded-3xl border border-gray-800/80 p-4 space-y-4" style={{ background: '#12121f' }}>
            <div>
              <p className="text-white font-semibold">{tab[0].toUpperCase() + tab.slice(1)}</p>
              <input
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                placeholder={listPlaceholder}
                className="mt-3 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
              />
            </div>

            {tab === 'songs' && (
              <div className="max-h-[72vh] overflow-y-auto space-y-2 pr-1">
                {filteredSongs.map(song => (
                  <button
                    key={song.id}
                    type="button"
                    onClick={() => setSelectedSongId(song.id)}
                    className={`w-full rounded-2xl border px-3 py-3 text-left transition-colors ${selectedSongId === song.id ? 'border-indigo-500 bg-indigo-950/40' : 'border-gray-800 bg-gray-950/30 hover:border-gray-700'}`}
                  >
                    <p className="text-sm font-medium text-white truncate">{song.title}</p>
                    <p className="text-xs text-gray-500 truncate">{song.artist ?? 'Unknown artist'}</p>
                  </button>
                ))}
              </div>
            )}

            {tab === 'playlists' && (
              <div className="max-h-[72vh] overflow-y-auto space-y-2 pr-1">
                {filteredPlaylists.map(playlist => (
                  <button
                    key={playlist.id}
                    type="button"
                    onClick={() => setSelectedPlaylistId(playlist.id)}
                    className={`w-full rounded-2xl border px-3 py-3 text-left transition-colors ${selectedPlaylistId === playlist.id ? 'border-indigo-500 bg-indigo-950/40' : 'border-gray-800 bg-gray-950/30 hover:border-gray-700'}`}
                  >
                    <p className="text-sm font-medium text-white truncate">{playlist.name}</p>
                    <p className="text-xs text-gray-500 truncate">{playlist.song_count} songs</p>
                  </button>
                ))}
              </div>
            )}

            {tab === 'users' && (
              <div className="max-h-[72vh] overflow-y-auto space-y-2 pr-1">
                {usersLoading && !users.length && <div className="py-4 text-sm text-gray-500">Loading users...</div>}
                {filteredUsers.map(item => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setSelectedUserId(item.id)}
                    className={`w-full rounded-2xl border px-3 py-3 text-left transition-colors ${selectedUserId === item.id ? 'border-indigo-500 bg-indigo-950/40' : 'border-gray-800 bg-gray-950/30 hover:border-gray-700'}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-medium text-white truncate">{item.display_name ?? item.email ?? item.spotify_id}</p>
                      {item.is_admin && <span className="rounded-md border border-amber-700/50 bg-amber-950/30 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">ADMIN</span>}
                    </div>
                    <p className="text-xs text-gray-500 truncate">{item.email ?? item.spotify_id}</p>
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="space-y-4">
            {tab === 'songs' && (
              <>
                <div className="rounded-3xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                  <div className="flex items-center justify-between gap-4 mb-4">
                    <div>
                      <p className="text-white font-semibold">Song Details</p>
                      <p className="text-xs text-gray-500">Metadata, source URLs, and playlist membership.</p>
                    </div>
                    <button type="button" onClick={() => void handleSaveSong()} disabled={!selectedSongId || !songDraft || songSaving} className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:bg-gray-800 disabled:text-gray-500">{songSaving ? 'Saving...' : 'Save Song'}</button>
                  </div>
                  {songError && <div className="mb-4 rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">{songError}</div>}
                  {songLoading || !songDraft ? (
                    <div className="py-10 text-sm text-gray-500">Loading song editor...</div>
                  ) : (
                    <div className="space-y-4">
                      <div className="grid gap-4 md:grid-cols-2">
                        <label className="block text-xs text-gray-500">Spotify URI<input value={songDraft.spotify_uri} onChange={e => setSongDraft(prev => prev ? { ...prev, spotify_uri: e.target.value } : prev)} className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" /></label>
                        <label className="block text-xs text-gray-500">Artist<input value={songDraft.artist} onChange={e => setSongDraft(prev => prev ? { ...prev, artist: e.target.value } : prev)} className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" /></label>
                      </div>
                      <label className="block text-xs text-gray-500">Title<input value={songDraft.title} onChange={e => setSongDraft(prev => prev ? { ...prev, title: e.target.value } : prev)} className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" /></label>
                      <div className="grid gap-4 md:grid-cols-2">
                        <label className="block text-xs text-gray-500">YouTube URL<input value={songDraft.youtube_url} onChange={e => setSongDraft(prev => prev ? { ...prev, youtube_url: e.target.value } : prev)} className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" /></label>
                        <label className="block text-xs text-gray-500">Apple Music URL<input value={songDraft.apple_music_url} onChange={e => setSongDraft(prev => prev ? { ...prev, apple_music_url: e.target.value } : prev)} className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" /></label>
                      </div>
                      <div>
                        <p className="text-xs text-gray-500 mb-2">Playlist membership</p>
                        <div className="grid gap-2 md:grid-cols-2">
                          {playlists.map(playlist => (
                            <label key={playlist.id} className="flex items-center gap-3 rounded-xl border border-gray-800 bg-gray-950/30 px-3 py-2 text-sm text-gray-200">
                              <input type="checkbox" checked={songDraft.playlist_ids.includes(playlist.id)} onChange={() => toggleSongPlaylistMembership(playlist.id)} className="rounded border-gray-600 bg-gray-900 text-indigo-500 focus:ring-indigo-500" />
                              <span>{playlist.name}</span>
                            </label>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                <div className="rounded-3xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                  <div className="flex items-center justify-between gap-4 mb-4">
                    <div>
                      <p className="text-white font-semibold">Lyrics + Sync</p>
                      <p className="text-xs text-gray-500">Edit line text and timestamps for the selected song.</p>
                    </div>
                    <button type="button" onClick={() => void handleSaveLyrics()} disabled={!selectedSongId || lyricsSaving || !lyricsDraft.length} className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:bg-gray-800 disabled:text-gray-500">{lyricsSaving ? 'Saving...' : 'Save Lyrics'}</button>
                  </div>
                  <div className="max-h-[58vh] overflow-y-auto space-y-3 pr-1">
                    {lyricsDraft.map((line, index) => (
                      <div key={line.id} className="rounded-2xl border border-gray-800 bg-gray-950/30 p-4 space-y-3">
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-xs font-mono text-indigo-400">Line {index + 1}</span>
                          <div className="grid grid-cols-2 gap-2">
                            <input type="number" value={line.start_time_ms} onChange={e => setLyricsDraft(prev => prev.map((item, itemIndex) => itemIndex === index ? { ...item, start_time_ms: Number(e.target.value) } : item))} className="w-28 rounded-lg border border-gray-700 bg-gray-900/70 px-2 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500" />
                            <input type="number" value={line.end_time_ms} onChange={e => setLyricsDraft(prev => prev.map((item, itemIndex) => itemIndex === index ? { ...item, end_time_ms: Number(e.target.value) } : item))} className="w-28 rounded-lg border border-gray-700 bg-gray-900/70 px-2 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500" />
                          </div>
                        </div>
                        <textarea value={line.original_line} onChange={e => setLyricsDraft(prev => prev.map((item, itemIndex) => itemIndex === index ? { ...item, original_line: e.target.value } : item))} rows={2} className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                        <textarea value={line.phonetic_line ?? ''} onChange={e => setLyricsDraft(prev => prev.map((item, itemIndex) => itemIndex === index ? { ...item, phonetic_line: e.target.value || null } : item))} rows={2} placeholder="Phonetic line" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500" />
                        <textarea value={line.translation} onChange={e => setLyricsDraft(prev => prev.map((item, itemIndex) => itemIndex === index ? { ...item, translation: e.target.value } : item))} rows={2} placeholder="Translation" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500" />
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}

            {tab === 'playlists' && (
              <>
                <div className="rounded-3xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                  <div className="flex items-center justify-between gap-4 mb-4">
                    <div>
                      <p className="text-white font-semibold">Playlist Details</p>
                      <p className="text-xs text-gray-500">Edit playlist metadata and song membership.</p>
                    </div>
                    <div className="flex gap-2">
                      <button type="button" onClick={() => void handleSavePlaylist()} disabled={!selectedPlaylistId || playlistSaving} className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:bg-gray-800 disabled:text-gray-500">{playlistSaving ? 'Saving...' : 'Save Playlist'}</button>
                      <button type="button" onClick={() => void handleDeletePlaylist()} disabled={!selectedPlaylistId || playlistSaving} className="rounded-xl border border-red-900/60 px-4 py-2 text-sm font-semibold text-red-300 hover:bg-red-950/30 disabled:border-gray-800 disabled:text-gray-600">Delete</button>
                    </div>
                  </div>
                  {playlistError && <div className="mb-4 rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">{playlistError}</div>}
                  {playlistLoading ? (
                    <div className="py-10 text-sm text-gray-500">Loading playlist editor...</div>
                  ) : (
                    <div className="space-y-4">
                      <input value={playlistDraft.name} onChange={e => setPlaylistDraft(prev => ({ ...prev, name: e.target.value }))} placeholder="Playlist name" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                      <textarea value={playlistDraft.description} onChange={e => setPlaylistDraft(prev => ({ ...prev, description: e.target.value }))} rows={3} placeholder="Description" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                      <div className="grid gap-3 md:grid-cols-2">
                        <input value={playlistDraft.difficulty_level} onChange={e => setPlaylistDraft(prev => ({ ...prev, difficulty_level: e.target.value }))} placeholder="Difficulty level" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                        <input value={playlistDraft.language_code} onChange={e => setPlaylistDraft(prev => ({ ...prev, language_code: e.target.value }))} placeholder="Language code" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                      </div>
                      <div>
                        <div className="flex items-center justify-between gap-3 mb-2">
                          <p className="text-xs text-gray-500">Songs in playlist</p>
                          <input value={playlistSongQuery} onChange={e => setPlaylistSongQuery(e.target.value)} placeholder="Filter songs" className="w-56 rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-xs text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500" />
                        </div>
                        <div className="max-h-[52vh] overflow-y-auto space-y-2 pr-1">
                          {filteredSongsForPlaylist.map(song => (
                            <label key={song.id} className="flex items-center gap-3 rounded-xl border border-gray-800 bg-gray-950/30 px-3 py-2 text-sm text-gray-200">
                              <input type="checkbox" checked={playlistSongIds.includes(song.id)} onChange={() => togglePlaylistSongMembership(song.id)} className="rounded border-gray-600 bg-gray-900 text-indigo-500 focus:ring-indigo-500" />
                              <span className="min-w-0 flex-1 truncate">{song.title}</span>
                              <span className="text-xs text-gray-500 truncate">{song.artist ?? 'Unknown artist'}</span>
                            </label>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                <div className="rounded-3xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                  <p className="text-white font-semibold mb-3">Create Playlist</p>
                  <div className="space-y-3">
                    <input value={newPlaylistDraft.name} onChange={e => setNewPlaylistDraft(prev => ({ ...prev, name: e.target.value }))} placeholder="Playlist name" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                    <textarea value={newPlaylistDraft.description} onChange={e => setNewPlaylistDraft(prev => ({ ...prev, description: e.target.value }))} rows={3} placeholder="Description" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                    <div className="grid gap-3 md:grid-cols-2">
                      <input value={newPlaylistDraft.difficulty_level} onChange={e => setNewPlaylistDraft(prev => ({ ...prev, difficulty_level: e.target.value }))} placeholder="Difficulty level" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                      <input value={newPlaylistDraft.language_code} onChange={e => setNewPlaylistDraft(prev => ({ ...prev, language_code: e.target.value }))} placeholder="Language code" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                    </div>
                    <button type="button" onClick={() => void handleCreatePlaylist()} disabled={playlistSaving} className="w-full rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:bg-gray-800 disabled:text-gray-500">Create Playlist</button>
                  </div>
                </div>
              </>
            )}

            {tab === 'users' && (
              <div className="rounded-3xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                <div className="flex items-center justify-between gap-4 mb-4">
                  <div>
                    <p className="text-white font-semibold">User Details</p>
                    <p className="text-xs text-gray-500">Edit access, identity fields, and optionally reset the password.</p>
                  </div>
                  <button type="button" onClick={() => void handleSaveUser()} disabled={!selectedUserId || !userDraft || userSaving} className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:bg-gray-800 disabled:text-gray-500">{userSaving ? 'Saving...' : 'Save User'}</button>
                </div>
                {userError && <div className="mb-4 rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">{userError}</div>}
                {usersLoading && !userDraft ? (
                  <div className="py-10 text-sm text-gray-500">Loading user editor...</div>
                ) : !userDraft || !selectedUser ? (
                  <div className="py-10 text-sm text-gray-500">Select a user to edit.</div>
                ) : (
                  <div className="space-y-4">
                    <div className="grid gap-4 md:grid-cols-2">
                      <label className="block text-xs text-gray-500">Spotify ID<input value={selectedUser.spotify_id} disabled className="mt-1 w-full rounded-xl border border-gray-800 bg-gray-950/50 px-3 py-2 text-sm text-gray-500" /></label>
                      <label className="block text-xs text-gray-500">Created<input value={new Date(selectedUser.created_at * 1000).toLocaleString()} disabled className="mt-1 w-full rounded-xl border border-gray-800 bg-gray-950/50 px-3 py-2 text-sm text-gray-500" /></label>
                    </div>
                    <div className="grid gap-4 md:grid-cols-2">
                      <label className="block text-xs text-gray-500">Display name<input value={userDraft.display_name} onChange={e => setUserDraft(prev => prev ? { ...prev, display_name: e.target.value } : prev)} className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" /></label>
                      <label className="block text-xs text-gray-500">Email<input value={userDraft.email} onChange={e => setUserDraft(prev => prev ? { ...prev, email: e.target.value } : prev)} className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" /></label>
                    </div>
                    <label className="block text-xs text-gray-500">Reset password<input type="password" value={userDraft.password} onChange={e => setUserDraft(prev => prev ? { ...prev, password: e.target.value } : prev)} placeholder="Leave blank to keep current password" className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500" /></label>
                    <label className="flex items-center gap-3 rounded-xl border border-gray-800 bg-gray-950/30 px-4 py-3 text-sm text-gray-200">
                      <input type="checkbox" checked={userDraft.is_admin} onChange={e => setUserDraft(prev => prev ? { ...prev, is_admin: e.target.checked } : prev)} className="rounded border-gray-600 bg-gray-900 text-indigo-500 focus:ring-indigo-500" />
                      <span>Administrator access</span>
                    </label>
                    <div className="text-xs text-gray-500">Current password status: {selectedUser.has_password ? 'Password set' : 'No password set'}</div>
                  </div>
                )}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
