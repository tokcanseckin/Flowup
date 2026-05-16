import { useCallback, useEffect, useMemo, useState } from 'react'

import { AdminSongDetail, AdminReport, AlignmentTask, AdminUser, LocalizationItem, PlaylistDetail, PlaylistSummary, SongSummary, api } from '../api/client'
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

type TabKey = 'songs' | 'playlists' | 'users' | 'tasks' | 'localizations' | 'reports'

type PlaylistDraft = {
  name: string
  description: string
  difficulty_level: string
  language_code: string
  target_langs: string[]
  is_hidden: boolean
}

type SongDraft = {
  spotify_uri: string
  title: string
  artist: string
  youtube_url: string
  apple_music_url: string
  target_langs: string[]
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
    target_langs: [],
    is_hidden: false,
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
  const [availableTargetLangs, setAvailableTargetLangs] = useState<string[]>([])
  const [lyricsTargetLang, setLyricsTargetLang] = useState('')
  const [lyricsTranslations, setLyricsTranslations] = useState<Record<number, string>>({})
  const [lyricsTranslationsLoading, setLyricsTranslationsLoading] = useState(false)

  const [newSongDraft, setNewSongDraft] = useState<NewSongDraft>(emptyNewSongDraft())
  const [newSongSaving, setNewSongSaving] = useState(false)
  const [newSongError, setNewSongError] = useState<string | null>(null)

  const [selectedSongTask, setSelectedSongTask] = useState<AlignmentTask | null>(null)
  const [selectedSongTaskLoading, setSelectedSongTaskLoading] = useState(false)
  const [urlLookupLoading, setUrlLookupLoading] = useState<{ youtube: boolean; appleMusic: boolean }>({ youtube: false, appleMusic: false })
  const [urlLookupError, setUrlLookupError] = useState<{ youtube: string | null; appleMusic: string | null }>({ youtube: null, appleMusic: null })

  const [playlistDetail, setPlaylistDetail] = useState<PlaylistDetail | null>(null)
  const [playlistDraft, setPlaylistDraft] = useState<PlaylistDraft>(emptyPlaylistDraft())
  const [playlistTargetLangsText, setPlaylistTargetLangsText] = useState('')
  const [newPlaylistDraft, setNewPlaylistDraft] = useState<PlaylistDraft>(emptyPlaylistDraft())
  const [newPlaylistTargetLangsText, setNewPlaylistTargetLangsText] = useState('')
  const [songTargetLangsText, setSongTargetLangsText] = useState('')
  const [playlistSongIds, setPlaylistSongIds] = useState<number[]>([])
  const [playlistLoading, setPlaylistLoading] = useState(false)
  const [playlistSaving, setPlaylistSaving] = useState(false)
  const [playlistError, setPlaylistError] = useState<string | null>(null)
  const [playlistSongQuery, setPlaylistSongQuery] = useState('')
  const [coverUploading, setCoverUploading] = useState(false)
  const [coverKey, setCoverKey] = useState(0)
  // All playlists including hidden — loaded via admin endpoint
  const [adminPlaylists, setAdminPlaylists] = useState<PlaylistSummary[]>(playlists)

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

    if (nextTab === 'playlists' && !selectedPlaylistId && adminPlaylists[0]) {
      setSelectedPlaylistId(adminPlaylists[0].id)
      onNavigateRoute?.('playlists', adminPlaylists[0]?.id ?? null)
      return
    }

    const id = getIdForTab(nextTab)
    onNavigateRoute?.(nextTab, id ?? null)
  }, [adminPlaylists, getIdForTab, onNavigateRoute, selectedPlaylistId, selectedSongId, songs])

  const filteredSongs = useMemo(() => {
    const needle = searchQuery.trim().toLowerCase()
    if (tab !== 'songs' || !needle) return songs
    return songs.filter(song => `${song.title} ${song.artist ?? ''} ${song.spotify_uri}`.toLowerCase().includes(needle))
  }, [searchQuery, songs, tab])

  const filteredPlaylists = useMemo(() => {
    const needle = searchQuery.trim().toLowerCase()
    if (tab !== 'playlists' || !needle) return adminPlaylists
    return adminPlaylists.filter(playlist => `${playlist.name} ${playlist.description ?? ''} ${playlist.language_code ?? ''}`.toLowerCase().includes(needle))
  }, [adminPlaylists, searchQuery, tab])

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
    if (!selectedPlaylistId && adminPlaylists[0]) {
      setSelectedPlaylistId(adminPlaylists[0].id)
      if (tab === 'playlists') onNavigateRoute?.('playlists', adminPlaylists[0].id)
    }
  }, [onNavigateRoute, adminPlaylists, selectedPlaylistId, tab])

  // Load all playlists (including hidden) for the admin panel
  useEffect(() => {
    void api.adminListPlaylists().then(setAdminPlaylists).catch(() => setAdminPlaylists(playlists))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playlists])

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
      setAvailableTargetLangs([])
      setLyricsTargetLang('')
      setLyricsTranslations({})
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
          target_langs: detail.target_langs ?? [],
          playlist_ids: [...detail.playlist_ids],
        })
        setSongTargetLangsText((detail.target_langs ?? []).join(', '))
        // Load lyrics for the current source
        const sourceLinesEntry = detail.source_lines.find(s => s.source === lyricsSource)
        const linesToLoad = lyricsSource === 'default'
          ? detail.lines
          : (sourceLinesEntry?.lines ?? detail.lines)
        setLyricsDraft(linesToLoad.map(line => ({ ...line })))
        // Fetch available target langs and auto-select based on browse preference.
        void api.getSongTargetLangs(selectedSongId).then(({ target_langs }) => {
          if (cancelled) return
          setAvailableTargetLangs(target_langs)
          let preferred = target_langs[0] ?? ''
          try {
            const raw = localStorage.getItem('browse.targetLangMap')
            if (raw) {
              const map = JSON.parse(raw) as Record<string, string>
              const browsePref = map[detail.language.code]
              if (browsePref && target_langs.includes(browsePref)) preferred = browsePref
            }
          } catch { /* ignore */ }
          setLyricsTargetLang(preferred)
        }).catch(() => {})
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

  // Load the most recent alignment task for the currently-selected song.
  useEffect(() => {
    if (!adminSong) { setSelectedSongTask(null); return }
    const spotifyUri = adminSong.spotify_uri
    let cancelled = false
    void api.listAlignmentTasks()
      .then(all => {
        if (cancelled) return
        const match = all.find(t => t.spotify_uri === spotifyUri)
        setSelectedSongTask(match ?? null)
      })
      .catch(() => {})
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminSong?.spotify_uri])

  // Poll the task every 10 s while it is active.
  useEffect(() => {
    if (!selectedSongTask) return
    if (selectedSongTask.status !== 'pending' && selectedSongTask.status !== 'processing') return
    const id = setInterval(() => {
      void api.getAlignmentTask(selectedSongTask.id)
        .then(updated => setSelectedSongTask(updated))
        .catch(() => {})
    }, 10000)
    return () => clearInterval(id)
  }, [selectedSongTask?.id, selectedSongTask?.status])

  useEffect(() => {
    if (!selectedPlaylistId) {
      setPlaylistDetail(null)
      setPlaylistDraft(emptyPlaylistDraft())
      setPlaylistTargetLangsText('')
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
          target_langs: detail.target_langs ?? [],
          is_hidden: detail.is_hidden ?? false,
        })
        setPlaylistTargetLangsText((detail.target_langs ?? []).join(', '))
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

  // When lyricsTargetLang changes, fetch translations for the selected song.
  useEffect(() => {
    if (!selectedSongId || !lyricsTargetLang) {
      setLyricsTranslations({})
      return
    }
    // If this lang has no stored translations yet, show empty fields without fetching
    if (!availableTargetLangs.includes(lyricsTargetLang)) {
      setLyricsTranslations({})
      return
    }
    let cancelled = false
    setLyricsTranslationsLoading(true)
    void api.getSong(selectedSongId, undefined, lyricsTargetLang)
      .then(detail => {
        if (cancelled) return
        // Build map: line id → translation text (empty string if no translation stored)
        const map: Record<number, string> = {}
        detail.lines.forEach(line => { map[line.id] = line.translation === line.original_line ? '' : line.translation })
        setLyricsTranslations(map)
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLyricsTranslationsLoading(false) })
    return () => { cancelled = true }
  }, [selectedSongId, lyricsTargetLang, availableTargetLangs])

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

  const handleQueueTask = useCallback(async () => {
    if (!adminSong) return
    if (!adminSong.youtube_url) {
      setSongError('Song has no YouTube URL — required for the worker')
      return
    }
    setSelectedSongTaskLoading(true)
    setSongError(null)
    try {
      const task = await api.createAlignmentTask({
        artist: adminSong.artist ?? adminSong.title,
        title: adminSong.title,
        youtube_url: adminSong.youtube_url,
        lang: adminSong.language.code,
        spotify_uri: adminSong.spotify_uri || undefined,
      })
      setSelectedSongTask(task)
    } catch (err) {
      setSongError(err instanceof Error ? err.message : 'Failed to queue task')
    } finally {
      setSelectedSongTaskLoading(false)
    }
  }, [adminSong])

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
        target_langs: songDraft.target_langs,
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

  const handleFindYouTubeUrl = useCallback(async () => {
    if (!selectedSongId) return
    setUrlLookupLoading(prev => ({ ...prev, youtube: true }))
    setUrlLookupError(prev => ({ ...prev, youtube: null }))
    try {
      const result = await api.findYouTubeUrl(selectedSongId)
      if (result.url) {
        setSongDraft(prev => prev ? { ...prev, youtube_url: result.url! } : prev)
      } else {
        setUrlLookupError(prev => ({ ...prev, youtube: 'No match found' }))
      }
    } catch (err) {
      setUrlLookupError(prev => ({ ...prev, youtube: err instanceof Error ? err.message : 'Search failed' }))
    } finally {
      setUrlLookupLoading(prev => ({ ...prev, youtube: false }))
    }
  }, [selectedSongId])

  const handleFindAppleMusicUrl = useCallback(async () => {
    if (!selectedSongId) return
    setUrlLookupLoading(prev => ({ ...prev, appleMusic: true }))
    setUrlLookupError(prev => ({ ...prev, appleMusic: null }))
    try {
      const result = await api.findAppleMusicUrl(selectedSongId)
      if (result.url) {
        setSongDraft(prev => prev ? { ...prev, apple_music_url: result.url! } : prev)
      } else {
        setUrlLookupError(prev => ({ ...prev, appleMusic: 'No match found' }))
      }
    } catch (err) {
      setUrlLookupError(prev => ({ ...prev, appleMusic: err instanceof Error ? err.message : 'Search failed' }))
    } finally {
      setUrlLookupLoading(prev => ({ ...prev, appleMusic: false }))
    }
  }, [selectedSongId])

  const handleSaveLyrics = useCallback(async () => {
    if (!selectedSongId) return
    setLyricsSaving(true)
    setSongError(null)
    try {
      // If a target lang is active, save translations to LineTranslation table.
      if (lyricsTargetLang && Object.keys(lyricsTranslations).length > 0) {
        await api.updateSongTranslations(
          selectedSongId,
          lyricsTargetLang,
          Object.entries(lyricsTranslations).map(([id, text]) => ({ id: Number(id), text })),
        )
        // Check if this is a new target lang and update available list.
        setAvailableTargetLangs(prev => prev.includes(lyricsTargetLang) ? prev : [...prev, lyricsTargetLang].sort())
      }

      // Always save timing/original/phonetic changes via the default endpoint.
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
  }, [lyricsDraft, lyricsSource, lyricsTargetLang, lyricsTranslations, selectedSongId])

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
        target_langs: playlistDraft.target_langs,
        is_hidden: playlistDraft.is_hidden,
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
        target_langs: newPlaylistDraft.target_langs,
        is_hidden: newPlaylistDraft.is_hidden,
      })
      await onRefreshPlaylists()
      setSelectedPlaylistId(created.id)
      setNewPlaylistDraft(emptyPlaylistDraft())
      setNewPlaylistTargetLangsText('')
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

  const handleUploadCover = useCallback(async (file: File) => {
    if (!selectedPlaylistId) return
    setCoverUploading(true)
    setPlaylistError(null)
    try {
      await api.uploadPlaylistCover(selectedPlaylistId, file)
      setCoverKey(k => k + 1)
      const refreshed = await api.getPlaylist(selectedPlaylistId)
      setPlaylistDetail(refreshed)
      await onRefreshPlaylists()
    } catch (error) {
      setPlaylistError(error instanceof Error ? error.message : 'Failed to upload cover')
    } finally {
      setCoverUploading(false)
    }
  }, [selectedPlaylistId, onRefreshPlaylists])

  const handleDeleteCover = useCallback(async () => {
    if (!selectedPlaylistId) return
    setCoverUploading(true)
    setPlaylistError(null)
    try {
      await api.deletePlaylistCover(selectedPlaylistId)
      setCoverKey(k => k + 1)
      const refreshed = await api.getPlaylist(selectedPlaylistId)
      setPlaylistDetail(refreshed)
      await onRefreshPlaylists()
    } catch (error) {
      setPlaylistError(error instanceof Error ? error.message : 'Failed to remove cover')
    } finally {
      setCoverUploading(false)
    }
  }, [selectedPlaylistId, onRefreshPlaylists])

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
          <button type="button" onClick={() => openTab('localizations')} className={`rounded-xl border px-4 py-2 text-sm font-semibold transition-colors ${tabButtonClass(tab === 'localizations')}`}>Localizations</button>
          <button type="button" onClick={() => openTab('reports')} className={`rounded-xl border px-4 py-2 text-sm font-semibold transition-colors ${tabButtonClass(tab === 'reports')}`}>Reports</button>
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
                    <p className="text-xs text-gray-500 truncate">{playlist.song_count} songs{playlist.is_hidden ? ' · hidden' : ''}</p>
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
                      <button
                        type="button"
                        onClick={() => void handleQueueTask()}
                        disabled={!selectedSongId || !adminSong || selectedSongTaskLoading || songSaving || selectedSongTask?.status === 'pending' || selectedSongTask?.status === 'processing'}
                        className="rounded-xl border border-amber-700/60 bg-amber-950/20 px-4 py-2 text-sm font-semibold text-amber-300 hover:bg-amber-950/40 disabled:border-gray-800 disabled:text-gray-500"
                      >
                        {selectedSongTaskLoading ? 'Queuing…' : 'Regenerate Lyrics'}
                      </button>
                      <button type="button" onClick={() => void handleSaveSong()} disabled={!selectedSongId || !songDraft || songSaving} className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:bg-gray-800 disabled:text-gray-500">{songSaving ? 'Saving...' : 'Save Song'}</button>
                      <button type="button" onClick={() => void handleDeleteSong()} disabled={!selectedSongId || songSaving} className="rounded-xl border border-red-900/60 px-4 py-2 text-sm font-semibold text-red-300 hover:bg-red-950/30 disabled:border-gray-800 disabled:text-gray-600">Delete</button>
                    </div>
                  </div>
                  {songError && <div className="mb-4 rounded-xl border border-red-900/50 bg-red-950/20 px-4 py-3 text-sm text-red-400">{songError}</div>}
                  {selectedSongTask && (
                    <div className={`mb-4 rounded-xl border px-4 py-3 text-sm flex items-start justify-between gap-3 ${
                      selectedSongTask.status === 'done'       ? 'border-emerald-800/50 bg-emerald-950/20 text-emerald-300' :
                      selectedSongTask.status === 'failed'     ? 'border-red-900/50 bg-red-950/20 text-red-400' :
                      selectedSongTask.status === 'processing' ? 'border-indigo-800/50 bg-indigo-950/20 text-indigo-300' :
                                                                 'border-amber-900/50 bg-amber-950/20 text-amber-300'
                    }`}>
                      <div className="min-w-0">
                        <span className="font-semibold capitalize mr-2">{selectedSongTask.status}</span>
                        <span className="text-xs opacity-70">Task #{selectedSongTask.id} · {new Date(selectedSongTask.created_at * 1000).toLocaleString()}</span>
                        {selectedSongTask.status === 'failed' && selectedSongTask.error && (
                          <p className="mt-1 text-xs opacity-80 break-all">{selectedSongTask.error}</p>
                        )}
                        {selectedSongTask.status === 'done' && (
                          <p className="mt-1 text-xs opacity-70">Song data updated by worker. Reload to see new lyrics.</p>
                        )}
                        {(selectedSongTask.status === 'pending' || selectedSongTask.status === 'processing') && (
                          <p className="mt-1 text-xs opacity-70 animate-pulse">Worker will pick this up on next poll…</p>
                        )}
                      </div>
                      {selectedSongTask.status === 'done' && (
                        <button
                          type="button"
                          onClick={() => { setSelectedSongTask(null); setSelectedSongId(prev => { setTimeout(() => setSelectedSongId(prev), 0); return null }) }}
                          className="shrink-0 rounded-lg border border-emerald-700/50 px-3 py-1 text-xs font-semibold hover:bg-emerald-900/30"
                        >Reload</button>
                      )}
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
                        <div className="space-y-1">
                          <span className="text-xs text-gray-500">YouTube URL</span>
                          <div className="flex gap-2 mt-1">
                            <input value={songDraft.youtube_url} onChange={e => setSongDraft(prev => prev ? { ...prev, youtube_url: e.target.value } : prev)} className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                            <button type="button" onClick={() => void handleFindYouTubeUrl()} disabled={urlLookupLoading.youtube} title="Search YouTube for a match" className="shrink-0 rounded-lg border border-gray-700 px-3 py-1 text-xs text-gray-300 hover:border-indigo-500 hover:text-indigo-300 disabled:opacity-50">{urlLookupLoading.youtube ? '…' : 'Find'}</button>
                          </div>
                          {urlLookupError.youtube && <p className="text-xs text-red-400">{urlLookupError.youtube}</p>}
                        </div>
                        <div className="space-y-1">
                          <span className="text-xs text-gray-500">Apple Music URL</span>
                          <div className="flex gap-2 mt-1">
                            <input value={songDraft.apple_music_url} onChange={e => setSongDraft(prev => prev ? { ...prev, apple_music_url: e.target.value } : prev)} className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                            <button type="button" onClick={() => void handleFindAppleMusicUrl()} disabled={urlLookupLoading.appleMusic} title="Search iTunes for a match" className="shrink-0 rounded-lg border border-gray-700 px-3 py-1 text-xs text-gray-300 hover:border-indigo-500 hover:text-indigo-300 disabled:opacity-50">{urlLookupLoading.appleMusic ? '…' : 'Find'}</button>
                          </div>
                          {urlLookupError.appleMusic && <p className="text-xs text-red-400">{urlLookupError.appleMusic}</p>}
                        </div>
                      </div>
                      <label className="block text-xs text-gray-500">Target langs <span className="text-gray-600">(comma-separated)</span><input value={songTargetLangsText} onChange={e => { setSongTargetLangsText(e.target.value); setSongDraft(prev => prev ? { ...prev, target_langs: e.target.value.split(',').map(s => s.trim().toUpperCase()).filter(Boolean) } : prev) }} placeholder="e.g. EN, DE" className="mt-1 w-full rounded-xl border border-indigo-700/60 bg-indigo-950/20 px-3 py-2 text-sm text-indigo-200 placeholder-indigo-800 focus:outline-none focus:border-indigo-400" /></label>
                      <div>
                        <p className="text-xs text-gray-500 mb-2">Playlist membership</p>
                        <div className="grid gap-2 md:grid-cols-2">
                          {adminPlaylists.map(playlist => (
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
                    <div className="flex items-center gap-2">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs text-gray-500">Target language:</span>
                        <select
                          value={lyricsTargetLang}
                          onChange={e => setLyricsTargetLang(e.target.value)}
                          className="rounded-lg border border-indigo-700/50 bg-indigo-950/20 px-2 py-1 text-xs text-indigo-200 focus:outline-none focus:border-indigo-400"
                        >
                          <option value="">— default —</option>
                          {availableTargetLangs.map(lang => {
                            const preset = LANGUAGE_PRESETS.find(p => p.code.toUpperCase() === lang)
                            return (
                              <option key={lang} value={lang}>{preset ? `${preset.name} (${lang})` : lang}</option>
                            )
                          })}
                          {LANGUAGE_PRESETS.filter(p => !availableTargetLangs.includes(p.code.toUpperCase())).map(p => (
                            <option key={p.code} value={p.code.toUpperCase()}>+ {p.name} ({p.code.toUpperCase()})</option>
                          ))}
                        </select>
                        {lyricsTranslationsLoading && <span className="text-[10px] text-indigo-400 animate-pulse">loading…</span>}
                      </div>
                      <button type="button" onClick={() => void handleSaveLyrics()} disabled={!selectedSongId || lyricsSaving || !lyricsDraft.length} className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:bg-gray-800 disabled:text-gray-500">{lyricsSaving ? 'Saving...' : 'Save Lyrics'}</button>
                    </div>
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
                        {lyricsTargetLang ? (
                          <textarea
                            value={lyricsTranslations[line.id] ?? ''}
                            onChange={e => setLyricsTranslations(prev => ({ ...prev, [line.id]: e.target.value }))}
                            rows={2}
                            placeholder={`${lyricsTargetLang} translation…`}
                            className="w-full rounded-xl border border-indigo-700/60 bg-indigo-950/20 px-3 py-2 text-sm text-indigo-100 placeholder-indigo-800 focus:outline-none focus:border-indigo-400"
                          />
                        ) : (
                          <textarea
                            value={line.translation}
                            onChange={e => setLyricsDraft(prev => prev.map((item, itemIndex) => itemIndex === index ? { ...item, translation: e.target.value } : item))}
                            rows={2}
                            placeholder="Translation"
                            className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500"
                          />
                        )}
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
                      {adminPlaylists.map(playlist => (
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
                      {/* Cover image upload */}
                      <div className="flex items-center gap-3">
                        {playlistDetail?.cover_image_url && (
                          <img src={`${playlistDetail.cover_image_url}?k=${coverKey}`} alt="Cover" className="w-12 h-12 rounded-lg object-cover shrink-0" />
                        )}
                        <label className="flex-1 cursor-pointer rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-gray-400 hover:border-indigo-500 text-center select-none" style={{ opacity: coverUploading ? 0.5 : 1, pointerEvents: coverUploading ? 'none' : 'auto' }}>
                          {coverUploading ? 'Uploading…' : playlistDetail?.cover_image_url ? 'Replace Cover' : 'Upload Cover'}
                          <input type="file" accept="image/*" className="hidden" disabled={coverUploading} onChange={e => { const f = e.target.files?.[0]; if (f) void handleUploadCover(f); e.target.value = '' }} />
                        </label>
                        {playlistDetail?.cover_image_url && (
                          <button type="button" onClick={() => void handleDeleteCover()} disabled={coverUploading} className="rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-red-400 hover:border-red-500 disabled:opacity-50">Remove</button>
                        )}
                      </div>
                      <div className="grid gap-3 md:grid-cols-3">
                        <input value={playlistDraft.difficulty_level} onChange={e => setPlaylistDraft(prev => ({ ...prev, difficulty_level: e.target.value }))} placeholder="Difficulty level" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                        <input value={playlistDraft.language_code} onChange={e => setPlaylistDraft(prev => ({ ...prev, language_code: e.target.value }))} placeholder="Language code (e.g. ru)" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                        <label className="block text-xs text-gray-500">Target langs <span className="text-gray-600">(comma-separated)</span>
                          <input value={playlistTargetLangsText} onChange={e => { setPlaylistTargetLangsText(e.target.value); setPlaylistDraft(prev => ({ ...prev, target_langs: e.target.value.split(',').map(s => s.trim().toUpperCase()).filter(Boolean) })) }} placeholder="e.g. EN, DE" className="mt-1 w-full rounded-xl border border-indigo-700/60 bg-indigo-950/20 px-3 py-2 text-sm text-indigo-200 placeholder-indigo-800 focus:outline-none focus:border-indigo-400" />
                        </label>
                      </div>
                      <label className="flex items-center gap-3 cursor-pointer select-none">
                        <input type="checkbox" checked={playlistDraft.is_hidden} onChange={e => setPlaylistDraft(prev => ({ ...prev, is_hidden: e.target.checked }))} className="rounded border-gray-600 bg-gray-900 text-amber-500 focus:ring-amber-500" />
                        <span className="text-sm text-amber-300">Hidden <span className="text-xs text-gray-500 font-normal">(not shown to users)</span></span>
                      </label>
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
                    <div className="grid gap-3 md:grid-cols-3">
                      <input value={newPlaylistDraft.difficulty_level} onChange={e => setNewPlaylistDraft(prev => ({ ...prev, difficulty_level: e.target.value }))} placeholder="Difficulty level" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                      <input value={newPlaylistDraft.language_code} onChange={e => setNewPlaylistDraft(prev => ({ ...prev, language_code: e.target.value }))} placeholder="Language code (e.g. ru)" className="w-full rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500" />
                      <label className="block text-xs text-gray-500">Target langs <span className="text-gray-600">(comma-separated)</span>
                        <input value={newPlaylistTargetLangsText} onChange={e => { setNewPlaylistTargetLangsText(e.target.value); setNewPlaylistDraft(prev => ({ ...prev, target_langs: e.target.value.split(',').map(s => s.trim().toUpperCase()).filter(Boolean) })) }} placeholder="e.g. EN, DE" className="mt-1 w-full rounded-xl border border-indigo-700/60 bg-indigo-950/20 px-3 py-2 text-sm text-indigo-200 placeholder-indigo-800 focus:outline-none focus:border-indigo-400" />
                      </label>
                    </div>
                    <label className="flex items-center gap-3 cursor-pointer select-none">
                      <input type="checkbox" checked={newPlaylistDraft.is_hidden} onChange={e => setNewPlaylistDraft(prev => ({ ...prev, is_hidden: e.target.checked }))} className="rounded border-gray-600 bg-gray-900 text-amber-500 focus:ring-amber-500" />
                      <span className="text-sm text-amber-300">Hidden <span className="text-xs text-gray-500 font-normal">(not shown to users)</span></span>
                    </label>
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

            {tab === 'localizations' && (
              <LocalizationsTab />
            )}

            {tab === 'reports' && (
              <ReportsTab />
            )}
          </section>
        </div>
      </div>
    </div>
  )
}

function LocalizationsTab() {
  const [items, setItems] = useState<LocalizationItem[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [editKey, setEditKey] = useState<string | null>(null)
  const [editDraft, setEditDraft] = useState<{ en: string; tr: string; ru: string; es: string; pt: string; de: string }>({ en: '', tr: '', ru: '', es: '', pt: '', de: '' })
  const [saving, setSaving] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [newEn, setNewEn] = useState('')
  const [newTr, setNewTr] = useState('')
  const [newRu, setNewRu] = useState('')
  const [newEs, setNewEs] = useState('')
  const [newPt, setNewPt] = useState('')
  const [newDe, setNewDe] = useState('')
  const [addError, setAddError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    api.adminGetLocalizations()
      .then(rows => setItems(rows))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return items
    return items.filter(i => i.key.includes(q) || i.en.toLowerCase().includes(q) || i.tr.toLowerCase().includes(q) || i.ru.toLowerCase().includes(q) || i.es.toLowerCase().includes(q) || i.pt.toLowerCase().includes(q) || i.de.toLowerCase().includes(q))
  }, [items, search])

  function startEdit(item: LocalizationItem) {
    setEditKey(item.key)
    setEditDraft({ en: item.en, tr: item.tr, ru: item.ru, es: item.es, pt: item.pt, de: item.de })
  }

  async function saveEdit() {
    if (!editKey) return
    setSaving(true)
    try {
      await api.updateLocalization(editKey, editDraft)
      setItems(prev => prev.map(i => i.key === editKey ? { ...i, ...editDraft } : i))
      setEditKey(null)
    } catch {}
    setSaving(false)
  }

  async function handleAdd() {
    setAddError(null)
    if (!newKey.trim()) { setAddError('Key is required'); return }
    setSaving(true)
    try {
      const created = await api.upsertLocalization(newKey.trim(), { en: newEn, tr: newTr, ru: newRu, es: newEs, pt: newPt, de: newDe })
      setItems(prev => {
        const exists = prev.some(i => i.key === created.key)
        return exists ? prev.map(i => i.key === created.key ? created : i) : [...prev, created]
      })
      setNewKey(''); setNewEn(''); setNewTr(''); setNewRu(''); setNewEs(''); setNewPt(''); setNewDe('')
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : 'Failed to save')
    }
    setSaving(false)
  }

  async function handleDelete(key: string) {
    if (!confirm(`Delete localization key "${key}"?`)) return
    try {
      await api.deleteLocalization(key)
      setItems(prev => prev.filter(i => i.key !== key))
    } catch {}
  }

  const cellCls = 'px-3 py-2 text-sm text-gray-300 align-top'
  const inputCls = 'w-full rounded-lg border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-white focus:outline-none focus:border-indigo-500'

  return (
    <div className="rounded-3xl border border-gray-800/80 p-5 space-y-4" style={{ background: '#12121f' }}>
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-white font-semibold">Localizations</p>
          <p className="text-xs text-gray-500">{items.length} keys · EN / TR / RU / ES / PT / DE</p>
        </div>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search keys or text…"
          className="rounded-xl border border-gray-700 bg-gray-900 px-3 py-1.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500 w-64"
        />
      </div>

      {/* Add / overwrite key */}
      <div className="border border-gray-800 rounded-2xl p-4 space-y-2">
        <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Add / overwrite key</p>
        {addError && <p className="text-xs text-red-400">{addError}</p>}
        <div className="grid grid-cols-7 gap-2">
          <input value={newKey} onChange={e => setNewKey(e.target.value)} placeholder="key" className={inputCls} />
          <input value={newEn} onChange={e => setNewEn(e.target.value)} placeholder="EN" className={inputCls} />
          <input value={newTr} onChange={e => setNewTr(e.target.value)} placeholder="TR" className={inputCls} />
          <input value={newRu} onChange={e => setNewRu(e.target.value)} placeholder="RU" className={inputCls} />
          <input value={newEs} onChange={e => setNewEs(e.target.value)} placeholder="ES" className={inputCls} />
          <input value={newPt} onChange={e => setNewPt(e.target.value)} placeholder="PT" className={inputCls} />
          <input value={newDe} onChange={e => setNewDe(e.target.value)} placeholder="DE" className={inputCls} />
        </div>
        <button onClick={() => void handleAdd()} disabled={saving} className="rounded-xl bg-indigo-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50">
          {saving ? 'Saving…' : 'Add key'}
        </button>
      </div>

      {loading ? (
        <p className="text-gray-500 text-sm">Loading…</p>
      ) : (
        <div className="overflow-auto max-h-[60vh]">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase w-48">Key</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">EN</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">TR</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">RU</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">ES</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">PT</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">DE</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase w-24"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(item => (
                <tr key={item.key} className="border-b border-gray-800/50 hover:bg-gray-800/20">
                  <td className={`${cellCls} font-mono text-xs text-indigo-400`}>{item.key}</td>
                  {editKey === item.key ? (
                    <>
                      <td className={cellCls}><input value={editDraft.en} onChange={e => setEditDraft(d => ({ ...d, en: e.target.value }))} className={inputCls} /></td>
                      <td className={cellCls}><input value={editDraft.tr} onChange={e => setEditDraft(d => ({ ...d, tr: e.target.value }))} className={inputCls} /></td>
                      <td className={cellCls}><input value={editDraft.ru} onChange={e => setEditDraft(d => ({ ...d, ru: e.target.value }))} className={inputCls} /></td>
                      <td className={cellCls}><input value={editDraft.es} onChange={e => setEditDraft(d => ({ ...d, es: e.target.value }))} className={inputCls} /></td>
                      <td className={cellCls}><input value={editDraft.pt} onChange={e => setEditDraft(d => ({ ...d, pt: e.target.value }))} className={inputCls} /></td>
                      <td className={cellCls}><input value={editDraft.de} onChange={e => setEditDraft(d => ({ ...d, de: e.target.value }))} className={inputCls} /></td>
                      <td className={cellCls}>
                        <button onClick={() => void saveEdit()} disabled={saving} className="text-xs text-indigo-400 hover:text-indigo-300 mr-2 disabled:opacity-50">Save</button>
                        <button onClick={() => setEditKey(null)} className="text-xs text-gray-500 hover:text-gray-300">Cancel</button>
                      </td>
                    </>
                  ) : (
                    <>
                      <td className={cellCls}>{item.en}</td>
                      <td className={cellCls}>{item.tr}</td>
                      <td className={cellCls}>{item.ru}</td>
                      <td className={cellCls}>{item.es}</td>
                      <td className={cellCls}>{item.pt}</td>
                      <td className={cellCls}>{item.de}</td>
                      <td className={cellCls}>
                        <button onClick={() => startEdit(item)} className="text-xs text-gray-400 hover:text-white mr-2">Edit</button>
                        <button onClick={() => void handleDelete(item.key)} className="text-xs text-red-500 hover:text-red-400">Del</button>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function reportStatusClass(status: string): string {
  if (status === 'open') return 'border-amber-700/50 bg-amber-950/30 text-amber-300'
  if (status === 'resolved') return 'border-emerald-700/50 bg-emerald-950/30 text-emerald-300'
  return 'border-gray-700/50 bg-gray-900/30 text-gray-400'
}

function ReportsTab() {
  const [reports, setReports] = useState<AdminReport[]>([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState<string>('open')
  const [updatingId, setUpdatingId] = useState<number | null>(null)

  const load = useCallback((status: string) => {
    setLoading(true)
    api.listAdminReports(status === 'all' ? undefined : status)
      .then(rows => setReports(rows))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load(statusFilter) }, [load, statusFilter])

  async function setStatus(id: number, status: string) {
    setUpdatingId(id)
    try {
      const updated = await api.updateReportStatus(id, status)
      setReports(prev => prev.map(r => r.id === updated.id ? updated : r))
    } catch {}
    setUpdatingId(null)
  }

  const cellCls = 'px-3 py-2.5 text-sm text-gray-300 align-top'

  return (
    <div className="rounded-3xl border border-gray-800/80 p-5 space-y-4" style={{ background: '#12121f' }}>
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-white font-semibold">Reports</p>
          <p className="text-xs text-gray-500">{reports.length} report{reports.length !== 1 ? 's' : ''}</p>
        </div>
        <div className="flex gap-2">
          {(['open', 'resolved', 'dismissed', 'all'] as const).map(s => (
            <button
              key={s}
              type="button"
              onClick={() => setStatusFilter(s)}
              className={`rounded-xl border px-3 py-1 text-xs font-medium transition-colors capitalize ${statusFilter === s ? 'border-indigo-500 bg-indigo-950/40 text-white' : 'border-gray-800 bg-gray-950/30 text-gray-400 hover:border-gray-700 hover:text-gray-200'}`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <p className="text-gray-500 text-sm">Loading…</p>
      ) : reports.length === 0 ? (
        <p className="text-gray-600 text-sm">No reports.</p>
      ) : (
        <div className="overflow-auto max-h-[65vh]">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase w-20">Kind</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Word / Context</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Song</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">User</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Date</th>
                <th className="px-3 py-2 text-xs font-medium text-gray-500 uppercase w-36">Status</th>
              </tr>
            </thead>
            <tbody>
              {reports.map(r => (
                <tr key={r.id} className="border-b border-gray-800/50 hover:bg-gray-800/20">
                  <td className={cellCls}>
                    <span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${r.kind === 'word' ? 'border-violet-700/50 bg-violet-950/30 text-violet-300' : r.kind === 'line' ? 'border-blue-700/50 bg-blue-950/30 text-blue-300' : 'border-gray-700/50 bg-gray-900/30 text-gray-400'}`}>{r.kind}</span>
                  </td>
                  <td className={cellCls}>
                    {r.word && <p className="font-medium text-white">{r.word}{r.lemma ? ` · ${r.lemma}` : ''}</p>}
                    {r.context && <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{r.context}</p>}
                    {r.message && <p className="text-xs text-indigo-300 mt-0.5 italic">{r.message}</p>}
                  </td>
                  <td className={`${cellCls} text-xs text-gray-400`}>{r.song_title ?? '—'}</td>
                  <td className={`${cellCls} text-xs text-gray-400`}>{r.user_display_name ?? `#${r.user_id ?? '?'}`}</td>
                  <td className={`${cellCls} text-xs text-gray-500 whitespace-nowrap`}>{new Date(r.created_at * 1000).toLocaleDateString()}</td>
                  <td className={cellCls}>
                    <div className="flex flex-col gap-1">
                      <span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-semibold capitalize ${reportStatusClass(r.status)}`}>{r.status}</span>
                      {r.status !== 'resolved' && (
                        <button
                          type="button"
                          disabled={updatingId === r.id}
                          onClick={() => void setStatus(r.id, 'resolved')}
                          className="text-[10px] text-emerald-400 hover:text-emerald-300 disabled:opacity-50"
                        >Resolve</button>
                      )}
                      {r.status !== 'dismissed' && (
                        <button
                          type="button"
                          disabled={updatingId === r.id}
                          onClick={() => void setStatus(r.id, 'dismissed')}
                          className="text-[10px] text-gray-500 hover:text-gray-300 disabled:opacity-50"
                        >Dismiss</button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

