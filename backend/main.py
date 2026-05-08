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
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
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
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from database import AlignmentTask, Line, Playlist, PlaylistSong, Song, User, Word, create_tables, get_db
from models import (
    AdminLyricsUpdate,
    AdminSongDetailResponse,
    AdminSourceLyricsUpdate,
    AdminSourceLineUpdate,
    AdminUserResponse,
    AdminUserUpdate,
    AdminSongCreate,
    AdminSongUpdate,
    AlignmentTaskCreate,
    AlignmentTaskResponse,
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
    WorkerResultSubmit,
    WorkerTaskResponse,
)
from openrussian import ensure_loaded as _load_or, lookup as _or_lookup, lookup_local as _or_lookup_local
from spotify_auth import fetch_spotify_user, refresh_access_token
from google_auth import verify_google_id_token


# ── Server-side song cache ───────────────────────────────────────────────────
# Key: (song_id, source) where source is None for default/Spotify.
# Values: SongDetailResponse serialised as dict (JSON-ready, ~30 KB/song).
_song_response_cache: dict[tuple[int, Optional[str]], dict] = {}


def _cache_invalidate(song_id: int) -> None:
    """Remove all cached variants for a song. Call after any write."""
    keys = [k for k in _song_response_cache if k[0] == song_id]
    for k in keys:
        del _song_response_cache[k]


def _warm_song_cache() -> None:
    """Pre-populate the cache for every song in the DB at startup."""
    db = next(get_db())
    try:
        songs = db.query(Song).all()
        count = 0
        for song in songs:
            # Always cache the default view.
            detail = _song_detail(song, source=None)
            _song_response_cache[(song.id, None)] = detail.model_dump()
            count += 1
        print(f"[Cache] Warmed {count} songs.")
    except Exception as exc:
        print(f"[Cache] Warm-up failed (non-fatal): {exc}")
    finally:
        db.close()


# ── Startup ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    _seed_sample_data()
    _ensure_admin_user()
    _ensure_spotify_enabled_users()
    loop = asyncio.get_event_loop()
    # Pre-load OpenRussian index in a thread. If it fails, keep API alive.
    try:
        await loop.run_in_executor(None, _load_or)
    except Exception as exc:
        print(f"[OpenRussian] Startup preload failed (non-fatal): {exc}")
    # Warm the song cache so first requests are served from memory.
    try:
        await loop.run_in_executor(None, _warm_song_cache)
    except Exception as exc:
        print(f"[Cache] Startup warm failed (non-fatal): {exc}")
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


# ── Worker API key auth ────────────────────────────────────────────────────────

_WORKER_API_KEY = os.environ.get("WORKER_API_KEY", "").strip()


def _require_worker_key(
    x_worker_api_key: str | None = Header(default=None),
) -> None:
    """
    Dependency that validates the X-Worker-Api-Key header.
    Used on all /api/worker/* endpoints.
    """
    if not _WORKER_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Worker endpoint not configured (WORKER_API_KEY not set on server)",
        )
    if not x_worker_api_key:
        raise HTTPException(
            status_code=401,
            detail="X-Worker-Api-Key header is required",
        )
    if not secrets.compare_digest(
        x_worker_api_key.encode("utf-8"),
        _WORKER_API_KEY.encode("utf-8"),
    ):
        raise HTTPException(status_code=401, detail="Invalid worker API key")


# ── Internal helpers ───────────────────────────────────────────────────────────

def _strip_accents(s: str) -> str:
    """Strip combining accent marks so 'пи́сать' → 'писать' for dictionary lookup."""
    import unicodedata
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if not unicodedata.combining(c))


def _enrich_definition(raw_def: Optional[str], lemma: str) -> Optional[str]:
    """Replace stub definitions (e.g. '[mesto]') with OpenRussian lookups.

    Uses the local in-memory dict only (no Wiktionary network calls) so this
    function is always O(1) and never blocks a GET /songs/{id} request.
    """
    # Strip combining accents so a stressed lemma like 'пи́сать' looks up 'писать'
    bare_lemma = _strip_accents(lemma)
    if raw_def and raw_def.startswith("[") and raw_def.endswith("]"):
        live = _or_lookup_local(raw_def[1:-1]) or _or_lookup_local(bare_lemma)
        return live or raw_def
    return raw_def or _or_lookup_local(bare_lemma)


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
                # Resolve stubs at ingest time so the DB always has clean values.
                dictionary_definition=_enrich_definition(word_data.dictionary_definition, word_data.lemma),
            ))

    db.commit()
    db.refresh(song)
    return song


def _populate_source_lines(song: Song, db: Session) -> None:
    """Mirror default lyrics into source-specific rows for supported players."""
    default_lines = sorted([line for line in song.lines if line.source is None], key=lambda line: line.position)
    if not default_lines:
        return

    for source in ("youtube", "apple_music"):
        for existing in [line for line in song.lines if line.source == source]:
            db.delete(existing)
        db.flush()

        for line in default_lines:
            db.add(Line(
                song_id=song.id,
                source=source,
                position=line.position,
                start_time_ms=line.start_time_ms,
                end_time_ms=line.end_time_ms,
                original_line=line.original_line,
                phonetic_line=line.phonetic_line,
                translation=line.translation,
            ))


def _generate_song_with_pipeline(body: AdminSongCreate) -> SongIngest:
    """Run the pipeline script for a single song and return its ingest payload."""
    pipeline_script = Path(__file__).parent.parent / "pipeline" / "generate_song_data.py"
    if not pipeline_script.exists():
        raise HTTPException(status_code=500, detail="Pipeline script not found")

    spotify_uri = (body.spotify_uri or "").strip() or f"local:{uuid.uuid4().hex}"
    lang_code = body.language_code.strip() or "ru"
    artist = body.artist.strip() if body.artist else "Unknown Artist"
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        output_path = Path(tmp.name)

    command = [
        os.environ.get("PYTHON_EXECUTABLE", "python3"),
        str(pipeline_script),
        "--lang",
        lang_code,
        "--artist",
        artist,
        "--title",
        title,
        "--display-title",
        title,
        "--spotify-uri",
        spotify_uri,
        "--output",
        str(output_path),
    ]
    if body.youtube_url:
        command.extend(["--youtube-url", body.youtube_url])

    try:
        run = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(pipeline_script.parent),
            env=os.environ.copy(),
            check=False,
        )
        if run.returncode != 0:
            message = (run.stderr or run.stdout or "pipeline failed").strip()
            lower_message = message.lower()
            lyrics_not_found = (
                "could not retrieve synced lyrics" in lower_message
                or "no synced lyrics found" in lower_message
            )
            if not lyrics_not_found:
                raise HTTPException(status_code=502, detail=f"Lyrics generation failed: {message[:500]}")

            # Graceful fallback: still create a song so admins can edit/add lyrics manually.
            return SongIngest(
                spotify_uri=spotify_uri,
                title=title,
                artist=artist,
                language=LanguageIngest(
                    code=lang_code,
                    name=(body.language_name.strip() or "Unknown") if body.language_name else "Unknown",
                    script=body.language_script,
                    direction=body.language_direction,
                ),
                lines=[
                    {
                        "start_time_ms": 0,
                        "end_time_ms": 4000,
                        "original_line": "[Lyrics not found - add manually in Admin]",
                        "phonetic_line": None,
                        "translation": "[Lyrics not found - add manually in Admin]",
                        "words": [],
                    }
                ],
                youtube_url=body.youtube_url or None,
                apple_music_url=body.apple_music_url or None,
            )

        payload = json.loads(output_path.read_text(encoding="utf-8"))
        payload["youtube_url"] = body.youtube_url or None
        payload["apple_music_url"] = body.apple_music_url or None
        return SongIngest(**payload)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Lyrics generation timed out") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Lyrics generator produced invalid JSON") from exc
    finally:
        output_path.unlink(missing_ok=True)


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
    cache_key = (song_id, source or None)
    cached = _song_response_cache.get(cache_key)
    if cached is not None:
        return JSONResponse(
            content=cached,
            headers={"Cache-Control": "private, max-age=3600"},
        )
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    detail = _song_detail(song, source=source)
    data = detail.model_dump()
    _song_response_cache[cache_key] = data
    return JSONResponse(
        content=data,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.post("/api/songs", response_model=SongDetailResponse, status_code=201)
def create_song(body: SongIngest, db: Session = Depends(get_db)):
    """
    Ingest a processed song from the pipeline.
    If the spotify_uri already exists it is fully replaced.
    """
    song = _ingest_song(body, db)
    _cache_invalidate(song.id)
    detail = _song_detail(song)
    _song_response_cache[(song.id, None)] = detail.model_dump()
    return detail


@app.delete("/api/songs/{song_id}", status_code=204)
def delete_song(song_id: int, db: Session = Depends(get_db)):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    _cache_invalidate(song_id)
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
    _cache_invalidate(song_id)
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
    for entry in body.songs:
        spotify_uri = f"spotify:track:{entry.spotify_id}"
        s = db.query(Song).filter(Song.spotify_uri == spotify_uri).first()
        if s:
            _cache_invalidate(s.id)
    return {"updated": updated, "not_found": not_found}


@app.post("/api/admin/songs", response_model=AdminSongDetailResponse, status_code=201)
def create_admin_song(body: AdminSongCreate, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    ingest_payload = _generate_song_with_pipeline(body)
    song = _ingest_song(ingest_payload, db)

    for pos, playlist_id in enumerate(body.playlist_ids):
        pl = db.get(Playlist, playlist_id)
        if pl:
            clash = db.query(PlaylistSong).filter_by(playlist_id=playlist_id, song_id=song.id).first()
            if not clash:
                db.add(PlaylistSong(playlist_id=playlist_id, song_id=song.id, position=pos))

    _populate_source_lines(song, db)
    db.commit()
    db.refresh(song)
    _cache_invalidate(song.id)
    return _admin_song_detail(song, db)


@app.delete("/api/admin/songs/{song_id}", status_code=204)
def delete_admin_song(song_id: int, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    _cache_invalidate(song_id)
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
    _cache_invalidate(song_id)
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
    _cache_invalidate(song_id)
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
    _cache_invalidate(song_id)
    return _admin_song_detail(song, db)


@app.post("/api/admin/songs/{song_id}/regenerate")
async def regenerate_song_lyrics(
    song_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    """Re-run the full NLP pipeline for an existing song and replace all its lyrics."""
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    pipeline_script = Path(__file__).parent.parent / "pipeline" / "generate_song_data.py"
    if not pipeline_script.exists():
        raise HTTPException(status_code=500, detail="Pipeline script not found")

    # Capture all needed info before closing the DI session
    python_exe = os.environ.get("PYTHON_EXECUTABLE", "python3")
    lang_code = song.language_code
    artist = song.artist or "Unknown Artist"
    title = song.title
    spotify_uri = song.spotify_uri
    youtube_url = song.youtube_url
    script_dir = str(pipeline_script.parent)
    script_path = str(pipeline_script)
    env = os.environ.copy()

    async def event_stream():
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            output_path = Path(tmp.name)

        command = [
            python_exe, script_path,
            "--lang", lang_code,
            "--artist", artist,
            "--title", title,
            "--display-title", title,
            "--spotify-uri", spotify_uri,
            "--output", str(output_path),
        ]
        if youtube_url:
            command.extend(["--youtube-url", youtube_url])

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=script_dir,
                env=env,
            )

            async for raw in proc.stdout:
                line_text = raw.decode("utf-8", errors="replace").rstrip()
                if line_text:
                    safe = line_text.replace("\n", " ")
                    yield f"data: {safe}\n\n"

            await proc.wait()

            if proc.returncode != 0:
                yield f"event: error\ndata: Pipeline exited with code {proc.returncode}\n\n"
                return

            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError) as exc:
                yield f"event: error\ndata: Failed to read pipeline output: {exc}\n\n"
                return

            # Update DB with new lyrics using a fresh session
            from database import SessionLocal as _SessionLocal
            new_db = _SessionLocal()
            try:
                db_song = new_db.get(Song, song_id)
                if not db_song:
                    yield f"event: error\ndata: Song not found in database\n\n"
                    return

                # Delete all existing default lines (words cascade via FK)
                new_db.query(Line).filter(
                    Line.song_id == song_id,
                    Line.source.is_(None),
                ).delete(synchronize_session=False)
                new_db.flush()

                # Insert new default lines and words
                new_default_lines: list[Line] = []
                for pos, line_data in enumerate(payload.get("lines", [])):
                    new_line = Line(
                        song_id=song_id,
                        position=pos,
                        start_time_ms=line_data["start_time_ms"],
                        end_time_ms=line_data["end_time_ms"],
                        original_line=line_data["original_line"],
                        phonetic_line=line_data.get("phonetic_line"),
                        translation=line_data.get("translation", ""),
                    )
                    new_db.add(new_line)
                    new_db.flush()
                    new_default_lines.append(new_line)

                    for word_data in line_data.get("words", []):
                        new_db.add(Word(
                            line_id=new_line.id,
                            key_index=word_data.get("key", pos),
                            display_form=word_data.get("display_form", ""),
                            lemma=word_data.get("lemma", ""),
                            grammar=word_data.get("grammar"),
                            dictionary_definition=word_data.get("dictionary_definition"),
                        ))

                # Rebuild source-specific lines (youtube, apple_music) from new defaults
                for source in ("youtube", "apple_music"):
                    new_db.query(Line).filter(
                        Line.song_id == song_id,
                        Line.source == source,
                    ).delete(synchronize_session=False)
                    new_db.flush()
                    for dl in new_default_lines:
                        new_db.add(Line(
                            song_id=song_id,
                            source=source,
                            position=dl.position,
                            start_time_ms=dl.start_time_ms,
                            end_time_ms=dl.end_time_ms,
                            original_line=dl.original_line,
                            phonetic_line=dl.phonetic_line,
                            translation=dl.translation,
                        ))

                new_db.commit()
                new_db.refresh(db_song)
                result = _admin_song_detail(db_song, new_db)
                yield f"event: done\ndata: {result.model_dump_json()}\n\n"
            finally:
                new_db.close()

        except Exception as exc:
            yield f"event: error\ndata: {exc}\n\n"
        finally:
            output_path.unlink(missing_ok=True)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


# ── Worker task queue ──────────────────────────────────────────────────────────


@app.get("/api/worker/tasks/next", response_model=WorkerTaskResponse)
def worker_get_next_task(
    db: Session = Depends(get_db),
    _: None = Depends(_require_worker_key),
):
    """
    Atomically fetch the next pending alignment task and mark it as 'processing'.
    Returns HTTP 204 (no content) when the queue is empty.
    """
    task = (
        db.query(AlignmentTask)
        .filter(AlignmentTask.status == "pending")
        .order_by(AlignmentTask.created_at)
        .with_for_update(skip_locked=True)
        .first()
    )
    if task is None:
        from fastapi.responses import Response
        return Response(status_code=204)

    task.status = "processing"
    task.claimed_at = int(time.time())
    db.commit()
    db.refresh(task)
    return WorkerTaskResponse.model_validate(task)


@app.post("/api/worker/tasks/{task_id}/result")
def worker_submit_result(
    task_id: int,
    body: WorkerResultSubmit,
    db: Session = Depends(get_db),
    _: None = Depends(_require_worker_key),
):
    """
    Submit the alignment result (LRC string) or an error for a task.

    On success with an LRC, the backend automatically runs the full pipeline
    (translation + NLP) and ingests the finished song into the database.
    """
    task = db.get(AlignmentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in ("processing", "pending"):
        raise HTTPException(
            status_code=409,
            detail=f"Task is already in state '{task.status}'",
        )

    task.completed_at = int(time.time())

    if body.error:
        task.status = "failed"
        task.error = body.error
        db.commit()
        return {"status": "failed", "task_id": task_id}

    if not body.lrc:
        raise HTTPException(status_code=400, detail="Provide either 'lrc' or 'error'")

    task.status = "done"
    task.result_lrc = body.lrc
    db.commit()

    # ── Run the full pipeline (translation + NLP) and ingest the song ─────────
    pipeline_script = Path(__file__).parent.parent / "pipeline" / "generate_song_data.py"
    song_id: Optional[int] = None
    pipeline_error: Optional[str] = None

    if pipeline_script.exists():
        import tempfile as _tmpmod
        spotify_uri = task.spotify_uri or f"local:{uuid.uuid4().hex}"
        lang_code   = task.lang
        artist      = task.artist
        title       = task.display_title or task.title
        target_lang = task.target_lang or "EN-US"

        with _tmpmod.NamedTemporaryFile(suffix=".lrc", mode="w",
                                        encoding="utf-8", delete=False) as lf:
            lf.write(body.lrc)
            lrc_file = Path(lf.name)

        with _tmpmod.NamedTemporaryFile(suffix=".json", delete=False) as jf:
            out_path = Path(jf.name)

        try:
            cmd = [
                os.environ.get("PYTHON_EXECUTABLE", "python3"),
                str(pipeline_script),
                "--lang",         lang_code,
                "--artist",       artist,
                "--title",        title,
                "--display-title", title,
                "--spotify-uri",  spotify_uri,
                "--target-lang",  target_lang,
                "--lrc-file",     str(lrc_file),
                "--output",       str(out_path),
            ]
            run = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=600,
                cwd=str(pipeline_script.parent),
                env=os.environ.copy(),
                check=False,
            )
            if run.returncode == 0 and out_path.exists():
                payload = json.loads(out_path.read_text(encoding="utf-8"))
                payload.setdefault("youtube_url", task.youtube_url)
                ingest_payload = SongIngest(**payload)
                ingested = _ingest_song(ingest_payload, db)
                song_id = ingested.id
            else:
                pipeline_error = (run.stderr or run.stdout or "pipeline failed").strip()[:400]
        except Exception as exc:
            pipeline_error = str(exc)[:400]
        finally:
            lrc_file.unlink(missing_ok=True)
            out_path.unlink(missing_ok=True)
    else:
        pipeline_error = "Pipeline script not found on server"

    response: dict = {"status": "done", "task_id": task_id}
    if song_id is not None:
        response["song_id"] = song_id
    if pipeline_error:
        response["pipeline_warning"] = pipeline_error
    return response


# ── Admin: alignment task management ──────────────────────────────────────────


def _lrclib_synced(artist: str, title: str) -> Optional[str]:
    """Return synced LRC from LRCLIB if available, else None."""
    try:
        params = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
        req = urllib.request.Request(
            f"https://lrclib.net/api/search?{params}",
            headers={"User-Agent": "FlowUp/1.0 (https://singoling.com)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            hits = json.loads(resp.read().decode())
        if not hits:
            return None
        for hit in hits:
            if hit.get("syncedLyrics"):
                return hit["syncedLyrics"]
    except Exception:
        pass
    return None


def _run_pipeline_with_lrc(task: AlignmentTask, lrc: str, db: Session) -> dict:
    """Run the NLP pipeline with a ready LRC and ingest the song. Returns response dict."""
    pipeline_script = Path(__file__).parent.parent / "pipeline" / "generate_song_data.py"
    song_id: Optional[int] = None
    pipeline_error: Optional[str] = None

    if pipeline_script.exists():
        spotify_uri = task.spotify_uri or f"local:{uuid.uuid4().hex}"
        with tempfile.NamedTemporaryFile(suffix=".lrc", mode="w",
                                         encoding="utf-8", delete=False) as lf:
            lf.write(lrc)
            lrc_file = Path(lf.name)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as jf:
            out_path = Path(jf.name)
        try:
            cmd = [
                os.environ.get("PYTHON_EXECUTABLE", "python3"),
                str(pipeline_script),
                "--lang",          task.lang,
                "--artist",        task.artist,
                "--title",         task.display_title or task.title,
                "--display-title", task.display_title or task.title,
                "--spotify-uri",   spotify_uri,
                "--target-lang",   task.target_lang or "EN-US",
                "--lrc-file",      str(lrc_file),
                "--output",        str(out_path),
            ]
            run = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=600,
                cwd=str(pipeline_script.parent),
                env=os.environ.copy(),
                check=False,
            )
            if run.returncode == 0 and out_path.exists():
                payload = json.loads(out_path.read_text(encoding="utf-8"))
                payload.setdefault("youtube_url", task.youtube_url)
                ingested = _ingest_song(SongIngest(**payload), db)
                song_id = ingested.id
            else:
                pipeline_error = (run.stderr or run.stdout or "pipeline failed").strip()[:400]
        except Exception as exc:
            pipeline_error = str(exc)[:400]
        finally:
            lrc_file.unlink(missing_ok=True)
            out_path.unlink(missing_ok=True)
    else:
        pipeline_error = "Pipeline script not found on server"

    return {"song_id": song_id, "pipeline_error": pipeline_error}


@app.post(
    "/api/admin/alignment-tasks",
    response_model=AlignmentTaskResponse,
    status_code=201,
)
def create_alignment_task(
    body: AlignmentTaskCreate,
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    """
    Create an alignment task.

    If LRCLIB already has synced lyrics for this track the pipeline is run
    immediately on the server and the task is returned in 'done' state.
    Otherwise a 'pending' task is created for the worker.
    """
    now = int(time.time())
    task = AlignmentTask(
        status="pending",
        artist=body.artist,
        title=body.title,
        display_title=body.display_title,
        youtube_url=body.youtube_url,
        lang=body.lang,
        spotify_uri=body.spotify_uri,
        target_lang=body.target_lang,
        plain_lyrics=body.plain_lyrics,
    )

    # ── Check LRCLIB first ────────────────────────────────────────────────────
    synced_lrc = _lrclib_synced(body.artist, body.title)
    if synced_lrc:
        task.status = "done"
        task.result_lrc = synced_lrc
        task.completed_at = now
        db.add(task)
        db.commit()
        db.refresh(task)
        result = _run_pipeline_with_lrc(task, synced_lrc, db)
        if result["pipeline_error"]:
            # Pipeline failed but we still have the LRC — keep task as done,
            # admin will see song_id=None and can retry.
            task.error = result["pipeline_error"]
            db.commit()
        return AlignmentTaskResponse.model_validate(task)

    # ── No synced lyrics — queue for worker ───────────────────────────────────
    db.add(task)
    db.commit()
    db.refresh(task)
    return AlignmentTaskResponse.model_validate(task)


@app.get("/api/admin/alignment-tasks", response_model=list[AlignmentTaskResponse])
def list_alignment_tasks(
    status: Optional[str] = Query(default=None, description="Filter by status: pending|processing|done|failed"),
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    """List all alignment tasks, optionally filtered by status."""
    q = db.query(AlignmentTask).order_by(AlignmentTask.created_at.desc())
    if status:
        q = q.filter(AlignmentTask.status == status)
    return [AlignmentTaskResponse.model_validate(t) for t in q.all()]


@app.get("/api/admin/alignment-tasks/{task_id}", response_model=AlignmentTaskResponse)
def get_alignment_task(
    task_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    task = db.get(AlignmentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return AlignmentTaskResponse.model_validate(task)


@app.delete("/api/admin/alignment-tasks/{task_id}", status_code=204)
def delete_alignment_task(
    task_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    """Delete a task (cancel pending / remove failed)."""
    task = db.get(AlignmentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(task)
    db.commit()


@app.patch("/api/admin/alignment-tasks/{task_id}/retry", response_model=AlignmentTaskResponse)
def retry_alignment_task(
    task_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    """Reset a failed (or stuck processing) task back to pending so the worker retries it."""
    task = db.get(AlignmentTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status == "done":
        raise HTTPException(status_code=409, detail="Task already completed successfully")
    task.status = "pending"
    task.claimed_at = None
    task.completed_at = None
    task.error = None
    db.commit()
    db.refresh(task)
    return AlignmentTaskResponse.model_validate(task)


# ── Apple Music ─────────────────────────────────────────────────────────────────

# Cache: (token_str, expiry_unix_ts)
_apple_token_cache: tuple[str, int] | None = None


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
