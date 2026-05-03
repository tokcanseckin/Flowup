"""Spotify OAuth PKCE helpers.

The frontend performs the full PKCE flow client-side (no client_secret
needed). This module provides the backend-side token refresh proxy and
user profile fetch used by the /api/auth/refresh and /api/users/sync
endpoints.
"""

from __future__ import annotations

import os

import httpx

SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
_TOKEN_URL        = "https://accounts.spotify.com/api/token"
_ME_URL           = "https://api.spotify.com/v1/me"


async def refresh_access_token(refresh_token: str) -> dict:
    """Exchange a refresh token for a new access token (PKCE — no client secret)."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            _TOKEN_URL,
            data={
                "grant_type":    "refresh_token",
                "refresh_token": refresh_token,
                "client_id":     SPOTIFY_CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if not r.is_success:
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            raise ValueError(body.get("error_description") or f"Spotify token refresh failed ({r.status_code})")
        return r.json()


async def fetch_spotify_user(access_token: str) -> dict:
    """Fetch the Spotify user profile for the given access token."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            _ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if not r.is_success:
            raise ValueError(f"Failed to fetch Spotify user ({r.status_code})")
        return r.json()
