"""SQLAlchemy models and session management for FlowUp."""

from __future__ import annotations

import os
import time
from collections.abc import Generator

from sqlalchemy import Column, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./flowup.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)

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


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
