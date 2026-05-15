"""Pydantic request/response models for the SingoLing API."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ── Response models ────────────────────────────────────────────────────────────

class WordResponse(BaseModel):
    key: int
    display_form: str
    lemma: str
    grammar: Optional[str]
    dictionary_definition: Optional[str]

    model_config = {"from_attributes": True}


class LineResponse(BaseModel):
    id: int
    position: int
    start_time_ms: int
    end_time_ms: int
    original_line: str
    phonetic_line: Optional[str]
    translation: str
    words: list[WordResponse]
    source: Optional[str] = None

    model_config = {"from_attributes": True}


class LanguageResponse(BaseModel):
    code: str
    name: str
    script: str
    direction: str


class SongSummaryResponse(BaseModel):
    id: int
    spotify_uri: str
    title: str
    artist: Optional[str]
    language_code: str
    language_name: str
    youtube_url: Optional[str] = None
    apple_music_url: Optional[str] = None


class SongDetailResponse(BaseModel):
    id: int
    spotify_uri: str
    title: str
    artist: Optional[str]
    language: LanguageResponse
    lines: list[LineResponse]
    youtube_url: Optional[str] = None
    apple_music_url: Optional[str] = None
    target_langs: list[str] = []


# ── Playlist models ────────────────────────────────────────────────────────────

class PlaylistSongEntry(BaseModel):
    """A song's summary as it appears inside a playlist response."""
    position: int
    song_id: int
    spotify_uri: str
    title: str
    artist: Optional[str]
    youtube_url: Optional[str] = None
    apple_music_url: Optional[str] = None

    model_config = {"from_attributes": True}


class PlaylistResponse(BaseModel):
    id: int
    spotify_playlist_id: Optional[str]
    name: str
    description: Optional[str]
    cover_image_url: Optional[str] = None
    difficulty_level: Optional[str]
    language_code: Optional[str]
    target_lang: Optional[str] = None
    target_langs: list[str] = []
    is_hidden: bool = False
    song_count: int
    songs: list[PlaylistSongEntry]

    model_config = {"from_attributes": True}


class PlaylistSummaryResponse(BaseModel):
    id: int
    spotify_playlist_id: Optional[str]
    name: str
    description: Optional[str]
    cover_image_url: Optional[str] = None
    difficulty_level: Optional[str]
    language_code: Optional[str]
    target_lang: Optional[str] = None
    target_langs: list[str] = []
    is_hidden: bool = False
    song_count: int

    model_config = {"from_attributes": True}


class PlaylistCreate(BaseModel):
    spotify_playlist_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    difficulty_level: Optional[str] = None  # A1 | A2 | B1 | B2 | C1 | C2
    language_code: Optional[str] = None
    target_lang: Optional[str] = None
    target_langs: list[str] = []
    is_hidden: bool = False
    song_ids: list[int] = []


class PlaylistUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    difficulty_level: Optional[str] = None
    language_code: Optional[str] = None
    target_lang: Optional[str] = None
    target_langs: Optional[list[str]] = None
    is_hidden: Optional[bool] = None


class PlaylistAddSong(BaseModel):
    song_id: int
    position: Optional[int] = None  # appended to end if omitted


class UserResponse(BaseModel):
    id: int
    spotify_id: str
    display_name: Optional[str]
    email: Optional[str]
    has_password: bool
    needs_onboarding: bool
    is_admin: bool
    spotify_enabled: bool = False
    apple_music_user_token: Optional[str] = None
    admin_token: Optional[str] = None

    model_config = {"from_attributes": True}


class AdminUserResponse(BaseModel):
    id: int
    spotify_id: str
    display_name: Optional[str]
    email: Optional[str]
    has_password: bool
    is_admin: bool
    created_at: int


class AdminUserUpdate(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None
    is_admin: Optional[bool] = None
    password: Optional[str] = None


# ── Request models ─────────────────────────────────────────────────────────────

class UserSyncRequest(BaseModel):
    spotify_id: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    access_token: str
    refresh_token: str
    expires_in: int  # seconds until expiry


class CredentialLoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    display_name: str
    email: str
    password: str


class GoogleLoginRequest(BaseModel):
    id_token: str


class AppleLoginRequest(BaseModel):
    id_token: str


class CompleteOnboardingRequest(BaseModel):
    spotify_id: str
    email: str
    password: str


class UserSettings(BaseModel):
    exclude_stop_words_from_shortcuts: bool = True
    pause_on_inspect: bool = True
    last_playlist_id: Optional[int] = None
    last_song_id: Optional[int] = None
    preferred_source: str = "spotify"  # "spotify" | "youtube" | "apple_music"


class UserSettingsUpdate(BaseModel):
    exclude_stop_words_from_shortcuts: Optional[bool] = None
    pause_on_inspect: Optional[bool] = None
    last_playlist_id: Optional[int] = None
    last_song_id: Optional[int] = None
    preferred_source: Optional[str] = None


class AppleMusicTokenRequest(BaseModel):
    token: Optional[str] = None  # None = clear


class WordIngest(BaseModel):
    key: int
    display_form: str
    lemma: str
    grammar: Optional[str] = None
    dictionary_definition: Optional[str] = None   # legacy / fallback (single target lang)
    definitions: dict[str, str] = {}              # {"RU": "...", "EN-US": "..."} multi-lang


class LineIngest(BaseModel):
    start_time_ms: int
    end_time_ms: int
    original_line: str
    phonetic_line: Optional[str] = None
    translation: str = ""              # legacy / fallback (single target lang)
    translations: dict[str, str] = {}  # {"RU": "...", "EN-US": "..."} multi-lang
    words: list[WordIngest]


class LanguageIngest(BaseModel):
    code: str
    name: str
    script: str = "Latin"
    direction: str = "ltr"


class SongIngest(BaseModel):
    """Schema the pipeline POSTs when pushing a processed song into the DB."""
    spotify_uri: str
    title: str
    artist: Optional[str] = None
    language: LanguageIngest
    lines: list[LineIngest]
    youtube_url: Optional[str] = None
    apple_music_url: Optional[str] = None


# ── Word lookup models ─────────────────────────────────────────────────────────

class WordLookupCreate(BaseModel):
    """Body for POST /api/me/word-lookups."""
    lemma: str
    language: str         # source language (song language code, e.g. "ru", "tr")
    target_lang: str      # definition language (e.g. "en", "tr")
    display_form: str
    definition: Optional[str] = None
    grammar: Optional[str] = None
    song_id: Optional[int] = None


class WordLookupResponse(BaseModel):
    """One entry returned by GET /api/me/word-lookups."""
    lemma: str
    language: str
    target_lang: str
    display_form: str
    definition: Optional[str] = None
    grammar: Optional[str] = None
    song_id: Optional[int] = None
    looked_up_at: int


class SongSourcesUpdate(BaseModel):
    """PATCH body to update alternative source URLs for a song."""
    youtube_url: Optional[str] = None
    apple_music_url: Optional[str] = None


class BulkSongSourcesEntry(BaseModel):
    spotify_id: str  # bare track ID (without 'spotify:track:' prefix)
    youtube_url: Optional[str] = None
    apple_music_url: Optional[str] = None


class BulkSongSourcesUpdate(BaseModel):
    songs: list[BulkSongSourcesEntry]


class AdminSongCreate(BaseModel):
    title: str
    artist: Optional[str] = None
    spotify_uri: Optional[str] = None  # auto-generated (local:{uuid}) if omitted
    language_code: str = "ru"
    language_name: str = "Russian"
    language_script: str = "Cyrillic"
    language_direction: str = "ltr"
    youtube_url: Optional[str] = None
    apple_music_url: Optional[str] = None
    playlist_ids: list[int] = []


class AdminSongUpdate(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    spotify_uri: Optional[str] = None
    youtube_url: Optional[str] = None
    apple_music_url: Optional[str] = None
    target_langs: Optional[list[str]] = None
    playlist_ids: Optional[list[int]] = None


class AdminLineUpdate(BaseModel):
    id: int
    position: int
    start_time_ms: int
    end_time_ms: int
    original_line: str
    phonetic_line: Optional[str] = None
    translation: str


class AdminLyricsUpdate(BaseModel):
    lines: list[AdminLineUpdate]


class AdminSongDetailResponse(SongDetailResponse):
    playlist_ids: list[int]
    source_lines: list["SourceLinesResponse"] = []


class SourceLinesResponse(BaseModel):
    source: str
    lines: list[LineResponse]


class AdminSourceLineUpdate(BaseModel):
    position: int
    start_time_ms: int
    end_time_ms: int
    original_line: str
    phonetic_line: Optional[str] = None
    translation: str


class AdminSourceLyricsUpdate(BaseModel):
    lines: list[AdminSourceLineUpdate]


class AdminLineTranslationUpdate(BaseModel):
    id: int
    text: str


class AdminTranslationsUpdate(BaseModel):
    lines: list[AdminLineTranslationUpdate]


# ── Alignment task (worker queue) ──────────────────────────────────────────────


class AlignmentTaskCreate(BaseModel):
    """Body for POST /api/admin/alignment-tasks — create a new alignment task."""
    artist: str
    title: str
    youtube_url: str
    lang: str = "ru"
    spotify_uri: Optional[str] = None
    display_title: Optional[str] = None
    target_lang: str = "EN-US"
    # Provide plain lyrics here to skip the worker's LRCLIB lookup.
    plain_lyrics: Optional[str] = None


class AlignmentTaskResponse(BaseModel):
    """Full task record returned to admins."""
    id: int
    status: str
    artist: str
    title: str
    display_title: Optional[str]
    youtube_url: str
    lang: str
    spotify_uri: Optional[str]
    target_lang: str
    plain_lyrics: Optional[str]
    claimed_at: Optional[int]
    completed_at: Optional[int]
    result_lrc: Optional[str]
    error: Optional[str]
    created_at: int

    model_config = {"from_attributes": True}


class WorkerTaskResponse(BaseModel):
    """Minimal task payload sent to the worker (excludes large fields)."""
    id: int
    artist: str
    title: str
    youtube_url: str
    lang: str
    plain_lyrics: Optional[str]

    model_config = {"from_attributes": True}


class WorkerResultSubmit(BaseModel):
    """Body for POST /api/worker/tasks/{id}/result — submit LRC or error."""
    lrc: Optional[str] = None
    error: Optional[str] = None


# ── Localization models ────────────────────────────────────────────────────────

class LocalizationItem(BaseModel):
    key: str
    en: str = ''
    tr: str = ''
    ru: str = ''

    model_config = {"from_attributes": True}


class LocalizationUpsert(BaseModel):
    """Create or update a single localization entry."""
    en: str = ''
    tr: str = ''
    ru: str = ''


# ── Report models ──────────────────────────────────────────────────────────────

class ReportCreate(BaseModel):
    """Body for POST /api/reports — submit a problem report."""
    kind: str                    # 'word' | 'line' | 'song'
    song_id: Optional[int] = None
    word: Optional[str] = None   # display_form for word reports
    lemma: Optional[str] = None
    context: Optional[str] = None  # surrounding line text
    message: Optional[str] = None  # optional user note


class AdminReportResponse(BaseModel):
    """Full report record returned to admins."""
    id: int
    kind: str
    user_id: Optional[int]
    user_display_name: Optional[str]
    song_id: Optional[int]
    song_title: Optional[str]
    word: Optional[str]
    lemma: Optional[str]
    context: Optional[str]
    message: Optional[str]
    created_at: int
    status: str


class ReportStatusUpdate(BaseModel):
    """Body for PATCH /api/admin/reports/{id} — update report status."""
    status: str  # 'open' | 'resolved' | 'dismissed'

