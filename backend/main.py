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
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import pbkdf2_hmac, sha256
from pathlib import Path
from typing import Optional

import jwt as pyjwt
from dotenv import load_dotenv

load_dotenv()  # local .env (dev)
load_dotenv(Path.home() / ".credentials")  # server credentials (won't override already-set vars)

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session, noload

from database import AlignmentTask, Line, LineTranslation, Localization, PasswordResetToken, Playlist, PlaylistSong, Report, Song, User, UserFavorite, UserListenedSong, UserWordLookup, Word, WordDefinition, create_tables, get_db
import paddle_config
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
    ReportCreate,
    AdminReportResponse,
    ReportStatusUpdate,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    UpdateLangRequest,
)
import entitlements
import mailgun as _mailgun
from openrussian import ensure_loaded as _load_or, lookup as _or_lookup, lookup_local as _or_lookup_local
import italian_dict as _italian_dict
from spotify_auth import fetch_spotify_user, refresh_access_token  # noqa: E402
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
    return {row.key: {"key": row.key, "en": row.en, "tr": row.tr, "ru": row.ru, "es": row.es, "pt": row.pt, "de": row.de} for row in rows}


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


_SUPPORTED_EMAIL_LANGS = frozenset({'en', 'tr', 'ru', 'es', 'pt', 'de'})


def _get_email_t(kind: str, lang: str, db: Session) -> dict:
    """Return email template strings for kind ('welcome'|'passwordReset') in the given language."""
    l = lang if lang in _SUPPORTED_EMAIL_LANGS else 'en'
    cache = _get_loc_cache(db)
    prefix = f"email.{kind}."
    return {
        key[len(prefix):]: (cache[key].get(l) or cache[key].get('en', ''))
        for key in cache if key.startswith(prefix)
    }


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
    {"key": "auth.tagline1",                "en": "Learn languages through music.", "tr": "Müzikle dil öğren.",           "ru": "Учи языки через музыку.",           "es": "Aprende idiomas con música.",                      "pt": "Aprenda idiomas através da música.",              "de": "Lerne Sprachen durch Musik."},
    {"key": "auth.tagline2",                "en": "Real lyrics. Real grammar. Real context.", "tr": "Gerçek sözler. Gerçek gramer. Gerçek bağlam.", "ru": "Настоящие тексты. Настоящая грамматика. Настоящий контекст.", "es": "Letras reales. Gramática real. Contexto real.", "pt": "Letras reais. Gramática real. Contexto real.", "de": "Echte Liedtexte. Echte Grammatik. Echter Kontext."},
    {"key": "auth.signIn",                  "en": "Sign in",                    "tr": "Giriş yap",                 "ru": "Войти",                           "es": "Iniciar sesión",                              "pt": "Entrar",                                      "de": "Anmelden"},
    {"key": "auth.signingIn",               "en": "Signing in\u2026",                "tr": "Giriş yapılıyor\u2026",          "ru": "Вход\u2026",                           "es": "Iniciando sesión\u2026",                          "pt": "Entrando\u2026",                                "de": "Anmeldung läuft\u2026"},
    {"key": "auth.signUp",                  "en": "Sign up",                    "tr": "Kayıt ol",                  "ru": "Зарегистрироваться",             "es": "Registrarse",                                "pt": "Cadastrar-se",                                "de": "Registrieren"},
    {"key": "auth.createAccount",           "en": "Create account",             "tr": "Hesap oluştur",             "ru": "Создать аккаунт",               "es": "Crear cuenta",                               "pt": "Criar conta",                                 "de": "Konto erstellen"},
    {"key": "auth.creatingAccount",         "en": "Creating account\u2026",          "tr": "Hesap oluşturuluyor\u2026",      "ru": "Создание аккаунта\u2026",              "es": "Creando cuenta\u2026",                           "pt": "Criando conta\u2026",                             "de": "Konto wird erstellt\u2026"},
    {"key": "auth.dontHaveAccount",         "en": "Don't have an account?",     "tr": "Hesabınız yok mu?",         "ru": "Нет аккаунта?",                  "es": "\u00bfNo tienes una cuenta?",                     "pt": "Não tem uma conta?",                           "de": "Noch kein Konto?"},
    {"key": "auth.alreadyHaveAccount",      "en": "Already have an account?",   "tr": "Zaten hesabınız var mı?",   "ru": "Уже есть аккаунт?",             "es": "\u00bfYa tienes una cuenta?",                    "pt": "Já tem uma conta?",                            "de": "Bereits ein Konto?"},
    {"key": "auth.or",                      "en": "or",                         "tr": "veya",                      "ru": "или",                             "es": "o",                                          "pt": "ou",                                          "de": "oder"},
    {"key": "auth.continueWithApple",       "en": "Continue with Apple",        "tr": "Apple ile devam et",        "ru": "Войти через Apple",              "es": "Continuar con Apple",                         "pt": "Continuar com a Apple",                        "de": "Mit Apple fortfahren"},
    {"key": "auth.passwordsDoNotMatch",     "en": "Passwords do not match",     "tr": "Şifreler eşleşmiyor",       "ru": "Пароли не совпадают",            "es": "Las contraseñas no coinciden",                "pt": "As senhas não coincidem",                      "de": "Passwörter stimmen nicht überein"},
    {"key": "auth.emailPlaceholder",        "en": "Email",                      "tr": "E-posta",                   "ru": "Email",                           "es": "Correo electrónico",                          "pt": "E-mail",                                      "de": "E-Mail"},
    {"key": "auth.passwordPlaceholder",     "en": "Password",                   "tr": "Şifre",                     "ru": "Пароль",                          "es": "Contraseña",                                 "pt": "Senha",                                       "de": "Passwort"},
    {"key": "auth.namePlaceholder",         "en": "Name",                       "tr": "İsim",                      "ru": "Имя",                             "es": "Nombre",                                     "pt": "Nome",                                        "de": "Name"},
    {"key": "auth.passwordMinPlaceholder",  "en": "Password (min 8 chars)",     "tr": "Şifre (min 8 karakter)",    "ru": "Пароль (мин. 8 символов)",       "es": "Contraseña (mín. 8 caracteres)",              "pt": "Senha (mín. 8 caracteres)",                   "de": "Passwort (mind. 8 Zeichen)"},
    {"key": "auth.confirmPasswordPlaceholder", "en": "Confirm password",        "tr": "Şifreyi onayla",            "ru": "Подтвердите пароль",             "es": "Confirmar contraseña",                        "pt": "Confirmar senha",                              "de": "Passwort bestätigen"},
    {"key": "auth.signupAgreementPrefix",      "en": "By signing up, you agree to our", "tr": "Kayıt olarak",             "ru": "Регистрируясь, вы соглашаетесь с нашими", "es": "Al registrarte, aceptas nuestros",         "pt": "Ao se cadastrar, você concorda com nossos",    "de": "Mit der Registrierung stimmst du unseren"},
    {"key": "auth.termsOfService",             "en": "Terms of Service",               "tr": "Kullanım Koşulları",       "ru": "Условиями использования",        "es": "Términos de Servicio",            "pt": "Termos de Serviço",                         "de": "Nutzungsbedingungen"},
    {"key": "auth.privacyPolicy",              "en": "Privacy Policy",                 "tr": "Gizlilik Politikası",     "ru": "Политикой конфиденциальности",   "es": "Política de Privacidad",          "pt": "Política de Privacidade",                    "de": "Datenschutzrichtlinie"},
    {"key": "auth.and",                        "en": "and",                            "tr": "ve",                      "ru": "и",                              "es": "y",                               "pt": "e",                                         "de": "und"},
    {"key": "auth.forgotPassword",             "en": "Forgot password?",               "tr": "Şifremi unuttum?",        "ru": "Забыли пароль?",                 "es": "¿Olvidaste tu contraseña?",       "pt": "Esqueceu sua senha?",                        "de": "Passwort vergessen?"},
    {"key": "auth.resetPassword",              "en": "Reset password",                 "tr": "Şifremi sıfırla",         "ru": "Сбросить пароль",                "es": "Restablecer contraseña",          "pt": "Redefinir senha",                            "de": "Passwort zurücksetzen"},
    {"key": "auth.resetPasswordInstruction",   "en": "Enter your email and we'll send you a link to reset your password.", "tr": "E-postanızı girin, size bir sıfırlama bağlantısı göndereceğiz.", "ru": "Введите email, и мы отправим вам ссылку для сброса пароля.", "es": "Introduce tu correo y te enviaremos un enlace para restablecer tu contraseña.", "pt": "Insira seu e-mail e enviaremos um link para redefinir sua senha.", "de": "Gib deine E-Mail-Adresse ein und wir senden dir einen Link zum Zurücksetzen deines Passworts."},
    {"key": "auth.sendResetLink",              "en": "Send reset link",                "tr": "Sıfırlama bağlantısı gönder", "ru": "Отправить ссылку для сброса", "es": "Enviar enlace de restablecimiento", "pt": "Enviar link de redefinição",               "de": "Zurücksetzungslink senden"},
    {"key": "auth.sending",                    "en": "Sending\u2026",                  "tr": "Gönderiliyor\u2026",      "ru": "Отправляем\u2026",               "es": "Enviando\u2026",                  "pt": "Enviando\u2026",                             "de": "Wird gesendet\u2026"},
    {"key": "auth.resetLinkSent",              "en": "If an account with that email exists, you'll receive a reset link shortly.", "tr": "Bu e-postaya kayıtlı bir hesap varsa, kısa süre içinde bir sıfırlama bağlantısı alacaksınız.", "ru": "Если аккаунт с таким email существует, вы получите ссылку для сброса.", "es": "Si existe una cuenta con ese correo, recibirás un enlace de restablecimiento pronto.", "pt": "Se uma conta com esse e-mail existir, você receberá um link de redefinição em breve.", "de": "Falls ein Konto mit dieser E-Mail-Adresse existiert, erhältst du in Kürze einen Zurücksetzungslink."},
    {"key": "auth.backToSignIn",               "en": "Back to sign in",                "tr": "Girişe geri dön",         "ru": "Вернуться ко входу",             "es": "Volver a iniciar sesión",         "pt": "Voltar para entrar",                         "de": "Zurück zur Anmeldung"},
    {"key": "auth.somethingWentWrong",         "en": "Something went wrong. Please try again.", "tr": "Bir şeyler ters gitti. Lütfen tekrar deneyin.", "ru": "Что-то пошло не так. Пожалуйста, попробуйте снова.", "es": "Algo salió mal. Por favor, inténtalo de nuevo.", "pt": "Algo deu errado. Por favor, tente novamente.", "de": "Etwas ist schiefgelaufen. Bitte versuche es erneut."},
    {"key": "auth.chooseNewPassword",          "en": "Choose a new password",          "tr": "Yeni bir şifre seçin",    "ru": "Выберите новый пароль",          "es": "Elige una nueva contraseña",      "pt": "Escolha uma nova senha",                     "de": "Neues Passwort festlegen"},
    {"key": "auth.newPasswordPlaceholder",     "en": "New password (min 8 chars)",     "tr": "Yeni şifre (min 8 karakter)", "ru": "Новый пароль (мин. 8 символов)", "es": "Nueva contraseña (mín. 8 caracteres)", "pt": "Nova senha (mín. 8 caracteres)",            "de": "Neues Passwort (mind. 8 Zeichen)"},
    {"key": "auth.confirmNewPasswordPlaceholder", "en": "Confirm new password",        "tr": "Yeni şifreyi onayla",     "ru": "Подтвердите новый пароль",       "es": "Confirmar nueva contraseña",      "pt": "Confirmar nova senha",                       "de": "Neues Passwort bestätigen"},
    {"key": "auth.updating",                   "en": "Updating\u2026",                 "tr": "Güncelleniyor\u2026",     "ru": "Обновляем\u2026",                "es": "Actualizando\u2026",              "pt": "Atualizando\u2026",                          "de": "Wird aktualisiert\u2026"},
    {"key": "auth.setNewPassword",             "en": "Set new password",               "tr": "Yeni şifreyi kaydet",     "ru": "Установить новый пароль",        "es": "Establecer nueva contraseña",     "pt": "Definir nova senha",                         "de": "Neues Passwort festlegen"},
    {"key": "auth.passwordUpdated",            "en": "Your password has been updated. You can now sign in.", "tr": "Şifreniz güncellendi. Artık giriş yapabilirsiniz.", "ru": "Пароль обновлён. Теперь вы можете войти.", "es": "Tu contraseña ha sido actualizada. Ya puedes iniciar sesión.", "pt": "Sua senha foi atualizada. Agora você pode entrar.", "de": "Dein Passwort wurde aktualisiert. Du kannst dich jetzt anmelden."},
    # Navigation / Browser
    {"key": "nav.back",                    "en": "Back",                       "tr": "Geri",                      "ru": "Назад",                          "es": "Atrás",                                      "pt": "Voltar",                                     "de": "Zurück"},
    # Legal pages
    {"key": "legal.backToApp",            "en": "Back to SingoLing",          "tr": "SingoLing'e dön",           "ru": "Вернуться в SingoLing",          "es": "Volver a SingoLing",                         "pt": "Voltar ao SingoLing",                         "de": "Zurück zu SingoLing"},
    {"key": "legal.effectiveDate",        "en": "Effective Date:",             "tr": "Yürürlük Tarihi:",          "ru": "Дата вступления в силу:",        "es": "Fecha de vigencia:",                         "pt": "Data de vigência:",                           "de": "Gültigkeitsdatum:"},
    {"key": "legal.lastUpdated",          "en": "Last Updated:",               "tr": "Son Güncelleme:",           "ru": "Последнее обновление:",          "es": "Última actualización:",                      "pt": "Última atualização:",                         "de": "Zuletzt aktualisiert:"},
    {"key": "tutorial.word",              "en": "Hit {1} on your keyboard or {click} to instantly look up its definition", "tr": "Tanımı anında bulmak için klavyede {1}'e bas ya da {tıkla}", "ru": "Нажми {1} на клавиатуре или {нажми} мышью для мгновенного поиска", "es": "Pulsa {1} en el teclado o {haz clic} para ver la definición al instante", "pt": "Pressione {1} no teclado ou {clique} para ver a definição instantaneamente", "de": "Drücke {1} auf der Tastatur oder {klicken} für die sofortige Definition"},
    {"key": "tutorial.peek",              "en": "For even quicker lookup, simply {hold 2} key,\nor {click and hold}", "tr": "Daha hızlı bakmak için {2 tuşunu basılı tut}\nya da {tıklayıp basılı tut}", "ru": "Для быстрого поиска просто {удерживай 2},\nили {нажми и удерживай}", "es": "Para una búsqueda aún más rápida, {mantén tecla 2},\no {haz clic y mantén}", "pt": "Para busca ainda mais rápida, {segure a tecla 2},\nou {clique e segure}", "de": "Für noch schnellere Suche einfach {2 gedrückt halten},\noder {klicken und halten}"},
    {"key": "tutorial.lineTranslate",     "en": "Full-line translation, hit {0} key, or {click} here", "tr": "Tüm satır çevirisi için {0} tuşuna bas ya da buraya {tıkla}", "ru": "Перевод строки: нажми {0} или {нажми} здесь", "es": "Traducción de línea completa: pulsa {0} o {haz clic} aquí", "pt": "Tradução da linha inteira: pressione {0} ou {clique} aqui", "de": "Ganze Zeile übersetzen: drücke {0} oder {klicken} hier"},
    {"key": "tutorial.sourceToggle",      "en": "You can connect Apple Music for music only playback\nand switch between two", "tr": "Yalnızca müzik için Apple Music'e bağlanabilir\nve ikisi arasında geçiş yapabilirsin", "ru": "Подключи Apple Music для воспроизведения только музыки\nи переключайся между двумя", "es": "Puedes conectar Apple Music para reproducción solo de música\ny alternar entre los dos", "pt": "Você pode conectar o Apple Music para reprodução somente de música\ne alternar entre os dois", "de": "Du kannst Apple Music für reine Musikwiedergabe verbinden\nund zwischen beiden wechseln"},
    {"key": "tutorial.shortcuts",         "en": "And there's even more shortcuts, you can always check everything here", "tr": "Daha fazla kısayol da var, her zaman buradan her şeye bakabilirsin", "ru": "Есть ещё больше горячих клавиш — ты всегда можешь всё посмотреть здесь", "es": "Y hay aún más atajos, siempre puedes consultarlos todos aquí", "pt": "E há ainda mais atalhos, você sempre pode ver tudo aqui", "de": "Und es gibt noch mehr Tastenkombinationen — hier findest du jederzeit alles"},
    {"key": "tutorial.skip",            "en": "Skip", "tr": "Geç", "ru": "Пропустить", "es": "Omitir", "pt": "Pular", "de": "Überspringen"},
    {"key": "tutorial.next",            "en": "Next", "tr": "Sonraki", "ru": "Далее", "es": "Siguiente", "pt": "Próximo", "de": "Weiter"},
    {"key": "tutorial.done",            "en": "Done", "tr": "Tamam", "ru": "Готово", "es": "Listo", "pt": "Concluído", "de": "Fertig"},
    # Player / Tutorial (mobile)
    {"key": "player.tutorialStep1Mobile", "en": "Tap any word to see its definition instantly", "tr": "Tanımını anında görmek için herhangi bir kelimeye dokunun", "ru": "Нажмите на любое слово, чтобы мгновенно увидеть его определение", "es": "Toca cualquier palabra para ver su definición al instante", "pt": "Toque em qualquer palavra para ver sua definição instantaneamente", "de": "Tippe auf ein beliebiges Wort, um sofort seine Definition zu sehen"},
    {"key": "player.tutorialStep2Mobile", "en": "Tap and hold a word for a quick peek", "tr": "Hızlı bir göz atmak için bir kelimeye dokunup basılı tutun", "ru": "Нажмите и удерживайте слово для быстрого просмотра", "es": "Mantén presionada una palabra para un vistazo rápido", "pt": "Toque e segure uma palavra para uma olhada rápida", "de": "Tippe und halte ein Wort für einen kurzen Blick"},
    {"key": "player.tutorialStep3Mobile", "en": "Tap this icon to see the full line translation.", "tr": "Tam satır çevirisini görmek için bu simgeye dokunun.", "ru": "Нажмите на этот значок, чтобы увидеть перевод всей строки.", "es": "Toca este ícono para ver la traducción completa de la línea.", "pt": "Toque neste ícone para ver a tradução completa da linha.", "de": "Tippe auf dieses Symbol, um die vollständige Zeilenübersetzung zu sehen."},
    {"key": "player.loadingSong",         "en": "Loading song…", "tr": "Şarkı yükleniyor…", "ru": "Загрузка песни…", "es": "Cargando canción…", "pt": "Carregando música…", "de": "Lied wird geladen…"},
    # Accessibility / Aria Labels
    {"key": "aria.settings",              "en": "Settings", "tr": "Ayarlar", "ru": "Настройки", "es": "Configuración", "pt": "Configurações", "de": "Einstellungen"},
    {"key": "aria.albumArt",              "en": "Album art", "tr": "Albüm kapağı", "ru": "Обложка альбома", "es": "Arte del álbum", "pt": "Arte do álbum", "de": "Albumcover"},
    {"key": "aria.logoAlt",               "en": "SingoLing", "tr": "SingoLing", "ru": "SingoLing", "es": "SingoLing", "pt": "SingoLing", "de": "SingoLing"},
    {"key": "aria.help",                  "en": "Help", "tr": "Yardım", "ru": "Помощь", "es": "Ayuda", "pt": "Ajuda", "de": "Hilfe"},
    {"key": "aria.favorited",             "en": "Favorited", "tr": "Favorilere eklendi", "ru": "В избранном", "es": "Añadido a favoritos", "pt": "Adicionado aos favoritos", "de": "Zu Favoriten hinzugefügt"},
    {"key": "aria.moreOptions",           "en": "More options", "tr": "Daha fazla seçenek", "ru": "Дополнительные параметры", "es": "Más opciones", "pt": "Mais opções", "de": "Weitere Optionen"},
    {"key": "aria.backToPlaylists",       "en": "Back to all playlists", "tr": "Tüm çalma listelerine dön", "ru": "Вернуться ко всем плейлистам", "es": "Volver a todas las listas", "pt": "Voltar a todas as playlists", "de": "Zurück zu allen Playlists"},
    {"key": "aria.uiLanguage",            "en": "UI language", "tr": "Arayüz dili", "ru": "Язык интерфейса", "es": "Idioma de la interfaz", "pt": "Idioma da interface", "de": "UI-Sprache"},
    {"key": "aria.backButton",            "en": "Back", "tr": "Geri", "ru": "Назад", "es": "Atrás", "pt": "Voltar", "de": "Zurück"},
    {"key": "aria.goToBrowse",            "en": "Go to browse", "tr": "Göz atmaya git", "ru": "Перейти к обзору", "es": "Ir a explorar", "pt": "Ir para navegar", "de": "Zum Durchsuchen gehen"},
    {"key": "aria.translationLanguage",   "en": "Translation language", "tr": "Çeviri dili", "ru": "Язык перевода", "es": "Idioma de traducción", "pt": "Idioma de tradução", "de": "Übersetzungssprache"},
    {"key": "aria.songOptions",           "en": "Song options", "tr": "Şarkı seçenekleri", "ru": "Параметры песни", "es": "Opciones de canción", "pt": "Opções da música", "de": "Liedoptionen"},
    {"key": "aria.previousSong",          "en": "Previous song", "tr": "Önceki şarkı", "ru": "Предыдущая песня", "es": "Canción anterior", "pt": "Música anterior", "de": "Vorheriges Lied"},
    {"key": "aria.nextSong",              "en": "Next song", "tr": "Sonraki şarkı", "ru": "Следующая песня", "es": "Siguiente canción", "pt": "Próxima música", "de": "Nächstes Lied"},
    {"key": "aria.closeInspector",        "en": "Close inspector", "tr": "İncelemeyi kapat", "ru": "Закрыть инспектор", "es": "Cerrar inspector", "pt": "Fechar inspetor", "de": "Inspektor schließen"},
    {"key": "aria.close",                 "en": "Close", "tr": "Kapat", "ru": "Закрыть", "es": "Cerrar", "pt": "Fechar", "de": "Schließen"},
    # Report Modal
    {"key": "reportModal.placeholder",    "en": "Describe the issue…", "tr": "Sorunu açıklayın…", "ru": "Опишите проблему…", "es": "Describe el problema…", "pt": "Descreva o problema…", "de": "Beschreibe das Problem…"},
    {"key": "reportModal.categoryWrongTranslation", "en": "Wrong translation", "tr": "Yanlış çeviri", "ru": "Неправильный перевод", "es": "Traducción incorrecta", "pt": "Tradução incorreta", "de": "Falsche Übersetzung"},
    {"key": "reportModal.categoryWrongDefinition", "en": "Wrong definition", "tr": "Yanlış tanım", "ru": "Неправильное определение", "es": "Definición incorrecta", "pt": "Definição incorreta", "de": "Falsche Definition"},
    {"key": "reportModal.categoryMissingData", "en": "Missing or incomplete data", "tr": "Eksik veya tamamlanmamış veri", "ru": "Отсутствующие или неполные данные", "es": "Datos faltantes o incompletos", "pt": "Dados ausentes ou incompletos", "de": "Fehlende oder unvollständige Daten"},
    {"key": "reportModal.categoryAudioIssue", "en": "Audio issue", "tr": "Ses sorunu", "ru": "Проблема со звуком", "es": "Problema de audio", "pt": "Problema de áudio", "de": "Audioproblem"},
    {"key": "reportModal.categoryInappropriate", "en": "Inappropriate content", "tr": "Uygunsuz içerik", "ru": "Неприемлемый контент", "es": "Contenido inapropiado", "pt": "Conteúdo inadequado", "de": "Unangemessener Inhalt"},
    {"key": "reportModal.categoryOther",  "en": "Other", "tr": "Diğer", "ru": "Другое", "es": "Otro", "pt": "Outro", "de": "Andere"},
    {"key": "reportModal.successTitle",   "en": "Thanks for letting us know!", "tr": "Bize bildirdiğiniz için teşekkürler!", "ru": "Спасибо, что сообщили нам!", "es": "¡Gracias por avisarnos!", "pt": "Obrigado por nos informar!", "de": "Danke, dass du uns Bescheid gegeben hast!"},
    {"key": "reportModal.successMessage", "en": "We'll look into it and take care of it — your feedback helps make Singoling better for everyone.", "tr": "İnceleyeceğiz ve ilgileneceğiz — geri bildiriminiz Singoling'i herkes için daha iyi hale getiriyor.", "ru": "Мы рассмотрим это и позаботимся об этом — ваш отзыв помогает сделать Singoling лучше для всех.", "es": "Lo revisaremos y nos encargaremos — tu retroalimentación ayuda a mejorar Singoling para todos.", "pt": "Vamos analisar e resolver — seu feedback ajuda a tornar o Singoling melhor para todos.", "de": "Wir werden uns darum kümmern — dein Feedback hilft, Singoling für alle besser zu machen."},
    {"key": "reportModal.successClose",   "en": "Close", "tr": "Kapat", "ru": "Закрыть", "es": "Cerrar", "pt": "Fechar", "de": "Schließen"},
    {"key": "reportModal.title",          "en": "Report a problem", "tr": "Sorun bildir", "ru": "Сообщить о проблеме", "es": "Informar un problema", "pt": "Reportar um problema", "de": "Problem melden"},
    {"key": "reportModal.categoryLabel",  "en": "Category", "tr": "Kategori", "ru": "Категория", "es": "Categoría", "pt": "Categoria", "de": "Kategorie"},
    {"key": "reportModal.categoryPlaceholder", "en": "Select a category…", "tr": "Bir kategori seçin…", "ru": "Выберите категорию…", "es": "Selecciona una categoría…", "pt": "Selecione uma categoria…", "de": "Wähle eine Kategorie…"},
    {"key": "reportModal.detailsLabel",   "en": "Details", "tr": "Detaylar", "ru": "Детали", "es": "Detalles", "pt": "Detalhes", "de": "Details"},
    {"key": "reportModal.detailsOptional","en": "(optional)", "tr": "(isteğe bağlı)", "ru": "(необязательно)", "es": "(opcional)", "pt": "(opcional)", "de": "(optional)"},
    {"key": "reportModal.error",          "en": "Something went wrong. Please try again.", "tr": "Bir şeyler ters gitti. Lütfen tekrar deneyin.", "ru": "Что-то пошло не так. Пожалуйста, попробуйте снова.", "es": "Algo salió mal. Por favor, inténtalo de nuevo.", "pt": "Algo deu errado. Por favor, tente novamente.", "de": "Etwas ist schiefgelaufen. Bitte versuche es erneut."},
    {"key": "reportModal.cancel",         "en": "Cancel", "tr": "İptal", "ru": "Отмена", "es": "Cancelar", "pt": "Cancelar", "de": "Abbrechen"},
    {"key": "reportModal.sending",        "en": "Sending…", "tr": "Gönderiliyor…", "ru": "Отправка…", "es": "Enviando…", "pt": "Enviando…", "de": "Wird gesendet…"},
    {"key": "reportModal.submit",         "en": "Submit", "tr": "Gönder", "ru": "Отправить", "es": "Enviar", "pt": "Enviar", "de": "Senden"},
    {"key": "nav.admin",                    "en": "Admin",                      "tr": "Yönetici",                  "ru": "Администратор",                  "es": "Administrador",                               "pt": "Administrador",                                "de": "Administrator"},
    {"key": "nav.preferences",             "en": "Preferences",                "tr": "Tercihler",                 "ru": "Настройки",                      "es": "Preferencias",                                "pt": "Preferências",                                "de": "Einstellungen"},
    {"key": "nav.signOut",                  "en": "Sign out",                   "tr": "Çıkış yap",                 "ru": "Выйти",                           "es": "Cerrar sesión",                               "pt": "Sair",                                        "de": "Abmelden"},
    {"key": "nav.playlists",               "en": "Playlists",                  "tr": "Çalma listeleri",           "ru": "Плейлисты",                      "es": "Listas de reproducción",                      "pt": "Playlists",                                   "de": "Playlists"},
    {"key": "browser.songs",               "en": "Songs",                      "tr": "Şarkılar",                  "ru": "Песни",                           "es": "Canciones",                                  "pt": "Músicas",                                     "de": "Lieder"},
    {"key": "browser.play",                "en": "Play",                       "tr": "Oynat",                     "ru": "Играть",                          "es": "Reproducir",                                 "pt": "Reproduzir",                                  "de": "Abspielen"},
    {"key": "browser.progress",            "en": "Progress",                   "tr": "İlerleme",                  "ru": "Прогресс",                       "es": "Progreso",                                   "pt": "Progresso",                                   "de": "Fortschritt"},
    {"key": "browser.wordsLookedUp",       "en": "Words looked up",            "tr": "Aranan kelimeler",          "ru": "Слов изучено",                    "es": "Palabras consultadas",                        "pt": "Palavras consultadas",                         "de": "Nachgeschlagene Wörter"},
    {"key": "browser.loadingSongs",        "en": "Loading songs\u2026",             "tr": "Şarkılar yükleniyor\u2026",      "ru": "Загрузка песен\u2026",                 "es": "Cargando canciones\u2026",                       "pt": "Carregando músicas\u2026",                        "de": "Lieder werden geladen\u2026"},
    {"key": "browser.noSongs",             "en": "No songs available",         "tr": "Şarkı bulunamadı",          "ru": "Нет доступных песен",             "es": "No hay canciones disponibles",                "pt": "Nenhuma música disponível",                    "de": "Keine Lieder verfügbar"},
    {"key": "browser.unknownArtist",       "en": "Unknown artist",             "tr": "Bilinmeyen sanatçı",        "ru": "Неизвестный исполнитель",          "es": "Artista desconocido",                         "pt": "Artista desconhecido",                         "de": "Unbekannter Künstler"},
    {"key": "browser.addToFavorites",      "en": "Add to favorites",           "tr": "Favorilere ekle",           "ru": "В избранное",                     "es": "Añadir a favoritos",                          "pt": "Adicionar aos favoritos",                      "de": "Zu Favoriten hinzufügen"},
    {"key": "browser.removeFromFavorites", "en": "Remove from favorites",      "tr": "Favorilerden kaldır",       "ru": "Из избранного",                   "es": "Quitar de favoritos",                         "pt": "Remover dos favoritos",                        "de": "Aus Favoriten entfernen"},
    {"key": "browser.markAsNotListened",   "en": "Mark as not listened",       "tr": "Dinlenmedi olarak işaretle","ru": "Отметить как непрослушанное",    "es": "Marcar como no escuchado",                   "pt": "Marcar como não ouvido",                       "de": "Als nicht gehört markieren"},
    {"key": "browser.reportProblem",       "en": "Report a problem",           "tr": "Sorun bildir",              "ru": "Сообщить о проблеме",             "es": "Informar un problema",                        "pt": "Reportar um problema",                         "de": "Problem melden"},
    {"key": "browse.errorNoPlaylists",     "en": "Could not load the playlists.", "tr": "Çalma listeleri yüklenemedi.", "ru": "Не удалось загрузить плейлисты.", "es": "No se pudieron cargar las listas de reproducción.", "pt": "Não foi possível carregar as playlists.", "de": "Playlists konnten nicht geladen werden."},
    {"key": "browse.devInstruction",       "en": "cd backend && uvicorn main:app --reload", "tr": "cd backend && uvicorn main:app --reload", "ru": "cd backend && uvicorn main:app --reload", "es": "cd backend && uvicorn main:app --reload", "pt": "cd backend && uvicorn main:app --reload", "de": "cd backend && uvicorn main:app --reload"},
    {"key": "browse.langEN",               "en": "EN", "tr": "EN", "ru": "EN", "es": "EN", "pt": "EN", "de": "EN"},
    {"key": "browse.langTR",               "en": "TR", "tr": "TR", "ru": "TR", "es": "TR", "pt": "TR", "de": "TR"},
    {"key": "browse.langRU",               "en": "RU", "tr": "RU", "ru": "RU", "es": "RU", "pt": "RU", "de": "RU"},
    {"key": "browse.langES",               "en": "ES", "tr": "ES", "ru": "ES", "es": "ES", "pt": "ES", "de": "ES"},
    {"key": "browse.langPT",               "en": "PT", "tr": "PT", "ru": "PT", "es": "PT", "pt": "PT", "de": "PT"},
    {"key": "browse.langDE",               "en": "DE", "tr": "DE", "ru": "DE", "es": "DE", "pt": "DE", "de": "DE"},
    {"key": "browse.premiumListHeader",    "en": "Premium List", "tr": "Premium Liste", "ru": "Премиум-лист", "es": "Lista Premium", "pt": "Lista Premium", "de": "Premium-Liste"},
    {"key": "browse.trialSongsHeader",     "en": "Trial Songs", "tr": "Deneme Şarkıları", "ru": "Пробные песни", "es": "Canciones de prueba", "pt": "Músicas de teste", "de": "Test-Songs"},
    {"key": "browse.upgradeButton",        "en": "Upgrade", "tr": "Yükselt", "ru": "Обновить", "es": "Mejorar", "pt": "Atualizar", "de": "Upgraden"},
    # Account / Subscription
    {"key": "account.platform",            "en": "Platform", "tr": "Platform", "ru": "Платформа", "es": "Plataforma", "pt": "Plataforma", "de": "Plattform"},
    {"key": "account.started",             "en": "Started", "tr": "Başladı", "ru": "Начато", "es": "Iniciado", "pt": "Iniciado", "de": "Gestartet"},
    {"key": "account.expires",             "en": "Expires", "tr": "Sona eriyor", "ru": "Истекает", "es": "Expira", "pt": "Expira", "de": "Läuft ab"},
    {"key": "account.renews",              "en": "Renews", "tr": "Yenilenir", "ru": "Продлевается", "es": "Renueva", "pt": "Renova", "de": "Erneuert"},
    {"key": "account.access",              "en": "Access", "tr": "Erişim", "ru": "Доступ", "es": "Acceso", "pt": "Acesso", "de": "Zugang"},
    {"key": "account.lifetime",            "en": "Lifetime", "tr": "Yaşam boyu", "ru": "Пожизненный", "es": "De por vida", "pt": "Vitalício", "de": "Lebenslang"},
    {"key": "account.syncing",             "en": "Syncing...", "tr": "Senkronize ediliyor...", "ru": "Синхронизация...", "es": "Sincronizando...", "pt": "Sincronizando...", "de": "Synchronisiere..."},
    {"key": "account.synced",              "en": "✓ Synced", "tr": "✓ Senkronize edildi", "ru": "✓ Синхронизировано", "es": "✓ Sincronizado", "pt": "✓ Sincronizado", "de": "✓ Synchronisiert"},
    {"key": "account.syncFailed",          "en": "✗ Failed", "tr": "✗ Başarısız", "ru": "✗ Ошибка", "es": "✗ Falló", "pt": "✗ Falhou", "de": "✗ Fehlgeschlagen"},
    {"key": "account.syncButton",          "en": "Sync Subscription", "tr": "Aboneliği Senkronize Et", "ru": "Синхронизировать подписку", "es": "Sincronizar suscripción", "pt": "Sincronizar assinatura", "de": "Abonnement synchronisieren"},
    {"key": "account.syncHelper",          "en": "Refresh subscription status from Paddle", "tr": "Paddle'dan abonelik durumunu yenile", "ru": "Обновить статус подписки из Paddle", "es": "Actualizar estado de suscripción desde Paddle", "pt": "Atualizar status da assinatura do Paddle", "de": "Abonnementstatus von Paddle aktualisieren"},
    {"key": "account.premiumFeaturesHeader","en": "With Premium you get:", "tr": "Premium ile şunları elde edersiniz:", "ru": "С премиумом вы получаете:", "es": "Con Premium obtienes:", "pt": "Com Premium você obtém:", "de": "Mit Premium erhältst du:"},
    {"key": "account.manageTitle",         "en": "Manage Subscription", "tr": "Aboneliği Yönet", "ru": "Управление подпиской", "es": "Gestionar suscripción", "pt": "Gerenciar assinatura", "de": "Abonnement verwalten"},
    {"key": "account.manageDescription",   "en": "To update your payment method, billing information, or cancel your subscription, visit the Paddle billing portal.", "tr": "Ödeme yönteminizi, fatura bilgilerinizi güncellemek veya aboneliğinizi iptal etmek için Paddle fatura portalını ziyaret edin.", "ru": "Чтобы обновить способ оплаты, платежную информацию или отменить подписку, посетите портал выставления счетов Paddle.", "es": "Para actualizar tu método de pago, información de facturación o cancelar tu suscripción, visita el portal de facturación de Paddle.", "pt": "Para atualizar seu método de pagamento, informações de cobrança ou cancelar sua assinatura, visite o portal de cobrança do Paddle.", "de": "Um deine Zahlungsmethode, Rechnungsinformationen zu aktualisieren oder dein Abonnement zu kündigen, besuche das Paddle-Abrechnungsportal."},
    {"key": "account.billingPortalLink",   "en": "Open Billing Portal →", "tr": "Fatura Portalını Aç →", "ru": "Открыть портал выставления счетов →", "es": "Abrir portal de facturación →", "pt": "Abrir portal de cobrança →", "de": "Abrechnungsportal öffnen →"},
    {"key": "account.planSuffix",          "en": "Plan", "tr": "Plan", "ru": "План", "es": "Plan", "pt": "Plano", "de": "Plan"},
    {"key": "account.statusActive",        "en": "Active", "tr": "Aktif", "ru": "Активна", "es": "Activo", "pt": "Ativo", "de": "Aktiv"},
    {"key": "account.statusPastDue",       "en": "Past Due", "tr": "Gecikmiş", "ru": "Просрочено", "es": "Vencido", "pt": "Vencido", "de": "Überfällig"},
    {"key": "account.statusCanceled",      "en": "Canceled", "tr": "İptal edildi", "ru": "Отменена", "es": "Cancelado", "pt": "Cancelado", "de": "Gekündigt"},
    {"key": "account.statusFreeTier",      "en": "Free Tier", "tr": "Ücretsiz Katman", "ru": "Бесплатный уровень", "es": "Nivel gratuito", "pt": "Nível gratuito", "de": "Kostenlose Stufe"},
    {"key": "account.statusInactive",      "en": "Inactive", "tr": "İnaktif", "ru": "Неактивна", "es": "Inactivo", "pt": "Inativo", "de": "Inaktiv"},
    {"key": "account.upgradeButton",       "en": "Upgrade", "tr": "Yükselt", "ru": "Обновить", "es": "Mejorar", "pt": "Atualizar", "de": "Upgraden"},
    # Settings / Preferences
    {"key": "settings.preferences",       "en": "Preferences",                "tr": "Tercihler",                 "ru": "Настройки",                      "es": "Preferencias",                                "pt": "Preferências",                                "de": "Einstellungen"},
    {"key": "settings.account",           "en": "Account",                    "tr": "Hesap",                     "ru": "Аккаунт",                        "es": "Cuenta",                                     "pt": "Conta",                                       "de": "Konto"},
    {"key": "settings.subscription",      "en": "Subscription",               "tr": "Abonelik",                  "ru": "Подписка",                       "es": "Suscripción",                                "pt": "Assinatura",                                  "de": "Abonnement"},
    {"key": "settings.support",           "en": "Support",                    "tr": "Destek",                    "ru": "Поддержка",                      "es": "Soporte",                                    "pt": "Suporte",                                     "de": "Support"},
    {"key": "settings.musicSource",       "en": "Music source",               "tr": "Müzik kaynağı",             "ru": "Источник музыки",                 "es": "Fuente de música",                            "pt": "Fonte de música",                              "de": "Musikquelle"},
    {"key": "settings.musicSourceDesc",   "en": "Choose whether to use YouTube or Apple Music.", "tr": "Müziğin nereden çalınacağını seçin", "ru": "Выберите источник воспроизведения", "es": "Elige si usar YouTube o Apple Music.", "pt": "Escolha usar YouTube ou Apple Music.", "de": "Wähle YouTube oder Apple Music."},
    {"key": "settings.prioritizeContentWords",     "en": "Prioritize content words for 1-9 shortcuts",      "tr": "İçerik kelimelerine öncelik ver",   "ru": "Приоритет смысловых слов",   "es": "Priorizar palabras de contenido para atajos 1-9",  "pt": "Priorizar palavras de conteúdo para atalhos 1-9", "de": "Inhaltswörter für 1-9-Kürzel priorisieren"},
    {"key": "settings.prioritizeContentWordsDesc", "en": "When on, shortcut numbers skip common stop words (pronouns, prepositions, conjunctions) and target more meaningful words first.", "tr": "Önemli kelimelerin tanımlarını önce göster", "ru": "Сначала показывать определения важных слов", "es": "Cuando está activado, los atajos saltean palabras funcionales (pronombres, preposiciones, conjunciones) y se dirigen primero a palabras más significativas.", "pt": "Quando ativado, os atalhos ignoram palavras funcionais (pronomes, preposições, conjunções) e focam primeiro nas palavras mais significativas.", "de": "Wenn aktiviert, überspringen die Kürzel Funktionswörter (Pronomen, Präpositionen, Konjunktionen) und zielen zuerst auf bedeutungsvollere Wörter."},
    {"key": "settings.pauseOnInspect",    "en": "Pause playback while inspecting lyrics",           "tr": "İnceleme sırasında duraklat", "ru": "Пауза при изучении",  "es": "Pausar reproducción al inspeccionar letra",       "pt": "Pausar reprodução ao inspecionar a letra",        "de": "Wiedergabe bei Textprüfung pausieren"},
    {"key": "settings.pauseOnInspectDesc","en": "When on, playback pauses while definition/translation panels are open and resumes when you close them.", "tr": "Kelime incelerken oynatmayı duraklat", "ru": "Ставить на паузу при изучении слова", "es": "Cuando está activado, la reproducción se pausa mientras los paneles de definición/traducción están abiertos y se reanuda al cerrarlos.", "pt": "Quando ativado, a reprodução pausa enquanto os painéis de definição/tradução estão abertos e retoma ao fechá-los.", "de": "Wenn aktiviert, pausiert die Wiedergabe, während Definitions-/Übersetzungspanels geöffnet sind, und wird beim Schließen fortgesetzt."},
    {"key": "settings.connected",         "en": "Connected",                  "tr": "Bağlı",                     "ru": "Подключено",                     "es": "Conectado",                                  "pt": "Conectado",                                   "de": "Verbunden"},
    {"key": "settings.notConnected",      "en": "Not connected",              "tr": "Bağlı değil",               "ru": "Не подключено",                  "es": "No conectado",                               "pt": "Não conectado",                               "de": "Nicht verbunden"},
    {"key": "settings.appleMusic",        "en": "Apple Music",                "tr": "Apple Music",               "ru": "Apple Music",                    "es": "Apple Music",                                "pt": "Apple Music",                                 "de": "Apple Music"},
    {"key": "settings.contactUs",         "en": "Contact us",                 "tr": "Bize ulaşın",               "ru": "Связаться с нами",               "es": "Contáctenos",                                "pt": "Contate-nos",                                 "de": "Kontakt"},
    {"key": "settings.contactUsDesc",     "en": "Have a question or found an issue? We're happy to help.", "tr": "Bize mesaj gönderin",       "ru": "Напишите нам", "es": "¿Tienes una pregunta o encontraste un problema? Estamos aquí para ayudar.", "pt": "Tem uma dúvida ou encontrou um problema? Estamos aqui para ajudar.", "de": "Eine Frage oder ein Problem? Wir helfen gerne."},
    {"key": "settings.subject",           "en": "Subject",                    "tr": "Konu",                      "ru": "Тема",                           "es": "Asunto",                                     "pt": "Assunto",                                     "de": "Betreff"},
    {"key": "settings.message",           "en": "Message",                    "tr": "Mesaj",                     "ru": "Сообщение",                      "es": "Mensaje",                                    "pt": "Mensagem",                                    "de": "Nachricht"},
    {"key": "settings.sendMessage",       "en": "Send message",               "tr": "Mesaj gönder",              "ru": "Отправить сообщение",            "es": "Enviar mensaje",                              "pt": "Enviar mensagem",                              "de": "Nachricht senden"},
    {"key": "settings.sendAnotherMessage","en": "Send another message",       "tr": "Başka mesaj gönder",        "ru": "Отправить ещё",                  "es": "Enviar otro mensaje",                         "pt": "Enviar outra mensagem",                        "de": "Weitere Nachricht senden"},
    {"key": "settings.messageSent",       "en": "Message sent",               "tr": "Mesaj gönderildi",          "ru": "Сообщение отправлено",           "es": "Mensaje enviado",                             "pt": "Mensagem enviada",                             "de": "Nachricht gesendet"},
    {"key": "settings.messageReply",      "en": "We'll get back to you as soon as possible.", "tr": "E-postanıza yanıt vereceğiz.", "ru": "Мы ответим на ваш email.", "es": "Te responderemos lo antes posible.", "pt": "Responderemos o mais rápido possível.", "de": "Wir melden uns so schnell wie möglich."},
    {"key": "settings.subscriptionManagement", "en": "Subscription management",   "tr": "Aboneliği yönet",           "ru": "Управление подпиской",           "es": "Gestión de suscripción",                      "pt": "Gerenciamento de assinatura",                  "de": "Abonnementverwaltung"},
    {"key": "settings.subscriptionDesc",  "en": "Subscription details and billing will be available here soon.", "tr": "Aboneliğinizi App Store veya cihaz ayarlarınızdan yönetin.", "ru": "Управляйте подпиской в App Store или настройках устройства.", "es": "Los detalles de suscripción y facturación estarán disponibles pronto.", "pt": "Os detalhes de assinatura e cobrança estarão disponíveis em breve.", "de": "Abonnementdetails und Abrechnung werden bald verfügbar sein."},
    {"key": "settings.unknownUser",       "en": "Unknown user",               "tr": "Bilinmeyen kullanıcı",      "ru": "Неизвестный пользователь",       "es": "Usuario desconocido",                         "pt": "Usuário desconhecido",                         "de": "Unbekannter Benutzer"},
    {"key": "settings.uiLanguage",        "en": "Interface language",         "tr": "Arayüz dili",               "ru": "Язык интерфейса",                "es": "Idioma de la interfaz",                       "pt": "Idioma da interface",                          "de": "Anzeigesprache"},
    {"key": "settings.uiLanguageDesc",    "en": "Choose the language used throughout the app UI.", "tr": "Uygulama genelinde kullanılan dili seçin.", "ru": "Выберите язык интерфейса.", "es": "Elige el idioma de la interfaz de la aplicación.", "pt": "Escolha o idioma da interface do aplicativo.", "de": "Wähle die Sprache der App-Oberfläche."},
    {"key": "settings.sourceYoutubeDesc",    "en": "Embed YouTube videos when available",          "tr": "Mevcut olduğunda YouTube videolarını göster",  "ru": "Встраивать YouTube-видео при наличии",  "es": "Insertar videos de YouTube cuando estén disponibles", "pt": "Incorporar vídeos do YouTube quando disponíveis", "de": "YouTube-Videos einbetten, wenn verfügbar"},
    {"key": "settings.sourceAppleMusicDesc", "en": "Use Apple Music (requires subscription)",       "tr": "Apple Music kullan (abonelik gerektirir)",     "ru": "Использовать Apple Music (нужна подписка)", "es": "Usar Apple Music (requiere suscripción)",       "pt": "Usar Apple Music (requer assinatura)",          "de": "Apple Music verwenden (Abonnement erforderlich)"},
    {"key": "settings.subjectPlaceholder",   "en": "e.g. Bug report, Feature request\u2026",        "tr": "örn. Hata bildirimi, Özellik isteği\u2026",    "ru": "напр. Сообщение об ошибке, Пожелание\u2026", "es": "p.ej. Informe de error, Solicitud de función\u2026", "pt": "ex. Relatório de bug, Solicitação de recurso\u2026", "de": "z.B. Fehlerbericht, Funktionswunsch\u2026"},
    {"key": "settings.messagePlaceholder",   "en": "Describe your issue or question\u2026",         "tr": "Sorununuzu veya sorunuzu açıklayın\u2026",     "ru": "Опишите вашу проблему или вопрос\u2026", "es": "Describe tu problema o pregunta\u2026",         "pt": "Descreva seu problema ou pergunta\u2026",        "de": "Beschreibe dein Problem oder deine Frage\u2026"},
    {"key": "settings.legalHeader",          "en": "Legal", "tr": "Yasal", "ru": "Юридическая информация", "es": "Legal", "pt": "Legal", "de": "Rechtliches"},
    {"key": "settings.termsOfService",       "en": "Terms of Service", "tr": "Hizmet Şartları", "ru": "Условия обслуживания", "es": "Términos de servicio", "pt": "Termos de serviço", "de": "Nutzungsbedingungen"},
    {"key": "settings.privacyPolicy",        "en": "Privacy Policy", "tr": "Gizlilik Politikası", "ru": "Политика конфиденциальности", "es": "Política de privacidad", "pt": "Política de privacidade", "de": "Datenschutzrichtlinie"},
    # Pricing / Subscription
    {"key": "pricing.saveBadge",          "en": "(Save {percentage}%)",       "tr": "(%{percentage} tasarruf)",  "ru": "(Скидка {percentage}%)",        "es": "(Ahorra {percentage}%)",                      "pt": "(Economize {percentage}%)",                    "de": "({percentage}% sparen)"},
    {"key": "subscriptions.alertPaddleLoading", "en": "Payment system is loading. Please try again in a moment.", "tr": "Ödeme sistemi yükleniyor. Lütfen bir dakika sonra tekrar deneyin.", "ru": "Система оплаты загружается. Пожалуйста, попробуйте через минуту.", "es": "El sistema de pago se está cargando. Por favor, inténtalo de nuevo en un momento.", "pt": "O sistema de pagamento está carregando. Por favor, tente novamente em um momento.", "de": "Das Zahlungssystem wird geladen. Bitte versuche es gleich noch einmal."},
    {"key": "subscriptions.alertPricingUnavailable", "en": "Pricing information unavailable. Please try again later.", "tr": "Fiyatlandırma bilgisi mevcut değil. Lütfen daha sonra tekrar deneyin.", "ru": "Информация о ценах недоступна. Пожалуйста, попробуйте позже.", "es": "Información de precios no disponible. Por favor, inténtalo más tarde.", "pt": "Informações de preços indisponíveis. Por favor, tente novamente mais tarde.", "de": "Preisinformationen nicht verfügbar. Bitte versuche es später erneut."},
    {"key": "subscriptions.feature1",     "en": "Super fast and interactive translation along synced lyrics", "tr": "Senkronize şarkı sözleriyle süper hızlı ve interaktif çeviri", "ru": "Супер быстрый и интерактивный перевод вместе с синхронизированными текстами", "es": "Traducción súper rápida e interactiva junto con letras sincronizadas", "pt": "Tradução super rápida e interativa junto com letras sincronizadas", "de": "Superschnelle und interaktive Übersetzung mit synchronisierten Texten"},
    {"key": "subscriptions.feature2",     "en": "Curated songs for your level", "tr": "Seviyeniz için seçilmiş şarkılar", "ru": "Подобранные песни для вашего уровня", "es": "Canciones seleccionadas para tu nivel", "pt": "Músicas selecionadas para o seu nível", "de": "Kuratierte Songs für dein Level"},
    {"key": "subscriptions.feature3",     "en": "Instant definition lookups & quick keyboard shortcuts", "tr": "Anında tanım aramaları ve hızlı klavye kısayolları", "ru": "Мгновенный поиск определений и быстрые горячие клавиши", "es": "Búsquedas instantáneas de definiciones y atajos de teclado rápidos", "pt": "Buscas instantâneas de definições e atalhos de teclado rápidos", "de": "Sofortige Definitionssuchen und schnelle Tastaturkürzel"},
    {"key": "subscriptions.feature4",     "en": "Instant full-line translations", "tr": "Anında tam satır çevirileri", "ru": "Мгновенные переводы полных строк", "es": "Traducciones instantáneas de líneas completas", "pt": "Traduções instantâneas de linhas completas", "de": "Sofortige Vollzeilenübersetzungen"},
    {"key": "subscriptions.feature5",     "en": "Unlimited songs in our whole library", "tr": "Tüm kütüphanemizde sınırsız şarkı", "ru": "Неограниченные песни во всей нашей библиотеке", "es": "Canciones ilimitadas en toda nuestra biblioteca", "pt": "Músicas ilimitadas em toda a nossa biblioteca", "de": "Unbegrenzte Songs in unserer gesamten Bibliothek"},
    {"key": "subscriptions.feature6",     "en": "Translate to all language options for each playlist", "tr": "Her çalma listesi için tüm dil seçeneklerine çeviri", "ru": "Перевод на все языковые опции для каждого плейлиста", "es": "Traducir a todas las opciones de idioma para cada lista de reproducción", "pt": "Traduzir para todas as opções de idioma de cada playlist", "de": "Übersetzen in alle Sprachoptionen für jede Playlist"},
    {"key": "subscriptions.loadingPricing","en": "Loading pricing...", "tr": "Fiyatlandırma yükleniyor...", "ru": "Загрузка цен...", "es": "Cargando precios...", "pt": "Carregando preços...", "de": "Preise werden geladen..."},
    {"key": "subscriptions.backButton",   "en": "Back", "tr": "Geri", "ru": "Назад", "es": "Atrás", "pt": "Voltar", "de": "Zurück"},
    {"key": "subscriptions.closeButton",  "en": "Close", "tr": "Kapat", "ru": "Закрыть", "es": "Cerrar", "pt": "Fechar", "de": "Schließen"},
    {"key": "subscriptions.heading",      "en": "Upgrade to Premium", "tr": "Premium'a Yükselt", "ru": "Обновиться до премиума", "es": "Actualizar a Premium", "pt": "Atualizar para Premium", "de": "Auf Premium upgraden"},
    {"key": "subscriptions.subtitle",     "en": "Unlock unlimited interactive lyrics, translations, and word definitions across all songs", "tr": "Tüm şarkılarda sınırsız interaktif şarkı sözleri, çeviriler ve kelime tanımlarının kilidini açın", "ru": "Разблокируйте неограниченные интерактивные тексты, переводы и определения слов для всех песен", "es": "Desbloquea letras interactivas ilimitadas, traducciones y definiciones de palabras en todas las canciones", "pt": "Desbloqueie letras interativas ilimitadas, traduções e definições de palavras em todas as músicas", "de": "Schalte unbegrenzte interaktive Texte, Übersetzungen und Wortdefinitionen für alle Songs frei"},
    {"key": "subscriptions.monthly",      "en": "Monthly", "tr": "Aylık", "ru": "Ежемесячно", "es": "Mensual", "pt": "Mensal", "de": "Monatlich"},
    {"key": "subscriptions.annual",       "en": "Annual", "tr": "Yıllık", "ru": "Ежегодно", "es": "Anual", "pt": "Anual", "de": "Jährlich"},
    {"key": "subscriptions.yearPeriod",   "en": "year", "tr": "yıl", "ru": "год", "es": "año", "pt": "ano", "de": "Jahr"},
    {"key": "subscriptions.monthPeriod",  "en": "month", "tr": "ay", "ru": "месяц", "es": "mes", "pt": "mês", "de": "Monat"},
    {"key": "subscriptions.savingsDescription", "en": "Just {price}/month — Save {amount}/year", "tr": "Sadece {price}/ay — Yılda {amount} tasarruf edin", "ru": "Всего {price}/месяц — Экономьте {amount}/год", "es": "Solo {price}/mes — Ahorra {amount}/año", "pt": "Apenas {price}/mês — Economize {amount}/ano", "de": "Nur {price}/Monat — Spare {amount}/Jahr"},
    {"key": "subscriptions.checkoutLoading","en": "Loading...", "tr": "Yükleniyor...", "ru": "Загрузка...", "es": "Cargando...", "pt": "Carregando...", "de": "Wird geladen..."},
    {"key": "subscriptions.checkoutDisabled","en": "Pricing unavailable", "tr": "Fiyatlandırma mevcut değil", "ru": "Цены недоступны", "es": "Precios no disponibles", "pt": "Preços indisponíveis", "de": "Preise nicht verfügbar"},
    {"key": "subscriptions.checkoutCTA",  "en": "Start Learning Now", "tr": "Şimdi Öğrenmeye Başla", "ru": "Начать учиться сейчас", "es": "Comenzar a aprender ahora", "pt": "Começar a aprender agora", "de": "Jetzt mit dem Lernen beginnen"},
    {"key": "subscriptions.featuresHeading","en": "It lets you focus on fun while you get what you need instantly", "tr": "Eğlenceye odaklanmanızı sağlarken ihtiyacınız olanı anında almanızı sağlar", "ru": "Это позволяет сосредоточиться на удовольствии, мгновенно получая то, что вам нужно", "es": "Te permite concentrarte en la diversión mientras obtienes lo que necesitas al instante", "pt": "Permite que você se concentre na diversão enquanto obtém o que precisa instantaneamente", "de": "Damit kannst du dich auf den Spaß konzentrieren und bekommst sofort, was du brauchst"},
    {"key": "subscriptions.masterLanguageHeading","en": "Everything you need to master a new language", "tr": "Yeni bir dili öğrenmek için ihtiyacınız olan her şey", "ru": "Все, что нужно для освоения нового языка", "es": "Todo lo que necesitas para dominar un nuevo idioma", "pt": "Tudo o que você precisa para dominar um novo idioma", "de": "Alles, was du brauchst, um eine neue Sprache zu meistern"},
    # Inspect / Lyrics shortcuts
    {"key": "inspect.title",              "en": "INSPECT LYRICS",             "tr": "SÖZLERE BAK",               "ru": "ТЕКСТ ПЕСНИ",                    "es": "INSPECCIONAR LETRA",                         "pt": "INSPECIONAR LETRA",                           "de": "TEXT PRÜFEN"},
    {"key": "inspect.numberedWord",       "en": "Inspect a numbered word",    "tr": "Numaralı kelimeye bak",     "ru": "Изучить пронумерованное слово",  "es": "Inspeccionar una palabra numerada",           "pt": "Inspecionar uma palavra numerada",             "de": "Nummeriertes Wort prüfen"},
    {"key": "inspect.sentenceTranslation","en": "Sentence translation",       "tr": "Cümle çevirisi",            "ru": "Перевод предложения",            "es": "Traducción de la frase",                      "pt": "Tradução da frase",                            "de": "Satzübersetzung"},
    {"key": "inspect.peekWithoutPinning", "en": "Peek without pinning",       "tr": "Sabitleme ile gözetleme",   "ru": "Подглядеть без закрепления",     "es": "Ver sin fijar",                              "pt": "Espiar sem fixar",                             "de": "Vorschau ohne Pinnen"},
    {"key": "inspect.hold",               "en": "hold",                       "tr": "basılı tut",                "ru": "удерживать",                     "es": "mantener",                                   "pt": "manter",                                     "de": "halten"},
    {"key": "inspect.playPause",          "en": "Play / pause",               "tr": "Oynat / duraklat",          "ru": "Воспроизведение / пауза",        "es": "Reproducir / pausar",                         "pt": "Reproduzir / pausar",                          "de": "Abspielen / Pause"},
    {"key": "inspect.seekPrevNextLine",   "en": "Seek to prev / next line",   "tr": "Önceki / sonraki satıra git","ru": "К пред. / следующей строке",  "es": "Ir a la línea anterior / siguiente",           "pt": "Ir para linha anterior / seguinte",             "de": "Zur vor. / nächsten Zeile"},
    {"key": "inspect.prevNextSong",       "en": "Prev / next song",           "tr": "Önceki / sonraki şarkı",    "ru": "Пред. / следующая песня",       "es": "Canción anterior / siguiente",                "pt": "Música anterior / seguinte",                   "de": "Vor. / nächstes Lied"},
    {"key": "inspect.definition",         "en": "Definition",                 "tr": "Tanım",                     "ru": "Определение",                   "es": "Definición",                                 "pt": "Definição",                                   "de": "Definition"},
    {"key": "inspect.translation",        "en": "Translation",                "tr": "Çeviri",                    "ru": "Перевод",                       "es": "Traducción",                                 "pt": "Tradução",                                    "de": "Übersetzung"},
    {"key": "inspect.close",              "en": "Close",                      "tr": "Kapat",                     "ru": "Закрыть",                       "es": "Cerrar",                                     "pt": "Fechar",                                     "de": "Schließen"},
    {"key": "inspect.noDefinition",       "en": "No definition yet",          "tr": "Henüz tanım yok",           "ru": "Определение пока отсутствует",  "es": "Aún no hay definición",                       "pt": "Ainda sem definição",                          "de": "Noch keine Definition"},
    {"key": "inspect.infinitive",         "en": "infinitive",                 "tr": "mastar",                    "ru": "инфинитив",                     "es": "infinitivo",                                 "pt": "infinitivo",                                 "de": "Infinitiv"},
    {"key": "inspect.nominative",         "en": "nominative",                 "tr": "yalın hal",                 "ru": "именительный",                  "es": "nominativo",                                 "pt": "nominativo",                                 "de": "Nominativ"},
    {"key": "inspect.noTranslation",      "en": "No translation available for this line yet", "tr": "Bu satır için henüz çeviri yok", "ru": "Перевод для этой строки пока недоступен", "es": "Aún no hay traducción disponible para esta línea", "pt": "Ainda não há tradução disponível para esta linha", "de": "Noch keine Übersetzung für diese Zeile verfügbar"},
    # Player empty state
    {"key": "player.waitingForPlayback",  "en": "Waiting for playback...",    "tr": "Oynatma bekleniyor...",     "ru": "Ожидание воспроизведения...",   "es": "Esperando reproducción...",                   "pt": "Aguardando reprodução...",                     "de": "Warte auf Wiedergabe..."},
    {"key": "player.loadAndPlay",         "en": "Load a track and press Play","tr": "Bir parça yükle ve Oynat'a bas", "ru": "Загрузите трек и нажмите Воспроизвести", "es": "Carga una pista y presiona Reproducir", "pt": "Carregue uma faixa e pressione Reproduzir", "de": "Lade einen Track und drücke Abspielen"},
    # Language names
    {"key": "language.ru",  "en": "Russian",    "tr": "Rusça",      "ru": "Русский",      "es": "Ruso",        "pt": "Russo",       "de": "Russisch"},
    {"key": "language.en",  "en": "English",    "tr": "İngilizce",  "ru": "Английский",  "es": "Inglés",      "pt": "Inglês",      "de": "Englisch"},
    {"key": "language.es",  "en": "Spanish",    "tr": "İspanyolca", "ru": "Испанский",   "es": "Español",     "pt": "Espanhol",   "de": "Spanisch"},
    {"key": "language.fr",  "en": "French",     "tr": "Fransızca",  "ru": "Французский", "es": "Francés",     "pt": "Francês",    "de": "Französisch"},
    {"key": "language.de",  "en": "German",     "tr": "Almanca",    "ru": "Немецкий",    "es": "Alemán",      "pt": "Alemão",      "de": "Deutsch"},
    {"key": "language.it",  "en": "Italian",    "tr": "İtalyanca",  "ru": "Итальянский", "es": "Italiano",    "pt": "Italiano",   "de": "Italienisch"},
    {"key": "language.pt",  "en": "Portuguese", "tr": "Portekizce", "ru": "Португальский","es": "Portugués",   "pt": "Português",  "de": "Portugiesisch"},
    {"key": "language.ja",  "en": "Japanese",   "tr": "Japonca",    "ru": "Японский",    "es": "Japonés",     "pt": "Japonês",    "de": "Japanisch"},
    {"key": "language.ko",  "en": "Korean",     "tr": "Korece",     "ru": "Корейский",   "es": "Coreano",     "pt": "Coreano",    "de": "Koreanisch"},
    {"key": "language.zh",  "en": "Chinese",    "tr": "Çince",      "ru": "Китайский",   "es": "Chino",       "pt": "Chinês",     "de": "Chinesisch"},
    {"key": "language.tr",  "en": "Turkish",    "tr": "Türkçe",     "ru": "Турецкий",    "es": "Turco",       "pt": "Turco",      "de": "Türkisch"},
    # Browse / Discover
    {"key": "browse.learnTitle",    "en": "I want to improve",                            "tr": "Geliştirmek istiyorum",                     "ru": "Хочу улучшить",             "es": "Quiero mejorar",                              "pt": "Quero melhorar",                              "de": "Ich möchte mich verbessern"},
    {"key": "browse.learnSubtitle", "en": "Choose the language you want to learn",         "tr": "Öğrenmek istediğiniz dili seçin",           "ru": "Выберите язык для изучения", "es": "Elige el idioma que quieres aprender",         "pt": "Escolha o idioma que deseja aprender",         "de": "Wähle die Sprache, die du lernen möchtest"},
    {"key": "browse.playlist",      "en": "playlist",                                      "tr": "çalma listesi",                             "ru": "плейлист",                 "es": "lista de reproducción",                       "pt": "playlist",                                   "de": "Playlist"},
    {"key": "browse.playlists",     "en": "playlists",                                     "tr": "çalma listesi",                             "ru": "плейлистов",               "es": "listas de reproducción",                      "pt": "playlists",                                  "de": "Playlists"},
    {"key": "browse.song",          "en": "song",                                          "tr": "şarkı",                                     "ru": "песня",                    "es": "canción",                                    "pt": "música",                                     "de": "Lied"},
    {"key": "browse.songs",         "en": "songs",                                         "tr": "şarkı",                                     "ru": "песен",                    "es": "canciones",                                  "pt": "músicas",                                    "de": "Lieder"},
    {"key": "browse.speakTitle",    "en": "I speak",                                       "tr": "Konuştuğum dil",                            "ru": "Я говорю на",              "es": "Hablo",                                      "pt": "Falo",                                        "de": "Ich spreche"},
    {"key": "browse.speakSubtitle", "en": "Choose your native language for translations",  "tr": "Çeviriler için ana dilinizi seçin",         "ru": "Выберите родной язык для переводов", "es": "Elige tu idioma nativo para traducciones",     "pt": "Escolha seu idioma nativo para traduções",     "de": "Wähle deine Muttersprache für Übersetzungen"},
    {"key": "browse.playlistsTitle","en": "Playlists",                                     "tr": "Çalma listeleri",                           "ru": "Плейлисты",                "es": "Listas de reproducción",                      "pt": "Playlists",                                  "de": "Playlists"},
    {"key": "browse.available",     "en": "available",                                     "tr": "mevcut",                                    "ru": "доступно",                 "es": "disponibles",                                 "pt": "disponíveis",                                 "de": "verfügbar"},
    {"key": "browse.noPlaylists",   "en": "No playlists for this language pair yet.",      "tr": "Bu dil çifti için henüz çalma listesi yok.","ru": "Пока нет плейлистов для этой пары языков.", "es": "Aún no hay listas de reproducción para este par de idiomas.", "pt": "Ainda não há playlists para este par de idiomas.", "de": "Noch keine Playlists für dieses Sprachpaar."},
    # Difficulty tags
    {"key": "general.Tag.Difficulty.Beginner",     "en": "Beginner",     "tr": "Başlangıç",  "ru": "Начальный",      "es": "Principiante", "pt": "Iniciante",     "de": "Anfänger"},
    {"key": "general.Tag.Difficulty.Intermediate", "en": "Intermediate", "tr": "Orta Düzey", "ru": "Промежуточный",  "es": "Intermedio",   "pt": "Intermediário", "de": "Mittelstufe"},
    {"key": "general.Tag.Difficulty.Advanced",     "en": "Advanced",     "tr": "İleri Düzey","ru": "Продвинутый",    "es": "Avanzado",     "pt": "Avançado",      "de": "Fortgeschritten"},
    # Russian playlist names & descriptions
    {"key": "playlist.Russian.Beginner",
     "en": "Russian - Beginner",
     "tr": "Rusça - Başlangıç",
     "ru": "Русский - Начальный уровень",
     "es": "Ruso - Principiante",
     "pt": "Russo - Iniciante",
     "de": "Russisch - Anfänger"},
    {"key": "playlist.Description.Russian.Beginner",
     "en": "Start here. These songs move slowly enough to catch every word, and the language is exactly what you'd learn in your first few months — everyday vocabulary, simple structures, nothing to fear.",
     "tr": "Buradan başlayın. Bu şarkılar her kelimeyi yakalamak için yeterince yavaş hareket ediyor ve dil, ilk birkaç ayınızda öğreneceğiniz tam olarak — günlük kelime dağarcığı, basit yapılar, korkulacak bir şey yok.",
     "ru": "Начните здесь. Эти песни достаточно медленные, чтобы уловить каждое слово, а язык — именно то, что вы учите в первые месяцы: повседневный словарь, простые конструкции, ничего сложного.",
     "es": "Empieza aquí. Estas canciones avanzan con la suficiente lentitud para captar cada palabra, y el idioma es exactamente lo que aprenderías en tus primeros meses: vocabulario cotidiano, estructuras simples, nada que temer.",
     "pt": "Comece aqui. Essas músicas avançam devagar o suficiente para você captar cada palavra, e o idioma é exatamente o que você aprenderia nos seus primeiros meses — vocabulário do dia a dia, estruturas simples, nada para temer.",
     "de": "Fang hier an. Diese Lieder bewegen sich langsam genug, um jedes Wort zu erfassen, und die Sprache ist genau das, was du in deinen ersten Monaten lernst – Alltagsvokabular, einfache Strukturen, nichts zu fürchten."},
    {"key": "playlist.Russian.Intermediate",
     "en": "Russian - Intermediate",
     "tr": "Rusça - Orta Seviye",
     "ru": "Русский - Средний уровень",
     "es": "Ruso - Intermedio",
     "pt": "Russo - Intermediário",
     "de": "Russisch - Mittelstufe"},
    {"key": "playlist.Description.Russian.Intermediate",
     "en": "You know the basics — now it's time to actually sound like someone who means it. These songs sit in the sweet spot where the vocabulary stretches you without losing you, and the themes are real: cities, love, growing up, goodbyes.",
     "tr": "Temelleri biliyorsunuz — şimdi gerçekten bunu kasteden biri gibi görünmenin zamanı geldi. Bu şarkılar, kelime dağarcığının sizi kaybetmeden uzattığı tatlı noktada oturuyor ve temalar gerçek: şehirler, aşk, büyüme, vedalar.",
     "ru": "Вы знаете основы — теперь пришло время действительно звучать как кто-то, кто это имеет в виду. Эти песни находятся в сладком месте, где словарный запас растягивает вас, не теряя вас, и темы реальны: города, любовь, взросление, прощания.",
     "es": "Ya conoces los básicos — ahora es hora de sonar como alguien que lo vive de verdad. Estas canciones están en el punto justo donde el vocabulario te desafía sin perderte, y los temas son reales: ciudades, amor, crecer, despedidas.",
     "pt": "Você conhece o básico — agora é hora de soar como alguém que realmente quer isso. Essas músicas ficam no ponto ideal onde o vocabulário te desafia sem te perder, e os temas são reais: cidades, amor, crescimento, despedidas.",
     "de": "Du kennst die Grundlagen – jetzt ist es Zeit, wirklich so zu klingen, als ob du es meinst. Diese Lieder befinden sich genau im Bereich, wo der Wortschatz dich herausfordert, ohne dich zu verlieren, und die Themen sind real: Städte, Liebe, Aufwachsen, Abschiede."},
    {"key": "playlist.Russian.Advanced",
     "en": "Russian - Advanced",
     "tr": "Rusça - İleri Düzey",
     "ru": "Русский - Продвинутый уровень",
     "es": "Ruso - Avanzado",
     "pt": "Russo - Avançado",
     "de": "Russisch - Fortgeschritten"},
    {"key": "playlist.Description.Russian.Advanced",
     "en": "You're past survival Russian. These songs push into poetry, subtext, and the kind of cultural weight that only lands when you've been living in the language. Dense, layered, and worth every second.",
     "tr": "Hayatta kalma Rusçasının ötesine geçtiniz. Bu şarkılar şiir, alt metin ve dilde yaşadığınızda ortaya çıkan kültürel ağırlık türüne doğru ilerliyor. Yoğun, katmanlı ve her saniyeye değer.",
     "ru": "Вы вышли за рамки «разговорного минимума». Эти песни уходят в поэзию, подтекст и тот культурный пласт, который открывается только тем, кто живёт в языке. Плотно, многослойно и стоит каждой секунды.",
     "es": "Ya superaste el ruso de supervivencia. Estas canciones se adentran en la poesía, el subtexto y ese peso cultural que solo aterriza cuando llevas tiempo viviendo el idioma. Denso, lleno de capas y vale cada segundo.",
     "pt": "Você foi além do russo de sobrevivência. Essas músicas mergulham na poesia, no subtexto e no tipo de peso cultural que só faz sentido quando você está vivendo o idioma. Denso, cheio de camadas e vale cada segundo.",
     "de": "Du bist über das Überlebensrussisch hinaus. Diese Lieder gehen in Poesie, Subtext und die Art von kulturellem Gewicht, das nur landet, wenn du in der Sprache gelebt hast. Dicht, vielschichtig und jeden Moment wert."},
    # English playlist names & descriptions
    {"key": "playlist.English.Beginner",
     "en": "English - Beginner",
     "tr": "İngilizce - Başlangıç",
     "ru": "Английский - Начальный уровень",
     "es": "Inglés - Principiante",
     "pt": "Inglês - Iniciante",
     "de": "Englisch - Anfänger"},
    {"key": "playlist.Description.English.Beginner",
     "en": "Build a strong foundation. Slow tempos and everyday words to get you singing along in no time.",
     "tr": "Sağlam bir temel oluşturun. Yavaş tempolar ve günlük kelimeler sizi çok kısa sürede söylemeye başlatacak.",
     "ru": "Заложите прочную основу. Медленный темп и повседневные слова помогут вам запеть в кратчайшие сроки.",
     "es": "Construye una base sólida. Tempos lentos y palabras cotidianas para que empieces a cantar en poco tiempo.",
     "pt": "Construa uma base sólida. Tempos lentos e palavras do dia a dia para você cantar junto em pouco tempo.",
     "de": "Bau dir eine starke Grundlage. Langsame Tempos und alltägliche Wörter, damit du in kürzester Zeit mitsingen kannst."},
    {"key": "playlist.English.Intermediate",
     "en": "English - Intermediate",
     "tr": "İngilizce - Orta Düzey",
     "ru": "Английский - Средний уровень",
     "es": "Inglés - Intermedio",
     "pt": "Inglês - Intermediário",
     "de": "Englisch - Mittelstufe"},
    {"key": "playlist.Description.English.Intermediate",
     "en": "Ready for more? Discover new vocabulary, idioms, and natural rhythms with these engaging tracks.",
     "tr": "Daha fazlasına hazır mısınız? Bu ilgi çekici parçalarla yeni kelime bilgisi, deyimler ve doğal ritimler keşfedin.",
     "ru": "Готовы к большему? Откройте для себя новую лексику, идиомы и естественные ритмы с этими увлекательными треками.",
     "es": "¿Listo para más? Descubre nuevo vocabulario, modismos y ritmos naturales con estas pistas atractivas.",
     "pt": "Pronto para mais? Descubra novo vocabulário, expressões idiomáticas e ritmos naturais com essas faixas envolventes.",
     "de": "Bereit für mehr? Entdecke neues Vokabular, Redewendungen und natürliche Rhythmen mit diesen fesselnden Tracks."},
    {"key": "playlist.English.Advanced",
     "en": "English - Advanced",
     "tr": "İngilizce - İleri Düzey",
     "ru": "Английский - Продвинутый",
     "es": "Inglés - Avanzado",
     "pt": "Inglês - Avançado",
     "de": "Englisch - Fortgeschritten"},
    {"key": "playlist.Description.English.Advanced",
     "en": "Now you're here to feel them land the way a native speaker feels them. Slang that doesn't translate, rhythm that only makes sense when you stop thinking about it, lyrics where the joke is in the delivery.",
     "tr": "Şimdi onları anadili İngilizce olan birinin hissettiği şekilde hissetmek için buradasınız. Tercüme etmeyen argo, sadece düşünmeyi bıraktığınızda mantıklı olan ritim, şakanın sunumda olduğu şarkı sözleri.",
     "ru": "Теперь вы здесь, чтобы почувствовать, как они приземляются так, как их чувствует носитель языка. Сленг, который не переводится, ритм, который имеет смысл только тогда, когда вы перестаете думать об этом, тексты, где шутка в доставке.",
     "es": "Ahora estás aquí para sentirlos como los siente un hablante nativo. Jerga que no se traduce, ritmo que solo tiene sentido cuando dejas de pensar en ello, letras donde el chiste está en la entrega.",
     "pt": "Agora você está aqui para senti-las como um falante nativo as sente. Gírias que não se traduzem, ritmo que só faz sentido quando você para de pensar nisso, letras onde a piada está na entrega.",
     "de": "Jetzt bist du hier, um sie so zu fühlen, wie ein Muttersprachler sie fühlt. Slang, der sich nicht übersetzt, Rhythmus, der nur Sinn macht, wenn du aufhörst darüber nachzudenken, Texte, bei denen der Witz in der Vortragsweise liegt."},
    # Grammar terms (for the word-inspect panel)
    # Parts of speech
    {"key": "grammar.Noun",             "en": "Noun",              "tr": "İsim",              "ru": "Существительное", "es": "Sustantivo",       "pt": "Substantivo",     "de": "Substantiv"},
    {"key": "grammar.Verb",             "en": "Verb",              "tr": "Fiil",              "ru": "Глагол",           "es": "Verbo",            "pt": "Verbo",           "de": "Verb"},
    {"key": "grammar.Adjective",        "en": "Adjective",         "tr": "Sıfat",             "ru": "Прилагательное",  "es": "Adjetivo",         "pt": "Adjetivo",        "de": "Adjektiv"},
    {"key": "grammar.Adverb",           "en": "Adverb",            "tr": "Zarf",              "ru": "Наречие",          "es": "Adverbio",         "pt": "Advérbio",        "de": "Adverb"},
    {"key": "grammar.Preposition",      "en": "Preposition",       "tr": "Edat",              "ru": "Предлог",          "es": "Preposición",      "pt": "Preposição",      "de": "Präposition"},
    {"key": "grammar.Conjunction",      "en": "Conjunction",       "tr": "Bağlaç",            "ru": "Союз",             "es": "Conjunción",       "pt": "Conjunção",       "de": "Konjunktion"},
    {"key": "grammar.Particle",         "en": "Particle",          "tr": "Parçacık",          "ru": "Частица",          "es": "Partícula",        "pt": "Partícula",       "de": "Partikel"},
    {"key": "grammar.Participle",       "en": "Participle",        "tr": "Sıfat-fiil",        "ru": "Причастие",        "es": "Participio",       "pt": "Particípio",      "de": "Partizip"},
    {"key": "grammar.Pronoun",          "en": "Pronoun",           "tr": "Zamir",             "ru": "Местоимение",      "es": "Pronombre",        "pt": "Pronome",         "de": "Pronomen"},
    {"key": "grammar.Numeral",          "en": "Numeral",           "tr": "Sayı sıfatı",       "ru": "Числительное",     "es": "Numeral",          "pt": "Numeral",         "de": "Numerale"},
    {"key": "grammar.Interjection",     "en": "Interjection",      "tr": "Ünlem",             "ru": "Междометие",       "es": "Interjección",     "pt": "Interjeição",     "de": "Interjektion"},
    {"key": "grammar.Determiner",       "en": "Determiner",        "tr": "Belirteç",          "ru": "Артикль",          "es": "Determinante",     "pt": "Determinante",    "de": "Determinator"},
    {"key": "grammar.Proper_Noun",      "en": "Proper Noun",       "tr": "Özel İsim",         "ru": "Имя собственное",  "es": "Nombre propio",   "pt": "Nome próprio",    "de": "Eigenname"},
    {"key": "grammar.Auxiliary_Verb",   "en": "Auxiliary Verb",    "tr": "Yardımcı Fiil",     "ru": "Вспомогательный глагол", "es": "Verbo auxiliar", "pt": "Verbo auxiliar", "de": "Hilfsverb"},
    {"key": "grammar.Gerund",           "en": "Gerund",            "tr": "Ulaç",              "ru": "Деепричастие",     "es": "Gerundio",         "pt": "Gerúndio",        "de": "Gerundium"},
    {"key": "grammar.Adj_short",        "en": "Adj (short)",       "tr": "Kısa sıfat",        "ru": "Краткое прилагательное", "es": "Adj. (corto)",  "pt": "Adj. (curto)",   "de": "Adj. (kurz)"},
    {"key": "grammar.Participle_short", "en": "Participle (short)","tr": "Kısa sıfat-fiil",   "ru": "Краткое причастие", "es": "Participio (corto)", "pt": "Particípio (curto)", "de": "Partizip (kurz)"},
    {"key": "grammar.Verb_infinitive",  "en": "Infinitive",        "tr": "Mastar",            "ru": "Инфинитив",        "es": "Infinitivo",       "pt": "Infinitivo",      "de": "Infinitiv"},
    {"key": "grammar.Punctuation",      "en": "Punctuation",       "tr": "Noktalama",         "ru": "Знак препинания",  "es": "Puntuación",       "pt": "Pontuação",       "de": "Interpunktion"},
    # Number
    {"key": "grammar.Singular",         "en": "Singular",          "tr": "Tekil",             "ru": "Ед. ч.",           "es": "Singular",         "pt": "Singular",        "de": "Singular"},
    {"key": "grammar.Plural",           "en": "Plural",            "tr": "Çoğul",             "ru": "Мн. ч.",           "es": "Plural",           "pt": "Plural",          "de": "Plural"},
    # Gender
    {"key": "grammar.Masculine",        "en": "Masculine",         "tr": "Eril",              "ru": "Муж.",             "es": "Masculino",        "pt": "Masculino",       "de": "Maskulinum"},
    {"key": "grammar.Feminine",         "en": "Feminine",          "tr": "Dişil",             "ru": "Жен.",             "es": "Femenino",         "pt": "Feminino",        "de": "Femininum"},
    {"key": "grammar.Neuter",           "en": "Neuter",            "tr": "Yansız",            "ru": "Ср.",              "es": "Neutro",           "pt": "Neutro",          "de": "Neutrum"},
    # Case
    {"key": "grammar.Nominative",       "en": "Nominative",        "tr": "Yalın hal",         "ru": "Им.",              "es": "Nominativo",       "pt": "Nominativo",      "de": "Nominativ"},
    {"key": "grammar.Genitive",         "en": "Genitive",          "tr": "İyelik hali",       "ru": "Род.",             "es": "Genitivo",         "pt": "Genitivo",        "de": "Genitiv"},
    {"key": "grammar.Genitive_2",       "en": "Genitive 2",        "tr": "İyelik hali 2",     "ru": "Род. 2",           "es": "Genitivo 2",       "pt": "Genitivo 2",      "de": "Genitiv 2"},
    {"key": "grammar.Dative",           "en": "Dative",            "tr": "Yönelme hali",      "ru": "Дат.",             "es": "Dativo",           "pt": "Dativo",          "de": "Dativ"},
    {"key": "grammar.Accusative",       "en": "Accusative",        "tr": "Belirtme hali",     "ru": "Вин.",             "es": "Acusativo",        "pt": "Acusativo",       "de": "Akkusativ"},
    {"key": "grammar.Instrumental",     "en": "Instrumental",      "tr": "Araç hali",         "ru": "Твор.",            "es": "Instrumental",     "pt": "Instrumental",    "de": "Instrumental"},
    {"key": "grammar.Prepositional",    "en": "Prepositional",     "tr": "Edat hali",         "ru": "Пред.",            "es": "Preposicional",    "pt": "Preposicional",   "de": "Präpositional"},
    {"key": "grammar.Vocative",         "en": "Vocative",          "tr": "Seslenme hali",     "ru": "Зват.",            "es": "Vocativo",         "pt": "Vocativo",        "de": "Vokativ"},
    {"key": "grammar.Abs",              "en": "Abs",               "tr": "Mutlak hal",        "ru": "Абсолютив",        "es": "Abs.",             "pt": "Abs.",            "de": "Abs."},
    # Aspect
    {"key": "grammar.Perfective",       "en": "Perfective",        "tr": "Bitimli görünüş",   "ru": "Сов.",             "es": "Perfectivo",       "pt": "Perfectivo",      "de": "Perfektiv"},
    {"key": "grammar.Imperfective",     "en": "Imperfective",      "tr": "Süreğen görünüş",   "ru": "Несов.",           "es": "Imperfectivo",     "pt": "Imperfectivo",    "de": "Imperfektiv"},
    # Tense
    {"key": "grammar.Present",          "en": "Present",           "tr": "Şimdiki zaman",     "ru": "Наст.",            "es": "Presente",         "pt": "Presente",        "de": "Präsens"},
    {"key": "grammar.Past",             "en": "Past",              "tr": "Geçmiş zaman",      "ru": "Прош.",            "es": "Pasado",           "pt": "Passado",         "de": "Präteritum"},
    {"key": "grammar.Future",           "en": "Future",            "tr": "Gelecek zaman",     "ru": "Буд.",             "es": "Futuro",           "pt": "Futuro",          "de": "Futur"},
    {"key": "grammar.Imperfect",        "en": "Imperfect",         "tr": "Geçmiş süreç kipi", "ru": "Имперфект",        "es": "Imperfecto",       "pt": "Imperfeito",      "de": "Imperfekt"},
    # Person
    {"key": "grammar.1st_Person",       "en": "1st Person",        "tr": "1. şahıs",          "ru": "1-е л.",           "es": "1.\u00aa persona",   "pt": "1.\u00aa pessoa",   "de": "1. Person"},
    {"key": "grammar.2nd_Person",       "en": "2nd Person",        "tr": "2. şahıs",          "ru": "2-е л.",           "es": "2.\u00aa persona",   "pt": "2.\u00aa pessoa",   "de": "2. Person"},
    {"key": "grammar.3rd_Person",       "en": "3rd Person",        "tr": "3. şahıs",          "ru": "3-е л.",           "es": "3.\u00aa persona",   "pt": "3.\u00aa pessoa",   "de": "3. Person"},
    # Mood
    {"key": "grammar.Imperative",       "en": "Imperative",        "tr": "Emir kipi",         "ru": "Повел.",           "es": "Imperativo",       "pt": "Imperativo",      "de": "Imperativ"},
    {"key": "grammar.Conditional",      "en": "Conditional",       "tr": "Koşullu kip",       "ru": "Условное",         "es": "Condicional",      "pt": "Condicional",     "de": "Konditional"},
    {"key": "grammar.Indicative",       "en": "Indicative",        "tr": "Bildirme kipi",     "ru": "Изъявит.",         "es": "Indicativo",       "pt": "Indicativo",      "de": "Indikativ"},
    {"key": "grammar.Subjunctive",      "en": "Subjunctive",       "tr": "Dilek kipi",        "ru": "Сослагат.",        "es": "Subjuntivo",       "pt": "Subjuntivo",      "de": "Subjunktiv"},
    # Other
    {"key": "grammar.Comparative",      "en": "Comparative",       "tr": "Karşılaştırmalı",   "ru": "Сравн.",           "es": "Comparativo",      "pt": "Comparativo",     "de": "Komparativ"},
    {"key": "grammar.Predicative",      "en": "Predicative",       "tr": "Yüklem işlevi",     "ru": "Предикатив",       "es": "Predicativo",      "pt": "Predicativo",     "de": "Prädikativ"},
    {"key": "grammar.Definite",         "en": "Definite",          "tr": "Belirli",           "ru": "Определённый",     "es": "Definido",         "pt": "Definido",        "de": "Bestimmt"},
    {"key": "grammar.Indefinite",       "en": "Indefinite",        "tr": "Belirsiz",          "ru": "Неопределённый",   "es": "Indefinido",       "pt": "Indefinido",      "de": "Unbestimmt"},
    {"key": "grammar.Other",            "en": "Other",             "tr": "Diğer",             "ru": "Другое",           "es": "Otro",             "pt": "Outro",           "de": "Andere"},
    {"key": "grammar.Unknown",          "en": "Unknown",           "tr": "Bilinmiyor",        "ru": "Неизвестно",       "es": "Desconocido",      "pt": "Desconhecido",    "de": "Unbekannt"},
    # Help / Support widget
    {"key": "help.title",           "en": "Need help?",                                      "tr": "Yardıma mı ihtiyacın var?",                        "ru": "Нужна помощь?",                                        "es": "\u00bfNecesitas ayuda?",                                  "pt": "Precisa de ajuda?",                                      "de": "Hilfe benötigt?"},
    {"key": "help.description",    "en": "Got a question or running into something? Send us a message and we\u2019ll get back to you.", "tr": "Sorunuz mu var? Bize mesaj gönderin, size geri döneceğiz.", "ru": "Есть вопрос или проблема? Напишите нам — мы ответим.", "es": "\u00bfTienes alguna pregunta? Envíanos un mensaje y te responderemos.", "pt": "Tem alguma dúvida? Envie-nos uma mensagem e responderemos.", "de": "Haben Sie eine Frage? Schreiben Sie uns und wir melden uns."},
    {"key": "help.placeholder",    "en": "Describe your question or issue\u2026",              "tr": "Sorunuzu veya sorununuzu açıklayın\u2026",           "ru": "Опишите ваш вопрос или проблему\u2026",                "es": "Describe tu pregunta o problema\u2026",                   "pt": "Descreva sua dúvida ou problema\u2026",                   "de": "Beschreiben Sie Ihre Frage oder Ihr Problem\u2026"},
    {"key": "help.send",           "en": "Send",                                            "tr": "Gönder",                                           "ru": "Отправить",                                            "es": "Enviar",                                              "pt": "Enviar",                                              "de": "Senden"},
    {"key": "help.sending",        "en": "Sending\u2026",                                         "tr": "Gönderiliyor\u2026",                                    "ru": "Отправка\u2026",                                           "es": "Enviando\u2026",                                           "pt": "Enviando\u2026",                                           "de": "Senden\u2026"},
    {"key": "help.dismiss",        "en": "Dismiss",                                          "tr": "Kapat",                                            "ru": "Закрыть",                                              "es": "Cerrar",                                              "pt": "Fechar",                                              "de": "Schließen"},
    {"key": "help.close",          "en": "Close",                                            "tr": "Kapat",                                            "ru": "Закрыть",                                              "es": "Cerrar",                                              "pt": "Fechar",                                              "de": "Schließen"},
    {"key": "help.messageSent",    "en": "Message sent!",                                     "tr": "Mesaj gönderildi!",                                 "ru": "Сообщение отправлено!",                                 "es": "\u00a1Mensaje enviado!",                                   "pt": "Mensagem enviada!",                                       "de": "Nachricht gesendet!"},
    {"key": "help.messageSentDesc","en": "Thanks for reaching out \u2014 we\u2019ll get back to you as soon as we can.", "tr": "Ulaştığınız için teşekkürler \u2014 en kısa sürede geri döneceğiz.", "ru": "Спасибо за обращение \u2014 мы ответим вам как можно скорее.", "es": "Gracias por contactarnos \u2014 te responderemos lo antes posible.", "pt": "Obrigado por entrar em contato \u2014 responderemos em breve.", "de": "Danke für Ihre Nachricht \u2014 wir melden uns so bald wie möglich."},
    {"key": "help.error",          "en": "Something went wrong \u2014 please try again.",          "tr": "Bir şeyler ters gitti \u2014 lütfen tekrar deneyin.",   "ru": "Что-то пошло не так \u2014 пожалуйста, попробуйте снова.", "es": "Algo salió mal \u2014 por favor, inténtalo de nuevo.",    "pt": "Algo deu errado \u2014 por favor, tente novamente.",       "de": "Etwas ist schiefgelaufen \u2014 bitte versuchen Sie es erneut."},
    # Email — Welcome
    {"key": "email.welcome.subject",       "en": "Welcome to SingoLing \U0001f3b5",                    "tr": "SingoLing'e hoş geldiniz \U0001f3b5",                                    "ru": "Добро пожаловать в SingoLing \U0001f3b5",                           "es": "Bienvenido a SingoLing \U0001f3b5",                           "pt": "Bem-vindo ao SingoLing \U0001f3b5",                            "de": "Willkommen bei SingoLing \U0001f3b5"},
    {"key": "email.welcome.greeting",      "en": "Hi",                                                 "tr": "Merhaba",                                                                "ru": "Здравствуйте",                                                      "es": "Hola",                                                        "pt": "Olá",                                                          "de": "Hallo"},
    {"key": "email.welcome.emoji",         "en": "\U0001f3b5",                                         "tr": "\U0001f3b5",                                                             "ru": "\U0001f3b5",                                                        "es": "\U0001f3b5",                                                  "pt": "\U0001f3b5",                                                   "de": "\U0001f3b5"},
    {"key": "email.welcome.welcome_title", "en": "Welcome to SingoLing!",                             "tr": "SingoLing'e hoş geldiniz!",                                              "ru": "Добро пожаловать в SingoLing!",                                     "es": "¡Bienvenido a SingoLing!",                                    "pt": "Bem-vindo ao SingoLing!",                                      "de": "Willkommen bei SingoLing!"},
    {"key": "email.welcome.body",          "en": "You're all set to start learning languages through music. Pick a song, play it, and tap on any word to see its meaning and translation.", "tr": "Müzikle dil öğrenmeye başlamak için hazırsınız. Bir şarkı seçin, çalın ve anlamını görmek için herhangi bir kelimeye dokunun.", "ru": "Вы готовы начать изучать языки через музыку. Выберите песню, включите её и нажмите на любое слово, чтобы увидеть его значение и перевод.", "es": "Estás listo para comenzar a aprender idiomas a través de la música. Elige una canción, reprodúcela y toca cualquier palabra para ver su significado y traducción.", "pt": "Você está pronto para começar a aprender idiomas através da música. Escolha uma música, reproduza-a e toque em qualquer palavra para ver seu significado e tradução.", "de": "Sie sind bereit, Sprachen durch Musik zu lernen. Wählen Sie einen Song aus, spielen Sie ihn ab und tippen Sie auf ein beliebiges Wort, um seine Bedeutung und Übersetzung anzuzeigen."},
    {"key": "email.welcome.button",        "en": "Browse Songs",                                       "tr": "Şarkılara Göz At",                                                       "ru": "Просмотреть песни",                                                 "es": "Explorar canciones",                                          "pt": "Explorar músicas",                                             "de": "Songs durchsuchen"},
    {"key": "email.welcome.footer",        "en": "Questions? Reply to this email or visit support@singoling.com", "tr": "Sorularınız mı var? Bu e-postayı yanıtlayın veya support@singoling.com adresini ziyaret edin", "ru": "Вопросы? Ответьте на это письмо или посетите support@singoling.com", "es": "¿Preguntas? Responde a este correo o visita support@singoling.com", "pt": "Dúvidas? Responda este e-mail ou visite support@singoling.com", "de": "Fragen? Antworten Sie auf diese E-Mail oder besuchen Sie support@singoling.com"},
    # Email — Password Reset
    {"key": "email.passwordReset.subject", "en": "Reset your SingoLing password",                     "tr": "SingoLing şifrenizi sıfırlayın",                                         "ru": "Сбросить пароль SingoLing",                                         "es": "Restablecer su contraseña de SingoLing",                      "pt": "Redefinir sua senha do SingoLing",                             "de": "SingoLing-Passwort zurücksetzen"},
    {"key": "email.passwordReset.greeting","en": "Hi",                                                 "tr": "Merhaba",                                                                "ru": "Здравствуйте",                                                      "es": "Hola",                                                        "pt": "Olá",                                                          "de": "Hallo"},
    {"key": "email.passwordReset.body",    "en": "Click the button below to reset your password. This link expires in 1 hour.", "tr": "Şifrenizi sıfırlamak için aşağıdaki düğmeye tıklayın. Bu bağlantı 1 saat içinde sona erecektir.", "ru": "Нажмите кнопку ниже, чтобы сбросить пароль. Ссылка действительна 1 час.", "es": "Haga clic en el botón de abajo para restablecer su contraseña. Este enlace caduca en 1 hora.", "pt": "Clique no botão abaixo para redefinir sua senha. Este link expira em 1 hora.", "de": "Klicken Sie auf die Schaltfläche unten, um Ihr Passwort zurückzusetzen. Dieser Link läuft in 1 Stunde ab."},
    {"key": "email.passwordReset.button",  "en": "Reset Password",                                     "tr": "Şifreyi Sıfırla",                                                        "ru": "Сбросить пароль",                                                   "es": "Restablecer contraseña",                                      "pt": "Redefinir senha",                                              "de": "Passwort zurücksetzen"},
    {"key": "email.passwordReset.footer",  "en": "If you didn't request this, you can safely ignore this email.", "tr": "Bunu siz talep etmediyseniz, bu e-postayı güvenle yok sayabilirsiniz.", "ru": "Если вы не запрашивали сброс пароля, проигнорируйте это письмо.", "es": "Si no solicitó esto, puede ignorar este correo de forma segura.", "pt": "Se você não solicitou isso, pode ignorar este e-mail com segurança.", "de": "Wenn Sie dies nicht angefordert haben, können Sie diese E-Mail einfach ignorieren."},
]


def _seed_localizations() -> None:
    """Upsert the initial localization strings into the DB (idempotent)."""
    db = next(get_db())
    try:
        for entry in _INITIAL_LOCALIZATIONS:
            existing = db.query(Localization).filter(Localization.key == entry["key"]).first()
            if existing:
                existing.en = entry["en"]
                existing.tr = entry["tr"]
                existing.ru = entry["ru"]
                existing.es = entry.get("es", "")
                existing.pt = entry.get("pt", "")
                existing.de = entry.get("de", "")
            else:
                db.add(Localization(
                    key=entry["key"],
                    en=entry["en"], tr=entry["tr"], ru=entry["ru"],
                    es=entry.get("es", ""), pt=entry.get("pt", ""), de=entry.get("de", ""),
                ))
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


# ── Rate limiting ──────────────────────────────────────────────────────────────

class _RateLimiter:
    """Fixed-window in-memory rate limiter. Thread-safe, no external deps."""

    _ALERT_WINDOW = 86_400  # seconds between repeated alerts for same IP+endpoint

    def __init__(self) -> None:
        # key → (window_start, count)
        self._windows: dict[str, tuple[float, int]] = {}
        # alert_key → last_alert_timestamp  (deduplicate within 24 h)
        self._alerts: dict[str, float] = {}
        self._lock = threading.Lock()
        # Periodic cleanup every 5 minutes
        self._start_cleanup()

    def is_allowed(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int, int]:
        """Return (allowed, retry_after_seconds, attempt_count). retry_after is 0 when allowed."""
        now = time.time()
        with self._lock:
            start, count = self._windows.get(key, (now, 0))
            if now - start >= window_seconds:
                # New window
                self._windows[key] = (now, 1)
                return True, 0, 1
            if count < limit:
                self._windows[key] = (start, count + 1)
                return True, 0, count + 1
            retry_after = int(window_seconds - (now - start)) + 1
            return False, retry_after, count

    def should_alert(self, alert_key: str) -> bool:
        """True if no alert has been sent for this key within the last 24 h."""
        now = time.time()
        with self._lock:
            last = self._alerts.get(alert_key, 0.0)
            return now - last >= self._ALERT_WINDOW

    def mark_alerted(self, alert_key: str) -> None:
        """Record that an alert was just sent for this key."""
        with self._lock:
            self._alerts[alert_key] = time.time()

    def _cleanup(self) -> None:
        now = time.time()
        with self._lock:
            expired_w = [k for k, (start, _) in self._windows.items() if now - start >= 3600]
            for k in expired_w:
                del self._windows[k]
            expired_a = [k for k, ts in self._alerts.items() if now - ts >= self._ALERT_WINDOW]
            for k in expired_a:
                del self._alerts[k]

    def _start_cleanup(self) -> None:
        def _run() -> None:
            while True:
                time.sleep(300)
                self._cleanup()
        t = threading.Thread(target=_run, daemon=True)
        t.start()


_rate_limiter = _RateLimiter()


def _get_client_ip(request: Request) -> str:
    """Extract real client IP, honouring X-Forwarded-For from trusted proxies."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take leftmost (original client) IP
        ip = forwarded_for.split(",")[0].strip()
        try:
            ipaddress.ip_address(ip)
            return ip
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


def rate_limit(limit: int, window_seconds: int, key_prefix: str = "", endpoint_name: str = ""):
    """FastAPI dependency factory for rate limiting by IP, with first-breach alerting."""
    def dependency(request: Request, db: Session = Depends(get_db)):
        ip = _get_client_ip(request)
        key = f"{key_prefix}:{ip}"
        allowed, retry_after, attempt_count = _rate_limiter.is_allowed(key, limit, window_seconds)
        if not allowed:
            _maybe_send_rate_limit_alert(
                db=db,
                request=request,
                ip=ip,
                endpoint_name=endpoint_name or key_prefix,
                attempt_count=attempt_count,
            )
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )
    return Depends(dependency)


def _maybe_send_rate_limit_alert(
    *,
    db: Session,
    request: Request,
    ip: str,
    endpoint_name: str,
    attempt_count: int,
) -> None:
    """Create a Report and fire an admin email on the first breach per IP+endpoint within 24 h."""
    alert_key = f"{ip}:{endpoint_name}"
    if not _rate_limiter.should_alert(alert_key):
        return

    _rate_limiter.mark_alerted(alert_key)

    user_agent = request.headers.get("User-Agent", "")
    now_ts = int(time.time())
    body_data = {
        "ip": ip,
        "endpoint": endpoint_name,
        "timestamp": now_ts,
        "attempt_count": attempt_count,
        "user_agent": user_agent,
    }

    try:
        report = Report(
            kind    = "rate_limit",
            context = endpoint_name,
            message = json.dumps(body_data),
            status  = "open",
        )
        db.add(report)
        db.commit()
    except Exception as exc:
        print(f"[rate_limit] failed to create Report: {exc}")

    def _send_alert() -> None:
        subject = f"[Security Alert] Rate limit exceeded: {endpoint_name}"
        body = (
            f"Rate limit breached on /{endpoint_name}\n\n"
            f"IP:            {ip}\n"
            f"User-Agent:    {user_agent}\n"
            f"Attempt count: {attempt_count}\n"
            f"Timestamp:     {now_ts}\n\n"
            f"No further alerts will be sent for this IP+endpoint for 24 h."
        )
        try:
            _mailgun._send(to=_mailgun.ADMIN_PERSONAL_EMAIL, subject=subject, text=body)
        except Exception as exc:
            print(f"[rate_limit] failed to send alert email: {exc}")

    threading.Thread(target=_send_alert, daemon=True).start()


# Tier 1 — Auth (strict, IP-based)
_rl_login           = rate_limit(5,   15 * 60, "login",           endpoint_name="login")
_rl_register        = rate_limit(3,   15 * 60, "register",        endpoint_name="register")
_rl_forgot_password = rate_limit(3,   15 * 60, "forgot_password", endpoint_name="forgot_password")
_rl_reset_password  = rate_limit(5,   15 * 60, "reset_password",  endpoint_name="reset_password")
# Tier 2 — OAuth (relaxed, IP-based)
_rl_oauth           = rate_limit(10,  15 * 60, "oauth",           endpoint_name="oauth")
# Tier 3 — Webhooks (IP-based)
_rl_webhook         = rate_limit(100, 60,       "webhook",         endpoint_name="webhook")
# Tier 4 — Admin: rate limiting is embedded in _require_admin (30 req/min per user ID)
# Tier 4 — User actions (_rl_user_action): defined below after _get_current_user


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


def _line_response(line: Line, override_words: Optional[list] = None, lang_code: str = "ru", target_lang: Optional[str] = None, override_translations: Optional[list] = None) -> LineResponse:
    words = override_words if override_words is not None else line.words
    # Check normalized LineTranslation table first (multi-lang support).
    # override_translations lets callers supply translations from a different line object
    # (e.g. default lines when the response is built from source-specific lines).
    translation: str = line.translation
    translations_to_check = override_translations if override_translations is not None else line.translations
    if target_lang and translations_to_check:
        for lt in translations_to_check:
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
            # Index default lines by position so source lines can inherit their translations.
            # line_translations are only filled for default (source=None) lines, so we must
            # pass the default line's translations when building responses for source lines.
            default_translations_by_pos = {l.position: l.translations for l in default_lines}
            lines = [
                _line_response(
                    sl,
                    override_words=default_words_by_pos.get(sl.position, []),
                    lang_code=lang_code,
                    target_lang=target_lang,
                    override_translations=default_translations_by_pos.get(sl.position),
                )
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
        access_status=user.access_status or 'approved',
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
        os.environ.get("PYTHON_EXECUTABLE", sys.executable),
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

# ── Closed access mode (when True, new users default to pending-approval) ─────
CLOSED_ACCESS = os.environ.get("CLOSED_ACCESS", "false").lower() in ("true", "1", "yes")

# ── Paddle subscription configuration ──────────────────────────────────────────
PADDLE_WEBHOOK_SECRET = os.environ.get("PADDLE_WEBHOOK_SECRET", "")
PADDLE_API_KEY = os.environ.get("PADDLE_API_KEY", "")
PADDLE_CLIENT_TOKEN = os.environ.get("PADDLE_CLIENT_TOKEN", "")


def _make_admin_token(user: User) -> str:
    """Return a stable HMAC-SHA256 token tied to this user's identity."""
    msg = f"{user.id}:{user.spotify_id}:{user.email or ''}".encode()
    sig = _hmac.new(_ADMIN_TOKEN_SECRET, msg, sha256).hexdigest()
    return f"{user.id}.{sig}"


def _verify_admin_token(token: str, user: User) -> bool:
    expected = _make_admin_token(user)
    return secrets.compare_digest(token, expected)


def _user_to_response(user: User) -> UserResponse:
    """Convert User model to UserResponse with all fields populated."""
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
        preferred_lang=user.preferred_lang or 'en',
        # Subscription fields
        subscription_tier=user.subscription_tier or 'free',
        subscription_status=user.subscription_status,
        subscription_platform=user.subscription_platform,
        subscription_external_id=user.subscription_external_id,
        subscription_started_at=user.subscription_started_at.isoformat() if user.subscription_started_at else None,
        subscription_expires_at=user.subscription_expires_at.isoformat() if user.subscription_expires_at else None,
        subscription_cancel_at_period_end=user.subscription_cancel_at_period_end or False,
        original_platform=user.original_platform,
    )


def _require_admin(
    request: Request,
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
        # Rate-limit by user ID for accuracy
        key = f"admin:user:{user.id}"
        allowed, retry_after, _ = _rate_limiter.is_allowed(key, 30, 60)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )
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
        # Rate-limit by user ID for accuracy
        key = f"admin:user:{user.id}"
        allowed, retry_after, _ = _rate_limiter.is_allowed(key, 30, 60)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )
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


def _get_optional_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User | None:
    """Dependency: optional authentication. Returns User if authenticated, None if not."""
    if not authorization:
        return None

    try:
        scheme, credential = authorization.split(" ", 1)
    except ValueError:
        return None

    scheme = scheme.lower()

    if scheme == "bearer":
        try:
            user_id_str, _ = credential.split(".", 1)
            user_id = int(user_id_str)
        except Exception:
            return None
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not _verify_admin_token(credential, user):
            return None
        return user

    if scheme == "basic":
        try:
            decoded = base64.b64decode(credential).decode("utf-8")
            email, password = decoded.split(":", 1)
        except Exception:
            return None
        user = db.query(User).filter(User.email == email.strip().lower()).first()
        if not user or not _verify_password(password, user.password_hash):
            return None
        return user

    return None


def rate_limit_user(limit: int, window_seconds: int, key_prefix: str = ""):
    """FastAPI dependency factory for rate limiting by authenticated user ID."""
    def dependency(request: Request, current_user: User = Depends(_get_current_user)):
        key = f"{key_prefix}:user:{current_user.id}"
        allowed, retry_after, _ = _rate_limiter.is_allowed(key, limit, window_seconds)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )
    return Depends(dependency)


# Tier 4 — User actions (user-ID-based, defined here after _get_current_user)
_rl_user_action = rate_limit_user(100, 60, "user_action")


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/pricing")
def get_pricing():
    """Get current pricing from Paddle (cached)."""
    pricing_data = paddle_config.get_current_pricing()
    pricing_data['client_token'] = PADDLE_CLIENT_TOKEN
    return pricing_data


@app.post("/api/sync-subscription", response_model=UserResponse)
def sync_subscription(current_user: User = Depends(_get_current_user), db: Session = Depends(get_db)):
    """Sync subscription status from Paddle API by email.
    
    Similar to iOS 'restore purchase' - queries Paddle for active subscriptions
    and updates the user's subscription status in the database.
    Returns the updated user object.
    """
    import requests
    
    if not PADDLE_API_KEY:
        raise HTTPException(status_code=500, detail="Paddle API key not configured")
    
    try:
        # Query Paddle Subscriptions API by customer email
        print(f"[Sync Subscription] Querying Paddle for email: {current_user.email}")
        response = requests.get(
            'https://sandbox-api.paddle.com/subscriptions',
            headers={'Authorization': f'Bearer {PADDLE_API_KEY}'},
            params={'customer_email': current_user.email},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        subscriptions = data.get('data', [])
        print(f"[Sync Subscription] Found {len(subscriptions)} subscriptions")
        if subscriptions:
            print(f"[Sync Subscription] First subscription: {subscriptions[0]}")
        
        if not subscriptions:
            # No active subscription found - set to free
            print(f"[Sync Subscription] No subscriptions found, setting to free tier")
            if current_user.subscription_tier != 'free':
                current_user.subscription_tier = 'free'
                current_user.subscription_status = None
                current_user.subscription_external_id = None
                current_user.subscription_expires_at = None
                db.commit()
            return _user_to_response(current_user)
        
        # Get the most recent active subscription
        active_sub = None
        for sub in subscriptions:
            if sub.get('status') == 'active':
                active_sub = sub
                break
        
        # If no active, check for other statuses (past_due, etc.)
        if not active_sub and subscriptions:
            active_sub = subscriptions[0]
        
        if active_sub:
            # Extract subscription details
            subscription_id = active_sub.get('id')
            status = active_sub.get('status', 'active')
            next_billed_at = active_sub.get('next_billed_at')
            started_at = active_sub.get('started_at') or active_sub.get('created_at')
            scheduled_change = active_sub.get('scheduled_change')
            
            # Extract price_id from items to determine tier
            items = active_sub.get('items', [])
            price_id = items[0]['price']['id'] if items and len(items) > 0 else None
            tier = paddle_config.get_tier_for_price(price_id) if price_id else 'premium'
            print(f"[Sync Subscription] Found active subscription: {subscription_id}")
            print(f"[Sync Subscription] Price ID: {price_id}, Tier: {tier}, Status: {status}")
            
            
            # Update user subscription
            current_user.subscription_tier = tier
            current_user.subscription_status = status
            current_user.subscription_platform = 'paddle'
            current_user.subscription_external_id = subscription_id
            current_user.subscription_started_at = datetime.fromisoformat(started_at.replace('Z', '+00:00')) if started_at else None
            current_user.subscription_expires_at = datetime.fromisoformat(next_billed_at.replace('Z', '+00:00')) if next_billed_at else None
            current_user.subscription_cancel_at_period_end = scheduled_change is not None and scheduled_change.get('action') == 'cancel'
            
            if not current_user.original_platform:
                current_user.original_platform = 'paddle'
            
            db.commit()
            return _user_to_response(current_user)
        
        # No valid subscription found
        if current_user.subscription_tier != 'free':
            current_user.subscription_tier = 'free'
            current_user.subscription_status = None
            db.commit()
        return _user_to_response(current_user)
        
    except requests.exceptions.RequestException as e:
        print(f"Error syncing subscription from Paddle: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to sync with Paddle: {str(e)}")
    except Exception as e:
        print(f"Unexpected error syncing subscription: {e}")
        raise HTTPException(status_code=500, detail="Failed to sync subscription")


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
def get_song(
    song_id: int, 
    source: Optional[str] = Query(default=None), 
    target_lang: Optional[str] = Query(default=None),
    playlist_id: Optional[int] = Query(default=None),  # Optional playlist context
    db: Session = Depends(get_db),
    current_user: User | None = Depends(_get_optional_user),
):
    """Get song details with subscription-gated access to lyrics/translations.
    
    - Always returns timed lyrics (text + timestamps) for auto-scroll
    - Gates interactive features (translations, word data) behind subscription
    - Returns lyrics_unlocked and upgrade_cta when applicable
    """
    # Normalize to lowercase and resolve aliases (e.g. en-us → en)
    if target_lang:
        target_lang = _canon_lang(target_lang)
    
    # Cache keyed by (song_id, source, target_lang) — skip cache when:
    # 1. target_lang specified (varies per user preference)
    # 2. current_user is authenticated (subscription status varies)
    cache_key = (song_id, source or None) if not target_lang and not current_user else None
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
    
    # Get playlist context if provided or try to find one
    playlist = None
    position_in_playlist = None
    
    if playlist_id:
        playlist = db.get(Playlist, playlist_id)
        if playlist:
            # Find song position in this playlist
            playlist_song = db.query(PlaylistSong).filter(
                PlaylistSong.playlist_id == playlist_id,
                PlaylistSong.song_id == song_id
            ).first()
            if playlist_song:
                position_in_playlist = playlist_song.position
    # Note: If no playlist_id provided, playlist stays None
    # This ensures free users can only access songs with explicit playlist context
    
    # Check entitlements
    lyrics_unlocked = True
    upgrade_cta = None
    
    if current_user:
        lyrics_unlocked = entitlements.can_access_lyrics(
            current_user, 
            song, 
            playlist, 
            position_in_playlist
        )
        
        if not lyrics_unlocked:
            upgrade_cta = entitlements.get_upgrade_cta(current_user, song, playlist)
    else:
        # Free user (not authenticated) - check position-based trial
        if playlist and position_in_playlist is not None:
            lyrics_unlocked = position_in_playlist < 2
        else:
            lyrics_unlocked = False
        
        if not lyrics_unlocked:
            # Create a minimal user object for CTA generation
            free_user = User(subscription_tier='free')
            upgrade_cta = entitlements.get_upgrade_cta(free_user, song, playlist)
    
    # Get song detail
    detail = _song_detail(song, source=source, target_lang=target_lang)
    
    # Gate interactive features if locked
    if not lyrics_unlocked:
        # Strip words and translations from all lines (but keep timed lyrics text)
        for line in detail.lines:
            line.words = []
            line.translation = ""  # Empty translation
    
    # Add subscription fields
    detail.lyrics_unlocked = lyrics_unlocked
    detail.upgrade_cta = upgrade_cta
    
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
    python_exe = os.environ.get("PYTHON_EXECUTABLE", sys.executable)
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

    if "access_status" in body.model_fields_set and body.access_status is not None:
        if body.access_status not in ('approved', 'pending-approval'):
            raise HTTPException(status_code=400, detail="access_status must be 'approved' or 'pending-approval'")
        user.access_status = body.access_status

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
    "Beginner":                                0,
    "[[general.Tag.Difficulty.Intermediate]]": 1,
    "Intermediate":                            1,
    "[[general.Tag.Difficulty.Advanced]]":     2,
    "Advanced":                                2,
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
    _rl: None = _rl_user_action,
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
    return _user_to_response(user)


@app.post("/api/auth/login", response_model=UserResponse)
async def login_with_credentials(body: CredentialLoginRequest, db: Session = Depends(get_db), _rl: None = _rl_login):
    user = db.query(User).filter(User.email == body.email.strip().lower()).first()
    if not user or not _verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    return _user_to_response(user)


@app.post("/api/auth/register", response_model=UserResponse)
async def register_with_credentials(body: RegisterRequest, db: Session = Depends(get_db), _rl: None = _rl_register):
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
        preferred_lang=body.lang or 'en',
        access_status='pending-approval' if CLOSED_ACCESS else 'approved',
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if user.email:
        _t = _get_email_t('welcome', body.lang, db)
        _email = user.email
        _name = user.display_name

        def _welcome_reg() -> None:
            try:
                _mailgun.send_welcome_email(to=_email, display_name=_name, t=_t)
            except Exception:
                pass

        threading.Thread(target=_welcome_reg, daemon=True).start()

    return _user_to_response(user)


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

    return _user_to_response(user)


# ── Password reset ─────────────────────────────────────────────────────────────

@app.post("/api/auth/forgot-password", status_code=204)
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db), _rl: None = _rl_forgot_password):
    """Request a password-reset email. Always returns 204 to prevent email enumeration."""
    import time as _time

    email = body.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.email:
        return Response(status_code=204)

    raw_token = secrets.token_urlsafe(32)
    token_hash = sha256(raw_token.encode()).hexdigest()
    expires_at = int(_time.time()) + 3600  # 1 hour

    # Invalidate any existing unused tokens for this user
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used == 0,
    ).update({"used": 1})

    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        used=0,
    ))
    db.commit()

    site_url = _mailgun.SITE_URL
    reset_url = f"{site_url}/?reset_token={raw_token}"
    _email = user.email
    _name = user.display_name
    _t = _get_email_t('passwordReset', user.preferred_lang or 'en', db)

    def _send() -> None:
        try:
            _mailgun.send_password_reset(to=_email, display_name=_name, reset_url=reset_url, t=_t)
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()
    return Response(status_code=204)


@app.post("/api/auth/reset-password", status_code=204)
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db), _rl: None = _rl_reset_password):
    """Consume a reset token and update the user's password."""
    import time as _time

    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    token_hash = sha256(body.token.encode()).hexdigest()
    now = int(_time.time())

    record = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == token_hash,
        PasswordResetToken.expires_at > now,
        PasswordResetToken.used == 0,
    ).first()

    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user = db.get(User, record.user_id)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user.password_hash = _hash_password(body.password)
    record.used = 1
    db.commit()
    return Response(status_code=204)


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


_ALLOWED_LANGS = {'en', 'tr', 'ru', 'es', 'pt', 'de'}

@app.patch("/api/me/lang", status_code=204)
async def update_preferred_lang(
    body: UpdateLangRequest,
    current_user: User = Depends(_get_current_user),
    db: Session = Depends(get_db),
):
    """Persist the user's preferred UI/email language."""
    if body.lang in _ALLOWED_LANGS:
        current_user.preferred_lang = body.lang
        db.commit()
    return Response(status_code=204)


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
async def login_with_google(body: GoogleLoginRequest, db: Session = Depends(get_db), _rl: None = _rl_oauth):
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

    _is_new = False
    if not user:
        user = User(
            spotify_id=synthetic_id,
            display_name=display_name,
            email=email,
            google_user_id=google_sub,
            preferred_lang=body.lang or 'en',
            access_status='pending-approval' if CLOSED_ACCESS else 'approved',
        )
        db.add(user)
        _is_new = True
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

    if _is_new and user.email:
        _t = _get_email_t('welcome', body.lang, db)
        _email = user.email
        _name = user.display_name

        def _welcome_google() -> None:
            try:
                _mailgun.send_welcome_email(to=_email, display_name=_name, t=_t)
            except Exception:
                pass

        threading.Thread(target=_welcome_google, daemon=True).start()

    return _user_to_response(user)


@app.post("/api/auth/apple", response_model=UserResponse)
async def login_with_apple(body: AppleLoginRequest, db: Session = Depends(get_db), _rl: None = _rl_oauth):
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

    _is_new = False
    if not user:
        user = User(
            spotify_id=synthetic_id,
            display_name=display_name or email or "Apple User",
            email=email,
            apple_user_id=apple_sub,
            preferred_lang=body.lang or 'en',
            access_status='pending-approval' if CLOSED_ACCESS else 'approved',
        )
        db.add(user)
        _is_new = True
    else:
        if user.spotify_id != synthetic_id and not user.spotify_id.startswith("spotify:"):
            user.spotify_id = synthetic_id
        if not user.apple_user_id:
            user.apple_user_id = apple_sub
        if email and not user.email:
            user.email = email

    db.commit()
    db.refresh(user)

    if _is_new and user.email:
        _t = _get_email_t('welcome', body.lang, db)
        _email = user.email
        _name = user.display_name

        def _welcome_apple() -> None:
            try:
                _mailgun.send_welcome_email(to=_email, display_name=_name, t=_t)
            except Exception:
                pass

        threading.Thread(target=_welcome_apple, daemon=True).start()

    return _user_to_response(user)


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


# ── Mailgun inbound email webhook ──────────────────────────────────────────────
#
# Mailgun route (configure once in the Mailgun dashboard):
#   Expression : match_recipient(".*@singoling.com")
#   Action     : forward("https://singoling.com/api/mailgun/webhook")
#   Priority   : 0   (process before any other routes)
#
# Routing logic:
#   support@singoling.com  → creates an open ticket in the reports table (kind='email')
#   any other @singoling.com address → forwards to ADMIN_PERSONAL_EMAIL env var
#
# Signature verification uses HMAC-SHA256(MAILGUN_API_KEY, timestamp + token).

@app.post("/api/mailgun/webhook")
async def mailgun_inbound_webhook(request: Request, db: Session = Depends(get_db), _rl: None = _rl_webhook):
    """Receive inbound emails routed from Mailgun and process or forward them."""
    form = await request.form()

    # ── Verify Mailgun webhook signature ──────────────────────────────────────
    timestamp    = str(form.get("timestamp", ""))
    token        = str(form.get("token", ""))
    signature    = str(form.get("signature", ""))
    signing_key  = os.environ.get("MAILGUN_WEB_HOOK_KEY", "")

    if not _mailgun.verify_webhook_signature(signing_key, timestamp, token, signature):
        print("[Mailgun] webhook: rejected request with invalid signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # ── Parse email fields ─────────────────────────────────────────────────────
    recipient  = str(form.get("recipient", "")).lower().strip()
    sender     = str(form.get("sender",    "")).strip()
    subject    = str(form.get("subject",   "")).strip()
    body_plain = str(form.get("body-plain","")).strip()
    body_html  = str(form.get("body-html", "")).strip() or None

    print(f"[Mailgun] inbound: from={sender!r} to={recipient!r} subject={subject!r}")

    if recipient == "support@singoling.com":
        # ── Create support ticket ──────────────────────────────────────────────
        report = Report(
            kind    = "email",
            word    = sender,                               # sender address
            lemma   = subject,                              # email subject
            context = f"From: {sender}\nTo: {recipient}",  # routing context
            message = body_plain or body_html or "(no body)",
            status  = "open",
        )
        db.add(report)
        db.commit()
        print(f"[Mailgun] inbound: created ticket id={report.id} for support email from {sender!r}")
        return {"status": "processed"}
    else:
        # ── Forward to personal inbox ──────────────────────────────────────────
        personal_email = _mailgun.ADMIN_PERSONAL_EMAIL
        if not personal_email:
            print(f"[Mailgun] inbound: ADMIN_PERSONAL_EMAIL not set, dropping email to {recipient!r}")
            return {"status": "dropped"}

        fwd_subject = f"[Fwd: {recipient}] {subject}"
        fwd_body = (
            f"---------- Forwarded message ----------\n"
            f"From: {sender}\n"
            f"To: {recipient}\n"
            f"Subject: {subject}\n"
            f"---\n\n"
            f"{body_plain}"
        )
        try:
            _mailgun._send(to=personal_email, subject=fwd_subject, text=fwd_body, html=body_html)
        except Exception as exc:
            print(f"[Mailgun] inbound: failed to forward from {sender!r} to personal inbox: {exc}")
        return {"status": "forwarded"}


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
                os.environ.get("PYTHON_EXECUTABLE", sys.executable),
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
                os.environ.get("PYTHON_EXECUTABLE", sys.executable),
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


@app.get("/api/admin/localizations", response_model=list[LocalizationItem])
def admin_get_localizations(db: Session = Depends(get_db), _: User = Depends(_require_admin)):
    """Admin-only: return all localization strings directly from the DB (no cache)."""
    return db.query(Localization).order_by(Localization.key).all()


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
        row.es = body.es
        row.pt = body.pt
        row.de = body.de
    else:
        row = Localization(key=key, en=body.en, tr=body.tr, ru=body.ru, es=body.es, pt=body.pt, de=body.de)
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
    row.es = body.es
    row.pt = body.pt
    row.de = body.de
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


# ── Reports ────────────────────────────────────────────────────────────────────

@app.post("/api/reports", status_code=201)
def create_report(
    body: ReportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    """Submit a problem report (any authenticated user)."""
    import time as _time
    song = db.get(Song, body.song_id) if body.song_id else None
    report = Report(
        kind=body.kind,
        user_id=current_user.id,
        song_id=song.id if song else None,
        word=body.word,
        lemma=body.lemma,
        context=body.context,
        message=body.message,
        status="open",
        created_at=int(_time.time()),
    )
    db.add(report)
    db.commit()

    # Notify admin via email (fire-and-forget)
    _report_id = report.id
    _kind = body.kind
    _user_name = current_user.display_name
    _user_email = current_user.email
    _song_title = song.title if song else None
    _message = body.message

    def _notify() -> None:
        try:
            _mailgun.send_support_notification(
                report_id=_report_id,
                kind=_kind,
                user_name=_user_name,
                user_email=_user_email,
                song_title=_song_title,
                message=_message,
            )
        except Exception:
            pass

    threading.Thread(target=_notify, daemon=True).start()

    return {"id": report.id}


@app.get("/api/admin/reports", response_model=list[AdminReportResponse])
def list_admin_reports(
    status: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    q = db.query(Report).order_by(Report.created_at.desc())
    if status:
        q = q.filter(Report.status == status)
    reports = q.all()

    result = []
    for r in reports:
        user = db.get(User, r.user_id) if r.user_id else None
        song = db.get(Song, r.song_id) if r.song_id else None
        result.append(AdminReportResponse(
            id=r.id,
            kind=r.kind,
            user_id=r.user_id,
            user_display_name=user.display_name if user else None,
            song_id=r.song_id,
            song_title=song.title if song else None,
            word=r.word,
            lemma=r.lemma,
            context=r.context,
            message=r.message,
            created_at=r.created_at,
            status=r.status,
        ))
    return result


# ── Analytics tracking (Plausible) ────────────────────────────────────────────

def track_backend_event(
    event_name: str,
    props: Optional[dict[str, str | int | bool]] = None,
    user_id: Optional[int] = None,
) -> None:
    """
    Send a server-side event to Plausible Analytics.
    This is for backend-only events (e.g., subscription webhooks).
    """
    try:
        import requests
        
        payload = {
            "domain": "singoling.com",
            "name": event_name,
            "url": "https://singoling.com/api/webhook",  # Generic backend URL
        }
        
        if props:
            payload["props"] = props
        
        # Add user_id as a prop if provided
        if user_id is not None:
            if "props" not in payload:
                payload["props"] = {}
            payload["props"]["user_id"] = user_id
        
        response = requests.post(
            "https://plausible.io/api/event",
            json=payload,
            headers={
                "User-Agent": "SingoLing-Backend/1.0",
                "Content-Type": "application/json",
            },
            timeout=5,
        )
        
        if response.status_code != 202:
            print(f"[Analytics] Plausible returned {response.status_code}: {response.text}")
    except Exception as e:
        # Never let analytics failures affect the application
        print(f"[Analytics] Failed to track event '{event_name}': {e}")


# ── Paddle webhook endpoint ────────────────────────────────────────────────────
@app.post("/api/webhooks/paddle")
async def paddle_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle Paddle subscription webhooks.
    
    Paddle sends webhooks for subscription events (created, updated, canceled).
    Signature format: Paddle-Signature: ts=1234567890;h1=abc123...
    """
    # Get raw body for signature verification
    body = await request.body()
    body_str = body.decode('utf-8')
    
    # Get signature from header
    signature_header = request.headers.get("Paddle-Signature")
    
    # Log webhook receipt for debugging
    print(f"[Paddle Webhook] Received webhook")
    print(f"[Paddle Webhook] Signature header: {signature_header}")
    print(f"[Paddle Webhook] Body preview: {body_str[:200]}...")
    print(f"[Paddle Webhook] Webhook secret configured: {bool(PADDLE_WEBHOOK_SECRET)}")
    print(f"[Paddle Webhook] Secret length: {len(PADDLE_WEBHOOK_SECRET) if PADDLE_WEBHOOK_SECRET else 0}")
    
    # TEMPORARY: Log all headers for debugging
    print("[Paddle Webhook] All headers:")
    for header_name, header_value in request.headers.items():
        if 'paddle' in header_name.lower() or 'signature' in header_name.lower():
            print(f"  {header_name}: {header_value}")
    
    if not signature_header:
        print("[Paddle Webhook] ERROR: Missing Paddle-Signature header")
        raise HTTPException(status_code=401, detail="Missing signature header")
    
    if not PADDLE_WEBHOOK_SECRET:
        print("[Paddle Webhook] ERROR: PADDLE_WEBHOOK_SECRET not configured")
        raise HTTPException(status_code=401, detail="Webhook secret not configured")
    
    # Parse signature header (format: ts=timestamp;h1=signature)
    try:
        sig_parts = dict(part.split('=', 1) for part in signature_header.split(';') if '=' in part)
        timestamp = sig_parts.get('ts', '')
        signature = sig_parts.get('h1', '')
    except Exception as e:
        print(f"[Paddle Webhook] ERROR: Failed to parse signature header: {e}")
        raise HTTPException(status_code=401, detail="Invalid signature format")
    
    if not timestamp or not signature:
        print(f"[Paddle Webhook] ERROR: Missing timestamp or signature (ts={timestamp}, h1={bool(signature)})")
        raise HTTPException(status_code=401, detail="Invalid signature format")
    
    # Verify signature (Paddle uses HMAC SHA256)
    try:
        # Construct the signed payload exactly as Paddle does: timestamp + : + body
        signed_payload = f"{timestamp}:{body_str}"
        expected_signature = _hmac.new(
            PADDLE_WEBHOOK_SECRET.encode(),
            signed_payload.encode(),
            sha256
        ).hexdigest()
        
        print(f"[Paddle Webhook] Timestamp: {timestamp}")
        print(f"[Paddle Webhook] Signed payload length: {len(signed_payload)} bytes")
        print(f"[Paddle Webhook] Signed payload preview: {signed_payload[:100]}...")
        print(f"[Paddle Webhook] Received signature: {signature}")
        print(f"[Paddle Webhook] Expected signature: {expected_signature}")
        print(f"[Paddle Webhook] Signatures match: {signature == expected_signature}")
        
        if not secrets.compare_digest(signature, expected_signature):
            print("[Paddle Webhook] ERROR: Signature mismatch")
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        print("[Paddle Webhook] ✓ Signature verified successfully")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Paddle Webhook] ERROR: Signature verification failed: {e}")
        raise HTTPException(status_code=401, detail="Signature verification error")
    
    # Parse webhook event
    try:
        event = json.loads(body_str)
    except json.JSONDecodeError as e:
        print(f"[Paddle Webhook] ERROR: Invalid JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    event_type = event.get('event_type')
    data = event.get('data', {})
    
    print(f"[Paddle Webhook] Event type: {event_type}")
    
    # Extract user ID from custom_data/passthrough
    custom_data = data.get('custom_data') or {}
    user_id = custom_data.get('user_id') if custom_data else None
    
    if not user_id:
        # Fallback: try to get from passthrough (Paddle Classic format)
        user_id = event.get('passthrough')
    
    if not user_id:
        # Log and return 200 (don't fail webhook for missing user_id)
        print(f"Warning: Paddle webhook missing user_id. Event: {event_type}")
        return {"status": "ignored", "reason": "missing_user_id"}
    
    # Get user from database
    user = db.get(User, int(user_id))
    if not user:
        print(f"Warning: Paddle webhook for unknown user_id={user_id}")
        return {"status": "ignored", "reason": "user_not_found"}
    
    # Handle subscription events
    if event_type == 'subscription.created':
        subscription_id = data.get('id')
        next_billed_at = data.get('next_billed_at')
        started_at = data.get('started_at') or data.get('created_at')
        
        # Extract price_id from items to determine tier
        items = data.get('items', [])
        price_id = items[0]['price']['id'] if items and len(items) > 0 else None
        tier = paddle_config.get_tier_for_price(price_id) if price_id else 'premium'
        
        user.subscription_tier = tier
        user.subscription_status = 'active'
        user.subscription_platform = 'paddle'
        user.subscription_external_id = subscription_id
        user.subscription_started_at = datetime.fromisoformat(started_at.replace('Z', '+00:00')) if started_at else datetime.now(timezone.utc)
        user.subscription_expires_at = datetime.fromisoformat(next_billed_at.replace('Z', '+00:00')) if next_billed_at else None
        user.subscription_cancel_at_period_end = False
        
        if not user.original_platform:
            user.original_platform = 'paddle'
        
        db.commit()
        print(f"Subscription created for user_id={user_id}, subscription_id={subscription_id}")
        
        # Track subscription activation
        track_backend_event(
            "Subscription Activated",
            props={
                "tier": tier,
                "platform": "paddle",
            },
            user_id=user_id,
        )
    
    elif event_type == 'subscription.updated':
        subscription_id = data.get('id')
        status = data.get('status')
        next_billed_at = data.get('next_billed_at')
        scheduled_change = data.get('scheduled_change')
        
        # Map Paddle status to our status
        previous_status = user.subscription_status
        if status == 'active':
            user.subscription_status = 'active'
        elif status == 'past_due':
            user.subscription_status = 'past_due'
        elif status == 'canceled':
            user.subscription_status = 'canceled'
        elif status == 'paused':
            user.subscription_status = 'canceled'
        
        if next_billed_at:
            user.subscription_expires_at = datetime.fromisoformat(next_billed_at.replace('Z', '+00:00'))
        
        # Check if cancellation is scheduled
        if scheduled_change and scheduled_change.get('action') == 'cancel':
            user.subscription_cancel_at_period_end = True
        else:
            user.subscription_cancel_at_period_end = False
        
        db.commit()
        print(f"Subscription updated for user_id={user_id}, status={status}")
        
        # Track subscription activation if status changed to active
        if status == 'active' and previous_status != 'active':
            track_backend_event(
                "Subscription Activated",
                props={
                    "tier": user.subscription_tier,
                    "platform": "paddle",
                    "reason": "reactivation",
                },
                user_id=user_id,
            )
    
    elif event_type == 'subscription.canceled':
        # Subscription canceled - user retains access until expiry date
        user.subscription_status = 'canceled'
        user.subscription_cancel_at_period_end = True
        
        db.commit()
        print(f"Subscription canceled for user_id={user_id}")
        
        # Track subscription cancellation
        track_backend_event(
            "Subscription Canceled",
            props={
                "tier": user.subscription_tier,
                "platform": "paddle",
            },
            user_id=user_id,
        )
    
    elif event_type == 'subscription.past_due':
        user.subscription_status = 'past_due'
        
        db.commit()
        print(f"Subscription past_due for user_id={user_id}")
    
    else:
        # Unknown event type - log and return success
        print(f"Unhandled Paddle webhook event: {event_type}")
    
    return {"status": "success"}


@app.patch("/api/admin/reports/{report_id}", response_model=AdminReportResponse)
def update_report_status(
    report_id: int,
    body: ReportStatusUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(_require_admin),
):
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    report.status = body.status
    db.commit()
    db.refresh(report)
    user = db.get(User, report.user_id) if report.user_id else None
    song = db.get(Song, report.song_id) if report.song_id else None
    return AdminReportResponse(
        id=report.id,
        kind=report.kind,
        user_id=report.user_id,
        user_display_name=user.display_name if user else None,
        song_id=report.song_id,
        song_title=song.title if song else None,
        word=report.word,
        lemma=report.lemma,
        context=report.context,
        message=report.message,
        created_at=report.created_at,
        status=report.status,
    )

