"""SQLAlchemy models and session management for SingoLing."""

from __future__ import annotations

import os
import time
from collections.abc import Generator

from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./flowup.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys = ON")
        dbapi_conn.execute("PRAGMA journal_mode = WAL")
        dbapi_conn.execute("PRAGMA synchronous = NORMAL")
        dbapi_conn.execute("PRAGMA mmap_size = 134217728")  # 128 MB OS page cache

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
    password_hash    = Column(Text,        nullable=True)
    settings_json    = Column(Text,        nullable=True)
    is_admin         = Column(Integer,     nullable=False, default=0)
    spotify_enabled  = Column(Integer,     nullable=False, default=0)
    created_at       = Column(Integer,     default=lambda: int(time.time()))


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


class Playlist(Base):
    __tablename__ = "playlists"

    id                  = Column(Integer, primary_key=True)
    spotify_playlist_id = Column(String(128), unique=True, nullable=True)
    name                = Column(String(512), nullable=False)
    description         = Column(Text,        nullable=True)
    # CEFR-style level: A1, A2, B1, B2, C1, C2
    difficulty_level    = Column(String(8),   nullable=True)
    language_code       = Column(String(8),   nullable=True)
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


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_users_table()


def _migrate_users_table() -> None:
    """Best-effort migration for SQLite users and songs table columns."""
    if not DATABASE_URL.startswith("sqlite"):
        return

    with engine.begin() as conn:
        # users table
        user_cols = {str(row[1]) for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        if "password_hash" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN password_hash TEXT"))
        if "settings_json" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN settings_json TEXT"))
        if "is_admin" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"))
        if "spotify_enabled" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN spotify_enabled INTEGER NOT NULL DEFAULT 0"))

        # songs table
        song_cols = {str(row[1]) for row in conn.execute(text("PRAGMA table_info(songs)")).fetchall()}
        if "youtube_url" not in song_cols:
            conn.execute(text("ALTER TABLE songs ADD COLUMN youtube_url TEXT"))
        if "apple_music_url" not in song_cols:
            conn.execute(text("ALTER TABLE songs ADD COLUMN apple_music_url TEXT"))

        # lines table
        line_cols = {str(row[1]) for row in conn.execute(text("PRAGMA table_info(lines)")).fetchall()}
        if "source" not in line_cols:
            conn.execute(text("ALTER TABLE lines ADD COLUMN source VARCHAR(32)"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
