"""Pydantic request/response models for the FlowUp API."""

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
    start_time_ms: int
    end_time_ms: int
    original_line: str
    phonetic_line: Optional[str]
    translation: str
    words: list[WordResponse]

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


class SongDetailResponse(BaseModel):
    id: int
    spotify_uri: str
    title: str
    artist: Optional[str]
    language: LanguageResponse
    lines: list[LineResponse]


class UserResponse(BaseModel):
    id: int
    spotify_id: str
    display_name: Optional[str]
    email: Optional[str]

    model_config = {"from_attributes": True}


# ── Request models ─────────────────────────────────────────────────────────────

class UserSyncRequest(BaseModel):
    spotify_id: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    access_token: str
    refresh_token: str
    expires_in: int  # seconds until expiry


class WordIngest(BaseModel):
    key: int
    display_form: str
    lemma: str
    grammar: Optional[str] = None
    dictionary_definition: Optional[str] = None


class LineIngest(BaseModel):
    start_time_ms: int
    end_time_ms: int
    original_line: str
    phonetic_line: Optional[str] = None
    translation: str
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
