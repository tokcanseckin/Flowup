"""
FlowUp Backend — FastAPI

Endpoints
─────────
GET  /api/health                health check
GET  /api/songs                 list all songs (summary)
GET  /api/songs/{song_id}       full song data with lines and words
POST /api/songs                 ingest a processed song (from pipeline)
GET  /api/songs/{song_id}/export  export song as JSON (same format as pipeline output)
POST /api/users/sync            create/update a user from their Spotify tokens
POST /api/auth/refresh          proxy Spotify token refresh

Start:
    cd backend
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import Line, Song, User, Word, create_tables, get_db
from models import (
    LanguageIngest,
    LanguageResponse,
    LineResponse,
    SongDetailResponse,
    SongIngest,
    SongSummaryResponse,
    UserResponse,
    UserSyncRequest,
    WordResponse,
)
from openrussian import ensure_loaded as _load_or, lookup as _or_lookup
from spotify_auth import fetch_spotify_user, refresh_access_token


# ── Startup ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    _seed_sample_data()
    # Pre-load OpenRussian index in a thread. If it fails, keep API alive.
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _load_or)
    except Exception as exc:
        print(f"[OpenRussian] Startup preload failed (non-fatal): {exc}")
    yield


def _seed_sample_data() -> None:
    """If the DB is empty, import the sample song_data.json from the frontend."""
    sample_path = Path(__file__).parent.parent / "frontend" / "src" / "data" / "song_data.json"
    if not sample_path.exists():
        return

    db = next(get_db())
    try:
        if db.query(Song).count() > 0:
            return  # Already seeded
        print("[DB] Seeding sample song from song_data.json …")
        data = json.loads(sample_path.read_text(encoding="utf-8"))
        _ingest_song(SongIngest(**data), db)
        print("[DB] Sample song seeded.")
    except Exception as exc:
        print(f"[DB] Seed failed (non-fatal): {exc}")
    finally:
        db.close()


app = FastAPI(title="FlowUp API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _enrich_definition(raw_def: Optional[str], lemma: str) -> Optional[str]:
    """Replace stub definitions (e.g. '[mesto]') with OpenRussian lookups."""
    if raw_def and raw_def.startswith("[") and raw_def.endswith("]"):
        live = _or_lookup(raw_def[1:-1]) or _or_lookup(lemma)
        return live or raw_def
    return raw_def or _or_lookup(lemma)


def _word_response(word: Word) -> WordResponse:
    return WordResponse(
        key=word.key_index,
        display_form=word.display_form,
        lemma=word.lemma,
        grammar=word.grammar,
        dictionary_definition=_enrich_definition(word.dictionary_definition, word.lemma),
    )


def _line_response(line: Line) -> LineResponse:
    return LineResponse(
        start_time_ms=line.start_time_ms,
        end_time_ms=line.end_time_ms,
        original_line=line.original_line,
        phonetic_line=line.phonetic_line,
        translation=line.translation,
        words=[_word_response(w) for w in line.words],
    )


def _song_detail(song: Song) -> SongDetailResponse:
    return SongDetailResponse(
        id=song.id,
        spotify_uri=song.spotify_uri,
        title=song.title,
        artist=song.artist,
        language=LanguageResponse(
            code=song.language_code,
            name=song.language_name,
            script=song.language_script,
            direction=song.language_direction,
        ),
        lines=[_line_response(ln) for ln in song.lines],
    )


def _ingest_song(body: SongIngest, db: Session) -> Song:
    """Insert or fully replace a song and all its lines/words."""
    existing = db.query(Song).filter(Song.spotify_uri == body.spotify_uri).first()
    if existing:
        db.delete(existing)
        db.flush()

    song = Song(
        spotify_uri=body.spotify_uri,
        title=body.title,
        artist=body.artist,
        language_code=body.language.code,
        language_name=body.language.name,
        language_script=body.language.script,
        language_direction=body.language.direction,
    )
    db.add(song)
    db.flush()  # get song.id

    for pos, line_data in enumerate(body.lines):
        line = Line(
            song_id=song.id,
            position=pos,
            start_time_ms=line_data.start_time_ms,
            end_time_ms=line_data.end_time_ms,
            original_line=line_data.original_line,
            phonetic_line=line_data.phonetic_line,
            translation=line_data.translation,
        )
        db.add(line)
        db.flush()  # get line.id

        for word_data in line_data.words:
            db.add(Word(
                line_id=line.id,
                key_index=word_data.key,
                display_form=word_data.display_form,
                lemma=word_data.lemma,
                grammar=word_data.grammar,
                dictionary_definition=word_data.dictionary_definition,
            ))

    db.commit()
    db.refresh(song)
    return song


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


# ── Songs ──────────────────────────────────────────────────────────────────────

@app.get("/api/songs", response_model=list[SongSummaryResponse])
def list_songs(db: Session = Depends(get_db)):
    songs = db.query(Song).order_by(Song.created_at.desc()).all()
    return [
        SongSummaryResponse(
            id=s.id,
            spotify_uri=s.spotify_uri,
            title=s.title,
            artist=s.artist,
            language_code=s.language_code,
            language_name=s.language_name,
        )
        for s in songs
    ]


@app.get("/api/songs/{song_id}", response_model=SongDetailResponse)
def get_song(song_id: int, db: Session = Depends(get_db)):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    return _song_detail(song)


@app.post("/api/songs", response_model=SongDetailResponse, status_code=201)
def create_song(body: SongIngest, db: Session = Depends(get_db)):
    """
    Ingest a processed song from the pipeline.
    If the spotify_uri already exists it is fully replaced.
    """
    song = _ingest_song(body, db)
    return _song_detail(song)


@app.delete("/api/songs/{song_id}", status_code=204)
def delete_song(song_id: int, db: Session = Depends(get_db)):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    db.delete(song)
    db.commit()


@app.get("/api/songs/{song_id}/export")
def export_song(song_id: int, db: Session = Depends(get_db)):
    """Export song as the same JSON schema the pipeline produces."""
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    detail = _song_detail(song)
    return JSONResponse(content=detail.model_dump())


# ── Users ──────────────────────────────────────────────────────────────────────

@app.post("/api/users/sync", response_model=UserResponse)
async def sync_user(body: UserSyncRequest, db: Session = Depends(get_db)):
    """Create or update a user record from their Spotify tokens."""
    user = db.query(User).filter(User.spotify_id == body.spotify_id).first()
    if not user:
        user = User(spotify_id=body.spotify_id)
        db.add(user)

    user.display_name     = body.display_name
    user.email            = body.email
    user.access_token     = body.access_token
    user.refresh_token    = body.refresh_token
    user.token_expires_at = int(time.time()) + body.expires_in - 60

    db.commit()
    db.refresh(user)
    return UserResponse(
        id=user.id,
        spotify_id=user.spotify_id,
        display_name=user.display_name,
        email=user.email,
    )


# ── Auth ───────────────────────────────────────────────────────────────────────

@app.post("/api/auth/refresh")
async def refresh_token_endpoint(body: dict):
    """Proxy Spotify token refresh so client_id can be server-side if desired."""
    refresh_tok = body.get("refresh_token")
    if not refresh_tok:
        raise HTTPException(status_code=400, detail="refresh_token is required")
    try:
        tokens = await refresh_access_token(refresh_tok)
        return tokens
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
