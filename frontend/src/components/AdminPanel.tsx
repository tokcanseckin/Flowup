import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { AdminSongDetail, AlignmentTask, AdminUser, PlaylistDetail, PlaylistSummary, SongSummary, api, getAdminHeaders } from '../api/client'
import SyncCalibrator from './SyncCalibrator'

interface Props {
  songs: SongSummary[]
  playlists: PlaylistSummary[]
  onBack: () => void
  onLogout: () => void
  onRefreshSongs: () => Promise<void>
  onRefreshPlaylists: () => Promise<void>
  user: { display_name: string | null } | null
  routeTab?: TabKey
  routeObjectId?: number | null
  onNavigateRoute?: (tab: TabKey, id: number | null) => void
}

type TabKey = 'songs' | 'playlists' | 'users' | 'tasks'

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

type NewSongDraft = {
  title: string
  artist: string
  spotify_uri: string
  language_code: string
  language_name: string
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

type NewTaskDraft = {
  artist: string
  title: string
  display_title: string
  youtube_url: string
  lang: string
  spotify_uri: string
  plain_lyrics: string
}

function emptyPlaylistDraft(): PlaylistDraft {
  return {
    name: '',
    description: '',
    difficulty_level: '',
    language_code: '',
  }
}

const LANGUAGE_PRESETS: { code: string; name: string; script: string; direction: string }[] = [
  { code: 'ru', name: 'Russian',     script: 'Cyrillic', direction: 'ltr' },
  { code: 'uk', name: 'Ukrainian',   script: 'Cyrillic', direction: 'ltr' },
  { code: 'de', name: 'German',      script: 'Latin',    direction: 'ltr' },
  { code: 'es', name: 'Spanish',     script: 'Latin',    direction: 'ltr' },
  { code: 'fr', name: 'French',      script: 'Latin',    direction: 'ltr' },
  { code: 'it', name: 'Italian',     script: 'Latin',    direction: 'ltr' },
  { code: 'pt', name: 'Portuguese',  script: 'Latin',    direction: 'ltr' },
  { code: 'nl', name: 'Dutch',       script: 'Latin',    direction: 'ltr' },
  { code: 'pl', name: 'Polish',      script: 'Latin',    direction: 'ltr' },
  { code: 'sv', name: 'Swedish',     script: 'Latin',    direction: 'ltr' },
  { code: 'ja', name: 'Japanese',    script: 'CJK',      direction: 'ltr' },
  { code: 'zh', name: 'Chinese',     script: 'CJK',      direction: 'ltr' },
  { code: 'ko', name: 'Korean',      script: 'Hangul',   direction: 'ltr' },
  { code: 'tr', name: 'Turkish',     script: 'Latin',    direction: 'ltr' },
  { code: 'ar', name: 'Arabic',      script: 'Arabic',   direction: 'rtl' },
  { code: 'he', name: 'Hebrew',      script: 'Hebrew',   direction: 'rtl' },
]

function emptyNewSongDraft(): NewSongDraft {
  return {
    title: '',
    artist: '',
    spotify_uri: '',
    language_code: 'ru',
    language_name: 'Russian',
    youtube_url: '',
    apple_music_url: '',
    playlist_ids: [],
  }
}

function emptyNewTaskDraft(): NewTaskDraft {
  return {
    artist: '',
    title: '',
    display_title: '',
    youtube_url: '',
    lang: 'ru',
    spotify_uri: '',
    plain_lyrics: '',
  }
}

function taskStatusClass(status: string): string {
  if (status === 'pending') return 'border-amber-700/50 bg-amber-950/30 text-amber-300'
  if (status === 'processing') return 'border-blue-700/50 bg-blue-950/30 text-blue-300'
  if (status === 'done') return 'border-emerald-700/50 bg-emerald-950/30 text-emerald-300'
  return 'border-red-700/50 bg-red-950/30 text-red-300'
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
  routeTab,
  routeObjectId,
  onNavigateRoute,
}: Props) {
  const [tab, setTab] = useState<TabKey>('songs')
  const [searchQuery, setSearchQuery] = useState('')

  const [selectedSongId, setSelectedSongId] = useState<number | null>(songs[0]?.id ?? null)
  const [selectedPlaylistId, setSelectedPlaylistId] = useState<number | null>(playlists[0]?.id ?? null)
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null)

  const [songDraft, setSongDraft] = useState<SongDraft | null>(null)
  const [adminSong, setAdminSong] = useState<AdminSongDetail | null>(null)
  const [lyricsSource, setLyricsSource] = useState<'default' | 'youtube' | 'apple_music'>('default')
  const [lyricsDraft, setLyricsDraft] = useState<AdminSongDetail['lines']>([])
  const [songLoading, setSongLoading] = useState(false)
  const [songSaving, setSongSaving] = useState(false)
  const [lyricsSaving, setLyricsSaving] = useState(false)
  const [songError, setSongError] = useState<string | null>(null)

  const [newSongDraft, setNewSongDraft] = useState<NewSongDraft>(emptyNewSongDraft())
  const [newSongSaving, setNewSongSaving] = useState(false)
  const [newSongError, setNewSongError] = useState<string | null>(null)

  const [regenRunning, setRegenRunning] = useState(false)
  const [regenLog, setRegenLog] = useState<string[]>([])
  const [regenError, setRegenError] = useState<string | null>(null)
  const regenLogRef = useRef<HTMLDivElement>(null)

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

  const [tasks, setTasks] = useState<AlignmentTask[]>([])
  const [tasksLoading, setTasksLoading] = useState(false)
  const [taskStatusFilter, setTaskStatusFilter] = useState<string>('all')
  const [taskError, setTaskError] = useState<string | null>(null)
  const [newTaskDraft, setNewTaskDraft] = useState<NewTaskDraft>(emptyNewTaskDraft())
  const [newTaskSaving, setNewTaskSaving] = useState(false)
  const [newTaskError, setNewTaskError] = useState<string | null>(null)
  const [expandedTaskId, setExpandedTaskId] = useState<number | null>(null)

  const getIdForTab = useCallback((tabKey: TabKey): number | null => {
    if (tabKey === 'songs') return selectedSongId
    if (tabKey === 'playlists') return selectedPlaylistId
    if (tabKey === 'users') return selectedUserId
    return null
  }, [selectedPlaylistId, selectedSongId, selectedUserId])

  const openTab = useCallback((nextTab: TabKey) => {
    setTab(nextTab)
    setSearchQuery('')

    if (nextTab === 'songs' && !selectedSongId && songs[0]) {
      setSelectedSongId(songs[0].id)
      onNavigateRoute?.('songs', songs[0].id)
      return
    }

    if (nextTab === 'playlists' && !selectedPlaylistId && playlists[0]) {
      setSelectedPlaylistId(playlists[0].id)
      onNavigateRoute?.('playlists', playlists[0].id)
      return
    }

    const id = getIdForTab(nextTab)
    onNavigateRoute?.(nextTab, id ?? null)
  }, [getIdForTab, onNavigateRoute, playlists, selectedPlaylistId, selectedSongId, songs])

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

  const filteredTasks = useMemo(() => {
    let result = tasks
    if (taskStatusFilter !== 'all') {
      result = result.filter(t => t.status === taskStatusFilter)
    }
    const needle = searchQuery.trim().toLowerCase()
    if (tab === 'tasks' && needle) {
      result = result.filter(t => `${t.artist} ${t.title}`.toLowerCase().includes(needle))
    }
    return result
  }, [tasks, taskStatusFilter, searchQuery, tab])

  const filteredSongsForPlaylist = useMemo(() => {
    const needle = playlistSongQuery.trim().toLowerCase()
    if (!needle) return songs
    return songs.filter(song => `${song.title} ${song.artist ?? ''}`.toLowerCase().includes(needle))
  }, [playlistSongQuery, songs])

  useEffect(() => {
    if (!selectedSongId && songs[0]) {
      setSelectedSongId(songs[0].id)
      if (tab === 'songs') onNavigateRoute?.('songs', songs[0].id)
    }
  }, [onNavigateRoute, selectedSongId, songs, tab])

  useEffect(() => {
    if (!selectedPlaylistId && playlists[0]) {
      setSelectedPlaylistId(playlists[0].id)
      if (tab === 'playlists') onNavigateRoute?.('playlists', playlists[0].id)
    }
  }, [onNavigateRoute, playlists, selectedPlaylistId, tab])

  useEffect(() => {
    if (!routeTab) return
    setTab(routeTab)
    setSearchQuery('')
    if (routeObjectId === null || routeObjectId === undefined) return
    if (routeTab === 'songs') setSelectedSongId(routeObjectId)
    if (routeTab === 'playlists') setSelectedPlaylistId(routeObjectId)
    if (routeTab === 'users') setSelectedUserId(routeObjectId)
  }, [routeObjectId, routeTab])

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
        if (selectedUserId == null && loaded[0]?.id && tab === 'users') {
          onNavigateRoute?.('users', loaded[0].id)
        }
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
  }, [onNavigateRoute, selectedUserId, tab])

  useEffect(() => {
    if (!selectedSongId) {
      setAdminSong(null)
      return
    }

    let cancelled = false
    setSongLoading(true)
    setSongError(null)
    void api.getAdminSong(selectedSongId)
      .then((detail) => {
        if (cancelled) return
        setAdminSong(detail)
        setSongDraft({
          spotify_uri: detail.spotify_uri,
          title: detail.title,
          artist: detail.artist ?? '',
          youtube_url: detail.youtube_url ?? '',
          apple_music_url: detail.apple_music_url ?? '',
          playlist_ids: [...detail.playlist_ids],
        })
        // Load lyrics for the current source
        const sourceLinesEntry = detail.source_lines.find(s => s.source === lyricsSource)
        const linesToLoad = lyricsSource === 'default'
          ? detail.lines
          : (sourceLinesEntry?.lines ?? detail.lines)
        setLyricsDraft(linesToLoad.map(line => ({ ...line })))
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
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

  useEffect(() => {
    if (tab !== 'tasks') return
    let cancelled = false

    const loadTasks = () => {
      void api.listAlignmentTasks()
        .then(loaded => {
          if (cancelled) return
          setTasks(loaded)
          setTaskError(null)
          setTasksLoading(false)
        })
        .catch(err => {
          if (cancelled) return
          setTaskError(err instanceof Error ? err.message : 'Failed to load tasks')
          setTasksLoading(false)
        })
    }

    setTasksLoading(true)
    loadTasks()
    const interval = setInterval(loadTasks, 15000)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [tab])

  // When source tab changes, re-derive lyricsDraft from adminSong without a network request.
  useEffect(() => {
    if (!adminSong) return
    if (lyricsSource === 'default') {
      setLyricsDraft(adminSong.lines.map(line => ({ ...line })))
    } else {
      const sourceLinesEntry = adminSong.source_lines.find(s => s.source === lyricsSource)
      // If no source-specific lines exist yet, seed from default as a starting point.
      setLyricsDraft((sourceLinesEntry?.lines ?? adminSong.lines).map(line => ({ ...line })))
    }
  }, [adminSong, lyricsSource])

  const handleCreateSong = useCallback(async () => {
    if (!newSongDraft.title.trim()) {
      setNewSongError('Title is required')
      return
    }
    setNewSongSaving(true)
    setNewSongError(null)
    try {
      const preset = LANGUAGE_PRESETS.find(p => p.code === newSongDraft.language_code)
      const created = await api.createAdminSong({
        title: newSongDraft.title.trim(),
        artist: newSongDraft.artist.trim() || null,
        spotify_uri: newSongDraft.spotify_uri.trim() || null,
        language_code: newSongDraft.language_code,
        language_name: newSongDraft.language_name.trim() || (preset?.name ?? 'Unknown'),
        language_script: preset?.script ?? 'Latin',
        language_direction: preset?.direction ?? 'ltr',
        youtube_url: newSongDraft.youtube_url.trim() || null,
        apple_music_url: newSongDraft.apple_music_url.trim() || null,
        playlist_ids: newSongDraft.playlist_ids,
      })
      await Promise.all([onRefreshSongs(), onRefreshPlaylists()])
      setSelectedSongId(created.id)
      setNewSongDraft(emptyNewSongDraft())
      setTab('songs')
      onNavigateRoute?.('songs', created.id)
    } catch (error) {
      setNewSongError(error instanceof Error ? error.message : 'Failed to create song')
    } finally {
      setNewSongSaving(false)
    }
  }, [newSongDraft, onNavigateRoute, onRefreshPlaylists, onRefreshSongs])

  const handleDeleteSong = useCallback(async () => {
    if (!selectedSongId) return
    if (!window.confirm('Delete this song permanently? This cannot be undone.')) return
    setSongSaving(true)
    setSongError(null)
    try {
      await api.deleteAdminSong(selectedSongId)
      await Promise.all([onRefreshSongs(), onRefreshPlaylists()])
      const nextSongId = songs.find(s => s.id !== selectedSongId)?.id ?? null
      setSelectedSongId(nextSongId)
      onNavigateRoute?.('songs', nextSongId)
    } catch (error) {
      setSongError(error instanceof Error ? error.message : 'Failed to delete song')
    } finally {
      setSongSaving(false)
    }
  }, [onNavigateRoute, onRefreshPlaylists, onRefreshSongs, selectedSongId, songs])

  useEffect(() => {
    if (regenLogRef.current) {
      regenLogRef.current.scrollTop = regenLogRef.current.scrollHeight
    }
  }, [regenLog])

  const handleRegenerate = useCallback(async () => {
    if (!selectedSongId) return
    setRegenRunning(true)
    setRegenLog([])
    setRegenError(null)
    try {
      const resp = await fetch(`/api/admin/songs/${selectedSongId}/regenerate`, {
        method: 'POST',
        headers: getAdminHeaders(),
      })
      if (!resp.ok || !resp.body) {
        const body = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string }
        setRegenError(body.detail ?? `Error ${resp.status}`)
        return
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const chunks = buffer.split('\n\n')
        buffer = chunks.pop() ?? ''
        for (const chunk of chunks) {
          let eventType = 'message'
          let dataLine = ''
          for (const row of chunk.split('\n')) {
            if (row.startsWith('event: ')) eventType = row.slice(7).trim()
            else if (row.startsWith('data: ')) dataLine = row.slice(6)
          }
          if (eventType === 'done') {
            try {
              const detail = JSON.parse(dataLine) as AdminSongDetail
              setAdminSong(detail)
              const srcEntry = detail.source_lines.find(s => s.source === lyricsSource)
              const lines = lyricsSource === 'default' ? detail.lines : (srcEntry?.lines ?? detail.lines)
              setLyricsDraft(lines.map(l => ({ ...l })))
              setRegenLog(prev => [...prev, '✓ Lyrics updated successfully.'])
            } catch {
              setRegenError('Failed to parse updated song data')
            }
          } else if (eventType === 'error') {
            setRegenError(dataLine)
            setRegenLog(prev => [...prev, `✗ Error: ${dataLine}`])
          } else if (dataLine) {
            setRegenLog(prev => [...prev, dataLine])
          }
        }
      }
    } catch (err) {
      setRegenError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setRegenRunning(false)
    }
  }, [selectedSongId, lyricsSource])

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
      setAdminSong(updated)
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
      let updated: AdminSongDetail
      if (lyricsSource === 'default') {
        updated = await api.updateAdminLyrics(selectedSongId, {
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
      } else {
        updated = await api.updateSourceLyrics(
          selectedSongId,
          lyricsSource,
          lyricsDraft.map((line, index) => ({
            position: index,
            start_time_ms: line.start_time_ms,
            end_time_ms: line.end_time_ms,
            original_line: line.original_line,
            phonetic_line: line.phonetic_line,
            translation: line.translation,
          })),
        )
      }
      setAdminSong(updated)
      const refreshedEntry = updated.source_lines.find(s => s.source === lyricsSource)
      const refreshedLines = lyricsSource === 'default'
        ? updated.lines
        : (refreshedEntry?.lines ?? updated.lines)
      setLyricsDraft(refreshedLines.map(line => ({ ...line })))
    } catch (error) {
      setSongError(error instanceof Error ? error.message : 'Failed to save lyrics')
    } finally {
      setLyricsSaving(false)
    }
  }, [lyricsDraft, lyricsSource, selectedSongId])

  // Atomic: shift all timestamps by offsetMs then save — avoids stale-state race
  // that happens when onApplyOffset (setState) and onSave (reads state) are separate.
  const handleSyncApplyAndSave = useCallback(async (offsetMs: number) => {
    if (!selectedSongId) return
    setLyricsSaving(true)
    setSongError(null)
    try {
      const shiftedLines = lyricsDraft.map((line, index) => ({
        ...line,
        position: index,
        start_time_ms: line.start_time_ms + offsetMs,
        end_time_ms: line.end_time_ms + offsetMs,
      }))
      setLyricsDraft(shiftedLines)
      let updated: AdminSongDetail
      if (lyricsSource === 'default') {
        updated = await api.updateAdminLyrics(selectedSongId, {
          lines: shiftedLines.map(line => ({
            id: line.id,
            position: line.position,
            start_time_ms: line.start_time_ms,
            end_time_ms: line.end_time_ms,
            original_line: line.original_line,
            phonetic_line: line.phonetic_line,
            translation: line.translation,
          })),
        })
      } else {
        updated = await api.updateSourceLyrics(
          selectedSongId,
          lyricsSource,
          shiftedLines.map(line => ({
            position: line.position,
            start_time_ms: line.start_time_ms,
            end_time_ms: line.end_time_ms,
            original_line: line.original_line,
            phonetic_line: line.phonetic_line,
            translation: line.translation,
          })),
        )
      }
      setAdminSong(updated)
      const refreshedEntry = updated.source_lines.find(s => s.source === lyricsSource)
      const refreshedLines = lyricsSource === 'default'
        ? updated.lines
        : (refreshedEntry?.lines ?? updated.lines)
      setLyricsDraft(refreshedLines.map(line => ({ ...line })))
    } catch (error) {
      setSongError(error instanceof Error ? error.message : 'Failed to save lyrics')
    } finally {
      setLyricsSaving(false)
    }
  }, [lyricsDraft, lyricsSource, selectedSongId])

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
      onNavigateRoute?.('playlists', created.id)
    } catch (error) {
      setPlaylistError(error instanceof Error ? error.message : 'Failed to create playlist')
    } finally {
      setPlaylistSaving(false)
    }
  }, [newPlaylistDraft, onNavigateRoute, onRefreshPlaylists])

  const handleDeletePlaylist = useCallback(async () => {
    if (!selectedPlaylistId) return
    if (!window.confirm('Delete this playlist? Songs will remain in the library.')) return
    setPlaylistSaving(true)
    setPlaylistError(null)
    try {
      await api.deletePlaylist(selectedPlaylistId)
      await onRefreshPlaylists()
      const nextPlaylistId = playlists.find(item => item.id !== selectedPlaylistId)?.id ?? null
      setSelectedPlaylistId(nextPlaylistId)
      onNavigateRoute?.('playlists', nextPlaylistId)
    } catch (error) {
      setPlaylistError(error instanceof Error ? error.message : 'Failed to delete playlist')
    } finally {
      setPlaylistSaving(false)
    }
  }, [onNavigateRoute, onRefreshPlaylists, playlists, selectedPlaylistId])

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

  const handleCreateTask = useCallback(async () => {
    if (!newTaskDraft.artist.trim() || !newTaskDraft.title.trim() || !newTaskDraft.youtube_url.trim()) {
      setNewTaskError('Artist, title, and YouTube URL are required')
      return
    }
    setNewTaskSaving(true)
    setNewTaskError(null)
    try {
      await api.createAlignmentTask({
        artist: newTaskDraft.artist.trim(),
        title: newTaskDraft.title.trim(),
        display_title: newTaskDraft.display_title.trim() || null,
        youtube_url: newTaskDraft.youtube_url.trim(),
        lang: newTaskDraft.lang,
        spotify_uri: newTaskDraft.spotify_uri.trim() || null,
        plain_lyrics: newTaskDraft.plain_lyrics.trim() || null,
      })
      setNewTaskDraft(emptyNewTaskDraft())
      const loaded = await api.listAlignmentTasks()
      setTasks(loaded)
    } catch (err) {
      setNewTaskError(err instanceof Error ? err.message : 'Failed to create task')
    } finally {
      setNewTaskSaving(false)
    }
  }, [newTaskDraft])

  const handleDeleteTask = useCallback(async (taskId: number) => {
    if (!window.confirm('Delete this alignment task? This cannot be undone.')) return
    try {
      await api.deleteAlignmentTask(taskId)
      setTasks(prev => prev.filter(t => t.id !== taskId))
      if (expandedTaskId === taskId) setExpandedTaskId(null)
    } catch (err) {
      setTaskError(err instanceof Error ? err.message : 'Failed to delete task')
    }
  }, [expandedTaskId])

  const handleRetryTask = useCallback(async (taskId: number) => {
    try {
      const updated = await api.retryAlignmentTask(taskId)
      setTasks(prev => prev.map(t => t.id === taskId ? updated : t))
    } catch (err) {
      setTaskError(err instanceof Error ? err.message : 'Failed to retry task')
    }
  }, [])

  const toggleExpandedTask = useCallback((taskId: number) => {
    setExpandedTaskId(prev => prev === taskId ? null : taskId)
  }, [])

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
      : tab === 'tasks'
        ? 'Search tasks'
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
          <button type="button" onClick={() => openTab('songs')} className={`rounded-xl border px-4 py-2 text-sm font-semibold transition-colors ${tabButtonClass(tab === 'songs')}`}>Songs</button>
          <button type="button" onClick={() => openTab('playlists')} className={`rounded-xl border px-4 py-2 text-sm font-semibold transition-colors ${tabButtonClass(tab === 'playlists')}`}>Playlists</button>
          <button type="button" onClick={() => openTab('users')} className={`rounded-xl border px-4 py-2 text-sm font-semibold transition-colors ${tabButtonClass(tab === 'users')}`}>Users</button>
          <button type="button" onClick={() => openTab('tasks')} className={`rounded-xl border px-4 py-2 text-sm font-semibold transition-colors ${tabButtonClass(tab === 'tasks')}`}>Tasks</button>
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
                    onClick={() => {
                      setSelectedSongId(song.id)
                      onNavigateRoute?.('songs', song.id)
                    }}
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
                    onClick={() => {
                      setSelectedPlaylistId(playlist.id)
                      onNavigateRoute?.('playlists', playlist.id)
                    }}
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
                    onClick={() => {
                      setSelectedUserId(item.id)
                      onNavigateRoute?.('users', item.id)
                    }}
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

            {tab === 'tasks' && (
              <div className="space-y-1">
                <p className="text-xs text-gray-500 mb-2">Filter by status</p>
                {(['all', 'pending', 'processing', 'done', 'failed'] as const).map(s => {
                  const count = s === 'all' ? tasks.length : tasks.filter(t => t.status === s).length
                  return (
                    <button
                      key={s}
                      type="button"
                      onClick={() => setTaskStatusFilter(s)}
                      className={`w-full rounded-2xl border px-3 py-2.5 text-left transition-colors ${taskStatusFilter === s ? 'border-indigo-500 bg-indigo-950/40 text-white' : 'border-gray-800 bg-gray-950/30 text-gray-400 hover:border-gray-700 hover:text-gray-200'}`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium capitalize">{s}</span>
                        <span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-semibold ${s === 'all' ? 'border-gray-700 bg-gray-900/50 text-gray-400' : `${taskStatusClass(s)} opacity-80`}`}>{count}</span>
                      </div>
                    </button>
                  )
                })}
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
                    <div className="flex gap-2">
                      <button type="button" onClick={() => void handleRegenerate()} disabled={!selectedSongId || regenRunning || songSaving} className="rounded-xl border border-amber-700/60 bg-amber-950/20 px-4 py-2 text-sm font-semibold text-amber-300 hover:bg-amber-950/40 disabled:border-gray-800 disabled:text-gray-500">{regenRunning ? 'Running...' : 'Regenerate Lyrics'}</button>
                      <button type="button" onClick={() => void handleSaveSong()} disabled={!selectedSongId || !songDraft || songSaving} className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:bg-gray-800 disabled:text-gray-500">{songSaving ? 'Saving...' : 'Save Song'}</button>
                      <button type="button" onClick={() => void handleDeleteSong()} disabled={!selectedSongId || songSaving} className="rounded-xl border border-red-900/60 px-4 py-2 text-sm font-semibold text-red-300 hover:bg-red-950/30 disabled:border-gray-800 disabled:text-gray-600">Delete</button>
                    </div>
                  </div>
                  {songError && <div className="mb-4 rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">{songError}</div>}
                  {regenError && <div className="mb-4 rounded-xl border border-amber-900/50 bg-amber-950/20 px-4 py-3 text-sm text-amber-400">{regenError}</div>}
                  {(regenRunning || regenLog.length > 0) && (
                    <div
                      ref={regenLogRef}
                      className="mb-4 max-h-48 overflow-y-auto rounded-xl border border-gray-800 bg-gray-950/60 p-3 font-mono text-xs space-y-0.5"
                    >
                      {regenLog.map((line, i) => (
                        <div
                          key={i}
                          className={line.startsWith('✓') ? 'text-emerald-400' : line.startsWith('✗') ? 'text-red-400' : 'text-gray-400'}
                        >
                          {line}
                        </div>
                      ))}
                      {regenRunning && <div className="text-indigo-400 animate-pulse">Running pipeline…</div>}
                    </div>
                  )}
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

                {songDraft?.youtube_url && lyricsSource === 'youtube' && (
                  <div className="rounded-3xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                    <p className="text-white font-semibold mb-1">Sync Calibrator — YouTube</p>
                    <p className="text-xs text-gray-500 mb-4">Play the YouTube video and use the offset controls to align lyrics in real time. Click a line to seek to it.</p>
                    <SyncCalibrator
                      youtubeUrl={songDraft.youtube_url}
                      lines={lyricsDraft}
                      onApplyAndSave={handleSyncApplyAndSave}
                      saving={lyricsSaving}
                    />
                  </div>
                )}

                <div className="rounded-3xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                  <div className="flex items-center justify-between gap-4 mb-4">
                    <div>
                      <p className="text-white font-semibold">Lyrics + Sync</p>
                      <p className="text-xs text-gray-500">Edit line text and timestamps for the selected song.</p>
                    </div>
                    <button type="button" onClick={() => void handleSaveLyrics()} disabled={!selectedSongId || lyricsSaving || !lyricsDraft.length} className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:bg-gray-800 disabled:text-gray-500">{lyricsSaving ? 'Saving...' : 'Save Lyrics'}</button>
                  </div>
                  <div className="flex gap-2 mb-4">
                    {(['default', 'youtube', 'apple_music'] as const).map(src => {
                      const hasLines = src === 'default'
                        ? (adminSong?.lines.length ?? 0) > 0
                        : (adminSong?.source_lines.some(s => s.source === src) ?? false)
                      return (
                        <button
                          key={src}
                          type="button"
                          onClick={() => setLyricsSource(src)}
                          className={`rounded-xl border px-3 py-1.5 text-xs font-semibold transition-colors ${lyricsSource === src ? 'border-indigo-500 bg-indigo-950/40 text-white' : 'border-gray-800 bg-gray-950/30 text-gray-400 hover:border-gray-700 hover:text-gray-200'}`}
                        >
                          {src === 'default' ? 'Default' : src === 'youtube' ? 'YouTube' : 'Apple Music'}
                          {!hasLines && src !== 'default' && <span className="ml-1 text-gray-600">(copy)</span>}
                        </button>
                      )
                    })}
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

            {tab === 'songs' && (
              <div className="rounded-3xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                <p className="text-white font-semibold mb-3">Create Song</p>
                {newSongError && <div className="mb-3 rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">{newSongError}</div>}
                <div className="space-y-3">
                  <div className="grid gap-3 md:grid-cols-2">
                    <label className="block text-xs text-gray-500">Title *<input value={newSongDraft.title} onChange={e => setNewSongDraft(prev => ({ ...prev, title: e.target.value }))} placeholder="Song title" className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" /></label>
                    <label className="block text-xs text-gray-500">Artist<input value={newSongDraft.artist} onChange={e => setNewSongDraft(prev => ({ ...prev, artist: e.target.value }))} placeholder="Artist name" className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" /></label>
                  </div>
                  <label className="block text-xs text-gray-500">Spotify URI <span className="text-gray-600">(leave blank to auto-generate)</span><input value={newSongDraft.spotify_uri} onChange={e => setNewSongDraft(prev => ({ ...prev, spotify_uri: e.target.value }))} placeholder="spotify:track:... or leave blank" className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500" /></label>
                  <div className="grid gap-3 md:grid-cols-2">
                    <label className="block text-xs text-gray-500">Language
                      <select
                        value={newSongDraft.language_code}
                        onChange={e => {
                          const preset = LANGUAGE_PRESETS.find(p => p.code === e.target.value)
                          setNewSongDraft(prev => ({
                            ...prev,
                            language_code: e.target.value,
                            language_name: preset?.name ?? prev.language_name,
                          }))
                        }}
                        className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500"
                      >
                        {LANGUAGE_PRESETS.map(p => (
                          <option key={p.code} value={p.code}>{p.name} ({p.code})</option>
                        ))}
                      </select>
                    </label>
                    <label className="block text-xs text-gray-500">Language name<input value={newSongDraft.language_name} onChange={e => setNewSongDraft(prev => ({ ...prev, language_name: e.target.value }))} placeholder="e.g. Russian" className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" /></label>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    <label className="block text-xs text-gray-500">YouTube URL<input value={newSongDraft.youtube_url} onChange={e => setNewSongDraft(prev => ({ ...prev, youtube_url: e.target.value }))} placeholder="https://youtube.com/watch?v=..." className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500" /></label>
                    <label className="block text-xs text-gray-500">Apple Music URL<input value={newSongDraft.apple_music_url} onChange={e => setNewSongDraft(prev => ({ ...prev, apple_music_url: e.target.value }))} placeholder="https://music.apple.com/..." className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500" /></label>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500 mb-2">Add to playlists</p>
                    <div className="grid gap-2 md:grid-cols-2">
                      {playlists.map(playlist => (
                        <label key={playlist.id} className="flex items-center gap-3 rounded-xl border border-gray-800 bg-gray-950/30 px-3 py-2 text-sm text-gray-200">
                          <input type="checkbox" checked={newSongDraft.playlist_ids.includes(playlist.id)} onChange={() => setNewSongDraft(prev => ({ ...prev, playlist_ids: prev.playlist_ids.includes(playlist.id) ? prev.playlist_ids.filter(id => id !== playlist.id) : [...prev.playlist_ids, playlist.id] }))} className="rounded border-gray-600 bg-gray-900 text-indigo-500 focus:ring-indigo-500" />
                          <span>{playlist.name}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                  <button type="button" onClick={() => void handleCreateSong()} disabled={newSongSaving} className="w-full rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:bg-gray-800 disabled:text-gray-500">{newSongSaving ? 'Creating...' : 'Create Song'}</button>
                </div>
              </div>
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

            {tab === 'tasks' && (
              <>
                <div className="rounded-3xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                  <div className="flex items-center justify-between gap-4 mb-4">
                    <div>
                      <p className="text-white font-semibold">Alignment Tasks</p>
                      <p className="text-xs text-gray-500">Processed by the Mac Mini worker. Auto-refreshes every 15 s.</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setTasksLoading(true)
                        void api.listAlignmentTasks().then(loaded => { setTasks(loaded); setTaskError(null) }).catch(err => setTaskError(err instanceof Error ? err.message : 'Failed')).finally(() => setTasksLoading(false))
                      }}
                      className="rounded-xl border border-gray-700 px-3 py-1.5 text-xs font-semibold text-gray-300 hover:border-gray-500 hover:text-white transition-colors"
                    >
                      {tasksLoading ? 'Refreshing…' : 'Refresh'}
                    </button>
                  </div>

                  {taskError && <div className="mb-4 rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">{taskError}</div>}

                  {tasksLoading && !tasks.length ? (
                    <div className="py-10 text-sm text-gray-500">Loading tasks…</div>
                  ) : filteredTasks.length === 0 ? (
                    <div className="py-10 text-sm text-gray-500">No tasks{taskStatusFilter !== 'all' ? ` with status "${taskStatusFilter}"` : ''}.</div>
                  ) : (
                    <div className="space-y-2">
                      {filteredTasks.map(task => (
                        <div key={task.id} className="rounded-2xl border border-gray-800 bg-gray-950/30 p-4">
                          <div className="flex items-center gap-3">
                            <span className="shrink-0 font-mono text-xs text-gray-600">#{task.id}</span>
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-sm font-medium text-white">{task.artist} — {task.title}</p>
                              <p className="text-xs text-gray-500">
                                {task.lang.toUpperCase()}
                                {' · '}
                                {new Date(task.created_at * 1000).toLocaleString()}
                                {task.completed_at ? ` · done ${new Date(task.completed_at * 1000).toLocaleString()}` : ''}
                              </p>
                            </div>
                            <span className={`shrink-0 rounded-md border px-2 py-0.5 text-[11px] font-semibold ${taskStatusClass(task.status)}`}>
                              {task.status}
                            </span>
                            <div className="flex shrink-0 gap-1">
                              {(task.status === 'failed' || task.status === 'processing') && (
                                <button
                                  type="button"
                                  onClick={() => void handleRetryTask(task.id)}
                                  className="rounded-lg border border-amber-700/50 px-2 py-1 text-xs font-semibold text-amber-300 hover:bg-amber-950/30 transition-colors"
                                >
                                  Retry
                                </button>
                              )}
                              <button
                                type="button"
                                onClick={() => toggleExpandedTask(task.id)}
                                className="rounded-lg border border-gray-700 px-2 py-1 text-xs font-semibold text-gray-400 hover:border-gray-500 hover:text-white transition-colors"
                              >
                                {expandedTaskId === task.id ? 'Hide' : 'Details'}
                              </button>
                              <button
                                type="button"
                                onClick={() => void handleDeleteTask(task.id)}
                                className="rounded-lg border border-red-900/50 px-2 py-1 text-xs font-semibold text-red-400 hover:bg-red-950/20 transition-colors"
                              >
                                Delete
                              </button>
                            </div>
                          </div>

                          {expandedTaskId === task.id && (
                            <div className="mt-3 border-t border-gray-800 pt-3 space-y-2">
                              {task.plain_lyrics && (
                                <div>
                                  <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-600">Lyrics (input)</p>
                                  <pre className="max-h-32 overflow-y-auto rounded-lg bg-gray-950/60 p-2 font-mono text-xs text-gray-400 whitespace-pre-wrap">{task.plain_lyrics}</pre>
                                </div>
                              )}
                              {task.error && (
                                <div>
                                  <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-red-600">Error</p>
                                  <pre className="max-h-32 overflow-y-auto rounded-lg border border-red-900/40 bg-red-950/10 p-2 font-mono text-xs text-red-400 whitespace-pre-wrap">{task.error}</pre>
                                </div>
                              )}
                              {task.result_lrc && (
                                <div>
                                  <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-emerald-600">Result LRC</p>
                                  <pre className="max-h-48 overflow-y-auto rounded-lg border border-emerald-900/40 bg-emerald-950/10 p-2 font-mono text-xs text-emerald-400 whitespace-pre-wrap">{task.result_lrc}</pre>
                                </div>
                              )}
                              {!task.error && !task.result_lrc && (
                                <p className="text-xs text-gray-600">No additional details yet.</p>
                              )}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="rounded-3xl border border-gray-800/80 p-5" style={{ background: '#12121f' }}>
                  <p className="text-white font-semibold mb-3">Queue New Task</p>
                  {newTaskError && <div className="mb-3 rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">{newTaskError}</div>}
                  <div className="space-y-3">
                    <div className="grid gap-3 md:grid-cols-2">
                      <label className="block text-xs text-gray-500">Artist *
                        <input value={newTaskDraft.artist} onChange={e => setNewTaskDraft(prev => ({ ...prev, artist: e.target.value }))} placeholder="Artist name" className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                      </label>
                      <label className="block text-xs text-gray-500">Title *
                        <input value={newTaskDraft.title} onChange={e => setNewTaskDraft(prev => ({ ...prev, title: e.target.value }))} placeholder="Song title" className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                      </label>
                    </div>
                    <label className="block text-xs text-gray-500">YouTube URL *
                      <input value={newTaskDraft.youtube_url} onChange={e => setNewTaskDraft(prev => ({ ...prev, youtube_url: e.target.value }))} placeholder="https://youtube.com/watch?v=..." className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500" />
                    </label>
                    <div className="grid gap-3 md:grid-cols-2">
                      <label className="block text-xs text-gray-500">Language
                        <select value={newTaskDraft.lang} onChange={e => setNewTaskDraft(prev => ({ ...prev, lang: e.target.value }))} className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500">
                          {LANGUAGE_PRESETS.map(p => (
                            <option key={p.code} value={p.code}>{p.name} ({p.code})</option>
                          ))}
                        </select>
                      </label>
                      <label className="block text-xs text-gray-500">Spotify URI <span className="text-gray-600">(optional)</span>
                        <input value={newTaskDraft.spotify_uri} onChange={e => setNewTaskDraft(prev => ({ ...prev, spotify_uri: e.target.value }))} placeholder="spotify:track:..." className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500" />
                      </label>
                    </div>
                    <label className="block text-xs text-gray-500">Display title <span className="text-gray-600">(optional override)</span>
                      <input value={newTaskDraft.display_title} onChange={e => setNewTaskDraft(prev => ({ ...prev, display_title: e.target.value }))} placeholder="Leave blank to use title" className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500" />
                    </label>
                    <label className="block text-xs text-gray-500">Plain lyrics <span className="text-gray-600">(optional — worker can fetch automatically)</span>
                      <textarea value={newTaskDraft.plain_lyrics} onChange={e => setNewTaskDraft(prev => ({ ...prev, plain_lyrics: e.target.value }))} rows={6} placeholder="Paste lyrics here, one line per row…" className="mt-1 w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500" />
                    </label>
                    <button type="button" onClick={() => void handleCreateTask()} disabled={newTaskSaving} className="w-full rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:bg-gray-800 disabled:text-gray-500">
                      {newTaskSaving ? 'Queuing…' : 'Queue Task'}
                    </button>
                  </div>
                </div>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
