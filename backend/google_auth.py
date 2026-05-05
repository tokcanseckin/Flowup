"""Google Identity Services token verification for SingoLing.

Uses Google's tokeninfo endpoint to verify ID tokens issued by Google Sign-In.
No extra dependencies required — only httpx (already used by spotify_auth).
"""

from __future__ import annotations

import os

import httpx

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
_VALID_ISSUERS  = {"accounts.google.com", "https://accounts.google.com"}


async def verify_google_id_token(id_token: str) -> dict:
    """Verify a Google ID token and return the decoded claims.

    Raises ValueError if the token is invalid or the audience doesn't match.

    Returns a dict with at least: sub, email, name (may be absent), picture.
    """
    if not GOOGLE_CLIENT_ID:
        raise ValueError("GOOGLE_CLIENT_ID is not configured on the server")

    async with httpx.AsyncClient() as client:
        r = await client.get(
            _TOKENINFO_URL,
            params={"id_token": id_token},
            timeout=10,
        )

    if not r.is_success:
        raise ValueError("Google token verification failed — token may be expired or malformed")

    data: dict = r.json()

    if data.get("iss") not in _VALID_ISSUERS:
        raise ValueError("Google token has invalid issuer")

    aud = data.get("aud", "")
    if aud != GOOGLE_CLIENT_ID:
        raise ValueError("Google token audience does not match this app's client ID")

    if not data.get("sub"):
        raise ValueError("Google token missing subject (sub)")

    if not data.get("email"):
        raise ValueError("Google token missing email — enable email scope in your OAuth consent screen")

    return data
