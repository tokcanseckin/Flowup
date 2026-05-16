"""SQLAlchemy models and session management for SingoLing."""

from __future__ import annotations

import os
import time
from collections.abc import Generator

from sqlalchemy import Boolean, Column, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, deferred, relationship, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required")
# UpCloud (and Heroku) issue "postgres://" but SQLAlchemy 2.x requires "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, echo=False)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id               = Column(Integer, primary_key=True)
    spotify_id       = Column(String(64),  unique=True, nullable=False)
    display_name     = Column(String(255), nullable=True)
    email            = Column(String(255), nullable=True)
    access_token     = Column(Text,        nullable=True)
    refresh_token    = Column(Text,        nullable=True)
    token_expires_at = Column(Integer,     nullable=True)  # Unix seconds
    password_hash           = Column(Text,        nullable=True)
    settings_json           = Column(Text,        nullable=True)
    is_admin                = Column(Integer,     nullable=False, default=0)
    spotify_enabled         = Column(Integer,     nullable=False, default=0)
    apple_music_user_token  = Column(Text,        nullable=True)
    google_user_id          = Column(String(128), nullable=True, unique=True)
    apple_user_id           = Column(String(128), nullable=True, unique=True)
    created_at              = Column(Integer,     default=lambda: int(time.time()))


class Song(Base):
    __tablename__ = "songs"

    id                 = Column(Integer,     primary_key=True)
    spotify_uri        = Column(String(128), unique=True, nullable=False)
    title              = Column(String(512), nullable=False)
    artist             = Column(String(512), nullable=True)
    language_code      = Column(String(8),   nullable=False)
    language_name      = Column(String(64),  nullable=False)
    language_script    = Column(String(32),  nullable=False, default="Latin")
    language_direction = Column(String(3),   nullable=False, default="ltr")
    youtube_url        = Column(Text,        nullable=True)
    apple_music_url    = Column(Text,        nullable=True)
    target_langs       = Column(Text,        nullable=False, server_default='[]', default='[]')
    created_at         = Column(Integer,     default=lambda: int(time.time()))

    lines = relationship(
        "Line",
        back_populates="song",
        order_by="Line.position",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Line(Base):
    __tablename__ = "lines"

    id            = Column(Integer, primary_key=True)
    song_id       = Column(Integer, ForeignKey("songs.id", ondelete="CASCADE"), nullable=False)
    # NULL = default / Spotify timing; 'youtube' | 'apple_music' = source-specific timing
    source        = Column(String(32), nullable=True, default=None)
    position      = Column(Integer, nullable=False)
    start_time_ms = Column(Integer, nullable=False)
    end_time_ms   = Column(Integer, nullable=False)
    original_line = Column(Text,    nullable=False)
    phonetic_line = Column(Text,    nullable=True)
    translation   = Column(Text,    nullable=False)

    song  = relationship("Song", back_populates="lines")
    words = relationship(
        "Word",
        back_populates="line",
        order_by="Word.key_index",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    translations = relationship(
        "LineTranslation",
        back_populates="line",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Word(Base):
    __tablename__ = "words"

    id                    = Column(Integer, primary_key=True)
    line_id               = Column(Integer, ForeignKey("lines.id", ondelete="CASCADE"), nullable=False)
    key_index             = Column(Integer, nullable=False)
    display_form          = Column(Text,    nullable=False)
    lemma                 = Column(Text,    nullable=False)
    grammar               = Column(Text,    nullable=True)
    dictionary_definition = Column(Text,    nullable=True)

    line = relationship("Line", back_populates="words")
    definitions = relationship(
        "WordDefinition",
        back_populates="word",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class LineTranslation(Base):
    """Per-target-language translation of a lyric line."""
    __tablename__ = "line_translations"
    __table_args__ = (UniqueConstraint("line_id", "target_lang", name="uq_line_translation"),)

    id          = Column(Integer, primary_key=True)
    line_id     = Column(Integer, ForeignKey("lines.id", ondelete="CASCADE"), nullable=False)
    target_lang = Column(String(16), nullable=False)   # e.g. "RU", "EN-US"
    text        = Column(Text,       nullable=False)

    line = relationship("Line", back_populates="translations")


class WordDefinition(Base):
    """Per-target-language dictionary definition of a word."""
    __tablename__ = "word_definitions"
    __table_args__ = (UniqueConstraint("word_id", "target_lang", name="uq_word_definition"),)

    id          = Column(Integer, primary_key=True)
    word_id     = Column(Integer, ForeignKey("words.id", ondelete="CASCADE"), nullable=False)
    target_lang = Column(String(16), nullable=False)
    definition  = Column(Text,       nullable=True)

    word = relationship("Word", back_populates="definitions")


class Playlist(Base):
    __tablename__ = "playlists"

    id                  = Column(Integer, primary_key=True)
    spotify_playlist_id = Column(String(128), unique=True, nullable=True)
    name                = Column(String(512), nullable=False)
    description         = Column(Text,        nullable=True)
    cover_image         = deferred(Column(LargeBinary, nullable=True))
    cover_image_type    = Column(String(64),  nullable=True)
    # CEFR-style level: A1, A2, B1, B2, C1, C2 — or a [[localization.key]]
    difficulty_level    = Column(String(512), nullable=True)
    language_code       = Column(String(8),   nullable=True)
    # Target language for translations/definitions in this playlist (e.g. "RU")
    target_lang         = Column(String(16),  nullable=True)
    # JSON array of supported target languages, e.g. '["ru", "en"]'
    target_langs        = Column(Text,        nullable=False, server_default='[]', default='[]')
    is_hidden           = Column(Boolean,     nullable=False, default=False, server_default='0')
    created_at          = Column(Integer,     default=lambda: int(time.time()))

    playlist_songs = relationship(
        "PlaylistSong",
        back_populates="playlist",
        order_by="PlaylistSong.position",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def song_count(self) -> int:
        return len(self.playlist_songs)


class PlaylistSong(Base):
    """Junction table: ordered list of songs within a playlist."""
    __tablename__ = "playlist_songs"
    __table_args__ = (UniqueConstraint("playlist_id", "song_id", name="uq_playlist_song"),)

    id          = Column(Integer, primary_key=True)
    playlist_id = Column(Integer, ForeignKey("playlists.id", ondelete="CASCADE"), nullable=False)
    song_id     = Column(Integer, ForeignKey("songs.id",     ondelete="CASCADE"), nullable=False)
    position    = Column(Integer, nullable=False, default=0)

    playlist = relationship("Playlist", back_populates="playlist_songs")
    song     = relationship("Song")


class UserFavorite(Base):
    """A user's favorited song."""
    __tablename__ = "user_favorites"
    __table_args__ = (UniqueConstraint("user_id", "song_id", name="uq_user_favorite"),)

    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    song_id    = Column(Integer, ForeignKey("songs.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(Integer, default=lambda: int(time.time()))


class UserListenedSong(Base):
    """A song the user has opened/listened to."""
    __tablename__ = "user_listened_songs"
    __table_args__ = (UniqueConstraint("user_id", "song_id", name="uq_user_listened"),)

    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    song_id    = Column(Integer, ForeignKey("songs.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(Integer, default=lambda: int(time.time()))


class UserWordLookup(Base):
    """A word the user has looked up while studying a song."""
    __tablename__ = "user_word_lookups"
    __table_args__ = (UniqueConstraint("user_id", "lemma", "language", "target_lang", name="uq_user_word_lookup"),)

    id           = Column(Integer,     primary_key=True)
    user_id      = Column(Integer,     ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    lemma        = Column(String(256), nullable=False)
    language     = Column(String(8),   nullable=False)   # source language (song language)
    target_lang  = Column(String(8),   nullable=False, server_default='en')   # definition language
    display_form = Column(String(256), nullable=False)
    definition   = Column(Text,        nullable=True)
    grammar      = Column(String(256), nullable=True)
    song_id      = Column(Integer,     ForeignKey("songs.id", ondelete="SET NULL"), nullable=True)
    looked_up_at = Column(Integer,     nullable=False, default=lambda: int(time.time()))


class AlignmentTask(Base):
    """
    A queued request for the Mac Mini alignment worker to process one song.

    Lifecycle: pending → processing → done | failed
    """
    __tablename__ = "alignment_tasks"

    id            = Column(Integer,     primary_key=True)
    # pending | processing | done | failed
    status        = Column(String(16),  nullable=False, default="pending")
    artist        = Column(String(512), nullable=False)
    title         = Column(String(512), nullable=False)
    display_title = Column(String(512), nullable=True)
    youtube_url   = Column(Text,        nullable=False)
    lang          = Column(String(8),   nullable=False, default="ru")
    spotify_uri   = Column(String(128), nullable=True)
    target_lang   = Column(String(8),   nullable=True,  default="EN-US")
    # Optional: plain lyrics pre-fetched by the task creator.
    # If absent, the worker fetches from LRCLIB itself.
    plain_lyrics  = Column(Text,        nullable=True)
    claimed_at    = Column(Integer,     nullable=True)
    completed_at  = Column(Integer,     nullable=True)
    result_lrc    = Column(Text,        nullable=True)
    error         = Column(Text,        nullable=True)
    created_at    = Column(Integer,     default=lambda: int(time.time()))


class Localization(Base):
    """UI localization strings stored per key, with text for each supported UI language."""
    __tablename__ = "localizations"

    key = Column(String(128), primary_key=True)
    en  = Column(Text, nullable=False, default='')
    tr  = Column(Text, nullable=False, default='')
    ru  = Column(Text, nullable=False, default='')
    es  = Column(Text, nullable=False, default='')
    pt  = Column(Text, nullable=False, default='')
    de  = Column(Text, nullable=False, default='')


class Report(Base):
    """User-submitted problem reports (word errors, song issues, etc.)."""
    __tablename__ = "reports"

    id         = Column(Integer, primary_key=True)
    kind       = Column(String(32), nullable=False)                                       # 'word' | 'line' | 'song'
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    song_id    = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"), nullable=True)
    word       = Column(Text, nullable=True)    # display_form for word reports
    lemma      = Column(Text, nullable=True)
    context    = Column(Text, nullable=True)    # original line text for context
    message    = Column(Text, nullable=True)    # optional user-supplied description
    status     = Column(String(16), nullable=False, default="open")  # open | resolved | dismissed
    created_at = Column(Integer, default=lambda: int(time.time()))


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_playlists_pg()


def _migrate_playlists_pg() -> None:
    """Add columns to playlists and songs on PostgreSQL (idempotent)."""
    statements = [
        "ALTER TABLE playlists ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE playlists ADD COLUMN IF NOT EXISTS target_langs TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE songs ADD COLUMN IF NOT EXISTS target_langs TEXT NOT NULL DEFAULT '[]'",
        "CREATE TABLE IF NOT EXISTS localizations (key TEXT PRIMARY KEY, en TEXT NOT NULL DEFAULT '', tr TEXT NOT NULL DEFAULT '', ru TEXT NOT NULL DEFAULT '')",
        (
            "CREATE TABLE IF NOT EXISTS reports ("
            "id SERIAL PRIMARY KEY, kind VARCHAR(32) NOT NULL, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,"
            " song_id INTEGER REFERENCES songs(id) ON DELETE SET NULL, word TEXT, lemma TEXT, context TEXT,"
            " message TEXT, status VARCHAR(16) NOT NULL DEFAULT 'open', created_at INTEGER)"
        ),
    ]
    for stmt in statements:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception:
            pass  # column already exists or other non-fatal error


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
