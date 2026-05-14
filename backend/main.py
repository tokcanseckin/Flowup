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
import hmac as _hmac
import ipaddress
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import asynccontextmanager
from hashlib import pbkdf2_hmac, sha256
from pathlib import Path
from typing import Optional

import jwt as pyjwt
from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session, noload

from database import AlignmentTask, Line, LineTranslation, Localization, Playlist, PlaylistSong, Song, User, UserFavorite, UserListenedSong, UserWordLookup, Word, WordDefinition, create_tables, get_db
from models import (
    AdminLyricsUpdate,
    AdminSongDetailResponse,
    AdminSourceLyricsUpdate,
    AdminSourceLineUpdate,
    AdminTranslationsUpdate,
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
    RegisterRequest,
    GoogleLoginRequest,
    AppleLoginRequest,
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
    AppleMusicTokenRequest,
    UserSyncRequest,
    WordLookupCreate,
    WordLookupResponse,
    WordResponse,
    WorkerResultSubmit,
    WorkerTaskResponse,
    LocalizationItem,
    LocalizationUpsert,
)
from openrussian import ensure_loaded as _load_or, lookup as _or_lookup, lookup_local as _or_lookup_local
import italian_dict as _italian_dict
from spotify_auth import fetch_spotify_user, refresh_access_token
from google_auth import verify_google_id_token
from apple_auth import verify_apple_id_token


# ── Server-side song cache ───────────────────────────────────────────────────
# Key: (song_id, source) where source is None for default/Spotify.
# Values: SongDetailResponse serialised as dict (JSON-ready, ~30 KB/song).
_song_response_cache: dict[tuple[int, Optional[str]], dict] = {}

# Persistent disk cache — survives process restarts so the DB is only queried
# for songs that are new or have changed since the last save.
_CACHE_FILE = Path(__file__).parent / ".song_cache.json"
_disk_save_timer: Optional[threading.Timer] = None
_disk_save_lock = threading.Lock()


def _save_disk_cache() -> None:
    """Atomically write the in-memory cache to disk."""
    global _disk_save_timer
    try:
        songs: dict[str, dict] = {}
        for (song_id, source), val in _song_response_cache.items():
            songs[f"{song_id}:{source or ''}"] = val
        payload = {"version": 1, "saved_at": int(time.time()), "songs": songs}
        tmp = _CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(_CACHE_FILE)
        print(f"[Cache] Saved {len(songs)} entries to disk.")
    except Exception as exc:
        print(f"[Cache] Disk save failed (non-fatal): {exc}")
    finally:
        with _disk_save_lock:
            _disk_save_timer = None


def _load_disk_cache() -> int:
    """Populate the in-memory cache from disk. Returns number of entries loaded."""
    if not _CACHE_FILE.exists():
        return 0
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if data.get("version") != 1:
            return 0
        count = 0
        for key_str, val in data.get("songs", {}).items():
            parts = key_str.split(":", 1)
            song_id = int(parts[0])
            source: Optional[str] = parts[1] if len(parts) > 1 and parts[1] else None
            _song_response_cache[(song_id, source)] = val
            count += 1
        age_h = (time.time() - data.get("saved_at", 0)) / 3600
        print(f"[Cache] Loaded {count} entries from disk (age: {age_h:.1f}h).")
        return count
    except Exception as exc:
        print(f"[Cache] Disk load failed (non-fatal): {exc}")
        return 0


def _schedule_disk_save(delay: float = 2.0) -> None:
    """Debounced disk save — burst writes coalesce into a single flush."""
    global _disk_save_timer
    with _disk_save_lock:
        if _disk_save_timer is not None:
            _disk_save_timer.cancel()
        t = threading.Timer(delay, _save_disk_cache)
        t.daemon = True
        t.start()
        _disk_save_timer = t


def _cache_invalidate(song_id: int) -> None:
    """Remove all cached variants for a song and schedule a disk flush."""
    keys = [k for k in _song_response_cache if k[0] == song_id]
    for k in keys:
        del _song_response_cache[k]
    _schedule_disk_save()


def _warm_new_songs_only() -> None:
    """Fetch only songs not already in the in-memory cache, then persist to disk."""
    db = next(get_db())
    try:
        all_ids = {row[0] for row in db.query(Song.id).all()}
        missing = [sid for sid in all_ids if (sid, None) not in _song_response_cache]
        # Evict stale entries for songs deleted from the DB.
        stale = [sid for (sid, src) in list(_song_response_cache) if src is None and sid not in all_ids]
        for sid in stale:
            keys = [k for k in _song_response_cache if k[0] == sid]
            for k in keys:
                del _song_response_cache[k]
        if not missing:
            if stale:
                _save_disk_cache()
            print(f"[Cache] All {len(all_ids)} songs already cached from disk.")
            return
        print(f"[Cache] Fetching {len(missing)} new/missing songs from DB…")
        songs = db.query(Song).filter(Song.id.in_(missing)).all()
        for song in songs:
            detail = _song_detail(song, source=None)
            _song_response_cache[(song.id, None)] = detail.model_dump()
        print(f"[Cache] Added {len(songs)} songs. Total in cache: {len(_song_response_cache)}.")
        _save_disk_cache()
    except Exception as exc:
        print(f"[Cache] Background warm failed (non-fatal): {exc}")
    finally:
        db.close()


# ── Localization cache ─────────────────────────────────────────────────────────
# All rows from the `localizations` table, keyed by `key`.
# None means "not yet loaded"; invalidated on any admin write.
_loc_cache: dict[str, dict] | None = None
_loc_lock = threading.Lock()


def _load_loc_cache(db: Session) -> dict[str, dict]:
    rows = db.query(Localization).all()
    return {row.key: {"key": row.key, "en": row.en, "tr": row.tr, "ru": row.ru} for row in rows}


def _invalidate_loc_cache() -> None:
    global _loc_cache
    with _loc_lock:
        _loc_cache = None


def _get_loc_cache(db: Session) -> dict[str, dict]:
    global _loc_cache
    with _loc_lock:
        if _loc_cache is None:
            _loc_cache = _load_loc_cache(db)
        return _loc_cache


# ── Startup ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    _seed_sample_data()
    _seed_localizations()
    _ensure_admin_user()
    _ensure_spotify_enabled_users()
    loop = asyncio.get_event_loop()
    # Pre-load OpenRussian index in a thread. If it fails, keep API alive.
    try:
        await loop.run_in_executor(None, _load_or)
    except Exception as exc:
        print(f"[OpenRussian] Startup preload failed (non-fatal): {exc}")
    # Pre-load Italian OMW dictionary.
    try:
        await loop.run_in_executor(None, _italian_dict.ensure_loaded)
    except Exception as exc:
        print(f"[ItalianDict] Startup preload failed (non-fatal): {exc}")
    # Phase 1: Restore the disk cache instantly — no DB round-trip needed.
    _load_disk_cache()
    # Phase 2: Background sync — only fetch songs missing from the disk cache.
    warm_future = asyncio.ensure_future(loop.run_in_executor(None, _warm_new_songs_only))
    # Pre-warm localizations cache.
    try:
        _loc_db = next(get_db())
        _get_loc_cache(_loc_db)
        _loc_db.close()
        print("[Cache] Localizations cache loaded.")
    except Exception as exc:
        print(f"[Cache] Localization preload failed (non-fatal): {exc}")
    yield
    # Shutdown: cancel background warm if still running.
    warm_future.cancel()
    try:
        await warm_future
    except (asyncio.CancelledError, Exception):
        pass


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


_INITIAL_LOCALIZATIONS: list[dict] = [
    # Auth
    {"key": "auth.tagline1",                "en": "Learn languages through music.", "tr": "Müzikle dil öğren.",           "ru": "Учи языки через музыку."},
    {"key": "auth.tagline2",                "en": "Real lyrics. Real grammar. Real context.", "tr": "Gerçek sözler. Gerçek gramer. Gerçek bağlam.", "ru": "Настоящие тексты. Настоящая грамматика. Настоящий контекст."},
    {"key": "auth.signIn",                  "en": "Sign in",                    "tr": "Giriş yap",                 "ru": "Войти"},
    {"key": "auth.signingIn",               "en": "Signing in…",                "tr": "Giriş yapılıyor…",          "ru": "Вход…"},
    {"key": "auth.signUp",                  "en": "Sign up",                    "tr": "Kayıt ol",                  "ru": "Зарегистрироваться"},
    {"key": "auth.createAccount",           "en": "Create account",             "tr": "Hesap oluştur",             "ru": "Создать аккаунт"},
    {"key": "auth.creatingAccount",         "en": "Creating account…",          "tr": "Hesap oluşturuluyor…",      "ru": "Создание аккаунта…"},
    {"key": "auth.dontHaveAccount",         "en": "Don't have an account?",     "tr": "Hesabınız yok mu?",         "ru": "Нет аккаунта?"},
    {"key": "auth.alreadyHaveAccount",      "en": "Already have an account?",   "tr": "Zaten hesabınız var mı?",   "ru": "Уже есть аккаунт?"},
    {"key": "auth.or",                      "en": "or",                         "tr": "veya",                      "ru": "или"},
    {"key": "auth.continueWithApple",       "en": "Continue with Apple",        "tr": "Apple ile devam et",        "ru": "Войти через Apple"},
    {"key": "auth.passwordsDoNotMatch",     "en": "Passwords do not match",     "tr": "Şifreler eşleşmiyor",       "ru": "Пароли не совпадают"},
    {"key": "auth.emailPlaceholder",        "en": "Email",                      "tr": "E-posta",                   "ru": "Email"},
    {"key": "auth.passwordPlaceholder",     "en": "Password",                   "tr": "Şifre",                     "ru": "Пароль"},
    {"key": "auth.namePlaceholder",         "en": "Name",                       "tr": "İsim",                      "ru": "Имя"},
    {"key": "auth.passwordMinPlaceholder",  "en": "Password (min 8 chars)",     "tr": "Şifre (min 8 karakter)",    "ru": "Пароль (мин. 8 символов)"},
    {"key": "auth.confirmPasswordPlaceholder", "en": "Confirm password",        "tr": "Şifreyi onayla",            "ru": "Подтвердите пароль"},
    # Navigation / Browser
    {"key": "nav.admin",                    "en": "Admin",                      "tr": "Yönetici",                  "ru": "Администратор"},
    {"key": "nav.preferences",             "en": "Preferences",                "tr": "Tercihler",                 "ru": "Настройки"},
    {"key": "nav.signOut",                  "en": "Sign out",                   "tr": "Çıkış yap",                 "ru": "Выйти"},
    {"key": "nav.playlists",               "en": "Playlists",                  "tr": "Çalma listeleri",           "ru": "Плейлисты"},
    {"key": "browser.songs",               "en": "Songs",                      "tr": "Şarkılar",                  "ru": "Песни"},
    {"key": "browser.play",                "en": "Play",                       "tr": "Oynat",                     "ru": "Играть"},
    {"key": "browser.progress",            "en": "Progress",                   "tr": "İlerleme",                  "ru": "Прогресс"},
    {"key": "browser.wordsLookedUp",       "en": "Words looked up",            "tr": "Aranan kelimeler",          "ru": "Слов изучено"},
    {"key": "browser.loadingSongs",        "en": "Loading songs…",             "tr": "Şarkılar yükleniyor…",      "ru": "Загрузка песен…"},
    {"key": "browser.noSongs",             "en": "No songs available",         "tr": "Şarkı bulunamadı",          "ru": "Нет доступных песен"},
    {"key": "browser.unknownArtist",       "en": "Unknown artist",             "tr": "Bilinmeyen sanatçı",        "ru": "Неизвестный исполнитель"},
    {"key": "browser.addToFavorites",      "en": "Add to favorites",           "tr": "Favorilere ekle",           "ru": "В избранное"},
    {"key": "browser.removeFromFavorites", "en": "Remove from favorites",      "tr": "Favorilerden kaldır",       "ru": "Из избранного"},
    {"key": "browser.markAsNotListened",   "en": "Mark as not listened",       "tr": "Dinlenmedi olarak işaretle","ru": "Отметить как непрослушанное"},
    {"key": "browser.reportProblem",       "en": "Report a problem",           "tr": "Sorun bildir",              "ru": "Сообщить о проблеме"},
    # Settings / Preferences
    {"key": "settings.preferences",       "en": "Preferences",                "tr": "Tercihler",                 "ru": "Настройки"},
    {"key": "settings.account",           "en": "Account",                    "tr": "Hesap",                     "ru": "Аккаунт"},
    {"key": "settings.subscription",      "en": "Subscription",               "tr": "Abonelik",                  "ru": "Подписка"},
    {"key": "settings.support",           "en": "Support",                    "tr": "Destek",                    "ru": "Поддержка"},
    {"key": "settings.musicSource",       "en": "Music source",               "tr": "Müzik kaynağı",             "ru": "Источник музыки"},
    {"key": "settings.musicSourceDesc",   "en": "Choose whether to use YouTube or Apple Music.", "tr": "Müziğin nereden çalınacağını seçin", "ru": "Выберите источник воспроизведения"},
    {"key": "settings.prioritizeContentWords",     "en": "Prioritize content words for 1-9 shortcuts",      "tr": "İçerik kelimelerine öncelik ver",   "ru": "Приоритет смысловых слов"},
    {"key": "settings.prioritizeContentWordsDesc", "en": "When on, shortcut numbers skip common stop words (pronouns, prepositions, conjunctions) and target more meaningful words first.", "tr": "Önemli kelimelerin tanımlarını önce göster", "ru": "Сначала показывать определения важных слов"},
    {"key": "settings.pauseOnInspect",    "en": "Pause playback while inspecting lyrics",           "tr": "İnceleme sırasında duraklat", "ru": "Пауза при изучении"},
    {"key": "settings.pauseOnInspectDesc","en": "When on, playback pauses while definition/translation panels are open and resumes when you close them.", "tr": "Kelime incelerken oynatmayı duraklat", "ru": "Ставить на паузу при изучении слова"},
    {"key": "settings.connected",         "en": "Connected",                  "tr": "Bağlı",                     "ru": "Подключено"},
    {"key": "settings.notConnected",      "en": "Not connected",              "tr": "Bağlı değil",               "ru": "Не подключено"},
    {"key": "settings.appleMusic",        "en": "Apple Music",                "tr": "Apple Music",               "ru": "Apple Music"},
    {"key": "settings.contactUs",         "en": "Contact us",                 "tr": "Bize ulaşın",               "ru": "Связаться с нами"},
    {"key": "settings.contactUsDesc",     "en": "Have a question or found an issue? We're happy to help.", "tr": "Bize mesaj gönderin",       "ru": "Напишите нам"},
    {"key": "settings.subject",           "en": "Subject",                    "tr": "Konu",                      "ru": "Тема"},
    {"key": "settings.message",           "en": "Message",                    "tr": "Mesaj",                     "ru": "Сообщение"},
    {"key": "settings.sendMessage",       "en": "Send message",               "tr": "Mesaj gönder",              "ru": "Отправить сообщение"},
    {"key": "settings.sendAnotherMessage","en": "Send another message",       "tr": "Başka mesaj gönder",        "ru": "Отправить ещё"},
    {"key": "settings.messageSent",       "en": "Message sent",               "tr": "Mesaj gönderildi",          "ru": "Сообщение отправлено"},
    {"key": "settings.messageReply",      "en": "We'll get back to you as soon as possible.", "tr": "E-postanıza yanıt vereceğiz.", "ru": "Мы ответим на ваш email."},
    {"key": "settings.subscriptionManagement", "en": "Subscription management",   "tr": "Aboneliği yönet",           "ru": "Управление подпиской"},
    {"key": "settings.subscriptionDesc",  "en": "Subscription details and billing will be available here soon.", "tr": "Aboneliğinizi App Store veya cihaz ayarlarınızdan yönetin.", "ru": "Управляйте подпиской в App Store или настройках устройства."},
    {"key": "settings.unknownUser",       "en": "Unknown user",               "tr": "Bilinmeyen kullanıcı",      "ru": "Неизвестный пользователь"},
    {"key": "settings.uiLanguage",        "en": "Interface language",         "tr": "Arayüz dili",               "ru": "Язык интерфейса"},
    {"key": "settings.uiLanguageDesc",    "en": "Choose the language used throughout the app UI.", "tr": "Uygulama genelinde kullanılan dili seçin.", "ru": "Выберите язык интерфейса."},
    {"key": "settings.sourceYoutubeDesc",    "en": "Embed YouTube videos when available",          "tr": "Mevcut olduğunda YouTube videolarını göster",  "ru": "Встраивать YouTube-видео при наличии"},
    {"key": "settings.sourceAppleMusicDesc", "en": "Use Apple Music (requires subscription)",       "tr": "Apple Music kullan (abonelik gerektirir)",     "ru": "Использовать Apple Music (нужна подписка)"},
    {"key": "settings.subjectPlaceholder",   "en": "e.g. Bug report, Feature request\u2026",        "tr": "örn. Hata bildirimi, Özellik isteği\u2026",    "ru": "напр. Сообщение об ошибке, Пожелание\u2026"},
    {"key": "settings.messagePlaceholder",   "en": "Describe your issue or question\u2026",         "tr": "Sorununuzu veya sorunuzu açıklayın\u2026",     "ru": "Опишите вашу проблему или вопрос\u2026"},
    # Inspect / Lyrics shortcuts
    {"key": "inspect.title",              "en": "INSPECT LYRICS",             "tr": "SÖZLERE BAK",               "ru": "ТЕКСТ ПЕСНИ"},
    {"key": "inspect.numberedWord",       "en": "Inspect a numbered word",    "tr": "Numaralı kelimeye bak",     "ru": "Изучить пронумерованное слово"},
    {"key": "inspect.sentenceTranslation","en": "Sentence translation",       "tr": "Cümle çevirisi",            "ru": "Перевод предложения"},
    {"key": "inspect.peekWithoutPinning", "en": "Peek without pinning",       "tr": "Sabitleme ile gözetleme",   "ru": "Подглядеть без закрепления"},
    {"key": "inspect.hold",               "en": "hold",                       "tr": "basılı tut",                "ru": "удерживать"},
    {"key": "inspect.playPause",          "en": "Play / pause",               "tr": "Oynat / duraklat",          "ru": "Воспроизведение / пауза"},
    {"key": "inspect.seekPrevNextLine",   "en": "Seek to prev / next line",   "tr": "Önceki / sonraki satıra git","ru": "К пред. / следующей строке"},
    {"key": "inspect.prevNextSong",       "en": "Prev / next song",           "tr": "Önceki / sonraki şarkı",    "ru": "Пред. / следующая песня"},
    {"key": "inspect.definition",         "en": "Definition",                 "tr": "Tanım",                     "ru": "Определение"},
    {"key": "inspect.translation",        "en": "Translation",                "tr": "Çeviri",                    "ru": "Перевод"},
    {"key": "inspect.close",              "en": "Close",                      "tr": "Kapat",                     "ru": "Закрыть"},
    {"key": "inspect.noDefinition",       "en": "No definition yet",          "tr": "Henüz tanım yok",           "ru": "Определение пока отсутствует"},
    {"key": "inspect.infinitive",         "en": "infinitive",                 "tr": "mastar",                    "ru": "инфинитив"},
    {"key": "inspect.nominative",         "en": "nominative",                 "tr": "yalın hal",                 "ru": "именительный"},
    {"key": "inspect.noTranslation",      "en": "No translation available for this line yet", "tr": "Bu satır için henüz çeviri yok", "ru": "Перевод для этой строки пока недоступен"},
    # Player empty state
    {"key": "player.waitingForPlayback",  "en": "Waiting for playback...",    "tr": "Oynatma bekleniyor...",     "ru": "Ожидание воспроизведения..."},
    {"key": "player.loadAndPlay",         "en": "Load a track and press Play","tr": "Bir parça yükle ve Oynat'a bas", "ru": "Загрузите трек и нажмите Воспроизвести"},
    # Language names
    {"key": "language.ru",  "en": "Russian",   "tr": "Rusça",      "ru": "Русский"},
    {"key": "language.en",  "en": "English",   "tr": "İngilizce",  "ru": "Английский"},
    {"key": "language.es",  "en": "Spanish",   "tr": "İspanyolca", "ru": "Испанский"},
    {"key": "language.fr",  "en": "French",    "tr": "Fransızca",  "ru": "Французский"},
    {"key": "language.de",  "en": "German",    "tr": "Almanca",    "ru": "Немецкий"},
    {"key": "language.it",  "en": "Italian",   "tr": "İtalyanca",  "ru": "Итальянский"},
    {"key": "language.pt",  "en": "Portuguese","tr": "Portekizce", "ru": "Португальский"},
    {"key": "language.ja",  "en": "Japanese",  "tr": "Japonca",    "ru": "Японский"},
    {"key": "language.ko",  "en": "Korean",    "tr": "Korece",     "ru": "Корейский"},
    {"key": "language.zh",  "en": "Chinese",   "tr": "Çince",      "ru": "Китайский"},
    {"key": "language.tr",  "en": "Turkish",   "tr": "Türkçe",     "ru": "Турецкий"},
    # Browse / Discover
    {"key": "browse.learnTitle",    "en": "I want to improve",                            "tr": "Geliştirmek istiyorum",                     "ru": "Хочу улучшить"},
    {"key": "browse.learnSubtitle", "en": "Choose the language you want to learn",         "tr": "Öğrenmek istediğiniz dili seçin",           "ru": "Выберите язык для изучения"},
    {"key": "browse.playlist",      "en": "playlist",                                      "tr": "çalma listesi",                             "ru": "плейлист"},
    {"key": "browse.playlists",     "en": "playlists",                                     "tr": "çalma listesi",                             "ru": "плейлистов"},
    {"key": "browse.song",          "en": "song",                                          "tr": "şarkı",                                     "ru": "песня"},
    {"key": "browse.songs",         "en": "songs",                                         "tr": "şarkı",                                     "ru": "песен"},
    {"key": "browse.speakTitle",    "en": "I speak",                                       "tr": "Konuştuğum dil",                            "ru": "Я говорю на"},
    {"key": "browse.speakSubtitle", "en": "Choose your native language for translations",  "tr": "Çeviriler için ana dilinizi seçin",         "ru": "Выберите родной язык для переводов"},
    {"key": "browse.playlistsTitle","en": "Playlists",                                     "tr": "Çalma listeleri",                           "ru": "Плейлисты"},
    {"key": "browse.available",     "en": "available",                                     "tr": "mevcut",                                    "ru": "доступно"},
    {"key": "browse.noPlaylists",   "en": "No playlists for this language pair yet.",      "tr": "Bu dil çifti için henüz çalma listesi yok.","ru": "Пока нет плейлистов для этой пары языков."},
    # Difficulty tags
    {"key": "general.Tag.Difficulty.Beginner",     "en": "Beginner",     "tr": "Başlangıç",  "ru": "Начальный"},
    {"key": "general.Tag.Difficulty.Intermediate", "en": "Intermediate", "tr": "Orta Düzey", "ru": "Промежуточный"},
    {"key": "general.Tag.Difficulty.Advanced",     "en": "Advanced",     "tr": "İleri Düzey","ru": "Продвинутый"},
    # Russian playlist names & descriptions
    {"key": "playlist.Russian.Beginner",
     "en": "Russian - Beginner",
     "tr": "Rusça - Başlangıç",
     "ru": "Русский - Начальный уровень"},
    {"key": "playlist.Description.Russian.Beginner",
     "en": "Start here. These songs move slowly enough to catch every word, and the language is exactly what you'd learn in your first few months — everyday vocabulary, simple structures, nothing to fear.",
     "tr": "Buradan başlayın. Bu şarkılar her kelimeyi yakalamak için yeterince yavaş hareket ediyor ve dil, ilk birkaç ayınızda öğreneceğiniz tam olarak — günlük kelime dağarcığı, basit yapılar, korkulacak bir şey yok.",
     "ru": "Начните здесь. Эти песни достаточно медленные, чтобы уловить каждое слово, а язык — именно то, что вы учите в первые месяцы: повседневный словарь, простые конструкции, ничего сложного."},
    {"key": "playlist.Russian.Intermediate",
     "en": "Russian - Intermediate",
     "tr": "Rusça - Orta Seviye",
     "ru": "Русский - Средний уровень"},
    {"key": "playlist.Description.Russian.Intermediate",
     "en": "You know the basics — now it's time to actually sound like someone who means it. These songs sit in the sweet spot where the vocabulary stretches you without losing you, and the themes are real: cities, love, growing up, goodbyes.",
     "tr": "Temelleri biliyorsunuz — şimdi gerçekten bunu kasteden biri gibi görünmenin zamanı geldi. Bu şarkılar, kelime dağarcığının sizi kaybetmeden uzattığı tatlı noktada oturuyor ve temalar gerçek: şehirler, aşk, büyüme, vedalar.",
     "ru": "Вы знаете основы — теперь пришло время действительно звучать как кто-то, кто это имеет в виду. Эти песни находятся в сладком месте, где словарный запас растягивает вас, не теряя вас, и темы реальны: города, любовь, взросление, прощания."},
    {"key": "playlist.Russian.Advanced",
     "en": "Russian - Advanced",
     "tr": "Rusça - İleri Düzey",
     "ru": "Русский - Продвинутый уровень"},
    {"key": "playlist.Description.Russian.Advanced",
     "en": "You're past survival Russian. These songs push into poetry, subtext, and the kind of cultural weight that only lands when you've been living in the language. Dense, layered, and worth every second.",
     "tr": "Hayatta kalma Rusçasının ötesine geçtiniz. Bu şarkılar şiir, alt metin ve dilde yaşadığınızda ortaya çıkan kültürel ağırlık türüne doğru ilerliyor. Yoğun, katmanlı ve her saniyeye değer.",
     "ru": "Вы вышли за рамки «разговорного минимума». Эти песни уходят в поэзию, подтекст и тот культурный пласт, который открывается только тем, кто живёт в языке. Плотно, многослойно и стоит каждой секунды."},
    # English playlist names & descriptions
    {"key": "playlist.English.Advanced",
     "en": "English - Advanced",
     "tr": "İngilizce - İleri Düzey",
     "ru": "Английский - Продвинутый"},
    {"key": "playlist.Description.English.Advanced",
     "en": "Now you're here to feel them land the way a native speaker feels them. Slang that doesn't translate, rhythm that only makes sense when you stop thinking about it, lyrics where the joke is in the delivery.",
     "tr": "Şimdi onları anadili İngilizce olan birinin hissettiği şekilde hissetmek için buradasınız. Tercüme etmeyen argo, sadece düşünmeyi bıraktığınızda mantıklı olan ritim, şakanın sunumda olduğu şarkı sözleri.",
     "ru": "Теперь вы здесь, чтобы почувствовать, как они приземляются так, как их чувствует носитель языка. Сленг, который не переводится, ритм, который имеет смысл только тогда, когда вы перестаете думать об этом, тексты, где шутка в доставке."},
    # Grammar terms (for the word-inspect panel)
    # Parts of speech
    {"key": "grammar.Noun",             "en": "Noun",              "tr": "İsim",              "ru": "Существительное"},
    {"key": "grammar.Verb",             "en": "Verb",              "tr": "Fiil",              "ru": "Глагол"},
    {"key": "grammar.Adjective",        "en": "Adjective",         "tr": "Sıfat",             "ru": "Прилагательное"},
    {"key": "grammar.Adverb",           "en": "Adverb",            "tr": "Zarf",              "ru": "Наречие"},
    {"key": "grammar.Preposition",      "en": "Preposition",       "tr": "Edat",              "ru": "Предлог"},
    {"key": "grammar.Conjunction",      "en": "Conjunction",       "tr": "Bağlaç",            "ru": "Союз"},
    {"key": "grammar.Particle",         "en": "Particle",          "tr": "Parçacık",          "ru": "Частица"},
    {"key": "grammar.Participle",       "en": "Participle",        "tr": "Sıfat-fiil",        "ru": "Причастие"},
    {"key": "grammar.Pronoun",          "en": "Pronoun",           "tr": "Zamir",             "ru": "Местоимение"},
    {"key": "grammar.Numeral",          "en": "Numeral",           "tr": "Sayı sıfatı",       "ru": "Числительное"},
    {"key": "grammar.Interjection",     "en": "Interjection",      "tr": "Ünlem",             "ru": "Междометие"},
    {"key": "grammar.Determiner",       "en": "Determiner",        "tr": "Belirteç",          "ru": "Артикль"},
    {"key": "grammar.Proper_Noun",      "en": "Proper Noun",       "tr": "Özel İsim",         "ru": "Имя собственное"},
    {"key": "grammar.Auxiliary_Verb",   "en": "Auxiliary Verb",    "tr": "Yardımcı Fiil",     "ru": "Вспомогательный глагол"},
    {"key": "grammar.Gerund",           "en": "Gerund",            "tr": "Ulaç",              "ru": "Деепричастие"},
    {"key": "grammar.Adj_short",        "en": "Adj (short)",       "tr": "Kısa sıfat",        "ru": "Краткое прилагательное"},
    {"key": "grammar.Participle_short", "en": "Participle (short)","tr": "Kısa sıfat-fiil",   "ru": "Краткое причастие"},
    {"key": "grammar.Verb_infinitive",  "en": "Infinitive",        "tr": "Mastar",            "ru": "Инфинитив"},
    {"key": "grammar.Punctuation",      "en": "Punctuation",       "tr": "Noktalama",         "ru": "Знак препинания"},
    # Number
    {"key": "grammar.Singular",         "en": "Singular",          "tr": "Tekil",             "ru": "Ед. ч."},
    {"key": "grammar.Plural",           "en": "Plural",            "tr": "Çoğul",             "ru": "Мн. ч."},
    # Gender
    {"key": "grammar.Masculine",        "en": "Masculine",         "tr": "Eril",              "ru": "Муж."},
    {"key": "grammar.Feminine",         "en": "Feminine",          "tr": "Dişil",             "ru": "Жен."},
    {"key": "grammar.Neuter",           "en": "Neuter",            "tr": "Yansız",            "ru": "Ср."},
    # Case
    {"key": "grammar.Nominative",       "en": "Nominative",        "tr": "Yalın hal",         "ru": "Им."},
    {"key": "grammar.Genitive",         "en": "Genitive",          "tr": "İyelik hali",       "ru": "Род."},
    {"key": "grammar.Genitive_2",       "en": "Genitive 2",        "tr": "İyelik hali 2",     "ru": "Род. 2"},
    {"key": "grammar.Dative",           "en": "Dative",            "tr": "Yönelme hali",      "ru": "Дат."},
    {"key": "grammar.Accusative",       "en": "Accusative",        "tr": "Belirtme hali",     "ru": "Вин."},
    {"key": "grammar.Instrumental",     "en": "Instrumental",      "tr": "Araç hali",         "ru": "Твор."},
    {"key": "grammar.Prepositional",    "en": "Prepositional",     "tr": "Edat hali",         "ru": "Пред."},
    {"key": "grammar.Vocative",         "en": "Vocative",          "tr": "Seslenme hali",     "ru": "Зват."},
    {"key": "grammar.Abs",              "en": "Abs",               "tr": "Mutlak hal",        "ru": "Абсолютив"},
    # Aspect
    {"key": "grammar.Perfective",       "en": "Perfective",        "tr": "Bitimli görünüş",   "ru": "Сов."},
    {"key": "grammar.Imperfective",     "en": "Imperfective",      "tr": "Süreğen görünüş",   "ru": "Несов."},
    # Tense
    {"key": "grammar.Present",          "en": "Present",           "tr": "Şimdiki zaman",     "ru": "Наст."},
    {"key": "grammar.Past",             "en": "Past",              "tr": "Geçmiş zaman",      "ru": "Прош."},
    {"key": "grammar.Future",           "en": "Future",            "tr": "Gelecek zaman",     "ru": "Буд."},
    {"key": "grammar.Imperfect",        "en": "Imperfect",         "tr": "Geçmiş süreç kipi", "ru": "Имперфект"},
    # Person
    {"key": "grammar.1st_Person",       "en": "1st Person",        "tr": "1. şahıs",          "ru": "1-е л."},
    {"key": "grammar.2nd_Person",       "en": "2nd Person",        "tr": "2. şahıs",          "ru": "2-е л."},
    {"key": "grammar.3rd_Person",       "en": "3rd Person",        "tr": "3. şahıs",          "ru": "3-е л."},
    # Mood
    {"key": "grammar.Imperative",       "en": "Imperative",        "tr": "Emir kipi",         "ru": "Повел."},
    {"key": "grammar.Conditional",      "en": "Conditional",       "tr": "Koşullu kip",       "ru": "Условное"},
    {"key": "grammar.Indicative",       "en": "Indicative",        "tr": "Bildirme kipi",     "ru": "Изъявит."},
    {"key": "grammar.Subjunctive",      "en": "Subjunctive",       "tr": "Dilek kipi",        "ru": "Сослагат."},
    # Other
    {"key": "grammar.Comparative",      "en": "Comparative",       "tr": "Karşılaştırmalı",   "ru": "Сравн."},
    {"key": "grammar.Predicative",      "en": "Predicative",       "tr": "Yüklem işlevi",     "ru": "Предикатив"},
    {"key": "grammar.Definite",         "en": "Definite",          "tr": "Belirli",           "ru": "Определённый"},
    {"key": "grammar.Indefinite",       "en": "Indefinite",        "tr": "Belirsiz",          "ru": "Неопределённый"},
    {"key": "grammar.Other",            "en": "Other",             "tr": "Diğer",             "ru": "Другое"},
    {"key": "grammar.Unknown",          "en": "Unknown",           "tr": "Bilinmiyor",        "ru": "Неизвестно"},
]


def _seed_localizations() -> None:
    """Upsert the initial localization strings into the DB (idempotent)."""
    db = next(get_db())
    try:
        for entry in _INITIAL_LOCALIZATIONS:
            existing = db.query(Localization).filter(Localization.key == entry["key"]).first()
            if not existing:
                db.add(Localization(key=entry["key"], en=entry["en"], tr=entry["tr"], ru=entry["ru"]))
        db.commit()
        print("[DB] Localizations seeded.")
    except Exception as exc:
        print(f"[DB] Localization seed failed (non-fatal): {exc}")
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
    """Strip combining stress marks (U+0301) so 'пи́сать' → 'писать' for dictionary lookup.

    Only removes the acute accent (U+0301). Stripping all combining chars would
    corrupt letters like й (и + U+0306 breve) → и, breaking keys like нужный.
    """
    import unicodedata
    nfd = unicodedata.normalize("NFD", s)
    return unicodedata.normalize("NFC", "".join(c for c in nfd if c != "\u0301"))


def _enrich_definition(raw_def: Optional[str], lemma: str, lang_code: str = "ru", display_form: str = "") -> Optional[str]:
    """Replace stub definitions (e.g. '[mesto]') with local dictionary lookups.

    Uses the local in-memory dict only (no network calls) so this function is
    always fast and never blocks a GET /songs/{id} request.
    """
    if lang_code == "it":
        # Strip non-word chars from display_form (e.g. trailing punctuation).
        clean_display = re.sub(r"[^\w]", "", display_form, flags=re.UNICODE).lower() if display_form else ""
        # For stubs, try: stub inner key → stored lemma → display form.
        # Return None (rather than the ugly stub) when OMW has no entry.
        def _cap_it(s: str | None, n: int = 4) -> str | None:
            if not s:
                return s
            parts = [p.strip() for p in s.split(';') if p.strip()]
            return '; '.join(parts[:n]) if parts else None

        # Always re-lookup from the in-memory OMW (fast, no network) so that
        # DB-stored verbose glosses are replaced with the current lemma_names output.
        live = (
            _italian_dict.lookup(lemma)
            or (clean_display and _italian_dict.lookup(clean_display))
            or (raw_def and not raw_def.startswith("[") and raw_def)
            or None
        )
        return _cap_it(live)
    # Russian (default): strip combining accents so 'пи́сать' looks up 'писать'.
    bare_lemma = _strip_accents(lemma)
    if raw_def and raw_def.startswith("[") and raw_def.endswith("]"):
        live = _or_lookup_local(raw_def[1:-1]) or _or_lookup_local(bare_lemma)
        return live or raw_def
    return raw_def or _or_lookup_local(bare_lemma)


# Language code aliases — normalize regional variants to the canonical code stored in the DB.
_LANG_ALIASES: dict[str, str] = {
    "en-us": "en",
    "en-gb": "en",
    "en-au": "en",
}


def _canon_lang(lang: str) -> str:
    """Lowercase and resolve regional aliases (e.g. en-us → en)."""
    return _LANG_ALIASES.get(lang.lower(), lang.lower())


def _is_placeholder_def(s: Optional[str]) -> bool:
    """Return True for '[lemma]'-style placeholder definitions inserted by the pipeline."""
    if not s:
        return True
    s = s.strip()
    if not (len(s) < 60 and s.startswith("[") and s.endswith("]")):
        return False
    # JSON arrays like '["vagon"]' are valid definitions, not placeholders.
    # Real placeholders look like [lemma_form] — no quotes, a single token.
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list) and parsed:
            return False  # non-empty JSON array → valid definition
    except (ValueError, TypeError):
        pass
    return True


def _parse_definition(raw: Optional[str]) -> Optional[str]:
    """If raw is a JSON array, join elements with ', '. Otherwise return as-is."""
    if not raw:
        return raw
    stripped = raw.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list) and parsed:
                return ", ".join(str(x) for x in parsed if x)
        except (ValueError, TypeError):
            pass
    return raw


def _word_response(word: Word, lang_code: str = "ru", target_lang: Optional[str] = None) -> WordResponse:
    # Check normalized WordDefinition table first (multi-lang support).
    # Skip placeholder entries so we can fall through to a real definition.
    definition: Optional[str] = None
    if target_lang and word.definitions:
        for wd in word.definitions:
            if wd.target_lang.lower() == target_lang.lower() and not _is_placeholder_def(wd.definition):
                definition = _parse_definition(wd.definition)
                break
    if definition is None or _is_placeholder_def(definition):
        definition = word.dictionary_definition
    # Last resort: any non-placeholder entry from word_definitions
    if _is_placeholder_def(definition) and word.definitions:
        for wd in word.definitions:
            if not _is_placeholder_def(wd.definition):
                definition = _parse_definition(wd.definition)
                break
    return WordResponse(
        key=word.key_index,
        display_form=word.display_form,
        lemma=word.lemma,
        grammar=word.grammar,
        dictionary_definition=_enrich_definition(definition, word.lemma, lang_code, word.display_form or ""),
    )


def _line_response(line: Line, override_words: Optional[list] = None, lang_code: str = "ru", target_lang: Optional[str] = None) -> LineResponse:
    words = override_words if override_words is not None else line.words
    # Check normalized LineTranslation table first (multi-lang support)
    translation: str = line.translation
    if target_lang and line.translations:
        for lt in line.translations:
            if lt.target_lang.lower() == target_lang.lower():
                translation = lt.text
                break
    return LineResponse(
        id=line.id,
        position=line.position,
        start_time_ms=line.start_time_ms,
        end_time_ms=line.end_time_ms,
        original_line=line.original_line,
        phonetic_line=line.phonetic_line,
        translation=translation,
        words=[_word_response(w, lang_code, target_lang) for w in words],
        source=line.source,
    )


def _song_detail(song: Song, source: Optional[str] = None, target_lang: Optional[str] = None) -> SongDetailResponse:
    default_lines = [l for l in song.lines if l.source is None]
    lang_code = song.language_code or "ru"

    if source and source != "default":
        source_lines = [l for l in song.lines if l.source == source]
        if source_lines:
            default_words_by_pos = {l.position: l.words for l in default_lines}
            lines = [
                _line_response(sl, override_words=default_words_by_pos.get(sl.position, []), lang_code=lang_code, target_lang=target_lang)
                for sl in sorted(source_lines, key=lambda l: l.position)
            ]
        else:
            lines = [_line_response(l, lang_code=lang_code, target_lang=target_lang) for l in default_lines]
    else:
        lines = [_line_response(l, lang_code=lang_code, target_lang=target_lang) for l in default_lines]

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
        target_langs=json.loads(song.target_langs or '[]'),
    )


def _admin_song_detail(song: Song, db: Session) -> AdminSongDetailResponse:
    playlist_ids = [
        playlist_id
        for (playlist_id,) in db.query(PlaylistSong.playlist_id).filter(PlaylistSong.song_id == song.id).all()
    ]
    default_words_by_pos = {l.position: l.words for l in song.lines if l.source is None}
    source_lines_map: dict[str, list[LineResponse]] = {}
    lang_code = song.language_code or "ru"
    for line in song.lines:
        if line.source is not None:
            lr = _line_response(line, override_words=default_words_by_pos.get(line.position, []), lang_code=lang_code)
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
        # Use legacy `translation` field OR first value from `translations` dict
        legacy_translation = line_data.translation or (next(iter(line_data.translations.values()), "") if line_data.translations else "")
        line = Line(
            song_id=song.id,
            position=pos,
            start_time_ms=line_data.start_time_ms,
            end_time_ms=line_data.end_time_ms,
            original_line=line_data.original_line,
            phonetic_line=line_data.phonetic_line,
            translation=legacy_translation,
        )
        db.add(line)
        db.flush()  # get line.id

        # Write normalized per-target-language translations
        for tl_lang, tl_text in line_data.translations.items():
            db.add(LineTranslation(line_id=line.id, target_lang=_canon_lang(tl_lang), text=tl_text))

        for word_data in line_data.words:
            # Use legacy `dictionary_definition` OR first value from `definitions` dict
            legacy_def = word_data.dictionary_definition or (next(iter(word_data.definitions.values()), None) if word_data.definitions else None)
            word = Word(
                line_id=line.id,
                key_index=word_data.key,
                display_form=word_data.display_form,
                lemma=word_data.lemma,
                grammar=word_data.grammar,
                # Resolve stubs at ingest time so the DB always has clean values.
                dictionary_definition=_enrich_definition(legacy_def, word_data.lemma, body.language.code, word_data.display_form or ""),
            )
            db.add(word)
            db.flush()  # get word.id for WordDefinition FK

            # Write normalized per-target-language definitions
            for def_lang, def_text in word_data.definitions.items():
                db.add(WordDefinition(word_id=word.id, target_lang=_canon_lang(def_lang), definition=def_text))

    _sync_song_target_langs(song, db)
    db.commit()
    db.refresh(song)
    return song


def _sync_song_target_langs(song: Song, db: Session) -> None:
    """Derive song.target_langs from the distinct LineTranslation.target_lang values for its default lines."""
    seen: set[str] = set()
    for line in song.lines:
        if line.source is not None:
            continue
        for lt in line.translations:
            seen.add(lt.target_lang.lower())
    song.target_langs = json.dumps(sorted(seen))


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


# ── Admin token (HMAC-based, works for Google users who have no password) ─────
_ADMIN_TOKEN_SECRET = os.environ.get("FLOWUP_ADMIN_SECRET", "flowup-dev-admin-secret-change-in-prod").encode()


def _make_admin_token(user: User) -> str:
    """Return a stable HMAC-SHA256 token tied to this user's identity."""
    msg = f"{user.id}:{user.spotify_id}:{user.email or ''}".encode()
    sig = _hmac.new(_ADMIN_TOKEN_SECRET, msg, sha256).hexdigest()
    return f"{user.id}.{sig}"


def _verify_admin_token(token: str, user: User) -> bool:
    expected = _make_admin_token(user)
    return secrets.compare_digest(token, expected)


def _require_admin(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization:
        raise HTTPException(status_code=401, detail="Admin authentication required")

    try:
        scheme, credential = authorization.split(" ", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Authorization header") from exc

    scheme = scheme.lower()

    # ── Bearer (HMAC token) — works for both password and Google users ─────────
    if scheme == "bearer":
        try:
            user_id_str, _ = credential.split(".", 1)
            user_id = int(user_id_str)
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Invalid admin token format") from exc
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not _verify_admin_token(credential, user):
            raise HTTPException(status_code=401, detail="Invalid admin token")
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="Admin access required")
        return user

    # ── Basic (email:password) — legacy, kept for compatibility ───────────────
    if scheme == "basic":
        try:
            decoded = base64.b64decode(credential).decode("utf-8")
            email, password = decoded.split(":", 1)
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Invalid basic auth credentials") from exc
        user = db.query(User).filter(User.email == email.strip().lower()).first()
        if not user or not _verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid admin credentials")
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="Admin access required")
        return user

    raise HTTPException(status_code=401, detail="Unsupported authentication scheme")


def _get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Dependency: any authenticated user (password or Bearer token). Returns 401 if not authenticated."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        scheme, credential = authorization.split(" ", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Authorization header") from exc

    scheme = scheme.lower()

    if scheme == "bearer":
        try:
            user_id_str, _ = credential.split(".", 1)
            user_id = int(user_id_str)
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Invalid token format") from exc
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not _verify_admin_token(credential, user):
            raise HTTPException(status_code=401, detail="Invalid token")
        return user

    if scheme == "basic":
        try:
            decoded = base64.b64decode(credential).decode("utf-8")
            email, password = decoded.split(":", 1)
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Invalid basic auth credentials") from exc
        user = db.query(User).filter(User.email == email.strip().lower()).first()
        if not user or not _verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return user

    raise HTTPException(status_code=401, detail="Unsupported authentication scheme")


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/image-proxy")
def image_proxy(url: str = Query(..., min_length=8, max_length=2048)):
    """Fetch a remote image and serve it from same-origin for safe canvas sampling."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Only http(s) image URLs are allowed")

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise HTTPException(status_code=400, detail="Image URL host is required")
    if host == "localhost" or host.endswith(".local"):
        raise HTTPException(status_code=400, detail="Local hosts are not allowed")

    try:
        host_ip = ipaddress.ip_address(host)
        if (
            host_ip.is_private
            or host_ip.is_loopback
            or host_ip.is_link_local
            or host_ip.is_multicast
            or host_ip.is_reserved
            or host_ip.is_unspecified
        ):
            raise HTTPException(status_code=400, detail="Private hosts are not allowed")
    except ValueError:
        # Host is a domain name; allow and rely on standard outbound network controls.
        pass

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "FlowupImageProxy/1.0",
            "Accept": "image/*",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=8) as upstream:
            content_type = (upstream.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if not content_type.startswith("image/"):
                raise HTTPException(status_code=415, detail="URL did not return an image")

            max_bytes = 5 * 1024 * 1024
            payload = upstream.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise HTTPException(status_code=413, detail="Image is too large")

            return Response(
                content=payload,
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch image: {exc}") from exc


# ── Songs ──────────────────────────────────────────────────────────────────────

@app.get("/api/songs", response_model=list[SongSummaryResponse])
def list_songs(db: Session = Depends(get_db)):
    # noload(Song.lines) prevents the selectin cascade: lines → words → definitions
    songs = db.query(Song).options(noload(Song.lines)).order_by(Song.created_at.desc()).all()
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
def get_song(song_id: int, source: Optional[str] = Query(default=None), target_lang: Optional[str] = Query(default=None), db: Session = Depends(get_db)):
    # Normalize to lowercase and resolve aliases (e.g. en-us → en)
    if target_lang:
        target_lang = _canon_lang(target_lang)
    # Cache keyed by (song_id, source, target_lang) — skip cache when target_lang specified (varies per user preference)
    cache_key = (song_id, source or None) if not target_lang else None
    if cache_key is not None:
        cached = _song_response_cache.get(cache_key)
        if cached is not None:
            return JSONResponse(
                content=cached,
                headers={"Cache-Control": "private, max-age=3600"},
            )
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    detail = _song_detail(song, source=source, target_lang=target_lang)
    data = detail.model_dump()
    if cache_key is not None:
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
    if "target_langs" in body.model_fields_set and body.target_langs is not None:
        song.target_langs = json.dumps([lang.lower() for lang in body.target_langs])

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


@app.post("/api/admin/songs/{song_id}/find-youtube")
async def find_youtube_url(song_id: int, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    """Search YouTube for a studio recording matching the song's title and artist, save and return the URL."""
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    title = song.title
    artist = song.artist or song.title

    try:
        import sys as _sys
        _pipeline_dir = str(Path(__file__).parent.parent / "pipeline")
        if _pipeline_dir not in _sys.path:
            _sys.path.insert(0, _pipeline_dir)
        from fill_youtube_urls import search_youtube  # type: ignore[import]
        loop = asyncio.get_event_loop()
        url, _ = await loop.run_in_executor(None, search_youtube, title, artist)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"YouTube search failed: {exc}") from exc

    if url:
        song.youtube_url = url
        db.commit()
        _cache_invalidate(song_id)

    return {"url": url}


@app.post("/api/admin/songs/{song_id}/find-apple-music")
async def find_apple_music_url(song_id: int, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    """Search iTunes for a track matching the song's title and artist, save and return the URL."""
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    title = song.title
    artist = song.artist or song.title

    try:
        import sys as _sys
        _pipeline_dir = str(Path(__file__).parent.parent / "pipeline")
        if _pipeline_dir not in _sys.path:
            _sys.path.insert(0, _pipeline_dir)
        from fill_apple_music_urls import search_apple_music  # type: ignore[import]
        loop = asyncio.get_event_loop()
        url, _ = await loop.run_in_executor(None, search_apple_music, title, artist)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Apple Music search failed: {exc}") from exc

    if url:
        song.apple_music_url = url
        db.commit()
        _cache_invalidate(song_id)

    return {"url": url}


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


@app.get("/api/admin/songs/{song_id}/target-langs")
def get_song_target_langs(song_id: int, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    """Return the list of distinct target_lang codes that have LineTranslation rows for this song."""
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    langs: list[str] = []
    seen: set[str] = set()
    for line in song.lines:
        if line.source is not None:
            continue
        for lt in line.translations:
            if lt.target_lang not in seen:
                seen.add(lt.target_lang)
                langs.append(lt.target_lang)
    return {"target_langs": sorted(langs)}


@app.put("/api/admin/songs/{song_id}/translations")
def update_song_translations(
    song_id: int,
    target_lang: str = Query(..., description="Target language code, e.g. RU"),
    body: AdminTranslationsUpdate = ...,
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    """Upsert LineTranslation rows for the given target_lang."""
    target_lang = _canon_lang(target_lang)
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    default_line_ids = {line.id for line in song.lines if line.source is None}
    for item in body.lines:
        if item.id not in default_line_ids:
            raise HTTPException(status_code=400, detail=f"Line id={item.id} not found on song")
        existing = db.query(LineTranslation).filter_by(line_id=item.id, target_lang=target_lang).first()
        if existing:
            existing.text = item.text
        else:
            db.add(LineTranslation(line_id=item.id, target_lang=target_lang, text=item.text))
    db.flush()
    _sync_song_target_langs(song, db)
    db.commit()
    _cache_invalidate(song_id)
    return {"ok": True}


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

    # Check LRCLIB before spawning the worker — if synced lyrics exist we can
    # skip Whisper entirely and pass --lrc-file to the pipeline.
    synced_lrc = _lrclib_synced(artist, title, lang_code)

    async def event_stream():
        lrc_tmp_path: Optional[Path] = None
        if synced_lrc:
            yield "data: Found synced lyrics on LRCLIB — skipping Whisper alignment\n\n"
            with tempfile.NamedTemporaryFile(suffix=".lrc", mode="w",
                                             encoding="utf-8", delete=False) as lf:
                lf.write(synced_lrc)
                lrc_tmp_path = Path(lf.name)

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
        if lrc_tmp_path:
            command.extend(["--lrc-file", str(lrc_tmp_path)])

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
            if lrc_tmp_path:
                lrc_tmp_path.unlink(missing_ok=True)

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

# In-process cover image cache {playlist_id: (bytes, content_type)}
_cover_cache: dict[int, tuple[bytes, str]] = {}


def _playlist_response(pl: Playlist, db: Session) -> PlaylistResponse:
    # Load only the junction rows (already selectin on Playlist.playlist_songs)
    ps_list = pl.playlist_songs
    if ps_list:
        pos_map = {ps.song_id: ps.position for ps in ps_list}
        song_ids = list(pos_map.keys())
        # Single targeted query — selects only scalar columns, never triggers
        # the Song.lines selectin cascade (lines → words → definitions).
        rows = db.execute(
            sa_select(
                Song.id, Song.spotify_uri, Song.title, Song.artist,
                Song.youtube_url, Song.apple_music_url,
            ).where(Song.id.in_(song_ids))
        ).all()
        songs = sorted(
            [
                PlaylistSongEntry(
                    position=pos_map[r.id],
                    song_id=r.id,
                    spotify_uri=r.spotify_uri,
                    title=r.title,
                    artist=r.artist,
                    youtube_url=r.youtube_url,
                    apple_music_url=r.apple_music_url,
                )
                for r in rows
                if r.id in pos_map
            ],
            key=lambda e: e.position,
        )
    else:
        songs = []
    return PlaylistResponse(
        id=pl.id,
        spotify_playlist_id=pl.spotify_playlist_id,
        name=pl.name,
        description=pl.description,
        cover_image_url=f"/api/playlists/{pl.id}/cover" if pl.cover_image_type is not None else None,
        difficulty_level=pl.difficulty_level,
        language_code=pl.language_code,
        target_lang=pl.target_lang,
        target_langs=json.loads(pl.target_langs or '[]'),
        is_hidden=pl.is_hidden,
        song_count=pl.song_count,
        songs=songs,
    )


def _playlist_summary(pl: Playlist) -> PlaylistSummaryResponse:
    return PlaylistSummaryResponse(
        id=pl.id,
        spotify_playlist_id=pl.spotify_playlist_id,
        name=pl.name,
        description=pl.description,
        cover_image_url=f"/api/playlists/{pl.id}/cover" if pl.cover_image_type is not None else None,
        difficulty_level=pl.difficulty_level,
        language_code=pl.language_code,
        target_lang=pl.target_lang,
        target_langs=json.loads(pl.target_langs or '[]'),
        is_hidden=pl.is_hidden,
        song_count=pl.song_count,
    )


_DIFFICULTY_ORDER = {
    "[[general.Tag.Difficulty.Beginner]]":     0,
    "[[general.Tag.Difficulty.Intermediate]]": 1,
    "[[general.Tag.Difficulty.Advanced]]":     2,
}


def _difficulty_sort_key(pl: Playlist) -> tuple:
    """Sort by (language_code, difficulty position, name) so playlists of the
    same language are grouped and ordered Beginner → Intermediate → Advanced."""
    lang = (pl.language_code or "").lower()
    diff = _DIFFICULTY_ORDER.get(pl.difficulty_level or "", 99)
    return (lang, diff, pl.name)


@app.get("/api/playlists", response_model=list[PlaylistSummaryResponse])
def list_playlists(target_lang: Optional[str] = Query(None), db: Session = Depends(get_db)):
    q = db.query(Playlist).filter(Playlist.is_hidden == False)  # noqa: E712
    if target_lang:
        q = q.filter(Playlist.target_lang == target_lang)
    playlists = sorted(q.all(), key=_difficulty_sort_key)
    return [_playlist_summary(pl) for pl in playlists]


@app.get("/api/admin/playlists", response_model=list[PlaylistSummaryResponse])
def admin_list_playlists(db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    """Admin-only: returns all playlists including hidden ones."""
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
        target_lang=body.target_lang,
        target_langs=json.dumps(body.target_langs),
        is_hidden=body.is_hidden,
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
    return _playlist_response(pl, db)


@app.get("/api/playlists/{playlist_id}", response_model=PlaylistResponse)
def get_playlist(playlist_id: int, db: Session = Depends(get_db)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return _playlist_response(pl, db)


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
    if body.target_lang is not None:
        pl.target_lang = body.target_lang
    if body.target_langs is not None:
        pl.target_langs = json.dumps([l.lower() for l in body.target_langs])
    if body.is_hidden is not None:
        pl.is_hidden = body.is_hidden
    db.commit()
    db.refresh(pl)
    return _playlist_response(pl, db)


@app.delete("/api/playlists/{playlist_id}", status_code=204)
def delete_playlist(playlist_id: int, db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    db.delete(pl)
    db.commit()
    _cover_cache.pop(playlist_id, None)


@app.post("/api/playlists/{playlist_id}/cover", status_code=204)
async def upload_playlist_cover(
    playlist_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    data = await file.read()
    content_type = file.content_type or "image/jpeg"
    pl.cover_image = data
    pl.cover_image_type = content_type
    db.commit()
    _cover_cache[playlist_id] = (data, content_type)


@app.get("/api/playlists/{playlist_id}/cover")
async def get_playlist_cover(playlist_id: int, db: Session = Depends(get_db)):
    _COVER_HEADERS = {"Cache-Control": "public, max-age=86400"}
    if playlist_id in _cover_cache:
        data, content_type = _cover_cache[playlist_id]
        return Response(content=data, media_type=content_type, headers=_COVER_HEADERS)
    pl = db.get(Playlist, playlist_id)
    if not pl or pl.cover_image_type is None:
        raise HTTPException(status_code=404, detail="No cover image")
    data = pl.cover_image  # triggers deferred load
    _cover_cache[playlist_id] = (data, pl.cover_image_type)
    return Response(content=data, media_type=pl.cover_image_type, headers=_COVER_HEADERS)


@app.delete("/api/playlists/{playlist_id}/cover", status_code=204)
def delete_playlist_cover(
    playlist_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    pl.cover_image = None
    pl.cover_image_type = None
    db.commit()
    _cover_cache.pop(playlist_id, None)


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
    return _playlist_response(pl, db)


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
    return _playlist_response(pl, db)


# ── Favorites ──────────────────────────────────────────────────────────────────

@app.get("/api/me/favorites")
def get_favorites(
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """Return the list of song IDs favorited by the current user."""
    rows = db.query(UserFavorite.song_id).filter(UserFavorite.user_id == current_user.id).all()
    return {"song_ids": [r.song_id for r in rows]}


@app.post("/api/me/favorites/{song_id}", status_code=204)
def add_favorite(
    song_id: int,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """Add a song to the current user's favorites (idempotent)."""
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    existing = db.query(UserFavorite).filter(
        UserFavorite.user_id == current_user.id,
        UserFavorite.song_id == song_id,
    ).first()
    if not existing:
        db.add(UserFavorite(user_id=current_user.id, song_id=song_id))
        db.commit()


@app.delete("/api/me/favorites/{song_id}", status_code=204)
def remove_favorite(
    song_id: int,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a song from the current user's favorites (idempotent)."""
    db.query(UserFavorite).filter(
        UserFavorite.user_id == current_user.id,
        UserFavorite.song_id == song_id,
    ).delete()
    db.commit()


@app.get("/api/me/listened")
def get_listened(
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """Return the list of song IDs the current user has listened to."""
    rows = db.query(UserListenedSong.song_id).filter(UserListenedSong.user_id == current_user.id).all()
    return {"song_ids": [r.song_id for r in rows]}


@app.post("/api/me/listened/{song_id}", status_code=204)
def add_listened(
    song_id: int,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a song as listened (idempotent). Silently succeeds if song no longer exists."""
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        return  # song was deleted; treat as no-op
    existing = db.query(UserListenedSong).filter(
        UserListenedSong.user_id == current_user.id,
        UserListenedSong.song_id == song_id,
    ).first()
    if not existing:
        db.add(UserListenedSong(user_id=current_user.id, song_id=song_id))
        db.commit()


@app.delete("/api/me/listened/{song_id}", status_code=204)
def remove_listened(
    song_id: int,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """Unmark a song as listened (idempotent)."""
    db.query(UserListenedSong).filter(
        UserListenedSong.user_id == current_user.id,
        UserListenedSong.song_id == song_id,
    ).delete()
    db.commit()


# ── Word Lookups ───────────────────────────────────────────────────────────────

@app.get("/api/me/word-lookups", response_model=list[WordLookupResponse])
def get_word_lookups(
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """Return all words the current user has ever looked up."""
    rows = db.query(UserWordLookup).filter(
        UserWordLookup.user_id == current_user.id,
    ).all()
    return [
        WordLookupResponse(
            lemma=r.lemma,
            language=r.language,
            target_lang=r.target_lang,
            display_form=r.display_form,
            definition=r.definition,
            grammar=r.grammar,
            song_id=r.song_id,
            looked_up_at=r.looked_up_at,
        )
        for r in rows
    ]


@app.post("/api/me/word-lookups", status_code=204)
def record_word_lookup(
    body: WordLookupCreate,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """Upsert a word lookup — updates timestamp and details if the (lemma, language) pair already exists."""
    existing = db.query(UserWordLookup).filter(
        UserWordLookup.user_id == current_user.id,
        UserWordLookup.lemma == body.lemma,
        UserWordLookup.language == body.language,
        UserWordLookup.target_lang == body.target_lang,
    ).first()
    now = int(time.time())
    if existing:
        existing.display_form = body.display_form
        existing.definition   = body.definition
        existing.grammar      = body.grammar
        existing.song_id      = body.song_id
        existing.looked_up_at = now
    else:
        db.add(UserWordLookup(
            user_id=current_user.id,
            lemma=body.lemma,
            language=body.language,
            target_lang=body.target_lang,
            display_form=body.display_form,
            definition=body.definition,
            grammar=body.grammar,
            song_id=body.song_id,
            looked_up_at=now,
        ))
    db.commit()


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
        apple_music_user_token=user.apple_music_user_token,
        admin_token=_make_admin_token(user),
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
        apple_music_user_token=user.apple_music_user_token,
        admin_token=_make_admin_token(user),
    )


@app.post("/api/auth/register", response_model=UserResponse)
async def register_with_credentials(body: RegisterRequest, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    display_name = body.display_name.strip()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    if not display_name:
        raise HTTPException(status_code=400, detail="Display name is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=409, detail="An account with that email already exists")

    synthetic_id = f"email:{uuid.uuid4().hex}"
    user = User(
        spotify_id=synthetic_id,
        display_name=display_name,
        email=email,
        password_hash=_hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return UserResponse(
        id=user.id,
        spotify_id=user.spotify_id,
        display_name=user.display_name,
        email=user.email,
        has_password=True,
        needs_onboarding=False,
        is_admin=bool(user.is_admin),
        spotify_enabled=bool(user.spotify_enabled),
        apple_music_user_token=user.apple_music_user_token,
        admin_token=_make_admin_token(user),
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
        apple_music_user_token=user.apple_music_user_token,
        admin_token=_make_admin_token(user),
    )


@app.get("/api/me/settings", response_model=UserSettings)
async def get_user_settings(current_user: User = Depends(_get_current_user)):
    return _parse_user_settings(current_user.settings_json)


@app.put("/api/me/settings", response_model=UserSettings)
async def update_user_settings(
    body: UserSettingsUpdate,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    user = current_user

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


# ── Apple Music user token persistence ────────────────────────────────────────


@app.put("/api/me/apple-music-token")
async def save_apple_music_token(
    body: AppleMusicTokenRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """Persist (or clear) the user's MusicKit musicUserToken."""
    current_user.apple_music_user_token = body.token or None
    db.commit()
    return {"ok": True}


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
            google_user_id=google_sub,
        )
        db.add(user)
    else:
        # Keep google synthetic_id canonical going forward.
        if user.spotify_id != synthetic_id and not user.spotify_id.startswith("spotify:"):
            user.spotify_id = synthetic_id
        user.display_name = user.display_name or display_name
        if not user.email:
            user.email = email
        if not user.google_user_id:
            user.google_user_id = google_sub

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
        apple_music_user_token=user.apple_music_user_token,
        admin_token=_make_admin_token(user),
    )


@app.post("/api/auth/apple", response_model=UserResponse)
async def login_with_apple(body: AppleLoginRequest, db: Session = Depends(get_db)):
    """Verify a Sign In with Apple identity token and create/return the matching user."""
    try:
        claims = await verify_apple_id_token(body.id_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    apple_sub = claims["sub"]
    # Apple only sends email on the first sign-in; subsequent logins omit it.
    email_raw = claims.get("email")
    email = email_raw.strip().lower() if email_raw else None
    # Apple also only provides name on first sign-in.
    display_name: str | None = None

    synthetic_id = f"apple:{apple_sub}"

    user = db.query(User).filter(User.apple_user_id == apple_sub).first()
    if not user:
        user = db.query(User).filter(User.spotify_id == synthetic_id).first()
    if not user and email:
        user = db.query(User).filter(User.email == email).first()

    if not user:
        user = User(
            spotify_id=synthetic_id,
            display_name=display_name or email or "Apple User",
            email=email,
            apple_user_id=apple_sub,
        )
        db.add(user)
    else:
        if user.spotify_id != synthetic_id and not user.spotify_id.startswith("spotify:"):
            user.spotify_id = synthetic_id
        if not user.apple_user_id:
            user.apple_user_id = apple_sub
        if email and not user.email:
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
        apple_music_user_token=user.apple_music_user_token,
        admin_token=_make_admin_token(user),
    )


@app.post("/api/auth/apple/events", status_code=200)
async def apple_server_events(request: Request, db: Session = Depends(get_db)):
    """
    Receive Sign in with Apple server-to-server notifications.
    Apple POSTs a signed JWT in the 'payload' form field.
    Events: email-disabled, email-enabled, consent-revoked, account-delete
    """
    form = await request.form()
    payload_jwt = form.get("payload")
    if not payload_jwt:
        raise HTTPException(status_code=400, detail="Missing payload")

    try:
        # Decode without verifying signature here — for critical actions you can
        # verify with Apple's JWKS, but the event data itself is not sensitive.
        import jwt as pyjwt
        claims = pyjwt.decode(str(payload_jwt), options={"verify_signature": False})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload JWT")

    events = claims.get("events")
    if isinstance(events, str):
        import json as _json
        try:
            events = _json.loads(events)
        except Exception:
            events = {}

    event_type = events.get("type") if isinstance(events, dict) else None
    apple_sub = events.get("sub") if isinstance(events, dict) else None

    if event_type in ("consent-revoked", "account-delete") and apple_sub:
        user = db.query(User).filter(User.apple_user_id == apple_sub).first()
        if user:
            user.apple_user_id = None
            db.commit()

    return {"ok": True}


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


def _lrclib_synced(artist: str, title: str, lang: str = "") -> Optional[str]:
    """Return synced LRC from LRCLIB if available, else None."""
    _CYRILLIC_LANGS = {"ru", "uk", "bg", "sr", "mk"}
    require_cyrillic = lang in _CYRILLIC_LANGS

    def _ok(text: str) -> bool:
        if not text:
            return False
        if require_cyrillic:
            return sum(1 for c in text if '\u0400' <= c <= '\u04FF') >= 15
        return True

    try:
        # 1. Try the exact-match GET endpoint first (most precise)
        params = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
        req = urllib.request.Request(
            f"https://lrclib.net/api/get?{params}",
            headers={"User-Agent": "FlowUp/1.0 (https://singoling.com)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode())
            if isinstance(body, dict) and _ok(body.get("syncedLyrics", "")):
                return body["syncedLyrics"]
        except Exception:
            pass

        # 2. Fall back to search with q param
        params2 = urllib.parse.urlencode({"q": f"{artist} {title}"})
        req2 = urllib.request.Request(
            f"https://lrclib.net/api/search?{params2}",
            headers={"User-Agent": "FlowUp/1.0 (https://singoling.com)"},
        )
        with urllib.request.urlopen(req2, timeout=10) as resp2:
            hits = json.loads(resp2.read().decode())
        for hit in hits:
            if _ok(hit.get("syncedLyrics", "")):
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
    synced_lrc = _lrclib_synced(body.artist, body.title, body.lang or "")
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


# ── Localizations ──────────────────────────────────────────────────────────────

@app.get("/api/localizations", response_model=list[LocalizationItem])
def get_localizations(db: Session = Depends(get_db)):
    """Return all localization strings. Results are served from in-memory cache."""
    cache = _get_loc_cache(db)
    return list(cache.values())


@app.post("/api/admin/localizations", response_model=LocalizationItem, status_code=201)
def create_or_update_localization(
    key: str,
    body: LocalizationUpsert,
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    row = db.query(Localization).filter(Localization.key == key).first()
    if row:
        row.en = body.en
        row.tr = body.tr
        row.ru = body.ru
    else:
        row = Localization(key=key, en=body.en, tr=body.tr, ru=body.ru)
        db.add(row)
    db.commit()
    db.refresh(row)
    _invalidate_loc_cache()
    return LocalizationItem.model_validate(row)


@app.put("/api/admin/localizations/{key}", response_model=LocalizationItem)
def update_localization(
    key: str,
    body: LocalizationUpsert,
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    row = db.query(Localization).filter(Localization.key == key).first()
    if not row:
        raise HTTPException(status_code=404, detail="Localization key not found")
    row.en = body.en
    row.tr = body.tr
    row.ru = body.ru
    db.commit()
    db.refresh(row)
    _invalidate_loc_cache()
    return LocalizationItem.model_validate(row)


@app.delete("/api/admin/localizations/{key}", status_code=204)
def delete_localization(
    key: str,
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    row = db.query(Localization).filter(Localization.key == key).first()
    if not row:
        raise HTTPException(status_code=404, detail="Localization key not found")
    db.delete(row)
    db.commit()
    _invalidate_loc_cache()
