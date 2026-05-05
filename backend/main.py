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
import base64
import json
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from hashlib import pbkdf2_hmac
from pathlib import Path
from typing import Optional

import jwt as pyjwt
from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import Line, Playlist, PlaylistSong, Song, User, Word, create_tables, get_db
from models import (
    AdminLyricsUpdate,
    AdminSongDetailResponse,
    AdminSourceLyricsUpdate,
    AdminSourceLineUpdate,
    AdminUserResponse,
    AdminUserUpdate,
    AdminSongCreate,
    AdminSongUpdate,
    SourceLinesResponse,
    BulkSongSourcesUpdate,
    CompleteOnboardingRequest,
    CredentialLoginRequest,
    GoogleLoginRequest,
    LanguageIngest,
    LanguageResponse,
    LineResponse,
    PlaylistAddSong,
    PlaylistCreate,
    PlaylistResponse,
    PlaylistSongEntry,
    PlaylistSummaryResponse,
    PlaylistUpdate,
    SongDetailResponse,
    SongIngest,
    SongSourcesUpdate,
    SongSummaryResponse,
    UserResponse,
    UserSettings,
    UserSettingsUpdate,
    UserSyncRequest,
    WordResponse,
)
from openrussian import ensure_loaded as _load_or, lookup as _or_lookup
from spotify_auth import fetch_spotify_user, refresh_access_token
from google_auth import verify_google_id_token


# ── Startup ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    _seed_sample_data()
    _ensure_admin_user()
    _ensure_spotify_enabled_users()
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


def _ensure_admin_user() -> None:
    admin_email = os.environ.get("FLOWUP_ADMIN_EMAIL", "admin@flowup.local").strip().lower()
    admin_password = os.environ.get("FLOWUP_ADMIN_PASSWORD", "flowup-admin")
    admin_spotify_id = os.environ.get("FLOWUP_ADMIN_SPOTIFY_ID", "admin:local")
    if not admin_email or not admin_password:
        return

    db = next(get_db())
    try:
        user = db.query(User).filter(User.email == admin_email).first()
        if not user:
            user = User(
                spotify_id=admin_spotify_id,
                display_name="SingoLing Admin",
                email=admin_email,
                is_admin=1,
            )
            db.add(user)

        user.display_name = user.display_name or "SingoLing Admin"
        user.email = admin_email
        user.is_admin = 1
        if not user.password_hash:
            user.password_hash = _hash_password(admin_password)

        db.commit()
    finally:
        db.close()


def _ensure_spotify_enabled_users() -> None:
    """Grant spotify_enabled=1 to emails listed in FLOWUP_SPOTIFY_ENABLED_EMAILS."""
    raw = os.environ.get("FLOWUP_SPOTIFY_ENABLED_EMAILS", "")
    emails = [e.strip().lower() for e in raw.split(",") if e.strip()]
    if not emails:
        return
    db = next(get_db())
    try:
        for email in emails:
            user = db.query(User).filter(User.email == email).first()
            if user:
                user.spotify_enabled = 1
        db.commit()
    finally:
        db.close()


app = FastAPI(title="SingoLing API", version="0.2.0", lifespan=lifespan)

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


def _line_response(line: Line, override_words: Optional[list] = None) -> LineResponse:
    words = override_words if override_words is not None else line.words
    return LineResponse(
        id=line.id,
        position=line.position,
        start_time_ms=line.start_time_ms,
        end_time_ms=line.end_time_ms,
        original_line=line.original_line,
        phonetic_line=line.phonetic_line,
        translation=line.translation,
        words=[_word_response(w) for w in words],
        source=line.source,
    )


def _song_detail(song: Song, source: Optional[str] = None) -> SongDetailResponse:
    default_lines = [l for l in song.lines if l.source is None]

    if source and source != "default":
        source_lines = [l for l in song.lines if l.source == source]
        if source_lines:
            default_words_by_pos = {l.position: l.words for l in default_lines}
            lines = [
                _line_response(sl, override_words=default_words_by_pos.get(sl.position, []))
                for sl in sorted(source_lines, key=lambda l: l.position)
            ]
        else:
            lines = [_line_response(l) for l in default_lines]
    else:
        lines = [_line_response(l) for l in default_lines]

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
        lines=lines,
        youtube_url=song.youtube_url,
        apple_music_url=song.apple_music_url,
    )


def _admin_song_detail(song: Song, db: Session) -> AdminSongDetailResponse:
    playlist_ids = [
        playlist_id
        for (playlist_id,) in db.query(PlaylistSong.playlist_id).filter(PlaylistSong.song_id == song.id).all()
    ]
    default_words_by_pos = {l.position: l.words for l in song.lines if l.source is None}
    source_lines_map: dict[str, list[LineResponse]] = {}
    for line in song.lines:
        if line.source is not None:
            lr = _line_response(line, override_words=default_words_by_pos.get(line.position, []))
            source_lines_map.setdefault(line.source, []).append(lr)
    source_lines = [
        SourceLinesResponse(source=src, lines=sorted(lines, key=lambda l: l.position))
        for src, lines in source_lines_map.items()
    ]
    return AdminSongDetailResponse(**_song_detail(song).model_dump(), playlist_ids=playlist_ids, source_lines=source_lines)


def _admin_user_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        spotify_id=user.spotify_id,
        display_name=user.display_name,
        email=user.email,
        has_password=bool(user.password_hash),
        is_admin=bool(user.is_admin),
        created_at=user.created_at,
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
        youtube_url=body.youtube_url,
        apple_music_url=body.apple_music_url,
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


def _parse_user_settings(raw: Optional[str]) -> UserSettings:
    if not raw:
        return UserSettings()
    try:
        data = json.loads(raw)
        return UserSettings(**data)
    except Exception:
        return UserSettings()


def _hash_password(password: str, salt: bytes | None = None) -> str:
    actual_salt = salt or secrets.token_bytes(16)
    digest = pbkdf2_hmac("sha256", password.encode("utf-8"), actual_salt, 120_000)
    return f"pbkdf2_sha256$120000${base64.b64encode(actual_salt).decode()}${base64.b64encode(digest).decode()}"


def _verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    try:
        algo, iters, salt_b64, digest_b64 = stored_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
        return secrets.compare_digest(actual, expected)
    except Exception:
        return False


def _is_onboarding_required(user: User) -> bool:
    return not bool(user.email and user.password_hash)


def _require_admin(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Admin authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    try:
        scheme, encoded = authorization.split(" ", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Authorization header") from exc

    if scheme.lower() != "basic":
        raise HTTPException(
            status_code=401,
            detail="Basic authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
        email, password = decoded.split(":", 1)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid basic auth credentials") from exc

    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user or not _verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


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
            youtube_url=s.youtube_url,
            apple_music_url=s.apple_music_url,
        )
        for s in songs
    ]


@app.get("/api/songs/{song_id}", response_model=SongDetailResponse)
def get_song(song_id: int, source: Optional[str] = Query(default=None), db: Session = Depends(get_db)):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    return _song_detail(song, source=source)


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


@app.patch("/api/songs/{song_id}/sources", response_model=SongSummaryResponse)
def update_song_sources(song_id: int, body: SongSourcesUpdate, db: Session = Depends(get_db)):
    """Update YouTube / Apple Music URLs for a single song."""
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    # Support explicit nulls so clients can clear a stored source URL.
    if "youtube_url" in body.model_fields_set:
        song.youtube_url = body.youtube_url or None
    if "apple_music_url" in body.model_fields_set:
        song.apple_music_url = body.apple_music_url or None
    db.commit()
    db.refresh(song)
    return SongSummaryResponse(
        id=song.id,
        spotify_uri=song.spotify_uri,
        title=song.title,
        artist=song.artist,
        language_code=song.language_code,
        language_name=song.language_name,
        youtube_url=song.youtube_url,
        apple_music_url=song.apple_music_url,
    )


@app.post("/api/songs/bulk-sources")
def bulk_update_song_sources(body: BulkSongSourcesUpdate, db: Session = Depends(get_db)):
    """
    Bulk-update YouTube / Apple Music URLs from a CSV upload.
    Each entry maps a bare Spotify track ID to source URLs.
    Returns counts of updated and not-found songs.
    """
    updated = 0
    not_found = []
    for entry in body.songs:
        spotify_uri = f"spotify:track:{entry.spotify_id}"
        song = db.query(Song).filter(Song.spotify_uri == spotify_uri).first()
        if not song:
            not_found.append(entry.spotify_id)
            continue
        # Support explicit nulls so bulk updates can clear bad URLs.
        if "youtube_url" in entry.model_fields_set:
            song.youtube_url = entry.youtube_url or None
        if "apple_music_url" in entry.model_fields_set:
            song.apple_music_url = entry.apple_music_url or None
        updated += 1
    db.commit()
    return {"updated": updated, "not_found": not_found}


@app.post("/api/admin/songs", response_model=AdminSongDetailResponse, status_code=201)
def create_admin_song(body: AdminSongCreate, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    """Create a new song stub without lyrics (admin-only)."""
    spotify_uri = (body.spotify_uri or '').strip() or f"local:{uuid.uuid4().hex}"
    existing = db.query(Song).filter(Song.spotify_uri == spotify_uri).first()
    if existing:
        raise HTTPException(status_code=409, detail="spotify_uri already exists")
    song = Song(
        spotify_uri=spotify_uri,
        title=body.title.strip(),
        artist=body.artist.strip() if body.artist else None,
        language_code=body.language_code.strip() or "ru",
        language_name=body.language_name.strip() or "Russian",
        language_script=body.language_script,
        language_direction=body.language_direction,
        youtube_url=body.youtube_url or None,
        apple_music_url=body.apple_music_url or None,
    )
    db.add(song)
    db.flush()
    for pos, playlist_id in enumerate(body.playlist_ids):
        pl = db.get(Playlist, playlist_id)
        if pl:
            clash = db.query(PlaylistSong).filter_by(playlist_id=playlist_id, song_id=song.id).first()
            if not clash:
                db.add(PlaylistSong(playlist_id=playlist_id, song_id=song.id, position=pos))
    db.commit()
    db.refresh(song)
    return _admin_song_detail(song, db)


@app.delete("/api/admin/songs/{song_id}", status_code=204)
def delete_admin_song(song_id: int, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    db.delete(song)
    db.commit()


@app.get("/api/admin/songs/{song_id}", response_model=AdminSongDetailResponse)
def get_admin_song(song_id: int, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    return _admin_song_detail(song, db)


@app.patch("/api/admin/songs/{song_id}", response_model=AdminSongDetailResponse)
def update_admin_song(song_id: int, body: AdminSongUpdate, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    if "spotify_uri" in body.model_fields_set and body.spotify_uri:
        duplicate = db.query(Song).filter(Song.spotify_uri == body.spotify_uri, Song.id != song_id).first()
        if duplicate:
            raise HTTPException(status_code=409, detail="spotify_uri is already used by another song")
        song.spotify_uri = body.spotify_uri
    if "title" in body.model_fields_set and body.title is not None:
        song.title = body.title
    if "artist" in body.model_fields_set:
        song.artist = body.artist or None
    if "youtube_url" in body.model_fields_set:
        song.youtube_url = body.youtube_url or None
    if "apple_music_url" in body.model_fields_set:
        song.apple_music_url = body.apple_music_url or None

    if body.playlist_ids is not None:
        requested_ids = set(body.playlist_ids)
        existing_links = db.query(PlaylistSong).filter(PlaylistSong.song_id == song_id).all()
        existing_ids = {link.playlist_id for link in existing_links}

        for link in existing_links:
            if link.playlist_id not in requested_ids:
                db.delete(link)

        for playlist_id in requested_ids - existing_ids:
            playlist = db.get(Playlist, playlist_id)
            if not playlist:
                raise HTTPException(status_code=404, detail=f"Playlist {playlist_id} not found")
            db.add(PlaylistSong(
                playlist_id=playlist_id,
                song_id=song_id,
                position=len(playlist.playlist_songs),
            ))

    db.commit()
    db.refresh(song)
    return _admin_song_detail(song, db)


@app.put("/api/admin/songs/{song_id}/lyrics", response_model=AdminSongDetailResponse)
def update_admin_song_lyrics(song_id: int, body: AdminLyricsUpdate, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    """Update the default (Spotify-timed) lyrics in-place. Preserves word associations."""
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    # Only operate on default (source=None) lines so source-specific lines are untouched.
    default_lines = {line.id: line for line in song.lines if line.source is None}
    incoming_ids = [line.id for line in body.lines]
    if set(default_lines.keys()) != set(incoming_ids) or len(incoming_ids) != len(set(incoming_ids)):
        raise HTTPException(status_code=400, detail="Lyrics update must include every default line exactly once")

    for line_data in body.lines:
        line = default_lines[line_data.id]
        line.position = line_data.position
        line.start_time_ms = line_data.start_time_ms
        line.end_time_ms = line_data.end_time_ms
        line.original_line = line_data.original_line
        line.phonetic_line = line_data.phonetic_line
        line.translation = line_data.translation

    db.commit()
    db.refresh(song)
    return _admin_song_detail(song, db)


@app.put("/api/admin/songs/{song_id}/source-lyrics", response_model=AdminSongDetailResponse)
def update_source_lyrics(
    song_id: int,
    source: str = Query(..., description="Source key: 'youtube' or 'apple_music'"),
    body: AdminSourceLyricsUpdate = ...,
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    """Replace all lines for a specific source (youtube / apple_music). Fully idempotent."""
    if source in (None, "default", ""):
        raise HTTPException(status_code=400, detail="Use PUT /lyrics for the default source")
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    # Delete existing source-specific lines (words cascade automatically).
    for line in [l for l in song.lines if l.source == source]:
        db.delete(line)
    db.flush()

    # Insert new lines. Words are not stored here — they're borrowed from default lines at read time.
    for line_data in body.lines:
        db.add(Line(
            song_id=song_id,
            source=source,
            position=line_data.position,
            start_time_ms=line_data.start_time_ms,
            end_time_ms=line_data.end_time_ms,
            original_line=line_data.original_line,
            phonetic_line=line_data.phonetic_line,
            translation=line_data.translation,
        ))

    db.commit()
    db.refresh(song)
    return _admin_song_detail(song, db)


@app.get("/api/admin/users", response_model=list[AdminUserResponse])
def list_admin_users(db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    users = db.query(User).order_by(User.created_at.desc(), User.id.desc()).all()
    return [_admin_user_response(user) for user in users]


@app.get("/api/admin/users/{user_id}", response_model=AdminUserResponse)
def get_admin_user(user_id: int, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _admin_user_response(user)


@app.patch("/api/admin/users/{user_id}", response_model=AdminUserResponse)
def update_admin_user(user_id: int, body: AdminUserUpdate, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if "display_name" in body.model_fields_set:
        user.display_name = body.display_name or None

    if "email" in body.model_fields_set:
        next_email = (body.email or "").strip().lower() or None
        if next_email:
            existing = db.query(User).filter(User.email == next_email, User.id != user_id).first()
            if existing:
                raise HTTPException(status_code=409, detail="Email is already used by another account")
        user.email = next_email

    if "is_admin" in body.model_fields_set and body.is_admin is not None:
        user.is_admin = 1 if body.is_admin else 0

    if body.password is not None:
        if len(body.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
        user.password_hash = _hash_password(body.password)

    db.commit()
    db.refresh(user)
    return _admin_user_response(user)


# ── Playlists ──────────────────────────────────────────────────────────────────

def _playlist_song_entry(ps: PlaylistSong) -> PlaylistSongEntry | None:
    if ps.song is None:
        return None  # dangling FK — song was deleted without cascade
    return PlaylistSongEntry(
        position=ps.position,
        song_id=ps.song.id,
        spotify_uri=ps.song.spotify_uri,
        title=ps.song.title,
        artist=ps.song.artist,
    )


def _playlist_response(pl: Playlist) -> PlaylistResponse:
    songs = [e for ps in pl.playlist_songs if (e := _playlist_song_entry(ps)) is not None]
    return PlaylistResponse(
        id=pl.id,
        spotify_playlist_id=pl.spotify_playlist_id,
        name=pl.name,
        description=pl.description,
        difficulty_level=pl.difficulty_level,
        language_code=pl.language_code,
        song_count=pl.song_count,
        songs=songs,
    )


def _playlist_summary(pl: Playlist) -> PlaylistSummaryResponse:
    return PlaylistSummaryResponse(
        id=pl.id,
        spotify_playlist_id=pl.spotify_playlist_id,
        name=pl.name,
        description=pl.description,
        difficulty_level=pl.difficulty_level,
        language_code=pl.language_code,
        song_count=pl.song_count,
    )


@app.get("/api/playlists", response_model=list[PlaylistSummaryResponse])
def list_playlists(db: Session = Depends(get_db)):
    playlists = db.query(Playlist).order_by(Playlist.created_at.desc()).all()
    return [_playlist_summary(pl) for pl in playlists]


@app.post("/api/playlists", response_model=PlaylistResponse, status_code=201)
def create_playlist(body: PlaylistCreate, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    pl = Playlist(
        spotify_playlist_id=body.spotify_playlist_id,
        name=body.name,
        description=body.description,
        difficulty_level=body.difficulty_level,
        language_code=body.language_code,
    )
    db.add(pl)
    db.flush()

    for pos, song_id in enumerate(body.song_ids):
        song = db.get(Song, song_id)
        if not song:
            raise HTTPException(status_code=404, detail=f"Song {song_id} not found")
        db.add(PlaylistSong(playlist_id=pl.id, song_id=song_id, position=pos))

    db.commit()
    db.refresh(pl)
    return _playlist_response(pl)


@app.get("/api/playlists/{playlist_id}", response_model=PlaylistResponse)
def get_playlist(playlist_id: int, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return _playlist_response(pl)


@app.patch("/api/playlists/{playlist_id}", response_model=PlaylistResponse)
def update_playlist(playlist_id: int, body: PlaylistUpdate, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if body.name is not None:
        pl.name = body.name
    if body.description is not None:
        pl.description = body.description
    if body.difficulty_level is not None:
        pl.difficulty_level = body.difficulty_level
    if body.language_code is not None:
        pl.language_code = body.language_code
    db.commit()
    db.refresh(pl)
    return _playlist_response(pl)


@app.delete("/api/playlists/{playlist_id}", status_code=204)
def delete_playlist(playlist_id: int, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    db.delete(pl)
    db.commit()


@app.post("/api/playlists/{playlist_id}/songs", response_model=PlaylistResponse, status_code=201)
def add_song_to_playlist(playlist_id: int, body: PlaylistAddSong, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    song = db.get(Song, body.song_id)
    if not song:
        raise HTTPException(status_code=404, detail=f"Song {body.song_id} not found")
    # Check duplicate
    existing = db.query(PlaylistSong).filter_by(playlist_id=playlist_id, song_id=body.song_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Song already in playlist")
    pos = body.position if body.position is not None else (pl.song_count)
    db.add(PlaylistSong(playlist_id=pl.id, song_id=body.song_id, position=pos))
    db.commit()
    db.refresh(pl)
    return _playlist_response(pl)


@app.delete("/api/playlists/{playlist_id}/songs/{song_id}", response_model=PlaylistResponse)
def remove_song_from_playlist(playlist_id: int, song_id: int, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    ps = db.query(PlaylistSong).filter_by(playlist_id=playlist_id, song_id=song_id).first()
    if not ps:
        raise HTTPException(status_code=404, detail="Song not in playlist")
    db.delete(ps)
    db.commit()
    db.refresh(pl)
    return _playlist_response(pl)


# ── Users ──────────────────────────────────────────────────────────────────────

@app.post("/api/users/sync", response_model=UserResponse)
async def sync_user(body: UserSyncRequest, db: Session = Depends(get_db)):
    """Create or update a user record from their Spotify tokens."""
    user = db.query(User).filter(User.spotify_id == body.spotify_id).first()
    if not user:
        user = User(spotify_id=body.spotify_id)
        db.add(user)

    user.display_name     = body.display_name
    if body.email is not None:
        user.email = body.email
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
        has_password=bool(user.password_hash),
        needs_onboarding=_is_onboarding_required(user),
        is_admin=bool(user.is_admin),
        spotify_enabled=bool(user.spotify_enabled),
    )


@app.post("/api/auth/login", response_model=UserResponse)
async def login_with_credentials(body: CredentialLoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.strip().lower()).first()
    if not user or not _verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    return UserResponse(
        id=user.id,
        spotify_id=user.spotify_id,
        display_name=user.display_name,
        email=user.email,
        has_password=bool(user.password_hash),
        needs_onboarding=_is_onboarding_required(user),
        is_admin=bool(user.is_admin),
        spotify_enabled=bool(user.spotify_enabled),
    )


@app.post("/api/auth/complete-onboarding", response_model=UserResponse)
async def complete_onboarding(body: CompleteOnboardingRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.spotify_id == body.spotify_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    email = body.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    existing_email = db.query(User).filter(User.email == email, User.id != user.id).first()
    if existing_email:
        raise HTTPException(status_code=409, detail="Email is already used by another account")

    user.email = email
    user.password_hash = _hash_password(body.password)
    db.commit()
    db.refresh(user)

    return UserResponse(
        id=user.id,
        spotify_id=user.spotify_id,
        display_name=user.display_name,
        email=user.email,
        has_password=bool(user.password_hash),
        needs_onboarding=_is_onboarding_required(user),
        is_admin=bool(user.is_admin),
        spotify_enabled=bool(user.spotify_enabled),
    )


@app.get("/api/users/{spotify_id}/settings", response_model=UserSettings)
async def get_user_settings(spotify_id: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.spotify_id == spotify_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _parse_user_settings(user.settings_json)


@app.put("/api/users/{spotify_id}/settings", response_model=UserSettings)
async def update_user_settings(spotify_id: str, body: UserSettingsUpdate, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.spotify_id == spotify_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    current = _parse_user_settings(user.settings_json)
    patch = body.model_dump(exclude_unset=True)
    merged = UserSettings(
        exclude_stop_words_from_shortcuts=patch.get(
            "exclude_stop_words_from_shortcuts",
            current.exclude_stop_words_from_shortcuts,
        ),
        pause_on_inspect=patch.get(
            "pause_on_inspect",
            current.pause_on_inspect,
        ),
        last_playlist_id=patch.get(
            "last_playlist_id",
            current.last_playlist_id,
        ),
        last_song_id=patch.get(
            "last_song_id",
            current.last_song_id,
        ),
        preferred_source=patch.get(
            "preferred_source",
            current.preferred_source,
        ),
    )
    user.settings_json = merged.model_dump_json()
    db.commit()
    return merged


# ── Auth ───────────────────────────────────────────────────────────────────────

@app.post("/api/auth/google", response_model=UserResponse)
async def login_with_google(body: GoogleLoginRequest, db: Session = Depends(get_db)):
    """Verify a Google ID token and create/return the matching user."""
    try:
        claims = await verify_google_id_token(body.id_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    google_sub = claims["sub"]
    email = claims["email"].strip().lower()
    display_name = claims.get("name") or claims.get("email", "")

    # Synthetic stable ID for Google users so existing settings endpoints work.
    synthetic_id = f"google:{google_sub}"

    user = db.query(User).filter(User.spotify_id == synthetic_id).first()
    if not user:
        # Also try matching by email in case they previously used email/password.
        user = db.query(User).filter(User.email == email).first()

    if not user:
        user = User(
            spotify_id=synthetic_id,
            display_name=display_name,
            email=email,
        )
        db.add(user)
    else:
        # Keep google synthetic_id canonical going forward.
        if user.spotify_id != synthetic_id and not user.spotify_id.startswith("spotify:"):
            user.spotify_id = synthetic_id
        user.display_name = user.display_name or display_name
        if not user.email:
            user.email = email

    db.commit()
    db.refresh(user)
    return UserResponse(
        id=user.id,
        spotify_id=user.spotify_id,
        display_name=user.display_name,
        email=user.email,
        has_password=bool(user.password_hash),
        needs_onboarding=False,
        is_admin=bool(user.is_admin),
        spotify_enabled=bool(user.spotify_enabled),
    )


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


# ── Apple Music ─────────────────────────────────────────────────────────────────

# Cache: (token_str, expiry_unix_ts)
_apple_token_cache: tuple[str, int] | None = None


def _generate_apple_music_token() -> str:
    team_id     = os.environ.get("APPLE_MUSIC_TEAM_ID", "")
    key_id      = os.environ.get("APPLE_MUSIC_KEY_ID", "")
    private_key = os.environ.get("APPLE_MUSIC_PRIVATE_KEY", "").replace("\\n", "\n")

    if not (team_id and key_id and private_key):
        raise ValueError("Apple Music credentials not configured (APPLE_MUSIC_TEAM_ID / KEY_ID / PRIVATE_KEY)")

    now = int(time.time())
    exp = now + 15_552_000  # 180 days
    payload = {"iss": team_id, "iat": now, "exp": exp}
    token = pyjwt.encode(
        payload,
        private_key,
        algorithm="ES256",
        headers={"kid": key_id},
    )
    return token, exp  # type: ignore[return-value]


@app.get("/api/apple-music/token")
def get_apple_music_token():
    """Return a short-lived MusicKit developer token (cached until near expiry)."""
    global _apple_token_cache
    now = int(time.time())
    if _apple_token_cache is None or _apple_token_cache[1] < now + 3600:
        try:
            token, exp = _generate_apple_music_token()
            _apple_token_cache = (token, exp)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
    return {"token": _apple_token_cache[0]}
